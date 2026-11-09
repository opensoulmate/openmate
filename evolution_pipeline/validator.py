"""evolution_pipeline/validator.py

P0-stored截留完整性校验 + 显式fail-fast

在验证读取路径（从 stored/ 截留缓冲取验证数据的入口）增加完整性校验：
读取后比对源数据长度与 SHA256 哈希，任何不一致（截断/长度缩水/哈希漂移）
立即抛出显式 IncompleteDataError（fail-fast），将验证结果标记为 INVALID_DATA
（非失败非通过），禁止在截断数据上做通过/未通过判定，彻底消除假阴性。

约定：
1) 验证数据读取函数（apply_precise_patch 前置数据加载 / Grader 取数路径）
   增加 len + hash 双校验，校验基准为写入时持久化的元数据（原长度 + 原 SHA256）。
2) 校验失败时 raise 显式异常，并在验证报告中写入
   reason='data_truncated' / 'len_mismatch' / 'hash_mismatch'，绝不静默降级。
3) 校验通过才进入原有判定逻辑。
4) 保留原函数签名与调用点兼容。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

__all__ = [
    "IncompleteDataError",
    "IntegrityStatus",
    "VerificationStatus",
    "IntegrityMetadata",
    "VerificationReport",
    "compute_sha256",
    "compute_integrity_metadata",
    "persist_integrity_metadata",
    "load_integrity_metadata",
    "verify_buffer_integrity",
    "load_verification_data",
    "load_grader_data",
    "apply_precise_patch",
    "Grader",
    "Validator",
    "INTEGRITY_META_SUFFIX",
]

logger = logging.getLogger(__name__)

# 写入时持久化元数据的默认后缀：<data_path>.integrity.json
INTEGRITY_META_SUFFIX = ".integrity.json"


# ---------------------------------------------------------------------------
# 异常定义
# ---------------------------------------------------------------------------
class IncompleteDataError(RuntimeError):
    """显式完整性异常（fail-fast）。

    任何截断 / 长度缩水 / 哈希漂移都必须抛出此异常，绝不允许静默降级
    到 PASS/FAIL 判定，以彻底消除假阴性。
    """

    def __init__(
        self,
        reason: str,
        message: str,
        *,
        expected_len: Optional[int] = None,
        actual_len: Optional[int] = None,
        expected_sha256: Optional[str] = None,
        actual_sha256: Optional[str] = None,
        data_path: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.expected_len = expected_len
        self.actual_len = actual_len
        self.expected_sha256 = expected_sha256
        self.actual_sha256 = actual_sha256
        self.data_path = data_path

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reason": self.reason,
            "message": self.message,
            "expected_len": self.expected_len,
            "actual_len": self.actual_len,
            "expected_sha256": self.expected_sha256,
            "actual_sha256": self.actual_sha256,
            "data_path": self.data_path,
        }


# ---------------------------------------------------------------------------
# 状态枚举
# ---------------------------------------------------------------------------
class IntegrityStatus(str, Enum):
    OK = "OK"
    TRUNCATED = "data_truncated"
    LEN_MISMATCH = "len_mismatch"
    HASH_MISMATCH = "hash_mismatch"
    MISSING_META = "missing_meta"
    MISSING_DATA = "missing_data"


class VerificationStatus(str, Enum):
    """验证结果状态（INVALID_DATA 非失败非通过）。"""

    PASS = "PASS"
    FAIL = "FAIL"
    INVALID_DATA = "INVALID_DATA"  # 既非失败亦非通过，禁止二值化判定
    ERROR = "ERROR"


# ---------------------------------------------------------------------------
# 完整性元数据
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class IntegrityMetadata:
    """写入时持久化的完整性基准：原长度 + 原 SHA256。"""

    original_len: int
    original_sha256: str
    created_at: float = field(default_factory=time.time)
    data_path: Optional[str] = None
    encoding: str = "utf-8"

    def validate_shape(self) -> None:
        if not isinstance(self.original_len, int) or self.original_len < 0:
            raise ValueError("original_len must be a non-negative int")
        if not isinstance(self.original_sha256, str) or len(self.original_sha256) != 64:
            raise ValueError("original_sha256 must be a 64-char hex string")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "IntegrityMetadata":
        meta = cls(
            original_len=int(payload["original_len"]),
            original_sha256=str(payload["original_sha256"]).lower(),
            created_at=float(payload.get("created_at", time.time())),
            data_path=payload.get("data_path"),
            encoding=str(payload.get("encoding", "utf-8")),
        )
        meta.validate_shape()
        return meta


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def compute_sha256(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def compute_integrity_metadata(
    data: bytes, *, data_path: Optional[str] = None, encoding: str = "utf-8"
) -> IntegrityMetadata:
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("data must be bytes-like")
    raw = bytes(data)
    meta = IntegrityMetadata(
        original_len=len(raw),
        original_sha256=compute_sha256(raw),
        data_path=data_path,
        encoding=encoding,
    )
    meta.validate_shape()
    return meta


def _meta_path_for(data_path: str, meta_path: Optional[str] = None) -> str:
    return meta_path or (str(data_path) + INTEGRITY_META_SUFFIX)


def persist_integrity_metadata(
    meta: IntegrityMetadata,
    *,
    meta_path: Optional[str] = None,
    data_path: Optional[str] = None,
) -> str:
    """将完整性元数据持久化到磁盘，作为后续校验基准。"""
    target_data_path = data_path or meta.data_path
    if not target_data_path:
        raise ValueError("data_path is required to persist integrity metadata")
    out_path = _meta_path_for(target_data_path, meta_path)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    payload = dict(meta.to_dict())
    payload["data_path"] = target_data_path
    tmp_path = out_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, sort_keys=True)
        fp.flush()
        os.fsync(fp.fileno())
    os.replace(tmp_path, out_path)
    logger.info(
        "persisted integrity metadata path=%s len=%d sha256=%s",
        out_path,
        meta.original_len,
        meta.original_sha256,
    )
    return out_path


def load_integrity_metadata(
    data_path: str, *, meta_path: Optional[str] = None
) -> IntegrityMetadata:
    m_path = _meta_path_for(data_path, meta_path)
    if not os.path.exists(m_path):
        raise IncompleteDataError(
            reason=IntegrityStatus.MISSING_META.value,
            message=(
                "integrity metadata missing: cannot validate read path; "
                "fail-fast to avoid false-negative judgments"
            ),
            data_path=str(data_path),
        )
    with open(m_path, "r", encoding="utf-8") as fp:
        payload = json.load(fp)
    meta = IntegrityMetadata.from_dict(payload)
    return meta


# ---------------------------------------------------------------------------
# 核心校验：len + hash 双校验
# ---------------------------------------------------------------------------
def verify_buffer_integrity(
    raw: bytes, meta: IntegrityMetadata, *, data_path: Optional[str] = None
) -> Tuple[IntegrityStatus, Optional[IncompleteDataError]]:
    """对读取到的原始字节做 len + sha256 双校验。

    返回 (IntegrityStatus, Optional[IncompleteDataError])。
    校验失败时 status 为对应失败原因，error 为显式异常，调用方必须 raise，
    禁止静默降级为 PASS/FAIL。
    """
    meta.validate_shape()
    actual_len = len(raw)
    actual_sha256 = compute_sha256(raw)
    path = data_path or meta.data_path

    # 先判长度：截断 / 缩水
    if actual_len < meta.original_len:
        err = IncompleteDataError(
            reason=IntegrityStatus.TRUNCATED.value,
            message=(
                f"data truncated: expected_len={meta.original_len} "
                f"actual_len={actual_len}; INVALID_DATA (no pass/fail allowed)"
            ),
            expected_len=meta.original_len,
            actual_len=actual_len,
            expected_sha256=meta.original_sha256,
            actual_sha256=actual_sha256,
            data_path=path,
        )
        return IntegrityStatus.TRUNCATED, err

    if actual_len != meta.original_len:
        err = IncompleteDataError(
            reason=IntegrityStatus.LEN_MISMATCH.value,
            message=(
                f"length mismatch: expected_len={meta.original_len} "
                f"actual_len={actual_len}; INVALID_DATA (no pass/fail allowed)"
            ),
            expected_len=meta.original_len,
            actual_len=actual_len,
            expected_sha256=meta.original_sha256,
            actual_sha256=actual_sha256,
            data_path=path,
        )
        return IntegrityStatus.LEN_MISMATCH, err

    # 长度一致再判哈希：哈希漂移
    if actual_sha256 != meta.original_sha256:
        err = IncompleteDataError(
            reason=IntegrityStatus.HASH_MISMATCH.value,
            message=(
                f"sha256 mismatch: expected={meta.original_sha256} "
                f"actual={actual_sha256}; INVALID_DATA (no pass/fail allowed)"
            ),
            expected_len=meta.original_len,
            actual_len=actual_len,
            expected_sha256=meta.original_sha256,
            actual_sha256=actual_sha256,
            data_path=path,
        )
        return IntegrityStatus.HASH_MISMATCH, err

    return IntegrityStatus.OK, None


# ---------------------------------------------------------------------------
# 验证报告
# ---------------------------------------------------------------------------
@dataclass
class VerificationReport:
    """验证报告；INVALID_DATA 时必须携带 reason。"""

    status: VerificationStatus
    reason: Optional[str] = None
    message: str = ""
    data_path: Optional[str] = None
    expected_len: Optional[int] = None
    actual_len: Optional[int] = None
    expected_sha256: Optional[str] = None
    actual_sha256: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    @property
    def is_judgement_allowed(self) -> bool:
        """是否允许进入 PASS/FAIL 判定。INVALID_DATA 强制 False。"""
        return self.status in (VerificationStatus.PASS, VerificationStatus.FAIL)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "reason": self.reason,
            "message": self.message,
            "data_path": self.data_path,
            "expected_len": self.expected_len,
            "actual_len": self.actual_len,
            "expected_sha256": self.expected_sha256,
            "actual_sha256": self.actual_sha256,
            "payload": self.payload,
            "created_at": self.created_at,
            "judgement_allowed": self.is_judgement_allowed,
        }

    @classmethod
    def invalid_data_from_error(
        cls, err: IncompleteDataError
    ) -> "VerificationReport":
        """由 IncompleteDataError 构造 INVALID_DATA 报告（reason 显式落盘）。"""
        return cls(
            status=VerificationStatus.INVALID_DATA,
            reason=err.reason,
            message=err.message,
            data_path=err.data_path,
            expected_len=err.expected_len,
            actual_len=err.actual_len,
            expected_sha256=err.expected_sha256,
            actual_sha256=err.actual_sha256,
            payload={"exception": err.to_dict()},
        )


class ReportSink:
    """验证报告收集器，支持线程安全 append。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reports: List[VerificationReport] = []

    def add(self, report: VerificationReport) -> None:
        with self._lock:
            self._reports.append(report)

    def extend(self, reports: Iterable[VerificationReport]) -> None:
        with self._lock:
            self._reports.extend(reports)

    def list(self) -> List[VerificationReport]:
        with self._lock:
            return list(self._reports)

    def dump(self, out_path: str) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
        with self._lock:
            payload = [r.to_dict() for r in self._reports]
        tmp = out_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp, out_path)
        return out_path


