"""store.py — 任务/会话 JSON 持久化（原子写、损坏容忍）。"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, List


class TaskStore:
    """单一 JSON 状态文件：{"version": 1, "tasks": [...], "bt": {...}}。

    原子写（tmp + os.replace）防半截文件；读损坏时备份并重开空库。
    """

    VERSION = 1

    def __init__(self, path: str):
        self.path = path
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        d = os.path.dirname(os.path.abspath(self.path))
        if d:
            os.makedirs(d, exist_ok=True)

    def load(self) -> Dict[str, Any]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("tasks"), list):
                data.setdefault("bt", [])
                return data
            raise ValueError("结构不符")
        except FileNotFoundError:
            return {"version": self.VERSION, "tasks": [], "bt": []}
        except (json.JSONDecodeError, ValueError, OSError):
            # 损坏文件挪到 .bad 保留现场，重新开空库
            try:
                os.replace(self.path, self.path + ".bad")
            except OSError:
                pass
            return {"version": self.VERSION, "tasks": [], "bt": []}

    def save(self, tasks: List[Dict[str, Any]]) -> None:
        """原子写状态（tasks = HTTP + BT 全部任务记录）。"""
        self._ensure_dir()
        payload = {"version": self.VERSION, "tasks": tasks, "bt": []}
        fd, tmp = tempfile.mkstemp(
            prefix=".engine-state-", suffix=".tmp", dir=os.path.dirname(os.path.abspath(self.path))
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
