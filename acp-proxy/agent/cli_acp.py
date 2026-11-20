"""Generic CLI ACP adapter — 把一次性(one-shot) CLI agent 接入 ACP stdio 面。

用途：aider / qwen / gemini / interpreter / copilot 这类 CLI 不原生讲 ACP，
本适配器按 profile 把 session/prompt 的文本喂给对应 CLI 的一次性模式，
把真实输出作为 agent_message_chunk 回传；CLI 失败（缺凭据/报错）必须以
JSON-RPC error 如实上报，绝不假成功。

用法：python -m agent.cli_acp <profile>
profile ∈ {aider, qwen, gemini, interpreter, copilot}
"""

import json
import os
import re
import shutil
import subprocess
import sys
import uuid

_WORKDIR = os.path.expanduser(os.environ.get("CLI_ACP_WORKDIR", "~/acp-cli-workspace"))
_OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:0.5b")
_TIMEOUT = float(os.environ.get("CLI_ACP_TIMEOUT", "420"))


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _rpc(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _reply(id_, result=None, error=None) -> None:
    msg = {"jsonrpc": "2.0", "id": id_}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    _rpc(msg)


_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")


def _clean(text: str) -> str:
    return _ANSI.sub("", text).strip()


def _child_env() -> dict:
    env = dict(os.environ)
    # 防宿主 PYTHONPATH 污染子进程 venv（hermes site-packages 与工具 venv 冲突实测踩过）
    env.pop("PYTHONPATH", None)
    return env


def _profile_env(profile: str, env: dict) -> dict:
    """按 profile 注入模型端点配置（mimo 当大脑，2026-11-20）。

    copilot 走官方 BYOK（docs.github.com: COPILOT_PROVIDER_* 环境变量，
    GitHub 认证可选）；凭据取 gland 口径 XIAOMI_API_KEY，不硬编码。"""
    if profile == "copilot":
        key = os.environ.get("XIAOMI_API_KEY", "")
        if key:
            env["COPILOT_PROVIDER_BASE_URL"] = os.environ.get(
                "XIAOMI_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1")
            env["COPILOT_PROVIDER_API_KEY"] = key
            env.setdefault("COPILOT_PROVIDER_TYPE", "openai")
            env.setdefault("COPILOT_MODEL",
                           os.environ.get("CLI_ACP_COPILOT_MODEL", "mimo-v2.6-pro"))
    return env


_INTERP_SCRIPT = r"""
import sys
from interpreter import interpreter as oi
oi.llm.model = "ollama_chat/" + sys.argv[2]
oi.auto_run = True
oi.offline = True
oi.verbose = False
prompt = sys.argv[1]
out = []
def collect(x):
    if isinstance(x, str):
        out.append(x)
    elif isinstance(x, dict):
        if x.get("type") == "text":
            out.append(x.get("text", ""))
        elif x.get("type") == "code":
            out.append("[code executed]")
        else:
            for v in x.values():
                collect(v)
    elif isinstance(x, list):
        for v in x:
            collect(v)
for msg in oi.chat(prompt, display=False):
    if isinstance(msg, dict):
        if msg.get("type") == "code":
            out.append("[code executed]")
        else:
            collect(msg.get("content", msg))
    else:
        collect(msg)
text = "\n".join(x for x in out if x).strip()
if not text:
    text = "(interpreter returned no text)"
print(text)
"""


def _build_cmd(profile: str, prompt: str) -> list[str]:
    os.makedirs(_WORKDIR, exist_ok=True)
    if not os.path.isdir(os.path.join(_WORKDIR, ".git")):
        subprocess.run(["git", "init", "-q"], cwd=_WORKDIR, check=False,
                       capture_output=True)
    if profile == "aider":
        bin_ = os.path.expanduser("~/.local/share/uv/tools/aider-chat/bin/aider")
        return [bin_, "--yes-always", "--no-auto-commits", "--no-stream",
                "--no-check-update", "--no-gitignore", "--no-show-model-warnings",
                "--model", f"ollama_chat/{_OLLAMA_MODEL}", "--message", prompt]
    if profile == "qwen":
        bin_ = shutil.which("qwen") or os.path.expanduser("~/.npm-global/bin/qwen")
        return [bin_, "-m", _OLLAMA_MODEL, prompt]
    if profile == "gemini":
        bin_ = shutil.which("gemini") or os.path.expanduser("~/.npm-global/bin/gemini")
        return [bin_, "-p", prompt]
    if profile == "copilot":
        # 新版官方 Copilot CLI（npm @github/copilot）；旧 gh-copilot 扩展已废弃空壳
        bin_ = shutil.which("copilot") or os.path.expanduser("~/.npm-global/bin/copilot")
        return [bin_, "-p", prompt]
    if profile == "interpreter":
        py = os.path.expanduser("~/.local/share/uv/tools/open-interpreter/bin/python")
        # -I 隔离模式：工作目录里 agent 生成的杂文件（如 numbers.py）会遮蔽标准库
        # （python -c 把 cwd 放进 sys.path，numbers.py 遮蔽 stdlib numbers → decimal
        # import 炸 → pydantic_core 连环崩，2026-11-20 深测实测全回合阵亡）。
        return [py, "-I", "-c", _INTERP_SCRIPT, prompt, _OLLAMA_MODEL]
    raise ValueError(f"unknown profile: {profile}")


def _run_cli(profile: str, prompt: str, session_id: str) -> str:
    cmd = _build_cmd(profile, prompt)
    _log(f"[cli_acp:{profile}] run: {cmd[0]} ({len(cmd)} args)")
    env = _profile_env(profile, _child_env())
    proc = subprocess.run(cmd, cwd=_WORKDIR, env=env,
                          capture_output=True, text=True, timeout=_TIMEOUT)
    out = _clean(proc.stdout or "")
    err = _clean(proc.stderr or "")
    if proc.returncode != 0:
        tail = (err or out or "no output")[-500:]
        raise RuntimeError(f"cli exit {proc.returncode}: {tail}")
    text = out or err or ""
    if not text:
        raise RuntimeError("cli produced no output")
    _rpc({"jsonrpc": "2.0", "method": "session/update",
          "params": {"sessionId": session_id,
                     "update": {"sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text", "text": text}}}})
    return text


def main() -> None:
    profile = sys.argv[1] if len(sys.argv) > 1 else ""
    _log(f"[cli_acp] ready profile={profile} workdir={_WORKDIR}")
    session_id = None
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = msg.get("method")
        id_ = msg.get("id")
        if method == "initialize":
            _reply(id_, result={
                "protocolVersion": 1,
                "agentCapabilities": {
                    "loadSession": False,
                    "promptCapabilities": {"image": False, "embeddedContext": False},
                },
            })
        elif method == "session/new":
            session_id = f"{profile}-" + uuid.uuid4().hex[:12]
            _reply(id_, result={"sessionId": session_id})
        elif method == "session/prompt":
            params = msg.get("params") or {}
            sid = params.get("sessionId") or session_id
            prompt = "".join(
                p.get("text", "") for p in params.get("prompt", [])
                if isinstance(p, dict) and p.get("type") == "text")
            try:
                _run_cli(profile, prompt, sid)
                _reply(id_, result={"stopReason": "endTurn"})
            except Exception as e:  # noqa: BLE001 — 失败必须如实上报
                _log(f"[cli_acp:{profile}] failed: {e}")
                _reply(id_, error={"code": -32000,
                                   "message": f"{profile} failed: {str(e)[:800]}"})
        elif id_ is not None:
            _reply(id_, error={"code": -32601, "message": f"Method not found: {method}"})


if __name__ == "__main__":
    main()
