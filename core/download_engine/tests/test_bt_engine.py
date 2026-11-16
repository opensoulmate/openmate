"""BT 内核集成测试：假做种端（真 wire protocol）下载 + ut_metadata 元数据拉取。

全部走真实 TCP 环回连接，覆盖：handshake/bitfield/unchoke/request/piece、
SHA1 逐片校验、落盘、做种回传、BEP10 扩展协商 + BEP9 元数据分片交换。
"""

import asyncio
import hashlib
import os
import time

from download_engine import bencode, metadata as md, peer as pw, torrent as torrent_mod
from download_engine.bt_engine import BtEngine, BtSession, PieceWriter
from download_engine.magnet import parse as parse_magnet
from download_engine.models import DownloadTask, TaskType

PIECE_LEN = 32 * 1024
CONTENT = bytes((i * 7 + 13) % 256 for i in range(PIECE_LEN * 3 + 5000))


def _make_torrent():
    pieces = []
    for off in range(0, len(CONTENT), PIECE_LEN):
        pieces.append(hashlib.sha1(CONTENT[off : off + PIECE_LEN]).digest())
    raw = torrent_mod.build_torrent_bytes(
        "payload.bin", [("payload.bin", len(CONTENT))], PIECE_LEN, pieces,
        announce="http://127.0.0.1:9/announce",
    )
    return raw, torrent_mod.parse_torrent(raw)


class FakeSeeder:
    """最小做种端：握手 → 全 bitfield → unchoke → 响应 request。"""

    def __init__(self, content: bytes, info_hash: bytes, info_raw: bytes):
        self.content = content
        self.info_hash = info_hash
        self.info_raw = info_raw
        self.served = 0
        self.seeder_peer_id = b"-SEED0001-" + b"s" * 10
        self.server = None
        self.port = None

    async def start(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self):
        if self.server:
            self.server.close()

    async def _handle(self, reader, writer):
        try:
            data = await asyncio.wait_for(reader.readexactly(68), 10)
            got_hash, _, _ = pw.parse_handshake(data)
            assert got_hash == self.info_hash
            writer.write(pw.build_handshake(self.info_hash, self.seeder_peer_id))
            writer.write(pw.build_ext_handshake(len(self.info_raw)))
            num_pieces = (len(self.content) + PIECE_LEN - 1) // PIECE_LEN
            writer.write(pw.build_bitfield(pw.bitfield_bytes(num_pieces, [True] * num_pieces)))
            writer.write(pw.build_msg(pw.MSG_UNCHOKE))
            await writer.drain()
            while True:
                msg_id, payload = await pw_read(reader)
                if msg_id == pw.MSG_REQUEST:
                    index, begin, length = pw.parse_request(payload)
                    off = index * PIECE_LEN + begin
                    writer.write(pw.build_piece(index, begin, self.content[off : off + length]))
                    await writer.drain()
                    self.served += 1
                elif msg_id == pw.MSG_INTERESTED:
                    writer.write(pw.build_msg(pw.MSG_UNCHOKE))
                    await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.TimeoutError, AssertionError):
            pass
        finally:
            writer.close()


async def pw_read(reader):
    import struct

    hdr = await reader.readexactly(4)
    (length,) = struct.unpack(">I", hdr)
    if length == 0:
        return None, b""
    body = await reader.readexactly(length)
    return body[0], body[1:]


def test_download_from_fake_seeder(tmp_path):
    raw, meta = _make_torrent()
    torrent_path = tmp_path / "p.torrent"
    torrent_path.write_bytes(raw)

    async def scenario():
        seeder = FakeSeeder(CONTENT, meta.info_hash, meta.info_raw)
        await seeder.start()
        engine = BtEngine(persist=lambda: None, default_dir=str(tmp_path))
        task = await engine.add(str(torrent_path), str(tmp_path / "dl"))
        sess = engine._sessions[task.id]
        sess.known_peers.add(("127.0.0.1", seeder.port))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            cur = engine.list()[0]
            if cur.status.value in ("done", "error"):
                break
            await asyncio.sleep(0.1)
        cur = engine.list()[0]
        await engine.shutdown()
        await seeder.stop()
        return cur, seeder

    cur, seeder = asyncio.run(scenario())
    assert cur.status.value == "done", cur.error
    assert cur.total_bytes == len(CONTENT)
    assert cur.downloaded_bytes == len(CONTENT)
    out = tmp_path / "dl" / "payload.bin"
    assert out.read_bytes() == CONTENT, "落盘内容必须与种子内容逐字节一致（SHA1 逐片已校验）"
    assert seeder.served > 0


