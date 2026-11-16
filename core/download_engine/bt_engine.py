"""bt_engine.py — BT 下载/做种会话（自研内核）。

组成：tracker announce（公共 tracker 注入）→ peer 连接（BEP3 handshake / bitfield /
request / piece / have）→ rarest-first + endgame 分片调度（picker.py）→ SHA1 逐片校验
→ 落盘（pwrite 扁平偏移映射）→ 完成后做种（响应 request）。

磁力链：先 ut_metadata（BEP9/10）向对端拉 info dict（sha1==infohash 校验）再进下载。
DHT 二期（dht.py 接口位），当前磁力 peer 来源=tracker（公共 tracker 注入）。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from . import metadata as md, peer as pw, torrent as torrent_mod, trackers
from .dht import DhtClient
from .lsd import LsdClient
from .magnet import Magnet, parse as parse_magnet
from .models import DownloadTask, TaskStatus, TaskType
from .picker import PiecePicker

MAX_PEERS = 30
MAX_METADATA_PEERS = 12
PER_PEER_INFLIGHT = 12
METADATA_TIMEOUT = 90.0
CONNECT_TIMEOUT = 10.0


class BtError(Exception):
    pass


def parse_select_files(spec: Optional[str]) -> Optional[Set[int]]:
    """"1-3,5" → {0,1,2,4}（1-based 文件序号 → 0-based）。"""
    if not spec:
        return None
    out: Set[int] = set()
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                a, _, b = part.partition("-")
                out.update(range(int(a), int(b) + 1))
            else:
                out.add(int(part))
        except ValueError as e:
            raise BtError(f"select_files 非法: {spec!r}") from e
    return {i - 1 for i in out if i >= 1} or None


class PieceWriter:
    """扁平字节偏移 → 落盘文件映射（读写都走偏移，支撑选择性下载与做种回传）。"""

    def __init__(self, base_dir: str, files: List[torrent_mod.TorrentFile]):
        self.base = base_dir
        self.files = files

    def _map(self, offset: int, length: int) -> List[Tuple[str, int, int]]:
        """[(相对路径, 文件内偏移, 本文件内字节数)]（按扁平偏移顺序）"""
        out = []
        end = offset + length
        for f in self.files:
            s, e = max(offset, f.offset), min(end, f.offset + f.length)
            if e > s:
                out.append((f.path, s - f.offset, e - s))
        return out

    def ensure_files(self) -> None:
        for f in self.files:
            path = os.path.join(self.base, *f.path.split("/"))
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            if not os.path.exists(path):
                with open(path, "wb") as fh:
                    if f.length > 0:
                        fh.truncate(f.length)

    def write_at(self, offset: int, data: bytes) -> None:
        pos = 0
        for path, off, ln in self._map(offset, len(data)):
            full = os.path.join(self.base, *path.split("/"))
            parent = os.path.dirname(full)
            if parent:
                os.makedirs(parent, exist_ok=True)
            fd = os.open(full, os.O_RDWR | os.O_CREAT, 0o644)
            try:
                os.pwrite(fd, data[pos : pos + ln], off)
            finally:
                os.close(fd)
            pos += ln

    def read_at(self, offset: int, length: int) -> bytes:
        chunks = []
        for path, off, ln in self._map(offset, length):
            full = os.path.join(self.base, *path.split("/"))
            fd = os.open(full, os.O_RDONLY)
            try:
                chunks.append(os.pread(fd, ln, off))
            finally:
                os.close(fd)
        return b"".join(chunks)


class BtSession:
    """单个 BT 任务运行时（下载 + 做种）。"""

    def __init__(
        self,
        task: DownloadTask,
        source: str,
        meta: Optional[torrent_mod.TorrentMeta],
        magnet: Optional[Magnet],
        only_files: Optional[Set[int]],
        persist: Callable[[], None],
        dht: Optional[DhtClient] = None,
    ):
        self.task = task
        self.source = source
        self.meta = meta
        self.magnet = magnet
        self.only_files = only_files
        self._persist = persist
        self.dht = dht or DhtClient()
        self.cancel = asyncio.Event()
        self.peer_id = b"-OM0100-" + os.urandom(12)
        self.picker: Optional[PiecePicker] = None
        self.writer: Optional[PieceWriter] = None
        self.piece_bufs: Dict[int, Dict[int, bytes]] = {}
        self.peers: Dict[str, pw.PeerConnection] = {}
        self.known_peers: Set[Tuple[str, int]] = set()
        self.used_peers: Set[Tuple[str, int]] = set()
        self.seeding = False
        self.listen_port = 0  # 引擎 BT 监听端口（tracker/DHT/LSD announce 用）
        self.use_public_trackers = True  # options.use_public_trackers=false → 只走 DHT/PEX/LSD
        self.metadata_timeout = METADATA_TIMEOUT  # options.metadata_timeout 可调
        self._run_task: Optional[asyncio.Task] = None
        self._done_bytes = 0
        self._sample_t = time.monotonic()
        self._tracker_urls: List[str] = []
        self._stopping = False

    # ── 任务入口 ────────────────────────────────────────────

    async def run(self) -> None:
        self._run_task = asyncio.current_task()
        try:
            self.task.status = TaskStatus.DOWNLOADING
            self.task.error = None
            if self.meta is None:
                await self._fetch_metadata()
            self._prepare_from_meta()
            await self._swarm_loop()
        except asyncio.CancelledError:
            if self.task.status == TaskStatus.DOWNLOADING:
                self.task.status = TaskStatus.PAUSED
            raise
        except Exception as e:  # noqa: BLE001
            if not self._stopping:
                self.task.status = TaskStatus.ERROR
                self.task.error = str(e)
        finally:
            self._persist()

    async def stop(self, timeout: float = 5.0) -> None:
        """请求停止并等待 run() 退出（pause/remove 共用）。"""
        self._stopping = True
        self.cancel.set()
        t = self._run_task
        if t is not None and not t.done():
            try:
                await asyncio.wait_for(asyncio.shield(t), timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self.cancel = asyncio.Event()
        self._stopping = False

    def _prepare_from_meta(self) -> None:
        assert self.meta is not None
        self._tracker_urls = list(dict.fromkeys(self.meta.announce_list))
        pl = self.meta.piece_length
        num = self.meta.num_pieces
        if self.picker is None:
            only_pieces = None
            if self.only_files:
                only_pieces = set()
                for idx in sorted(self.only_files):
                    if idx < len(self.meta.files):
                        f = self.meta.files[idx]
                        first = f.offset // pl
                        last = (f.offset + max(f.length, 1) - 1) // pl
                        only_pieces.update(range(first, min(last + 1, num)))
            self.picker = PiecePicker(num, pl, self.meta.total_length, only_pieces)
            self.writer = PieceWriter(self.task.dir, self.meta.files)
            self.writer.ensure_files()
        if self.task.filename in ("", "download", "magnet-download"):
            self.task.filename = self.meta.name
        self.task.total_bytes = self.meta.total_length
        self._update_progress()

    def _update_progress(self) -> None:
        if self.picker is None:
            return
        done = sum(self.picker.piece_size(i) for i, h in enumerate(self.picker.have) if h)
        self.task.downloaded_bytes = done
        now = time.monotonic()
        dt = now - self._sample_t
        if dt >= 1.0:
            self.task.speed = max(0, int((done - self._done_bytes) / dt))
            self._done_bytes = done
            self._sample_t = now
        self.task.refresh_progress()

    # ── 磁力元数据获取（BEP9/10） ────────────────────────────

    async def _fetch_metadata(self) -> None:
        assert self.magnet is not None
        info_hash = self.magnet.info_hash
        asm = md.MetadataAssembler(info_hash)
        deadline = time.monotonic() + self.metadata_timeout
        attempted: Set[Tuple[str, int]] = set()
        while not asm.complete() and time.monotonic() < deadline:
            if self.cancel.is_set():
                raise asyncio.CancelledError()
            peers = await self._collect_peers(attempted)
            if not peers:
                await asyncio.sleep(2)
                continue
            tasks = [
                asyncio.get_running_loop().create_task(
                    self._metadata_from_peer(host, port, info_hash, asm)
                )
                for host, port in peers[:MAX_METADATA_PEERS]
            ]
            try:
                await asyncio.wait(tasks, timeout=max(1.0, deadline - time.monotonic()))
            except asyncio.CancelledError:
                for t in tasks:
                    t.cancel()
                raise
            for t in tasks:
                t.cancel()
        if not asm.complete():
            raise BtError("磁力元数据获取失败：tracker/对端均未通过 ut_metadata 交付元数据（超时）")
        raw = asm.assemble()  # sha1(元数据) == infohash 校验
        self.meta = torrent_mod.parse_info_raw(raw, self._magnet_trackers())
        self.task.total_bytes = self.meta.total_length

    async def _metadata_from_peer(
        self, host: str, port: int, info_hash: bytes, asm: md.MetadataAssembler
    ) -> None:
        conn = await pw.PeerConnection.connect(host, port, CONNECT_TIMEOUT)
        try:
            await conn.do_handshake(info_hash, self.peer_id)
            await conn.send(pw.build_ext_handshake())
            remote_id: Optional[int] = None
            msgs = 0
            while not asm.complete() and msgs < asm.num_pieces + 20 and not self.cancel.is_set():
                try:
                    msg_id, payload = await asyncio.wait_for(conn.read_message(), 15)
                except asyncio.TimeoutError:
                    break
                msgs += 1
                if msg_id != pw.MSG_EXTENDED:
                    continue
                ext_id, body = pw.parse_extended(payload)
                if ext_id == 0:
                    remote_id, msize, _pex = pw.parse_ext_handshake(body)
                    if remote_id is None:
                        break
                    if asm.total_size is None:
                        if msize is None:
                            break
                        asm.total_size = msize
                    for p in asm.needed_pieces()[:4]:
                        await conn.send(pw.build_extended(remote_id, md.encode_request(p)))
                elif ext_id == pw.LOCAL_UT_METADATA_ID and remote_id is not None:
                    mtype, info = md.parse_message(body)
                    if mtype == md.MSG_TYPE_DATA:
                        _, block = md.split_data(body)
                        if asm.total_size is None and info.get("total_size"):
                            asm.total_size = info["total_size"]
                        asm.add(info["piece"], block)
                        need = asm.needed_pieces()
                        if need:
                            await conn.send(
                                pw.build_extended(remote_id, md.encode_request(need[0]))
                            )
                        else:
                            break
                    else:  # reject
                        break
        finally:
            await conn.close()

    def _magnet_trackers(self) -> List[str]:
        urls = list(self.magnet.trackers) if self.magnet else []
        if self.use_public_trackers:  # 公共 tracker 注入（options.use_public_trackers=false 可关）
            for t in trackers.PUBLIC_TRACKERS:
                if t not in urls:
                    urls.append(t)
        return urls

    async def _collect_peers(self, attempted: Set[Tuple[str, int]]) -> List[Tuple[str, int]]:
        info_hash = self.magnet.info_hash if self.magnet else self.meta.info_hash  # type: ignore[union-attr]
        urls = self._tracker_urls or self._magnet_trackers()
        self._tracker_urls = urls
        params = trackers.build_announce_params(
            info_hash, self.peer_id, self.listen_port or 6881, 0,
            self.task.downloaded_bytes, max(1, self.task.total_bytes), "started",
        )
        found: Set[Tuple[str, int]] = set()
        for url in urls:
            try:
                _, peers = await asyncio.wait_for(trackers.announce(url, params, timeout=8), 10)
                found.update(peers)
            except Exception:  # noqa: BLE001
                continue
        # peer 来源并集：tracker ∪ DHT（用户添加/PEX/LSD 已预先入 known_peers）
        try:
            found.update(await self.dht.get_peers(info_hash))
        except Exception:  # noqa: BLE001
            pass
        out = [p for p in found if p not in attempted and p not in self.used_peers]
        if not out and found:
            # 新 peer 枯竭：允许重试之前失败的（防火墙 peer 可能时来时走）
            attempted.clear()
            out = [p for p in found if p not in self.used_peers]
        attempted.update(out)
        self.known_peers.update(out)
        for p in out:
            self.dht.report_peer(info_hash, p)  # 回馈 DHT 网络
        return out

    # ── 下载/做种主循环 ──────────────────────────────────────

    async def _swarm_loop(self) -> None:
        assert self.picker is not None and self.meta is not None
        announcer = asyncio.get_running_loop().create_task(self._announce_loop())
        connector = asyncio.get_running_loop().create_task(self._connect_loop())
        try:
            while not self.picker.is_complete():
                if self.cancel.is_set():
                    raise asyncio.CancelledError()
                await asyncio.sleep(0.2)
            # 完成 → 做种
            self.task.status = TaskStatus.DONE
            self.task.speed = 0
            self.task.downloaded_bytes = self.task.total_bytes
            self.task.refresh_progress()
            self.seeding = True
            self._persist()
            while not self.cancel.is_set():
                await asyncio.sleep(1)
                self._update_progress()
        finally:
            announcer.cancel()
            connector.cancel()
            for c in list(self.peers.values()):
                await c.close()
            self.peers.clear()

    async def _announce_loop(self) -> None:
        info_hash = self.meta.info_hash  # type: ignore[union-attr]
        # tracker 来源：torrent 自带 announce-list；磁力任务在 _magnet_trackers 里已注入公共 tracker
        urls = list(dict.fromkeys(self._tracker_urls))
        if not urls and self.magnet is not None and self.use_public_trackers:
            urls = trackers.PUBLIC_TRACKERS
        event: Optional[str] = "started"
        while True:
            params = trackers.build_announce_params(
                info_hash, self.peer_id, self.listen_port or 6881, 0,
                self.task.downloaded_bytes,
                max(0, self.task.total_bytes - self.task.downloaded_bytes),
                event,
            )
            event = None
            min_interval = 1800
            for url in urls:
                try:
                    interval, peers = await asyncio.wait_for(
                        trackers.announce(url, params, timeout=8), 10
                    )
                    min_interval = min(min_interval, max(60, interval))
                    for p in peers:
                        if p not in self.used_peers:
                            self.known_peers.add(p)
                except Exception:  # noqa: BLE001
                    continue
            await asyncio.sleep(min_interval)

    async def _connect_loop(self) -> None:
        while not self.cancel.is_set():
            if len(self.peers) >= MAX_PEERS:
                await asyncio.sleep(1)
                continue
            candidates = [p for p in self.known_peers if p not in self.used_peers]
            for host, port in candidates[: MAX_PEERS - len(self.peers)]:
                self.used_peers.add((host, port))
                asyncio.get_running_loop().create_task(self._peer_pump(host, port))
            await asyncio.sleep(1)

    async def _peer_pump(self, host: str, port: int) -> None:
        info_hash = self.meta.info_hash  # type: ignore[union-attr]
        key = f"{host}:{port}"
        try:
            conn = await pw.PeerConnection.connect(host, port, CONNECT_TIMEOUT)
        except (OSError, asyncio.TimeoutError):
            self.used_peers.discard((host, port))
            return
        self.peers[key] = conn
        try:
            await conn.do_handshake(info_hash, self.peer_id)
        except (asyncio.CancelledError, asyncio.IncompleteReadError, ConnectionError, pw.PeerError, OSError):
            self.peers.pop(key, None)
            await conn.close()
            return
        await self._run_peer(conn, key)

    async def _run_peer(self, conn: pw.PeerConnection, key: str) -> None:
        """握手完成后的对端消息泵（出站/入站共用）。"""
        try:
            if conn.supports_ext:
                msize = len(self.meta.info_raw) if self.meta else None  # type: ignore[union-attr]
                await conn.send(pw.build_ext_handshake(msize))
            assert self.picker is not None
            if any(self.picker.have):
                await conn.send(
                    pw.build_bitfield(pw.bitfield_bytes(self.picker.num_pieces, self.picker.have))
                )
            await conn.send(pw.build_msg(pw.MSG_INTERESTED))
            conn.interested = True
            outstanding = 0
            idle_rounds = 0
            while not self.cancel.is_set():
                try:
                    msg_id, payload = await asyncio.wait_for(conn.read_message(), 60)
                except asyncio.TimeoutError:
                    await conn.send(pw.build_keepalive())
                    idle_rounds += 1
                    if idle_rounds > 6:
                        break
                    continue
                idle_rounds = 0
                if msg_id is None:
                    continue
                if msg_id == pw.MSG_CHOKE:
                    conn.choked = True
                elif msg_id == pw.MSG_UNCHOKE:
                    conn.choked = False
                elif msg_id == pw.MSG_HAVE:
                    self._peer_has(key, {pw.parse_have(payload)})
                elif msg_id == pw.MSG_BITFIELD:
                    have = {
                        i
                        for i, h in enumerate(
                            pw.bitfield_pieces(payload, self.picker.num_pieces)
                        )
                        if h
                    }
                    self._peer_has(key, have)
                elif msg_id == pw.MSG_REQUEST:
                    await self._serve_request(conn, *pw.parse_request(payload))
                elif msg_id == pw.MSG_PIECE:
                    index, begin, block = pw.parse_piece(payload)
                    outstanding = max(0, outstanding - 1)
                    await self._on_block(key, index, begin, block)
                elif msg_id == pw.MSG_CANCEL:
                    pass
                elif msg_id == pw.MSG_EXTENDED:
                    ext_id, body = pw.parse_extended(payload)
                    if ext_id == 0:
                        conn.remote_ut_metadata, conn.remote_metadata_size, conn.remote_ut_pex = (
                            pw.parse_ext_handshake(body)
                        )
                        if conn.remote_ut_pex is not None:
                            await self._send_pex(conn)
                    elif ext_id == pw.LOCAL_UT_PEX_ID:
                        self._on_pex(body)
                        if conn.remote_ut_pex is not None:
                            await self._send_pex(conn)  # 礼尚往来
                # 填充下载请求（rarest-first；endgame 由 picker 处理重复借还）
                if not conn.choked and not self.seeding:
                    reqs = self.picker.pick(key, PER_PEER_INFLIGHT - outstanding)
                    for r in reqs:
                        await conn.send(pw.build_request(r.piece, r.offset, r.length))
                        outstanding += 1
        except (asyncio.CancelledError, asyncio.IncompleteReadError, ConnectionError, pw.PeerError, OSError):
            pass
        finally:
            self.peers.pop(key, None)
            if self.picker is not None:
                self.picker.peer_drop(key)
            await conn.close()

    # ── PEX（BEP11） ─────────────────────────────────────────

    def _on_pex(self, payload: bytes) -> None:
        """ut_pex 增量合并进 peer 池（added 采纳、dropped 剔除）。"""
        try:
            added, added6, dropped = pw.parse_pex(payload)
        except pw.PeerError:
            return
        info_hash = self.meta.info_hash if self.meta else None  # type: ignore[union-attr]
        for p in added + added6:
            self.known_peers.add(p)  # adopted：known_peers 是"认识的"，含在用的
            if info_hash is not None:
                self.dht.report_peer(info_hash, p)
        for p in dropped:
            self.known_peers.discard(p)
            self.used_peers.discard(p)  # 允许后续重连（对方说它换了端口）

    async def _send_pex(self, conn: pw.PeerConnection) -> None:
        if conn.remote_ut_pex is None:
            return
        sample = list(self.known_peers)[:50]  # compact v4；IPv6 由编码器自动跳过
        try:
            await conn.send(pw.build_extended(conn.remote_ut_pex, pw.build_pex(added=sample)))
        except Exception:  # noqa: BLE001
            pass

    def _peer_has(self, key: str, pieces: Set[int]) -> None:
        assert self.picker is not None
        cur = self.picker._peer_pieces.get(key, set())  # noqa: SLF001
        self.picker.peer_update(key, cur | pieces)

    async def _on_block(self, key: str, index: int, begin: int, block: bytes) -> None:
        picker = self.picker
        assert picker is not None and self.meta is not None and self.writer is not None
        if index >= picker.num_pieces or picker.have[index]:
            return
        bufs = self.piece_bufs.setdefault(index, {})
        if begin in bufs:
            return
        bufs[begin] = block
        _, dup_peers = picker.block_received(index, begin, from_peer=key)
        # endgame：block 已到，撤销其他对端的重复请求
        for dk in dup_peers:
            if dk != key and dk in self.peers:
                try:
                    await self.peers[dk].send(pw.build_cancel(index, begin, len(block)))
                except Exception:  # noqa: BLE001
                    pass
        if not picker.piece_complete(index):
            return
        data = b"".join(bufs[o] for o in sorted(bufs))
        self.piece_bufs.pop(index, None)
        if len(data) != picker.piece_size(index) or hashlib.sha1(data).digest() != self.meta.pieces[index]:
            # 校验失败：整片作废重新下载
            picker.done_blocks.pop(index, None)  # noqa: SLF001
            for k in [k for k in picker.inflight if k[0] == index]:  # noqa: SLF001
                del picker.inflight[k]  # noqa: SLF001
            return
        self.writer.write_at(index * picker.piece_length, data)
        picker.mark_have(index)
        for peer in list(self.peers.values()):
            try:
                await peer.send(pw.build_have(index))
            except Exception:  # noqa: BLE001
                pass
        self._update_progress()
        if picker.is_complete():
            self.task.status = TaskStatus.DONE
            self._persist()

    async def _serve_request(self, conn: pw.PeerConnection, index: int, begin: int, length: int) -> None:
        """做种：回传对端请求的 block。"""
        picker = self.picker
        if picker is None or index >= picker.num_pieces or not picker.have[index]:
            return
        if length <= 0 or length > 128 * 1024 or begin + length > picker.piece_size(index):
            return
        try:
            data = self.writer.read_at(index * picker.piece_length + begin, length)  # type: ignore[union-attr]
            await conn.send(pw.build_piece(index, begin, data))
        except OSError:
            pass


class BtEngine:
    """BT 引擎：任务登记/持久化 + 会话生命周期。接口与 HttpEngine 对齐。"""

    def __init__(self, persist: Callable[[], None], default_dir: str, dht: Optional[DhtClient] = None):
        self._persist = persist
        self._default_dir = default_dir
        self._dht = dht or DhtClient()
        self._sessions: Dict[str, BtSession] = {}
        self._tasks: Dict[str, DownloadTask] = {}
        self._counter = int(time.time() * 1000) % 100000
        self.listen_port = 0
        self._server: Optional[asyncio.base_events.Server] = None
        self._lsd: Optional[LsdClient] = None

    def _next_id(self) -> str:
        self._counter += 1
        return f"bt-{self._counter}"

    # ── 入站 peer 监听 + LSD（BEP14） ─────────────────────────

    async def start(self) -> None:
        """启动入站 peer 监听（DHT announce_peer / LSD / tracker 的 Port 从此真实）。

        监听失败（端口/权限）时降级：只有出站连接，LSD 不可用则跳过。
        """
        if self._server is not None:
            return
        loop = asyncio.get_running_loop()
        try:
            self._server = await asyncio.start_server(self._on_incoming, "0.0.0.0", 0)
            self.listen_port = self._server.sockets[0].getsockname()[1]
        except OSError:
            self._server = None
        self._dht.announce_port = self.listen_port or None
        self._lsd = LsdClient(self.listen_port, self._on_lsd_peer)
        started = await self._lsd.start()
        if not started:
            self._lsd = None  # 组播不可用：降级容忍
        for sess in self._sessions.values():
            sess.listen_port = self.listen_port
            self._lsd_register(sess)

    def _lsd_register(self, sess: BtSession) -> None:
        if self._lsd is None:
            return
        h = self._info_hash_of(sess)
        if h is not None:
            self._lsd.add_info_hash(h)

    def _info_hash_of(self, sess: BtSession) -> Optional[bytes]:
        if sess.meta is not None:
            return sess.meta.info_hash
        if sess.magnet is not None:
            return sess.magnet.info_hash
        return None

    def _session_by_hash(self, info_hash: bytes) -> Optional[BtSession]:
        for sess in self._sessions.values():
            if self._info_hash_of(sess) == info_hash:
                return sess
        return None

    async def _on_incoming(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """入站 peer：按 info_hash 路由到对应任务会话（做种/下载都走同一消息泵）。"""
        conn = pw.PeerConnection(reader, writer)
        try:
            info_hash = await conn.do_handshake_incoming(b"-OM0100-" + os.urandom(12))
            sess = self._session_by_hash(info_hash)
            if sess is None:
                await conn.close()
                return
            host = writer.get_extra_info("peername") or ("?", 0)
            key = f"in:{host[0]}:{host[1]}"
            sess.peers[key] = conn
            await sess._run_peer(conn, key)  # noqa: SLF001
        except (asyncio.IncompleteReadError, ConnectionError, pw.PeerError, OSError, asyncio.TimeoutError):
            await conn.close()

    def _on_lsd_peer(self, info_hash: bytes, peer: Tuple[str, int]) -> None:
        """LSD 发现的局域网做种端 → 对应任务的 peer 池。"""
        sess = self._session_by_hash(info_hash)
        if sess is not None and peer not in sess.used_peers:
            sess.known_peers.add(peer)
        self._dht.report_peer(info_hash, peer)

    # ── 持久化 ──────────────────────────────────────────────

    def export_states(self) -> List[Dict[str, Any]]:
        out = []
        for tid, task in self._tasks.items():
            sess = self._sessions.get(tid)
            meta = sess.meta if sess else None
            d = task.state_dict()
            d["source"] = sess.source if sess else task.url
            d["info_raw_hex"] = meta.info_raw.hex() if meta else ""
            d["announce_list"] = meta.announce_list if meta else []
            d["only_files"] = sorted(sess.only_files) if sess and sess.only_files else None
            if sess and sess.picker:
                d["bitfield"] = pw.bitfield_bytes(
                    sess.picker.num_pieces, sess.picker.have
                ).hex()
            out.append(d)
        return out

    def restore(self, states: List[Dict[str, Any]]) -> None:
        for d in states:
            try:
                task = DownloadTask.from_dict(d)
                source = d.get("source", task.url)
                info_raw = bytes.fromhex(d.get("info_raw_hex", ""))
                meta = (
                    torrent_mod.parse_info_raw(info_raw, d.get("announce_list") or [])
                    if info_raw
                    else None
                )
                magnet = None
                if meta is None and source.lower().startswith("magnet:"):
                    magnet = parse_magnet(source)
                only = set(d["only_files"]) if d.get("only_files") else None
                sess = BtSession(task, source, meta, magnet, only, self._persist, self._dht)
                if meta is not None:
                    sess._prepare_from_meta()  # noqa: SLF001
                    bf = bytes.fromhex(d.get("bitfield", ""))
                    if bf and sess.picker:
                        for i, h in enumerate(pw.bitfield_pieces(bf, sess.picker.num_pieces)):
                            if h:
                                sess.picker.mark_have(i)
                self._tasks[task.id] = task
                self._sessions[task.id] = sess
                sess.listen_port = self.listen_port
                self._lsd_register(sess)
            except Exception:  # noqa: BLE001
                continue

    # ── 对外操作 ────────────────────────────────────────────

    async def add(
        self,
        source: str,
        dir_path: str,
        filename: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> DownloadTask:
        options = options or {}
        only = parse_select_files(options.get("select_files"))
        meta = None
        magnet = None
        if source.strip().lower().startswith("magnet:"):
            magnet = parse_magnet(source)
            name = filename or options.get("rename") or magnet.name or "magnet-download"
        else:
            with open(source, "rb") as f:
                data = f.read()
            meta = torrent_mod.parse_torrent(data)
            name = filename or options.get("rename") or meta.name
        task = DownloadTask(
            id=self._next_id(),
            url=source,
            filename=name,
            dir=dir_path,
            status=TaskStatus.PENDING,
            type=TaskType.BT,
        )
        sess = BtSession(task, source, meta, magnet, only, self._persist, self._dht)
        sess.listen_port = self.listen_port
        sess.use_public_trackers = bool(options.get("use_public_trackers", True))
        if options.get("metadata_timeout"):
            sess.metadata_timeout = float(options["metadata_timeout"])
        # 用户添加的 peer（options.peers: ["host:port", ...]）直接入 peer 池
        for entry in options.get("peers") or []:
            try:
                h, _, p = str(entry).rpartition(":")
                sess.known_peers.add((h, int(p)))
            except ValueError:
                continue
        self._tasks[task.id] = task
        self._sessions[task.id] = sess
        self._lsd_register(sess)
        self._persist()
        asyncio.get_running_loop().create_task(sess.run())
        return task

    async def pause(self, task_id: str) -> DownloadTask:
        sess = self._sessions.get(task_id)
        if sess is None:
            raise BtError(f"任务不存在: {task_id}")
        await sess.stop()
        if sess.task.status == TaskStatus.DOWNLOADING or sess.task.status == TaskStatus.PENDING:
            sess.task.status = TaskStatus.PAUSED
            sess.task.speed = 0
        self._persist()
        return sess.task

    async def resume(self, task_id: str) -> DownloadTask:
        sess = self._sessions.get(task_id)
        if sess is None:
            raise BtError(f"任务不存在: {task_id}")
        if sess.task.status == TaskStatus.DONE:
            raise BtError("任务已完成（做种中），无需继续")
        sess.task.error = None
        sess.task.status = TaskStatus.PENDING
        asyncio.get_running_loop().create_task(sess.run())
        self._persist()
        return sess.task

    async def remove(self, task_id: str, remove_files: bool = False) -> None:
        sess = self._sessions.get(task_id)
        if sess is None:
            raise BtError(f"任务不存在: {task_id}")
        await sess.stop()
        self._sessions.pop(task_id, None)
        self._tasks.pop(task_id, None)
        if remove_files and sess.meta:
            for f in sess.meta.files:
                try:
                    os.unlink(os.path.join(sess.task.dir, *f.path.split("/")))
                except OSError:
                    pass
        self._persist()

    def list(self) -> List[DownloadTask]:
        for sess in self._sessions.values():
            if sess.picker and sess.task.status == TaskStatus.DOWNLOADING:
                sess._update_progress()  # noqa: SLF001
        return list(self._tasks.values())

    async def metadata(self, source: str, timeout: float = METADATA_TIMEOUT, options: Optional[Dict[str, Any]] = None) -> List[Tuple[str, int]]:
        """磁力/torrent → 文件清单 [(path, length)]（磁力经 ut_metadata 拉取）。"""
        if source.strip().lower().startswith("magnet:"):
            magnet = parse_magnet(source)
            task = DownloadTask(
                id=self._next_id(),
                url=source,
                filename=magnet.name or "magnet-download",
                dir=self._default_dir,
                type=TaskType.BT,
            )
            sess = BtSession(task, source, None, magnet, None, lambda: None, self._dht)
            opts = options or {}
            sess.use_public_trackers = bool(opts.get("use_public_trackers", True))
            if opts.get("metadata_timeout"):
                sess.metadata_timeout = float(opts["metadata_timeout"])
            await asyncio.wait_for(sess._fetch_metadata(), timeout)  # noqa: SLF001
            assert sess.meta is not None
            return torrent_mod.file_list(sess.meta)
        with open(source, "rb") as f:
            data = f.read()
        return torrent_mod.file_list(torrent_mod.parse_torrent(data))

    async def shutdown(self) -> None:
        for sess in list(self._sessions.values()):
            await sess.stop()
        if self._lsd is not None:
            await self._lsd.close()
            self._lsd = None
        if self._server is not None:
            self._server.close()
            self._server = None
        await self._dht.close()