_DEFAULT_SINK = ReportSink()


def _default_sink(sink: Optional[ReportSink]) -> ReportSink:
    return sink if sink is not None else _DEFAULT_SINK


# ---------------------------------------------------------------------------
# 验证数据读取路径（前置数据加载 / Grader 取数）
# ---------------------------------------------------------------------------
def _read_raw_bytes(data_path: str) -> bytes:
    if not os.path.exists(data_path):
        raise IncompleteDataError(
            reason=IntegrityStatus.MISSING_DATA.value,
            message=(
                "verification data missing on read path; "
                "fail-fast to avoid false-negative judgments"
            ),
            data_path=str(data_path),
        )
    with open(data_path, "rb") as fp:
        return fp.read()


def load_verification_data(
    data_path: str,
    *,
    meta_path: Optional[str] = None,
    encoding: str = "utf-8",
    report_sink: Optional[ReportSink] = None,
) -> bytes:
    """验证数据读取入口（前置数据加载路径）。

    在返回数据前强制做 len + sha256 双校验，基准为写入时持久化的元数据
    （原长度 + 原 SHA256）。任何不一致立即 raise IncompleteDataError
    并写入 INVALID_DATA 报告（reason=data_truncated/len_mismatch/hash_mismatch）。
    仅校验通过后才把数据交给上层判定逻辑。
    """
    sink = _default_sink(report_sink)
    meta = load_integrity_metadata(data_path, meta_path=meta_path)
    raw = _read_raw_bytes(data_path)
    status, err = verify_buffer_integrity(
        raw, meta, data_path=str(data_path)
    )
    if status is not IntegrityStatus.OK:
        assert err is not None, "failed integrity check must carry explicit error"
        report = VerificationReport.invalid_data_from_error(err)
        sink.add(report)
        logger.error(
            "INTEGRITY FAIL reason=%s data_path=%s expected_len=%s actual_len=%s",
            err.reason,
            data_path,
            err.expected_len,
            err.actual_len,
        )
        raise err  # fail-fast，禁止继续判定

    logger.info(
        "integrity OK data_path=%s len=%d sha256=%s",
        data_path,
        meta.original_len,
        meta.original_sha256,
    )
    return raw


