"""dht.py — DHT（BEP5）接口占位：二期实现，先保证磁力走 tracker（公共 tracker 注入）。

保留统一接口位：BT 引擎只依赖 `get_peers(info_hash)`，二期把实现换成真 DHT
（KRPC over UDP、路由表、get_peers/find_node）即可，调用方零改动。
"""

from __future__ import annotations

from typing import List, Tuple


class DhtClient:
    """二期：DHT 节点查询。当前恒返回空 peer 列表。"""

    def __init__(self, bootstrap: List[str] | None = None):
        self.bootstrap = bootstrap or []

    async def get_peers(self, info_hash: bytes) -> List[Tuple[str, int]]:
        """返回 [(host, port)]。二期：KRPC get_peers 查询。"""
        return []

    async def close(self) -> None:
        return None
