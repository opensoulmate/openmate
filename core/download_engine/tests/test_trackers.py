"""tracker announce（HTTP/UDP，BEP3/15）单测：解析 + 假 tracker 端到端。"""

import asyncio
import socket
import struct
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from download_engine import bencode, trackers


def test_parse_compact_peers():
    data = bytes([127, 0, 0, 1]) + struct.pack(">H", 6881) + bytes([10, 0, 0, 2]) + struct.pack(">H", 51413)
    assert trackers.parse_compact_peers(data) == [("127.0.0.1", 6881), ("10.0.0.2", 51413)]
    assert trackers.parse_compact_peers(b"\x00" * 5) == []  # 不足 6 字节忽略


def test_parse_announce_response_compact():
    peers = bytes([1, 2, 3, 4]) + struct.pack(">H", 80)
    resp = bencode.encode({b"interval": 60, b"peers": peers})
    interval, got = trackers.parse_announce_response(resp)
    assert interval == 60 and got == [("1.2.3.4", 80)]


def test_parse_announce_response_dict_peers_and_failure():
    resp = bencode.encode(
        {b"interval": 30, b"peers": [{b"ip": b"9.9.9.9", b"port": 700, b"peer_id": b"x" * 20}]}
    )
    _, got = trackers.parse_announce_response(resp)
    assert got == [("9.9.9.9", 700)]
    with pytest.raises(trackers.TrackerError):
        trackers.parse_announce_response(bencode.encode({b"failure reason": b"nope"}))


def test_build_announce_params():
    p = trackers.build_announce_params(b"\x00" * 20, b"-x" * 10, 6881, 0, 0, 100, "started")
    assert p["info_hash"] == b"\x00" * 20
    assert p["event"] == "started"
    assert p["compact"] == 1


class _TrackerHandler(BaseHTTPRequestHandler):
    log = []

    def do_GET(self):  # noqa: N802
        _TrackerHandler.log.append(self.path)
        peers = bytes([127, 0, 0, 1]) + struct.pack(">H", 9999)
        body = bencode.encode({b"interval": 120, b"peers": peers})
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # noqa: ANN002
        pass


def test_announce_http_end_to_end():
    server = HTTPServer(("127.0.0.1", 0), _TrackerHandler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        params = trackers.build_announce_params(b"\xaa" * 20, b"-OM0100-" + b"x" * 12, 6881, 0, 0, 1, "started")
        interval, peers = asyncio.run(
            trackers.announce_http(f"http://127.0.0.1:{port}/announce", params, timeout=5)
        )
        assert interval == 120
        assert peers == [("127.0.0.1", 9999)]
        # info_hash 必须按原始字节百分号编码出现在 query 里
        assert any("event=started" in p for p in _TrackerHandler.log)
        assert any("%aa" in p.lower() for p in _TrackerHandler.log)
    finally:
        server.shutdown()


def test_announce_udp_end_to_end():
    info_hash = b"\xcc" * 20
    peer_id = b"-OM0100-" + b"y" * 12

    class Proto(asyncio.DatagramProtocol):
        def __init__(self):
            self.transport = None

        def connection_made(self, transport):
            self.transport = transport

        def datagram_received(self, data, addr):
            if len(data) >= 16 and struct.unpack(">I", data[8:12])[0] == trackers.UDP_ACTION_CONNECT:
                # connect 响应
                txid = struct.unpack(">I", data[12:16])[0]
                self.transport.sendto(
                    struct.pack(">II", 0, txid) + b"C" * 8, addr
                )
            elif len(data) >= 98:
                # announce：回 compact peers
                txid = struct.unpack(">I", data[12:16])[0]
                peers = bytes([127, 0, 0, 1]) + struct.pack(">H", 4242)
                self.transport.sendto(
                    struct.pack(">IIII", 1, txid, 300, 1) + b"\x00" * 4 + peers, addr
                )

    async def scenario():
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            Proto, local_addr=("127.0.0.1", 0)
        )
        port = transport.get_extra_info("sockname")[1]
        try:
            params = trackers.build_announce_params(info_hash, peer_id, 6881, 0, 0, 1, "started")
            return await trackers.announce_udp(f"udp://127.0.0.1:{port}/announce", params, timeout=5)
        finally:
            transport.close()

    interval, peers = asyncio.run(scenario())
    assert interval == 300
    assert peers == [("127.0.0.1", 4242)]


def test_announce_dispatch_rejects_unknown_scheme():
    with pytest.raises(trackers.TrackerError):
        asyncio.run(trackers.announce("ftp://x/announce", {}, timeout=1))
