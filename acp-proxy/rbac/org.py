"""组织架构表（Org Chart）— 角色路由=数据（Paperclip 抄底 D1/D2）。

- org 表：member / role / reports_to / team（数据驱动，不写死在流程定义里；
  流程 YAML 只写 role 不写人）
- 递归汇报链（D1，对位 Paperclip agent-reporting-chain 递归 CTE）：
  角色无可用成员时沿 reports_to 向上找 backup（主管兜底接单/审批）
- 落盘 JSON：<ORG_CHART_FILE 或 data/org-chart.json>（原子写；进程级单例）

职责边界：org 表管"人是谁、归谁管、什么角色"（路由数据）；RBAC 管"谁能做什么"
（权限）。路由解析先查 org 表，RBAC 角色表兜底（plugin.flow_collab.RoleRouter）。
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class OrgMember:
    """组织成员（人或员工 Agent 的责任人身份）。"""
    user_id: str
    name: str = ""
    role: str = ""          # 组织角色（路由键；可与 RBAC Role.id 对齐）
    team: str = ""
    reports_to: str = ""    # 上级 user_id（递归汇报链；空=顶层）
    metadata: dict = field(default_factory=dict)


class OrgChart:
    """组织架构表（JSON 落盘 + 线程安全）。

    查询面：
    - members_with_role(role)     角色成员（路由候选）
    - reporting_chain(user_id)    递归汇报链（自底向上，防环）
    - backup_for(role)            角色成员沿汇报链向上找 backup（D1）
    """

    def __init__(self, path: Path | str | None = None):
        env = os.environ.get("ORG_CHART_FILE")
        self.path = Path(path or env or
                         Path(__file__).resolve().parent.parent / "data" / "org-chart.json")
        self._lock = threading.Lock()
        self._members: dict[str, OrgMember] = {}
        self._loaded = False

    # -- 落盘 --

    def _load(self) -> None:
        if self._loaded:
            return
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                for item in data.get("members") or []:
                    member = OrgMember(**{k: v for k, v in item.items()
                                          if k in OrgMember.__dataclass_fields__})
                    self._members[member.user_id] = member
            except (json.JSONDecodeError, TypeError, ValueError):
                pass  # 坏文件显式容忍：空表起步（路由失败会显式报因）
        self._loaded = True

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"members": [asdict(m) for m in self._members.values()]}
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    # -- 写面 --

    def upsert(self, member: OrgMember) -> OrgMember:
        with self._lock:
            self._load()
            self._members[member.user_id] = member
            self._save()
        return member

    def remove(self, user_id: str) -> bool:
        with self._lock:
            self._load()
            existed = self._members.pop(user_id, None) is not None
            if existed:
                self._save()
            return existed

    # -- 查面 --

    def get(self, user_id: str) -> OrgMember | None:
        with self._lock:
            self._load()
            return self._members.get(user_id)

    def list_members(self) -> list[OrgMember]:
        with self._lock:
            self._load()
            return list(self._members.values())

    def members_with_role(self, role: str) -> list[OrgMember]:
        with self._lock:
            self._load()
            return [m for m in self._members.values() if m.role == role]

    def reporting_chain(self, user_id: str) -> list[str]:
        """递归汇报链（自底向上：[上级, 上级的上级, …]；防环，链长≤32）。"""
        chain: list[str] = []
        seen = {user_id}
        cursor = self.get(user_id)
        while cursor is not None and cursor.reports_to and cursor.reports_to not in seen \
                and len(chain) < 32:
            chain.append(cursor.reports_to)
            seen.add(cursor.reports_to)
            cursor = self.get(cursor.reports_to)
        return chain

    def backup_for(self, role: str) -> OrgMember | None:
        """角色无可用成员时的 backup（D1）：角色成员沿 reports_to 递归向上，
        汇报链上第一个组织成员即兜底人（主管接单/审批）。多成员取链最短者。"""
        candidates = self.members_with_role(role)
        best: OrgMember | None = None
        best_depth = 10 ** 9
        for member in candidates:
            for depth, ancestor_id in enumerate(self.reporting_chain(member.user_id)):
                ancestor = self.get(ancestor_id)
                if ancestor is not None and depth < best_depth:
                    best, best_depth = ancestor, depth
                    break
        return best


# 进程级单例（与 RBACStore 同惯例）
_chart: OrgChart | None = None
_chart_lock = threading.Lock()


def get_org_chart() -> OrgChart:
    global _chart
    with _chart_lock:
        if _chart is None:
            _chart = OrgChart()
        return _chart


def reset_org_chart() -> None:
    """测试用：重置单例（ORG_CHART_FILE 改后生效）。"""
    global _chart
    with _chart_lock:
        _chart = None
