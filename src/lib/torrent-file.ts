/**
 * .torrent 文件解析（纯前端）：从 bencode 字节流中定位顶层 "info" 字典原始字节，
 * SHA-1（Web Crypto）→ infohash → magnet:?xt=urn:btih:<hex>。
 * 解析失败（非安全上下文无 crypto.subtle、非法文件）返回 null，由调用方提示降级。
 */

function isDigit(b: number): boolean {
  return b >= 0x30 && b <= 0x39;
}

/** 从 bencode 字典（dictStart 指向 'd'）的直接条目中定位 key 对应 value 的字节区间 [start, end) */
function findDictValue(buf: Uint8Array, dictStart: number, targetKey: string): { start: number; end: number } | null {
  let pos = dictStart;
  if (buf[pos] !== 0x64 /* 'd' */) return null;
  pos += 1;
  const decoder = new TextDecoder('latin1');
  // 跳过一个 bencode 值，返回其结束位置；解析失败返回 -1
  const skipValue = (p: number): number => {
    if (p >= buf.length) return -1;
    const c = buf[p];
    if (c === 0x69 /* 'i' */) {
      const e = buf.indexOf(0x65 /* 'e' */, p + 1);
      return e === -1 ? -1 : e + 1;
    }
    if (isDigit(c)) {
      const colon = buf.indexOf(0x3a /* ':' */, p);
      if (colon === -1) return -1;
      const len = parseInt(decoder.decode(buf.subarray(p, colon)), 10);
      if (!Number.isFinite(len) || len < 0) return -1;
      return colon + 1 + len;
    }
    if (c === 0x6c /* 'l' */ || c === 0x64 /* 'd' */) {
      let q = p + 1;
      while (q < buf.length && buf[q] !== 0x65 /* 'e' */) {
        if (c === 0x64) {
          q = skipValue(q); // key
          if (q < 0) return -1;
        }
        q = skipValue(q); // value
        if (q < 0) return -1;
      }
      return q >= buf.length ? -1 : q + 1;
    }
    return -1;
  };

  while (pos < buf.length && buf[pos] !== 0x65 /* 'e' */) {
    const keyEnd = skipValue(pos);
    if (keyEnd < 0) return null;
    const colon = buf.indexOf(0x3a, pos);
    if (colon === -1) return null;
    const key = decoder.decode(buf.subarray(colon + 1, keyEnd));
    const valueStart = keyEnd;
    const valueEnd = skipValue(valueStart);
    if (valueEnd < 0) return null;
    if (key === targetKey) return { start: valueStart, end: valueEnd };
    pos = valueEnd;
  }
  return null;
}

function bufToHex(buf: ArrayBuffer): string {
  return Array.from(new Uint8Array(buf)).map((b) => b.toString(16).padStart(2, '0')).join('');
}

/**
 * .torrent 字节流 → { magnet, name }；无法解析返回 null。
 * name 取顶层 "name" 字段（展示用）。
 */
export async function torrentFileToMagnet(data: ArrayBuffer): Promise<{ magnet: string; name: string } | null> {
  try {
    const buf = new Uint8Array(data);
    const info = findDictValue(buf, 0, 'info');
    if (!info) return null;
    const subtle = typeof crypto !== 'undefined' ? crypto.subtle : undefined;
    if (!subtle) return null; // 非安全上下文：无 SHA-1，降级
    const digest = await subtle.digest('SHA-1', buf.subarray(info.start, info.end));
    const hash = bufToHex(digest);
    const nameRange = info ? findDictValue(buf, info.start, 'name') : null;
    let name = '';
    if (nameRange) {
      // name 的 value 是字符串 "len:bytes"，截掉长度前缀
      const raw = new Uint8Array(data).subarray(nameRange.start, nameRange.end);
      const colon = raw.indexOf(0x3a);
      name = new TextDecoder('utf-8').decode(colon === -1 ? raw : raw.subarray(colon + 1));
    }
    return {
      magnet: `magnet:?xt=urn:btih:${hash}${name ? `&dn=${encodeURIComponent(name)}` : ''}`,
      name,
    };
  } catch {
    return null;
  }
}