def test_seeding_serves_request(tmp_path):
    """做种：已完成任务回传对端请求的 block。"""
    raw, meta = _make_torrent()
    dl = tmp_path / "dl"
    dl.mkdir()
    # 已完成状态：文件已落盘
    (dl / "payload.bin").write_bytes(CONTENT)
    task = DownloadTask(id="bt-1", url="x", filename="payload.bin", dir=str(dl), type=TaskType.BT)
    sess = BtSession(task, "x", meta, None, None, lambda: None)
    sess._prepare_from_meta()
    for i in range(meta.num_pieces):
        sess.picker.mark_have(i)

    async def scenario():
        # 环回连接：另一端读我们回传的 piece 消息
        got = asyncio.get_running_loop().create_future()

        async def echo(reader, writer):
            msg_id, payload = await pw_read(reader)
            if not got.done():
                got.set_result((msg_id, payload))
            writer.close()

        server = await asyncio.start_server(echo, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        conn = await pw.PeerConnection.connect("127.0.0.1", port, 5)
        await sess._serve_request(conn, 0, 1024, 4096)
        msg_id, payload = await asyncio.wait_for(got, 5)
        await conn.close()
        server.close()
        return msg_id, payload

    msg_id, payload = asyncio.run(scenario())
    assert msg_id == pw.MSG_PIECE
    index, begin, block = pw.parse_piece(payload)
    assert (index, begin) == (0, 1024)
    assert block == CONTENT[1024 : 1024 + 4096]


def test_metadata_from_fake_ut_metadata_peer(tmp_path):
    raw, meta = _make_torrent()
    info_raw = meta.info_raw
    info_hash = meta.info_hash

    async def handle(reader, writer):
        try:
            data = await asyncio.wait_for(reader.readexactly(68), 10)
            pw.parse_handshake(data)
            writer.write(pw.build_handshake(info_hash, b"-META0001-" + b"m" * 10))
            # BEP10：advertise ut_metadata=7 + metadata_size（build_ext_handshake 已带帧）
            writer.write(pw.build_ext_handshake(len(info_raw)))
            await writer.drain()
            while True:
                msg_id, payload = await pw_read(reader)
                if msg_id == pw.MSG_EXTENDED:
                    ext_id, body = pw.parse_extended(payload)
                    if ext_id == 0:
                        continue
                    # 请求打到我们公布的 id(7)
                    mtype, info = md.parse_message(body)
                    if mtype == md.MSG_TYPE_REQUEST:
                        p = info["piece"]
                        block = info_raw[p * md.METADATA_PIECE_SIZE : (p + 1) * md.METADATA_PIECE_SIZE]
                        writer.write(
                            pw.build_extended(pw.LOCAL_UT_METADATA_ID, md.encode_data(p, len(info_raw), block))
                        )
                        await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.TimeoutError):
            pass
        finally:
            writer.close()

    async def scenario():
        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        task = DownloadTask(id="bt-9", url="magnet:?xt=urn:btih:" + info_hash.hex(), dir=str(tmp_path), type=TaskType.BT)
        magnet = parse_magnet(task.url)
        sess = BtSession(task, task.url, None, magnet, None, lambda: None)
        # 绕过 tracker：直接指向本地假 peer（magnet 元数据获取的真实代码路径）
        async def fake_collect(attempted):
            attempted.add(("127.0.0.1", port))
            return [("127.0.0.1", port)]

        sess._collect_peers = fake_collect  # type: ignore[method-assign]
        await asyncio.wait_for(sess._fetch_metadata(), 20)
        server.close()
        return sess

    sess = asyncio.run(scenario())
    assert sess.meta is not None
    assert sess.meta.info_hash == info_hash
    assert torrent_mod.file_list(sess.meta) == [("payload.bin", len(CONTENT))]
    assert sess.task.total_bytes == len(CONTENT)


def test_piece_writer_offset_mapping(tmp_path):
    files = [
        torrent_mod.TorrentFile(path="t/a.bin", length=10, offset=0),
        torrent_mod.TorrentFile(path="t/b.bin", length=5, offset=10),
    ]
    w = PieceWriter(str(tmp_path), files)
    w.ensure_files()
    w.write_at(0, b"0123456789")
    w.write_at(8, b"89ABCDE")  # 跨文件边界写
    assert (tmp_path / "t" / "a.bin").read_bytes() == b"0123456789"
    assert (tmp_path / "t" / "b.bin").read_bytes() == b"ABCDE"
    assert w.read_at(0, 15) == b"0123456789ABCDE"
