from __future__ import annotations

import json
from pathlib import Path
from typing import Dict


class DataManager:
    """数据管理类"""

    def __init__(self, base_dir: Path):
        self.data_path: Path = base_dir
        self.ics_path: Path = self.data_path / "ics"
        self.user_data_file: Path = self.data_path / "userdata.json"
        self._init_data()

    def _init_data(self) -> None:
        """初始化插件数据文件和目录"""
        self.data_path.mkdir(parents=True, exist_ok=True)
        self.ics_path.mkdir(exist_ok=True)
        if not self.user_data_file.exists():
            self.user_data_file.write_text("{}", encoding="utf-8")

    def load_user_data(self) -> Dict:
        """加载用户数据"""
        try:
            return json.loads(self.user_data_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save_user_data(self, user_data: Dict) -> None:
        """保存用户数据"""
        self.user_data_file.write_text(
            json.dumps(user_data, ensure_ascii=False, indent=4),
            encoding="utf-8",
        )

    def get_ics_file_path(self, user_id: str, group_id: str) -> Path:
        """获取用户的 ICS 文件路径"""
        return self.ics_path / f"{user_id}_{group_id}.ics"
