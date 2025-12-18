from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict
import base64
import aiohttp
from nonebot import get_driver, logger, on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment
from nonebot.matcher import Matcher
from PIL import Image, ImageDraw, ImageFont, ImageOps
from .data_manager import DataManager
from .ics_parser import ICSParser
from .image_generator import ImageGenerator
from .schedule_helper import ScheduleHelper
from io import BytesIO

SHANGHAI_TZ = timezone(timedelta(hours=8))

_driver = get_driver()

def image_to_base64(img: Image.Image, format='JPEG',quality=75) -> str:
    if img.mode == 'RGBA':
        img = img.convert('RGB')
    output_buffer = BytesIO()
    img.save(output_buffer, format=format, quality=quality)
    byte_data = output_buffer.getvalue()
    base64_str = base64.b64encode(byte_data).decode()
    return 'base64://' + base64_str

def _get_base_data_dir() -> Path:
    data_dir = getattr(_driver.config, "data_dir", None)
    if data_dir:
        return Path(str(data_dir)) / "course_schedule"
    return Path("data") / "course_schedule"


_data_manager = DataManager(_get_base_data_dir())
_ics_parser = ICSParser()
_image_generator = ImageGenerator()
_user_data: Dict = _data_manager.load_user_data()
_schedule_helper = ScheduleHelper(_data_manager, _ics_parser, _image_generator, _user_data)

# 绑定请求：key = "{group_id}-{user_id}"
_binding_requests: Dict[str, Dict] = {}


async def _download_to_file(url: str, dst: Path) -> bool:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return False
                dst.write_bytes(await resp.read())
                return True
    except Exception as e:
        logger.warning(f"下载文件失败: {e}")
        return False


async def _try_download_ics_from_message(bot: Bot, event: GroupMessageEvent, dst: Path) -> bool:
    """尽量从消息中解析并下载 .ics 文件（OneBot v11 常见实现）。"""
    for seg in event.get_message():
        if seg.type != "file":
            continue

        data = seg.data or {}

        url = data.get("url")
        if isinstance(url, str) and url.startswith("http"):
            return await _download_to_file(url, dst)

        file_id = data.get("file_id") or data.get("file")
        if not file_id:
            continue

        try:
            info = await bot.call_api("get_file", file_id=file_id)
            url2 = info.get("url") or info.get("download_url")
            if isinstance(url2, str) and url2.startswith("http"):
                return await _download_to_file(url2, dst)

            path = info.get("file")
            if isinstance(path, str) and os.path.exists(path):
                dst.write_bytes(Path(path).read_bytes())
                return True
        except Exception:
            pass

        try:
            info = await bot.call_api(
                "get_group_file_url", group_id=event.group_id, file_id=file_id
            )
            url3 = info.get("url")
            if isinstance(url3, str) and url3.startswith("http"):
                return await _download_to_file(url3, dst)
        except Exception:
            pass

    return False


def _img_seg_from_path(path: str) -> MessageSegment:
    p = os.path.abspath(path)
    return MessageSegment.image(f"file:///{p}")


# --------- 指令注册 ---------
bind_cmd = on_command("绑定课表", aliases={"/绑定课表"}, priority=5, block=True)
show_today_cmd = on_command("查看课表", aliases={"/查看课表"}, priority=5, block=True)
show_tomorrow_cmd = on_command("查看明日课表", aliases={"/查看明日课表"}, priority=5, block=True)
group_now_cmd = on_command("群友在上什么课", aliases={"/群友在上什么课"}, priority=5, block=True)
group_tomorrow_cmd = on_command("群友明天上什么课", aliases={"/群友明天上什么课"}, priority=5, block=True)
weekly_rank_cmd = on_command("本周上课排行", aliases={"/本周上课排行"}, priority=5, block=True)


@bind_cmd.handle()
async def _(matcher: Matcher, event: GroupMessageEvent):
    key = f"{event.group_id}-{event.user_id}"
    _binding_requests[key] = {
        "timestamp": time.time(),
        "group_id": str(event.group_id),
        "user_id": str(event.user_id),
        "nickname": event.sender.card or event.sender.nickname or str(event.user_id),
    }
    await matcher.finish("请在 60 秒内，在本群直接发送你的 .ics 文件或 WakeUp 分享口令。")


binding_listener = on_message(priority=4, block=False)


