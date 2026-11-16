"""ut_metadata（BEP9/10）编解码与元数据组装单测。"""

import hashlib

import pytest

from download_engine import bencode, metadata as md


def test_request_roundtrip():
    raw = md.encode_request(2)
    mtype, info = md.parse_message(raw)
    assert mtype == md.MSG_TYPE_REQUEST
    assert info["piece"] == 2


def test_data_message_split():
    block = b"x" * 100
    raw = md.encode_data(1, 200, block)
    mtype, info = md.parse_message(raw)
    assert mtype == md.MSG_TYPE_DATA
    assert info["piece"] == 1 and info["total_size"] == 200
    _, got = md.split_data(raw)
    assert got == block


def test_reject_roundtrip():
    mtype, info = md.parse_message(md.encode_reject(0))
    assert mtype == md.MSG_TYPE_REJECT and info["piece"] == 0


def test_parse_message_errors():
    with pytest.raises(md.MetadataError):
        md.parse_message(b"garbage")
    with pytest.raises(md.MetadataError):
        md.parse_message(bencode.encode({b"piece": 1}))  # 缺 msg_type
    with pytest.raises(md.MetadataError):
        md.split_data(md.encode_request(0))  # 不是 data


def test_assembler_verifies_sha1():
    raw = b"m" * (md.METADATA_PIECE_SIZE + 500)  # 2 片
    info_hash = hashlib.sha1(raw).digest()
    asm = md.MetadataAssembler(info_hash, len(raw))
    assert asm.num_pieces == 2
    assert asm.needed_pieces() == [0, 1]
    asm.add(1, raw[md.METADATA_PIECE_SIZE:])
    assert not asm.complete()
    asm.add(0, raw[:md.METADATA_PIECE_SIZE])
    assert asm.complete()
    assert asm.assemble() == raw


def test_assembler_rejects_bad_block_or_hash():
    raw = b"m" * 100
    asm = md.MetadataAssembler(hashlib.sha1(raw).digest(), 100)
    with pytest.raises(md.MetadataError):
        asm.add(0, b"short")  # 长度不符
    asm.add(0, b"X" * 100)   # 内容不对
    assert asm.complete()
    with pytest.raises(md.MetadataError):
        asm.assemble()  # sha1 校验失败
    with pytest.raises(md.MetadataError):
        md.MetadataAssembler(b"\x00" * 20, -1)
