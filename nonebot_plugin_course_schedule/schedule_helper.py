# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple


class ScheduleHelper:
    """课表查询辅助类（NoneBot 版）"""

    def __init__(self, data_manager, ics_parser, image_generator, user_data: Dict):
        self.data_manager = data_manager
        self.ics_parser = ics_parser
        self.image_generator = image_generator
        self.user_data = user_data

    async def get_schedule_for_date(
        self,
        user_id: str,
        group_id: str,
        sender_name: str,
        target_date,
        date_description: str,
    ) -> Tuple[Optional[List[Dict]], Optional[str]]:
        """根据指定日期获取个人课程安排"""
        if (
            not group_id
            or group_id not in self.user_data
            or user_id not in self.user_data[group_id].get("users", {})
        ):
            return None, "你还没有在这个群绑定课表哦，请在群内发送 /绑定课表 指令，然后发送 .ics 文件或 WakeUp 分享口令来绑定。"

        ics_file_path = self.data_manager.get_ics_file_path(user_id, group_id)
        if not os.path.exists(ics_file_path):
            return None, "课表文件不存在，可能已被删除。请重新绑定。"

        courses = self.ics_parser.parse_ics_file(str(ics_file_path))

        shanghai_tz = timezone(timedelta(hours=8))
        now = datetime.now(shanghai_tz)

        target_courses: List[Dict] = []
        for course in courses:
            if course.get("start_time") and course["start_time"].date() == target_date:
                if target_date == now.date():
                    # 今天只显示“还没开始”的课（和原逻辑一致）
                    if course["start_time"] > now:
                        target_courses.append(course)
                else:
                    target_courses.append(course)

        if not target_courses:
            date_map = {"的今日课程": "今天", "的明日课程": "明天"}
            date_str = date_map.get(date_description, "")
            return None, f"你{date_str}没有课啦！" if date_str else "这天你没有课啦！"

        target_courses.sort(key=lambda x: x["start_time"])

        nickname = (
            self.user_data[group_id]["users"].get(user_id, {}).get("nickname", sender_name)
        )
        for course in target_courses:
            course["nickname"] = nickname

        return target_courses, None

    async def get_group_schedule_for_date(
        self,
        group_id: str,
        target_date,
        is_today: bool = True,
    ) -> Tuple[Optional[List[Dict]], Optional[str]]:
        """根据指定日期获取群友课程安排"""
        if not group_id or group_id not in self.user_data:
            return None, "本群还没有人绑定课表哦。"

        shanghai_tz = timezone(timedelta(hours=8))
        now = datetime.now(shanghai_tz)
        next_courses: List[Dict] = []

        group_users = self.user_data[group_id].get("users", {})
        for user_id, user_info in group_users.items():
            nickname = user_info.get("nickname", user_id)
            ics_file_path = self.data_manager.get_ics_file_path(user_id, group_id)
            if not os.path.exists(ics_file_path):
                continue

            courses = self.ics_parser.parse_ics_file(str(ics_file_path))
            target_date_courses = [
                c for c in courses
                if c.get("start_time") and c["start_time"].date() == target_date
            ]

            user_next_course = None
            if is_today:
                user_current_course = None
                user_future_course = None
                for course in target_date_courses:
                    start_time = course.get("start_time")
                    end_time = course.get("end_time")
                    if not start_time or not end_time:
                        continue

                    if start_time <= now < end_time:
                        user_current_course = course
                        break
                    elif start_time > now:
                        if user_future_course is None or start_time < user_future_course.get("start_time"):
                            user_future_course = course
                user_next_course = user_current_course if user_current_course else user_future_course
            else:
                for course in target_date_courses:
                    start_time = course.get("start_time")
                    if start_time and (user_next_course is None or start_time < user_next_course.get("start_time")):
                        user_next_course = course

            if user_next_course:
                next_courses.append(
                    {
                        "summary": user_next_course.get("summary"),
                        "description": user_next_course.get("description"),
                        "location": user_next_course.get("location"),
                        "start_time": user_next_course.get("start_time"),
                        "end_time": user_next_course.get("end_time"),
                        "user_id": user_id,
                        "nickname": nickname,
                    }
                )
            else:
                summary = "今日无课" if is_today else "明日无课"
                next_courses.append(
                    {
                        "summary": summary,
                        "description": "",
                        "location": "",
                        "start_time": None,
                        "end_time": None,
                        "user_id": user_id,
                        "nickname": nickname,
                    }
                )

        if not next_courses:
            return None, f"群友们{'接下来都没有课啦！' if is_today else '明天都没有课啦！'}"

        next_courses.sort(key=lambda x: (x["start_time"] is None, x["start_time"]))
        return next_courses, None
