"""torrent 元数据解析单测。"""

import hashlib

import pytest

from download_engine import bencode, torrent


def _single() -> bytes:
    return (
        b"d8:announce37:udp://tracker.example.com:80/announce"
        b"4:infod4:name8:test.bin6:lengthi1024e"
        b"6:pieces20:" + b"\x01" * 20 + b"12:piece lengthi16384eee"
    )


def _multi() -> bytes:
    info = (
        b"d5:filesld6:lengthi2048e4:pathl3:sub5:a.bineed6:lengthi512e4:pathl5:b.txteee"
        b"4:name7:testdir6:pieces20:" + b"\x02" * 20 + b"12:piece lengthi16384ee"
    )  # info 字典自带结束 e，外层再包 d4:info ... e
    return b"d4:info" + info + b"e"


def test_parse_single_file():
    meta = torrent.parse_torrent(_single())
    assert meta.name == "test.bin"
    assert meta.total_length == 1024
    assert [(f.path, f.length) for f in meta.files] == [("test.bin", 1024)]
    assert meta.piece_length == 16384
    assert meta.announce == "udp://tracker.example.com:80/announce"
    assert meta.num_pieces == 1


def test_parse_multi_file():
    meta = torrent.parse_torrent(_multi())
    assert meta.name == "testdir"
    assert torrent.file_list(meta) == [("testdir/sub/a.bin", 2048), ("testdir/b.txt", 512)]
    assert meta.total_length == 2560
    assert meta.files[0].offset == 0
    assert meta.files[1].offset == 2048


def test_infohash_is_sha1_of_raw_info():
    data = _single()
    info_raw = torrent._find_info_raw(data)
    meta = torrent.parse_torrent(data)
    assert meta.info_hash == hashlib.sha1(info_raw).digest()
    assert meta.info_raw == info_raw


def test_parse_info_raw_matches_parse_torrent():
    data = _multi()
    info_raw = torrent._find_info_raw(data)
    m1 = torrent.parse_info_raw(info_raw, ["udp://t/announce"])
    m2 = torrent.parse_torrent(data)
    assert m1.info_hash == m2.info_hash
    assert torrent.file_list(m1) == torrent.file_list(m2)
    assert m1.announce_list == ["udp://t/announce"]


def test_path_traversal_rejected():
    bad = (
        b"d4:infod5:filesld6:lengthi1e4:pathl2:..5:a.bineee"
        b"4:name1:x6:pieces20:" + b"\x00" * 20 + b"12:piece lengthi1eee"
    )
    with pytest.raises(torrent.TorrentError):
        torrent.parse_torrent(bad)


def test_bad_torrent_rejected():
    with pytest.raises(torrent.TorrentError):
        torrent.parse_torrent(b"d4:infod1:ai1eee")  # info 缺 name/pieces/length
    with pytest.raises((torrent.TorrentError, bencode.BencodeError)):
        torrent.parse_torrent(b"garbage")
    # pieces 长度非 20 倍数
    bad = b"d4:infod4:name1:x6:pieces3:abc12:piece lengthi1e6:lengthi1eee"
    with pytest.raises(torrent.TorrentError):
        torrent.parse_torrent(bad)


def test_build_torrent_bytes_roundtrip():
    data = torrent.build_torrent_bytes(
        "my.bin", [("my.bin", 3000)], 1024, [b"\x03" * 20, b"\x04" * 20, b"\x05" * 20],
        announce="http://t/announce",
    )
    meta = torrent.parse_torrent(data)
    assert meta.name == "my.bin"
    assert meta.total_length == 3000
    assert meta.num_pieces == 3
    assert meta.pieces[0] == b"\x03" * 20
