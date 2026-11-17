// 语音输入（对标 Hermes Desktop useVoiceRecorder 链路）：
// 点击麦克风开始录音 → 再点结束 → 录音 Blob 转 data URL → 后端 /api/voice/transcribe
// （本地 faster-whisper，同 Hermes STT 后端）→ 转写文字回调进输入框 → Enter 发送。
// 录音中有态反馈（recording + 秒表），转写中 transcribing（禁重复触发）。
'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { getApiBaseUrl, getToken } from '@/lib/api-client';
import { pickRecorderMimeType } from '@/lib/audio-record';

export type VoiceInputStatus = 'idle' | 'recording' | 'transcribing';

export interface VoiceInputOptions {
  /** 转写完成回调：把文字交给调用方插入输入框 */
  onTranscript: (text: string) => void;
  /** 单次录音上限（秒），到时自动停止并转写 */
  maxSeconds?: number;
}

const MIC_ERROR_COPY: Record<string, string> = {
  NotAllowedError: '麦克风权限被拒绝：请在浏览器地址栏的权限图标中允许本页使用麦克风后重试',
  SecurityError: '麦克风权限被拒绝：请在浏览器地址栏的权限图标中允许本页使用麦克风后重试',
  NotFoundError: '未检测到麦克风设备：请接入麦克风或在系统声音设置里检查输入设备后重试',
  DevicesNotFoundError: '未检测到麦克风设备：请接入麦克风或在系统声音设置里检查输入设备后重试',
  NotReadableError: '麦克风被占用：请关闭其他正在使用麦克风的应用后重试',
  TrackStartError: '麦克风被占用：请关闭其他正在使用麦克风的应用后重试',
};

function micErrorMessage(error: unknown): string {
  const name = error instanceof DOMException ? error.name : (error as Error)?.name || '';
  if (MIC_ERROR_COPY[name]) return MIC_ERROR_COPY[name];
  return `无法开始录音：${(error as Error)?.message || String(error)}`;
}

/** 录音环境自检失败时的原因+解决指引（不许裸拒） */
function unsupportedMessage(): string {
  const insecure = typeof window !== 'undefined' && window.isSecureContext === false;
  if (insecure) {
    return '麦克风不可用：当前经 HTTP 非本机地址访问，浏览器安全策略禁用麦克风。解决：改用 https:// 入口（见 docs/voice-https-entry.md，反向代理/NetBird 两种配置）或用 http://localhost 本机访问；桌面版请以 localhost 回源启动';
  }
  return '麦克风不可用：当前浏览器缺少录音接口（MediaRecorder），请换用新版浏览器或桌面版';
}

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error || new Error('读取音频数据失败'));
    reader.readAsDataURL(blob);
  });
}

export function useVoiceInput({ onTranscript, maxSeconds = 120 }: VoiceInputOptions) {
  const [status, setStatus] = useState<VoiceInputStatus>('idle');
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const capRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const startedAtRef = useRef(0);
  // 回调用 ref 兜底：转写异步落回时用最新回调，避免 stale closure
  const onTranscriptRef = useRef(onTranscript);
  onTranscriptRef.current = onTranscript;

  const clearTimers = () => {
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    if (capRef.current) { clearTimeout(capRef.current); capRef.current = null; }
  };

  const releaseMedia = () => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    recorderRef.current = null;
    setElapsedSeconds(0);
  };

  // 卸载收尾：释放麦克风，不静默留后台录音
  useEffect(() => () => {
    clearTimers();
    const rec = recorderRef.current;
    if (rec && rec.state !== 'inactive') rec.stop();
    streamRef.current?.getTracks().forEach((t) => t.stop());
  }, []);

  // 录音结束 → 转写 → 文字回调（对齐 Hermes：空转写=未检测到语音，不报错弹窗）
  const finish = useCallback(async (chunks: Blob[], mimeType: string) => {
    clearTimers();
    releaseMedia();
    if (!chunks.length) {
      setStatus('idle');
      setError('录音为空，请重试');
      return;
    }
    setStatus('transcribing');
    try {
      const blob = new Blob(chunks, { type: mimeType });
      const dataUrl = await blobToDataUrl(blob);
      const token = getToken();
      const headers: Record<string, string> = { 'Content-Type': 'application/json' };
      if (token) headers['Authorization'] = `Bearer ${token}`;
      const res = await fetch(`${getApiBaseUrl()}/api/voice/transcribe`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ data_url: dataUrl, mime_type: mimeType }),
      });
      if (!res.ok) throw new Error(`语音转写失败（${res.status}）`);
      const data = await res.json();
      const transcript = String(data.transcript || '').trim();
      if (!transcript) {
        setError('未检测到语音，请重试');
      } else {
        setError(null);
        onTranscriptRef.current(transcript);
      }
    } catch (e) {
      setError(`语音转写失败：${(e as Error)?.message || e}`);
    } finally {
      setStatus('idle');
    }
  }, []);

  const start = useCallback(async () => {
    setError(null);
    if (typeof MediaRecorder === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
      setError(unsupportedMessage());
      return;
    }
    if (recorderRef.current) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
      const mimeType = pickRecorderMimeType();
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      chunksRef.current = [];
      streamRef.current = stream;
      recorderRef.current = recorder;
      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.onstop = () => {
        const chunks = chunksRef.current;
        const type = recorder.mimeType || mimeType || 'audio/webm';
        chunksRef.current = [];
        void finish(chunks, type);
      };
      recorder.onerror = () => {
        releaseMedia();
        setStatus('idle');
        setError('录音出错，请重试');
      };
      recorder.start();
      startedAtRef.current = Date.now();
      setElapsedSeconds(0);
      setStatus('recording');
      timerRef.current = setInterval(() => setElapsedSeconds((Date.now() - startedAtRef.current) / 1000), 250);
      const cap = Math.max(1, Math.min(Math.trunc(maxSeconds), 600));
      capRef.current = setTimeout(() => {
        const rec = recorderRef.current;
        if (rec && rec.state !== 'inactive') rec.stop();
      }, cap * 1000);
    } catch (e) {
      releaseMedia();
      setStatus('idle');
      setError(micErrorMessage(e));
    }
  }, [finish, maxSeconds]);

  // 点击=开始说话；再点=结束录音（转写中禁重复触发）
  const dictate = useCallback(() => {
    if (status === 'recording') {
      const rec = recorderRef.current;
      if (rec && rec.state !== 'inactive') rec.stop();
    } else if (status === 'idle') {
      void start();
    }
  }, [status, start]);

  return { dictate, status, elapsedSeconds, error, clearError: () => setError(null) };
}
