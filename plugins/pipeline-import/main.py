"""
pipeline-import — 导入加工插件（流水线=后端工序的流程引擎桥）

用户架构纠正（2026-11-19）："那我觉得它应该是后端的功能流程啊，怎么会是前端的"
——OpenPipeline 流水线（ocr/asr/text/video 四线+history）是 OpenSoul 后端
工序，本插件把它注册为流程引擎 tool（tool 节点可调「导入加工」），证明
"后端工序"定位：流程节点调 tool → OpenSoul /api/pipeline/* 全工序
（存储→提取→免疫→知识入库）→ pipeline_id+逐段结果回流流程上下文。

工具与 UI / 对话共用同一 handler（MCP 工具注册桥）；调用面：
  - file_path：本地文件 → POST /api/pipeline/upload（multipart 全工序）
  - file_id：Vein 已存文件 → POST /api/pipeline/run（跳过重复存储）
"""
from __future__ import annotations

import asyncio
import mimetypes
import os
from pathlib import Path
from typing import Any

OPENSOUL_BASE = os.environ.get("OPENSOUL_BASE_URL", "http://127.0.0.1:8090")

PIPELINES = ("auto", "ocr", "asr", "text", "video")


# ── OpenSoul 流水线 HTTP 面（stdlib 语义、httpx 执行）──────────────────────


def _upload_file(file_path: str, fields: dict[str, str]) -> dict[str, Any]:
    """POST /api/pipeline/upload：multipart 上传→vein/sense/immune/knowledge 全工序。"""
    import httpx

    path = Path(file_path)
    data = path.read_bytes()
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    with httpx.Client(base_url=OPENSOUL_BASE, timeout=600.0) as client:
        resp = client.post(
            "/api/pipeline/upload",
            files={"file": (path.name, data, mime)},
            data=fields,
        )
        resp.raise_for_status()
        return resp.json()


def _run_by_id(file_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """POST /api/pipeline/run：Vein 已存文件按 id 跑流水线。"""
    import httpx

    with httpx.Client(base_url=OPENSOUL_BASE, timeout=600.0) as client:
        resp = client.post("/api/pipeline/run", json=fields)
        resp.raise_for_status()
        return resp.json()


async def handle_import(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """MCP 工具：导入加工（后端工序）。

    args:
      file_path | file_id  二选一（本地文件 / Vein 已存文件）
      pipeline             auto|ocr|asr|text|video，默认 auto（按 MIME 选线）
      tags                 知识条目标签
      user_id              归档用户名（写键派生），默认 default
    """
    file_path = str(args.get("file_path") or "").strip()
    file_id = str(args.get("file_id") or "").strip()
    pipeline = str(args.get("pipeline") or "auto").strip() or "auto"
    tags = args.get("tags") or []
    user_id = str(args.get("user_id") or "default").strip() or "default"

    if not file_path and not file_id:
        return {"error": "file_path 与 file_id 需提供其一（导入加工入参）"}
    if pipeline not in PIPELINES:
        return {"error": f"pipeline 取值非法: {pipeline!r}（应为 {PIPELINES}）"}
    if not isinstance(tags, list):
        tags = [str(tags)]

    fields = {
        "pipeline": pipeline,
        "user_id": user_id,
        "tags": ",".join(str(t) for t in tags),
        "skip_immune": "false",
        "skip_knowledge": "false",
    }

    ctx.logger.info(
        "pipeline-import 导入加工: %s pipeline=%s user=%s",
        file_path or f"file_id={file_id}", pipeline, user_id)

    try:
        if file_path and Path(file_path).is_file():
            result = await asyncio.to_thread(_upload_file, file_path, fields)
        elif file_id:
            result = await asyncio.to_thread(
                _run_by_id, file_id,
                {
                    "file_id": file_id,
                    "pipeline": pipeline,
                    "user_id": user_id,
                    "tags": [str(t) for t in tags],
                },
            )
        else:
            return {"error": f"文件不存在: {file_path}"}
    except Exception as e:  # noqa: BLE001 — 工具边界：失败如实报因
        ctx.logger.warning("pipeline-import 调用失败: %s", e)
        return {"error": f"{type(e).__name__}: {e}", "pipeline": pipeline}

    steps = result.get("steps") or []
    return {
        "ok": result.get("status") in ("completed", "partial"),
        "pipeline_id": result.get("pipeline_id"),
        "pipeline_type": result.get("pipeline_type"),
        "status": result.get("status"),
        "file_id": result.get("file_id"),
        "elapsed_ms": result.get("elapsed_ms"),
        "steps": [
            {"step": s.get("step"), "status": s.get("status")}
            for s in steps
        ],
        "knowledge_entry_id": next(
            (s.get("entry_id") for s in steps if s.get("step") == "knowledge"), None
        ),
        "result": result,
    }