@binding_listener.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    key = f"{event.group_id}-{event.user_id}"
    req = _binding_requests.get(key)
    if not req:
        return

    if time.time() - req["timestamp"] > 60:
        _binding_requests.pop(key, None)
        return

    group_id = str(event.group_id)
    user_id = str(event.user_id)
    nickname = req.get("nickname") or user_id

    text = str(event.get_message()).strip()
    token = _ics_parser.parse_wakeup_token(text) if text else None
    if token:
        try:
            json_data = await _ics_parser.fetch_wakeup_schedule(token)
            if not json_data:
                await bot.send(event, "无法获取 WakeUp 课程表数据，请检查口令是否正确或已过期。")
                _binding_requests.pop(key, None)
                return

            ics_content = _ics_parser.convert_wakeup_to_ics(json_data)
            if not ics_content:
                await bot.send(event, "课程表数据解析失败，无法生成 ICS 文件。")
                _binding_requests.pop(key, None)
                return

            ics_file_path = _data_manager.get_ics_file_path(user_id, group_id)
            ics_file_path.write_text(ics_content, encoding="utf-8")

            if group_id not in _user_data:
                _user_data[group_id] = {"users": {}}

            _user_data[group_id]["users"][user_id] = {
                "nickname": nickname,
                "reminder": False,
            }
            _data_manager.save_user_data(_user_data)

            _ics_parser.clear_cache(str(ics_file_path))
            _binding_requests.pop(key, None)
            await bot.send(event, f"通过 WakeUp 口令绑定课表成功！群号：{group_id}")
            return
        except Exception as e:
            logger.error(f"处理 WakeUp 口令失败: {e}")
            _binding_requests.pop(key, None)
            await bot.send(event, f"处理 WakeUp 口令失败: {e}")
            return

    ics_file_path = _data_manager.get_ics_file_path(user_id, group_id)
    ok = await _try_download_ics_from_message(bot, event, ics_file_path)
    if not ok:
        return

    if not ics_file_path.exists():
        _binding_requests.pop(key, None)
        await bot.send(event, "文件下载失败，请重试。")

    if group_id not in _user_data:
        _user_data[group_id] = {"users": {}}

    _user_data[group_id]["users"][user_id] = {
        "nickname": nickname,
        "reminder": False,
    }
    _data_manager.save_user_data(_user_data)

    _ics_parser.clear_cache(str(ics_file_path))
    _binding_requests.pop(key, None)
    await bot.send(event, f"课表绑定成功！群号：{group_id}")


@show_today_cmd.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    now = datetime.now(SHANGHAI_TZ)
    today = now.date()

    courses, err = await _schedule_helper.get_schedule_for_date(
        str(event.user_id),
        str(event.group_id),
        event.sender.card or event.sender.nickname or str(event.user_id),
        today,
        "的今日课程",
    )
    if err:
        await bot.send(event, err)
        return

    image_path = await _image_generator.generate_user_schedule_image(
        courses, event.sender.card or event.sender.nickname or str(event.user_id), "的今日课程"
    )
    img = Image.open(image_path)
    await bot.finish(MessageSegment.image(image_to_base64(img)))


@show_tomorrow_cmd.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    now = datetime.now(SHANGHAI_TZ)
    tomorrow = now.date() + timedelta(days=1)

    courses, err = await _schedule_helper.get_schedule_for_date(
        str(event.user_id),
        str(event.group_id),
        event.sender.card or event.sender.nickname or str(event.user_id),
        tomorrow,
        "的明日课程",
    )
    if err:
        await bot.send(event, err)
        return

    image_path = await _image_generator.generate_user_schedule_image(
        courses, event.sender.card or event.sender.nickname or str(event.user_id), "的明日课程"
    )
    img = Image.open(image_path)
    await bot.finish(MessageSegment.image(image_to_base64(img)))


@group_now_cmd.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    now = datetime.now(SHANGHAI_TZ)
    today = now.date()

    next_courses, err = await _schedule_helper.get_group_schedule_for_date(
        str(event.group_id), today, is_today=True
    )
    if err:
        await bot.send(event, err)
        return

    image_path = await _image_generator.generate_schedule_image(next_courses, date_type="today")
    img = Image.open(image_path)
    await bot.finish(MessageSegment.image(image_to_base64(img)))


@group_tomorrow_cmd.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    now = datetime.now(SHANGHAI_TZ)
    tomorrow = now.date() + timedelta(days=1)

    next_courses, err = await _schedule_helper.get_group_schedule_for_date(
        str(event.group_id), tomorrow, is_today=False
    )
    if err:
        await bot.send(event, err)
        return

    image_path = await _image_generator.generate_schedule_image(next_courses, date_type="tomorrow")
    img = Image.open(image_path)
    await bot.finish(MessageSegment.image(image_to_base64(img)))


@weekly_rank_cmd.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    group_id = str(event.group_id)
    if not group_id or group_id not in _user_data:
        await bot.send(event, "本群还没有人绑定课表哦。")
        return

    now = datetime.now(SHANGHAI_TZ)
    today = now.date()
    start_of_week = today - timedelta(days=today.weekday())
    end_of_week = start_of_week + timedelta(days=6)

    ranking_data = []
    group_users = _user_data[group_id].get("users", {})

    for user_id, user_info in group_users.items():
        ics_file_path = _data_manager.get_ics_file_path(user_id, group_id)
        if not os.path.exists(ics_file_path):
            continue

        courses = _ics_parser.parse_ics_file(str(ics_file_path))
        total_duration = timedelta()
        course_count = 0

        for course in courses:
            start_time = course.get("start_time")
            end_time = course.get("end_time")
            if not start_time or not end_time:
                continue

            course_date = start_time.date()
            if start_of_week <= course_date <= end_of_week:
                total_duration += end_time - start_time
                course_count += 1

        if course_count > 0:
            ranking_data.append(
                {
                    "user_id": user_id,
                    "nickname": user_info.get("nickname", user_id),
                    "total_duration": total_duration,
                    "course_count": course_count,
                }
            )

    if not ranking_data:
        await bot.send(event, "本周大家都没有课呢！")
        return

    ranking_data.sort(key=lambda x: x["total_duration"], reverse=True)

    image_path = await _image_generator.generate_ranking_image(ranking_data, start_of_week, end_of_week)
    img = Image.open(image_path)
    await bot.finish(MessageSegment.image(image_to_base64(img)))
