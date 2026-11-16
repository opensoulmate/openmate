"""状态机 / 来源分类 / 快雷链解码 / 文件名解析 单测。"""

import pytest

from download_engine.models import (
    DownloadTask,
    TaskStatus,
    TaskType,
    classify_source,
    filename_from_content_disposition,
    filename_from_url,
    thunder_decode,
)

ALL = list(TaskStatus)


def test_status_machine_legal_transitions():
    legal = [
        (TaskStatus.PENDING, TaskStatus.DOWNLOADING),
        (TaskStatus.PENDING, TaskStatus.PAUSED),
        (TaskStatus.PENDING, TaskStatus.ERROR),
        (TaskStatus.PENDING, TaskStatus.REMOVED),
        (TaskStatus.DOWNLOADING, TaskStatus.PENDING),
        (TaskStatus.DOWNLOADING, TaskStatus.PAUSED),
        (TaskStatus.DOWNLOADING, TaskStatus.DONE),
        (TaskStatus.DOWNLOADING, TaskStatus.ERROR),
        (TaskStatus.DOWNLOADING, TaskStatus.REMOVED),
        (TaskStatus.PAUSED, TaskStatus.PENDING),
        (TaskStatus.PAUSED, TaskStatus.DOWNLOADING),
        (TaskStatus.PAUSED, TaskStatus.ERROR),
        (TaskStatus.PAUSED, TaskStatus.REMOVED),
        (TaskStatus.DONE, TaskStatus.REMOVED),
        (TaskStatus.ERROR, TaskStatus.PENDING),
        (TaskStatus.ERROR, TaskStatus.REMOVED),
    ]
    for a, b in legal:
        assert a.can_transition(b), f"{a} -> {b} 应合法"


def test_status_machine_illegal_transitions():
    illegal = [
        (TaskStatus.DONE, TaskStatus.DOWNLOADING),
        (TaskStatus.DONE, TaskStatus.PAUSED),
        (TaskStatus.DONE, TaskStatus.PENDING),
        (TaskStatus.DONE, TaskStatus.ERROR),
        (TaskStatus.REMOVED, TaskStatus.DOWNLOADING),
        (TaskStatus.REMOVED, TaskStatus.PENDING),
        (TaskStatus.REMOVED, TaskStatus.DONE),
        (TaskStatus.PAUSED, TaskStatus.DONE),
        (TaskStatus.PENDING, TaskStatus.DONE),
    ]
    for a, b in illegal:
        assert not a.can_transition(b), f"{a} -> {b} 应非法"


def test_status_self_transition_always_legal():
    for s in ALL:
        assert s.can_transition(s)


def test_task_transition_raises_on_illegal():
    t = DownloadTask(id="x", url="u")
    t.transition(TaskStatus.DOWNLOADING)
    t.transition(TaskStatus.DONE)
    with pytest.raises(ValueError):
        t.transition(TaskStatus.DOWNLOADING)


def test_classify_source():
    assert classify_source("magnet:?xt=urn:btih:aa") == TaskType.BT
    assert classify_source("/tmp/x.torrent") == TaskType.BT
    assert classify_source("https://h/x.torrent?x=1") == TaskType.BT
    assert classify_source("thunder://QUFhYVpa") == TaskType.THUNDER
    assert classify_source("flashget://x") == TaskType.THUNDER
    assert classify_source("qqdl://x") == TaskType.THUNDER
    assert classify_source("https://youtube.com/watch?v=1") == TaskType.VIDEO
    assert classify_source("https://www.bilibili.com/video/BV1") == TaskType.VIDEO
    assert classify_source("https://example.com/a.iso") == TaskType.HTTP
    assert classify_source("ftp://example.com/a.iso") == TaskType.HTTP


def test_thunder_decode_vectors():
    import base64

    real = "https://example.com/f.zip"
    thunder = "thunder://" + base64.b64encode(b"AA" + real.encode() + b"ZZ").decode()
    assert thunder_decode(thunder) == real
    flashget = "flashget://" + base64.b64encode(
        b"[FLASHGET]" + real.encode() + b"[FLASHGET]"
    ).decode()
    assert thunder_decode(flashget) == real
    qqdl = "qqdl://" + base64.b64encode(real.encode()).decode()
    assert thunder_decode(qqdl) == real
    assert thunder_decode(real) == real  # 非快雷链原样返回
    with pytest.raises(ValueError):
        thunder_decode("thunder://" + base64.b64encode(b"garbage-no-url").decode())


def test_filename_from_url():
    assert filename_from_url("https://h/p/a.iso?x=1#y") == "a.iso"
    assert filename_from_url("ftp://h/p/dir/") == "dir"
    assert filename_from_url("https://h/") == "download"
    assert filename_from_url("https://h/p/%E6%96%87%E4%BB%B6.zip") == "文件.zip"


def test_filename_from_content_disposition():
    assert filename_from_content_disposition('attachment; filename="a.zip"') == "a.zip"
    assert (
        filename_from_content_disposition("attachment; filename*=UTF-8''%E6%96%87.zip")
        == "文.zip"
    )
    assert filename_from_content_disposition(None) is None
    assert filename_from_content_disposition("inline") is None


def test_task_to_dict_contract_fields():
    t = DownloadTask(id="http-1", url="https://h/a", filename="a", dir="/dl")
    t.total_bytes = 100
    t.downloaded_bytes = 40
    t.speed = 10
    t.status = TaskStatus.DOWNLOADING
    d = t.to_dict()
    for key in (
        "id", "url", "filename", "dest", "totalBytes", "downloadedBytes",
        "speed", "eta", "progress", "status", "type", "addedAt", "origin", "remove_files",
    ):
        assert key in d, f"缺字段 {key}"
    assert d["dest"] == "/dl/a"
    assert d["progress"] == 40.0
    assert d["origin"] == "local"
    # from_dict 往返
    t2 = DownloadTask.from_dict(d)
    assert (t2.id, t2.total_bytes, t2.downloaded_bytes, t2.status) == ("http-1", 100, 40, TaskStatus.DOWNLOADING)