def load_grader_data(
    data_path: str,
    *,
    meta_path: Optional[str] = None,
    encoding: str = "utf-8",
    report_sink: Optional[ReportSink] = None,
) -> bytes:
    """Grader 取数路径：与 load_verification_data 同强度的完整性校验。"""
    return load_verification_data(
        data_path,
        meta_path=meta_path,
        encoding=encoding,
        report_sink=report_sink,
    )


def _decode(data: bytes, encoding: str, errors: str = "strict") -> str:
    try:
        return data.decode(encoding, errors=errors)
    except UnicodeDecodeError as exc:
        err = IncompleteDataError(
            reason=REASON_DECODE_ERROR,
            message=(
                f"decode failed after integrity check: encoding={encoding} "
                f"error={exc}; INVALID_DATA (no pass/fail allowed)"
            ),
            actual_len=len(data),
            actual_sha256=compute_sha256(data),
        )
        raise err from exc


# 额外显式失败原因（不参与 len/hash 双校验，但同样强制 INVALID_DATA）
REASON_DECODE_ERROR = "decode_error"
REASON_PATCH_MISS = "patch_mismatch"
REASON_META_MISSING = "missing_meta"
REASON_SELF_CHECK = "self_check_failed"


# ---------------------------------------------------------------------------
# PatchApplicationError：目标数据漂移时同样 fail-fast
# ---------------------------------------------------------------------------
class PatchApplicationError(IncompleteDataError):
    """精确补丁无法精确命中（目标文本漂移/缺失）时的显式异常。"""
