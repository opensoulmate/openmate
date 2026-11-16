"""bencode 编解码单测。"""

import pytest

from download_engine import bencode
from download_engine.bencode import BencodeError


def test_decode_scalars():
    assert bencode.decode(b"i42e") == 42
    assert bencode.decode(b"i-7e") == -7
    assert bencode.decode(b"4:spam") == b"spam"
    assert bencode.decode(b"0:") == b""
    assert bencode.decode(b"l4:spami3ee") == [b"spam", 3]


def test_decode_dict_and_nested():
    d = bencode.decode(b"d3:bar4:spam3:fooi42ee")
    assert d == {b"bar": b"spam", b"foo": 42}
    d = bencode.decode(b"d1:ald1:bi1eeee")
    assert d == {b"a": [{b"b": 1}]}


def test_decode_errors():
    for bad in (b"i42", b"4:spamX", b"5:spam", b"x", b"i03e", b"i-0e", b""):
        with pytest.raises(BencodeError):
            bencode.decode(bad)
    with pytest.raises(BencodeError):
        bencode.decode(b"i1ei2e")  # 尾部垃圾


def test_decode_prefix():
    v, n = bencode.decode_prefix(b"i42ei99e")
    assert v == 42 and n == 4
    v, n = bencode.decode_prefix(b"d1:ai1eeXX")
    assert v == {b"a": 1} and n == len(b"d1:ai1ee")


def test_encode_roundtrip():
    for obj in (0, -5, 12345678901234, b"bytes", "", [1, b"a", [2]], {b"a": 1, b"b": [b"x"]}):
        raw = bencode.encode(obj)
        val = bencode.decode(raw)
        if isinstance(obj, str):
            assert val == obj.encode()
        elif isinstance(obj, dict):
            assert val == obj
        else:
            assert val == obj


def test_encode_dict_sorts_keys():
    raw = bencode.encode({b"b": 1, b"a": 2})
    assert raw == b"d1:ai2e1:bi1ee"


def test_encode_rejects_bool_and_unknown():
    with pytest.raises(BencodeError):
        bencode.encode(True)
    with pytest.raises(BencodeError):
        bencode.encode(1.5)
