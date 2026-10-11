// 语音输入音频工具（17.2 前端语音输入）
// MediaRecorder 录音格式随浏览器而异（Chromium 常见 audio/webm、Firefox audio/ogg、Safari audio/mp4），
// 而后端 attach_audio_parts → MiMo input_audio 只认 MP3/WAV/FLAC/M4A/OGG（webm 会被显式跳过报因）。
// 因此录音统一转成 16-bit PCM 单声道 WAV 再附着：格式永远可直发，不依赖浏览器录音格式。
// 转换失败时诚实回退原格式附着（ogg/mp4/mpeg 仍可直发），绝不假装转换成功。

/** 按偏好顺序挑选浏览器支持的录音 MIME（全不支持返回 null，由调用方显式提示） */
export function pickRecorderMimeType(): string | null {
  if (typeof MediaRecorder === 'undefined') return null;
  const candidates = ['audio/ogg;codecs=opus', 'audio/ogg', 'audio/mp4', 'audio/webm;codecs=opus', 'audio/webm'];
  for (const m of candidates) {
    try {
      if (MediaRecorder.isTypeSupported(m)) return m;
    } catch {
      // 老浏览器无 isTypeSupported — 交给 MediaRecorder 默认格式
    }
  }
  return null;
}

/** Float32 PCM → WAV 容器字节（16-bit PCM 单声道） */
export function encodeWavPcm16(channel: Float32Array, sampleRate: number): Uint8Array {
  const frames = channel.length;
  const buf = new ArrayBuffer(44 + frames * 2);
  const view = new DataView(buf);
  const writeStr = (off: number, s: string) => {
    for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i));
  };
  writeStr(0, 'RIFF');
  view.setUint32(4, 36 + frames * 2, true);
  writeStr(8, 'WAVE');
  writeStr(12, 'fmt ');
  view.setUint32(16, 16, true); // fmt chunk size
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  writeStr(36, 'data');
  view.setUint32(40, frames * 2, true);
  let off = 44;
  for (let i = 0; i < frames; i++) {
    const s = Math.max(-1, Math.min(1, channel[i]));
    view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    off += 2;
  }
  return new Uint8Array(buf);
}

function bytesToBase64(bytes: Uint8Array): string {
  let bin = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    bin += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(bin);
}

function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve((reader.result as string).split(',')[1] || '');
    reader.onerror = () => reject(reader.error || new Error('读取音频数据失败'));
    reader.readAsDataURL(blob);
  });
}

export interface AudioAttachment {
  type: 'audio';
  data: string;
  name: string;
  mime_type: string;
  /** 是否已统一转换为 WAV（false=原格式回退附着） */
  converted: boolean;
}

/**
 * 录音 Blob → 可附着的音频附件（base64 裸数据，与图片/文件附件同形态）。
 * 默认转 WAV；解码/转换失败时回退原格式（converted=false，mime 如实）。
 */
export async function blobToAudioAttachment(blob: Blob): Promise<AudioAttachment> {
  const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  try {
    const ctx = new AudioContext();
    try {
      const audioBuf = await ctx.decodeAudioData(await blob.arrayBuffer());
      let channel: Float32Array;
      if (audioBuf.numberOfChannels > 1) {
        // 多声道混成单声道（等权平均），语音场景体积减半不影响听感
        const mixed = new Float32Array(audioBuf.length);
        for (let c = 0; c < audioBuf.numberOfChannels; c++) {
          const data = audioBuf.getChannelData(c);
          for (let i = 0; i < mixed.length; i++) mixed[i] += data[i] / audioBuf.numberOfChannels;
        }
        channel = mixed;
      } else {
        channel = audioBuf.getChannelData(0);
      }
      const wav = encodeWavPcm16(channel, audioBuf.sampleRate);
      return { type: 'audio', data: bytesToBase64(wav), name: `voice-${stamp}.wav`, mime_type: 'audio/wav', converted: true };
    } finally {
      ctx.close().catch(() => {});
    }
  } catch {
    const ext = (blob.type.split('/')[1] || 'bin').split(';')[0];
    return {
      type: 'audio',
      data: await blobToBase64(blob),
      name: `voice-${stamp}.${ext}`,
      mime_type: blob.type || 'audio/webm',
      converted: false,
    };
  }
}
