"""picker.py — BT 分片调度：rarest-first + endgame（纯逻辑，单测覆盖）。

以 16KiB block 为请求单位。稀有优先：在对端拥有的候选片中挑「全网拥有者最少」的片；
剩余未完成 block 全部已派发时进入 endgame：允许向多个对端重复请求同一 block，
先到者胜出并向其余对端发 cancel。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple

BLOCK_SIZE = 16 * 1024


@dataclass(frozen=True)
class BlockReq:
    piece: int
    offset: int   # 片内偏移
    length: int

    @property
    def key(self) -> Tuple[int, int]:
        return (self.piece, self.offset)


class PiecePicker:
    def __init__(
        self,
        num_pieces: int,
        piece_length: int,
        total_length: int,
        only_files: Optional[Set[int]] = None,
    ):
        self.num_pieces = num_pieces
        self.piece_length = piece_length
        self.total_length = total_length
        self.only_pieces: Optional[Set[int]] = only_files  # 选择性下载时限定的片集合
        self.have: List[bool] = [False] * num_pieces
        self.done_blocks: Dict[int, Set[int]] = {}
        self.inflight: Dict[Tuple[int, int], Set[str]] = {}  # (piece, offset) -> 已派发对端集合
        self.availability: Counter = Counter()
        self._peer_pieces: Dict[str, Set[int]] = {}
        self.endgame = False

    # ── 尺寸 ────────────────────────────────────────────────

    def piece_size(self, piece: int) -> int:
        if piece < 0 or piece >= self.num_pieces:
            raise ValueError(f"片号越界: {piece}")
        if piece == self.num_pieces - 1:
            last = self.total_length - self.piece_length * (self.num_pieces - 1)
            return max(0, last)
        return self.piece_length

    def blocks_of(self, piece: int) -> List[Tuple[int, int]]:
        """[(片内偏移, 长度)]"""
        size = self.piece_size(piece)
        out = []
        off = 0
        while off < size:
            ln = min(BLOCK_SIZE, size - off)
            out.append((off, ln))
            off += ln
        return out

    # ── 对端位图维护 ─────────────────────────────────────────

    def peer_update(self, peer_id: str, has: Iterable[int]) -> None:
        new_set = set(has)
        old = self._peer_pieces.get(peer_id, set())
        for p in old - new_set:
            self.availability[p] -= 1
        for p in new_set - old:
            self.availability[p] += 1
        self._peer_pieces[peer_id] = new_set

    def peer_drop(self, peer_id: str) -> None:
        old = self._peer_pieces.pop(peer_id, set())
        for p in old:
            self.availability[p] -= 1
        for k, holders in list(self.inflight.items()):
            holders.discard(peer_id)
            if not holders:
                del self.inflight[k]

    # ── 进度 ────────────────────────────────────────────────

    def mark_have(self, piece: int) -> None:
        self.have[piece] = True
        self.done_blocks.pop(piece, None)
        for off, _ in self.blocks_of(piece):
            self.inflight.pop((piece, off), None)

    def block_received(self, piece: int, offset: int, from_peer: str | None = None) -> Tuple[Optional[BlockReq], List[str]]:
        """收到 block：登记完成；返回 (block 描述, 需要发 cancel 的重复请求对端)。

        后者用于 endgame：除交付者外还持有该请求的对端。
        """
        self.done_blocks.setdefault(piece, set()).add(offset)
        req = None
        for off, ln in self.blocks_of(piece):
            if off == offset:
                req = BlockReq(piece, offset, ln)
        holders = self.inflight.pop((piece, offset), set())
        if from_peer is not None:
            holders.discard(from_peer)
        dup_peers = sorted(holders)
        if self.piece_complete(piece):
            self.mark_have(piece)
        return req, dup_peers

    def piece_complete(self, piece: int) -> bool:
        if self.have[piece]:
            return True
        return self.done_blocks.get(piece, set()) >= {o for o, _ in self.blocks_of(piece)}

    @property
    def missing_pieces(self) -> int:
        return sum(1 for i in range(self.num_pieces) if not self.have[i] and self._wanted(i))

    def _wanted(self, piece: int) -> bool:
        return self.only_pieces is None or piece in self.only_pieces

    def is_complete(self) -> bool:
        return all(self.have[i] or not self._wanted(i) for i in range(self.num_pieces))

    # ── 选取 ────────────────────────────────────────────────

    def pick(self, peer_id: str, max_n: int = 8) -> List[BlockReq]:
        """rarest-first 选 block；无新 block 可选时进入 endgame 重复借还。"""
        peer_has = self._peer_pieces.get(peer_id, set())
        candidates = [
            p
            for p in peer_has
            if not self.have[p] and self._wanted(p) and not self._piece_fully_requested(p)
        ]
        candidates.sort(key=lambda p: (self.availability[p], p))
        out: List[BlockReq] = []
        for piece in candidates:
            for off, ln in self.blocks_of(piece):
                if off in self.done_blocks.get(piece, set()):
                    continue
                if (piece, off) in self.inflight:
                    continue
                self.inflight.setdefault((piece, off), set()).add(peer_id)
                out.append(BlockReq(piece, off, ln))
                if len(out) >= max_n:
                    return out
        if not out:
            # endgame：所有剩余 block 都已派发 → 允许重复请求
            self.endgame = True
            for piece in candidates_endgame(self, peer_has):
                for off, ln in self.blocks_of(piece):
                    if off in self.done_blocks.get(piece, set()):
                        continue
                    if peer_id in self.inflight.get((piece, off), set()):
                        continue
                    self.inflight.setdefault((piece, off), set()).add(peer_id)
                    out.append(BlockReq(piece, off, ln))
                    if len(out) >= max_n:
                        return out
        return out

    def _piece_fully_requested(self, piece: int) -> bool:
        done = self.done_blocks.get(piece, set())
        return all(
            off in done or (piece, off) in self.inflight for off, _ in self.blocks_of(piece)
        )


def candidates_endgame(picker: PiecePicker, peer_has: Set[int]) -> List[int]:
    lst = [p for p in peer_has if not picker.have[p] and picker._wanted(p)]
    lst.sort(key=lambda p: (picker.availability[p], p))
    return lst
