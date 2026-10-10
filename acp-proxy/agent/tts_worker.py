"""tts_worker.py — edge-tts 独立子进程 worker（路线图 7.3：TTS/语音）。

为什么独立子进程（对标 browser_worker 模式）：
- edge_tts 是 asyncio 网络 IO，主 agent 进程可能已在跑事件循环，进程内 asyncio.run
  不可用；子进程隔离 + 有界超时（TTSTools 侧 subprocess timeout）最稳；
- 合成失败/网络不可达只影响本子进程，退出码+JSON 报因回主进程，绝不静默。

协议（单发 JSON，stdout 最后一行）：
    python tts_worker.py <text> <out_path> <voice> <rate>
→ {"ok": true, "out": "..."}  或  {"ok": false, "error": "..."}（退出码 1）
"""
import asyncio
import json
import sys


async def _synthesize(text: str, out_path: str, voice: str, rate: str) -> None:
    import edge_tts
    communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
    await communicate.save(out_path)


def main() -> int:
    if len(sys.argv) < 5:
        print(json.dumps({"ok": False,
                          "error": "usage: tts_worker.py <text> <out_path> <voice> <rate>"},
                         ensure_ascii=False))
        return 2
    text, out_path, voice, rate = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    try:
        asyncio.run(_synthesize(text, out_path, voice, rate))
    except Exception as e:  # noqa: BLE001 — 合成失败必须如实回传原因
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"},
                         ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "out": out_path}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
