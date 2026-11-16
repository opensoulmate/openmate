"""分片调度（rarest-first + endgame）单测。"""

from download_engine.picker import BLOCK_SIZE, PiecePicker


def test_piece_and_block_sizes():
    p = PiecePicker(3, 32 * 1024, 32 * 1024 * 2 + 100)
    assert p.piece_size(0) == 32 * 1024
    assert p.piece_size(1) == 32 * 1024
    assert p.piece_size(2) == 100
    assert p.blocks_of(0) == [(0, BLOCK_SIZE), (BLOCK_SIZE, BLOCK_SIZE)]
    assert p.blocks_of(2) == [(0, 100)]


def test_rarest_first_ordering():
    p = PiecePicker(4, 1024, 4 * 1024)
    p.peer_update("a", {0, 1, 2})       # 0,1,2 各 1 个拥有者
    p.peer_update("b", {1, 2, 3})       # 1,2 各 2 个；3 有 1 个
    reqs = p.pick("a", max_n=8)
    pieces = [r.piece for r in reqs]
    # 0 的拥有者最少（1），必须先于 1/2（各 2）
    assert pieces.index(0) < pieces.index(1)
    assert pieces.index(0) < pieces.index(2)


def test_pick_only_peer_owned_pieces():
    p = PiecePicker(3, 1024, 3 * 1024)
    p.peer_update("a", {1})
    p.peer_update("b", {2})
    assert all(r.piece == 1 for r in p.pick("a"))
    assert all(r.piece == 2 for r in p.pick("b"))


def test_endgame_duplicates_and_cancel_list():
    p = PiecePicker(1, 1024, 1024)  # 单片单 block
    p.peer_update("a", {0})
    p.peer_update("b", {0})
    first = p.pick("a", max_n=4)
    assert len(first) == 1 and not p.endgame
    second = p.pick("b", max_n=4)   # 唯一 block 已派发 → endgame 重复借还
    assert p.endgame
    assert len(second) == 1
    # 先到者胜出，返回其余重复持有者用于 cancel（交付者 b 自己除外）
    req, dups = p.block_received(0, 0, from_peer="b")
    assert req is not None and dups == ["a"]
    assert p.have[0] and p.is_complete()


def test_block_received_marks_piece_done():
    p = PiecePicker(1, BLOCK_SIZE * 2, BLOCK_SIZE * 2)
    p.peer_update("a", {0})
    p.pick("a", max_n=2)
    p.block_received(0, 0)
    assert not p.piece_complete(0)
    p.block_received(0, BLOCK_SIZE)
    assert p.piece_complete(0) and p.have[0]
    assert p.is_complete()


def test_peer_drop_releases_inflight():
    p = PiecePicker(2, 1024, 2 * 1024)
    p.peer_update("a", {0, 1})
    p.pick("a", max_n=2)
    assert len(p.inflight) == 2
    p.peer_drop("a")
    assert len(p.inflight) == 0
    assert p.availability[0] == 0


def test_only_files_restricts_pieces():
    p = PiecePicker(4, 1024, 4 * 1024, only_files={2})
    p.peer_update("a", {0, 1, 2, 3})
    reqs = p.pick("a", max_n=8)
    assert {r.piece for r in reqs} == {2}
    p.block_received(2, 0)
    assert p.is_complete()  # 只要 2 号片
