"""CronReporter — 路线图 3.2 定时汇报（主动向 OpenMate 推送任务进展/异常）。

缺口（路线图原文）："3.2 定时汇报：主动向 OpenMate 推送任务进展/异常"；
3.1遗留注记："cron回合turn_origin=cron已落库但wakeup_relay只推wakeup标记行，
实时推送归3.2"。

方案（对齐 wakeup_relay 已验证的"落库行=单一真源+轮询推送"模式，不新造通道）：
1. **汇报行落库**：每次定时任务执行完（成功/失败都报），CronReporter 把一条
   确定性生成的 📊 汇报行写进该任务会话的 agent_messages（role=assistant，
   metadata.turn_origin=cron_report）——进展/异常即使在"回合零输出"失败形态下
   也有可见产物（failed 回合本身可能什么都没留下，汇报行兜底可见）；
2. **实时推送**：wakeup_relay 把 turn_origin ∈ {wakeup, cron, cron_report} 的
   落库行补推给正连该会话的前端（cron 回合行 + 汇报行都在此列）——任务进展/
   异常对正连 OpenMate 的用户即时可见；未连接时历史加载同样可见（行已落库）。

纪律：
- 汇报内容确定性拼装（任务名/状态✅❌/结果或异常/执行与下次时间/连续失败提醒），
  不走LLM——汇报是事实转述，不需要生成；
- 汇报失败绝不阻塞调度链路（fire/落日志/排下次优先级更高），只计数+日志；
- 无 session_id 的任务无处可报 → 显式跳过（计数可见），不写野行。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime

logger = logging.getLogger("acp-proxy.cron_reporter")

# 汇报行出身标记（wakeup_relay 实时推送识别依据；与 wakeup/cron 同族）
CRON_REPORT_TURN_ORIGIN = "cron_report"

_OPENSOUL_ROOT = os.environ.get("OPENSOUL_ROOT", "/home/climbing/opensoul")
DEFAULT_DB_PATH = os.environ.get(
    "SOULMATE_REPORT_DB_PATH", f"{_OPENSOUL_ROOT}/data/opensoul.db")
_DB_TIMEOUT_S = 30.0
_DETAIL_CAP = 300

# 连续失败N次后在汇报里显式提醒（与 scheduler._FAIL_STREAK_HINT 同值语义）
_FAIL_STREAK_HINT = 3

# 模块级观测计数（失败必须可见，mem0 §1.1）
_STATS: dict = {
    "reports_built": 0,
    "reports_written": 0,
    "write_errors": 0,
    "skipped_no_session": 0,
}


def report_stats() -> dict:
    """汇报面观测快照（scheduler snapshot / 测试消费面）"""
    return dict(_STATS)


def _fmt_ms(ms: int | None) -> str:
    """毫秒UTC → 本地 'MM-DD HH:MM'（全局时间规范：存储UTC展示本地）"""
    if not ms:
        return "-"
    try:
        return datetime.fromtimestamp(int(ms) / 1000).strftime("%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return "-"


def build_run_report(
    task: dict,
    status: str,
    detail: str,
    next_run_ms: int | None = None,
    fail_streak: int = 0,
    now_ms: int | None = None,
) -> str:
    """定时任务执行结果 → 确定性汇报文本（成功/失败同构，状态行不同）。

    形状（测试即规格）：
      📊 [定时汇报] 任务#<id> <name>｜✅ 成功
      结果: <detail>
      执行: <MM-DD HH:MM>（下次 <MM-DD HH:MM>）
    失败态首行｜❌ 失败、detail 行头改"异常:"，连续失败≥3次追加"（连续失败N次，请关注）"。
    """
    task = task or {}
    ok = str(status) == "ok"
    icon = "✅ 成功" if ok else "❌ 失败"
    detail_s = str(detail or "").strip()[:_DETAIL_CAP] or "（无详情）"
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    streak = int(fail_streak or 0)
    hint = ""
    if not ok and streak >= _FAIL_STREAK_HINT:
        hint = f"（连续失败{streak}次，请关注）"
    lines = [
        f"📊 [定时汇报] 任务#{task.get('id', '?')} {task.get('name', '')}｜{icon}",
        f"{'结果' if ok else '异常'}: {detail_s}",
        f"执行: {_fmt_ms(now)}（下次 {_fmt_ms(next_run_ms)}）{hint}",
    ]
    _STATS["reports_built"] += 1
    return "\n".join(lines)


class CronReporter:
    """定时汇报落库面：汇报行写 opensoul.db（wakeup_relay 推送面的单一真源）。"""

    def __init__(self, db_path: str | None = None, now_fn=None):
        self._db_path = (
            db_path or os.environ.get("SOULMATE_REPORT_DB_PATH") or DEFAULT_DB_PATH)
        self._now_fn = now_fn or (lambda: int(time.time() * 1000))

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path, timeout=_DB_TIMEOUT_S)

    def write_report(
        self,
        session_id: str,
        report: str,
        task_id: int | None = None,
        status: str = "",
    ) -> int | None:
        """汇报行落 agent_messages（role=assistant + turn_origin=cron_report）。

        返回新行id；落库异常计数后上抛（report() 收口吞掉，不阻塞调度）。
        """
        meta = {
            "turn_origin": CRON_REPORT_TURN_ORIGIN,
            "cron_task_id": int(task_id) if isinstance(task_id, (int, float)) else None,
            "status": str(status or ""),
        }
        db = self._connect()
        try:
            cur = db.execute(
                "INSERT INTO agent_messages (session_id, role, content, timestamp,"
                " metadata) VALUES (?,?,?,?,?)",
                (str(session_id), "assistant", str(report),
                 self._now_fn() / 1000, json.dumps(meta, ensure_ascii=False)),
            )
            try:  # 会话计数/活跃时间联动（表缺失只跳过不炸）
                db.execute(
                    "UPDATE agent_sessions SET message_count=message_count+1,"
                    " last_activity_at=? WHERE id=?",
                    (self._now_fn() / 1000, str(session_id)))
            except sqlite3.OperationalError:
                pass
            db.commit()
            row_id = int(cur.lastrowid)
        finally:
            db.close()
        _STATS["reports_written"] += 1
        return row_id

    def report(
        self,
        task: dict,
        status: str,
        detail: str,
        next_run_ms: int | None = None,
        fail_streak: int = 0,
    ) -> str:
        """一次执行结果的完整汇报链路：拼文本→落库。返回汇报文本（跳过/失败也返回）。

        绝不抛异常——汇报失败不能阻塞调度主链路（fire/日志/排下次优先级更高）。
        """
        task = task or {}
        text = build_run_report(
            task, status, detail,
            next_run_ms=next_run_ms if next_run_ms is not None
            else task.get("next_run_at_ms"),
            fail_streak=fail_streak,
            now_ms=self._now_fn(),
        )
        sid = str(task.get("session_id") or "").strip()
        if not sid:
            _STATS["skipped_no_session"] += 1
            logger.info("[cron-report] 任务#%s 无session_id，汇报无处可投，跳过落库",
                        task.get("id", "?"))
            return text
        try:
            self.write_report(sid, text, task_id=task.get("id"), status=status)
        except Exception as e:
            _STATS["write_errors"] += 1
            logger.warning("[cron-report] 汇报落库失败（不阻塞调度）: %s", e)
        return text
