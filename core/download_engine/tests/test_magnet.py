"""magnet 解析单测。"""

import base64

import pytest

from download_engine.magnet import Magnet, MagnetError, build_magnet, is_bare_infohash, parse

HEX = "0123456789abcdef0123456789abcdef01234567"


def test_parse_hex_btih():
    m = parse(f"magnet:?xt=urn:btih:{HEX}&dn=my%20file&tr=udp%3A%2F%2Ft%2Fannounce")
    assert m.info_hash_hex == HEX
    assert len(m.info_hash) == 20
    assert m.name == "my file"
    assert m.trackers == ["udp://t/announce"]


def test_parse_base32_btih():
    raw = bytes(range(20))
    b32 = base64.b32encode(raw).decode().rstrip("=")
    m = parse(f"magnet:?xt=urn:btih:{b32}")
    assert m.info_hash == raw


def test_parse_multiple_trackers():
    m = parse(f"magnet:?xt=urn:btih:{HEX}&tr=udp%3A%2F%2Fa&tr=http%3A%2F%2Fb")
    assert m.trackers == ["udp://a", "http://b"]


def test_parse_errors():
    with pytest.raises(MagnetError):
        parse("https://not-a-magnet")
    with pytest.raises(MagnetError):
        parse("magnet:?dn=only-name")
    with pytest.raises(MagnetError):
        parse("magnet:?xt=urn:btih:zzz-not-valid")


def test_build_magnet_roundtrip():
    info_hash = bytes(range(20))
    uri = build_magnet(info_hash, "name x", ["udp://t/announce"])
    m = parse(uri)
    assert m.info_hash == info_hash
    assert m.name == "name x"
    assert m.trackers == ["udp://t/announce"]


def test_is_bare_infohash():
    assert is_bare_infohash(HEX)
    assert not is_bare_infohash("magnet:?x")
