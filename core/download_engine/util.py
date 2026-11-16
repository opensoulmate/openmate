"""util.py — 小工具。"""

from __future__ import annotations

import os
import time


def generate_token() -> str:
    """本地 REST token（随机 32 hex）。"""
    return os.urandom(16).hex()


def now() -> float:
    return time.time()
