// 纯 JS AES-256-CBC + PKCS#7 —— 仅作非安全上下文（http://LAN_IP 明文源无 crypto.subtle）
// 的本地密码加密兜底；安全上下文走 WebCrypto AES-GCM（见 login-vault.ts）。
// 正确性优先（载荷只是短密码）：S-box 由 GF(2^8) 运算生成（杜绝手抄表错字），
// 与 Node crypto.createCipheriv('aes-256-cbc') 对拍验证过。

const SBOX = new Uint8Array(256);
const INV_SBOX = new Uint8Array(256);

(function initSbox() {
  const rotl8 = (x: number, n: number) => ((x << n) | (x >>> (8 - n))) & 0xff;
  let p = 1;
  let q = 1;
  do {
    p = (p ^ (p << 1) ^ (p & 0x80 ? 0x1b : 0)) & 0xff; // p *= 3
    q ^= (q << 1) & 0xff; // q *= 3^-1
    q ^= (q << 2) & 0xff;
    q ^= (q << 4) & 0xff;
    if (q & 0x80) q ^= 0x09;
    q &= 0xff;
    const x = q ^ rotl8(q, 1) ^ rotl8(q, 2) ^ rotl8(q, 3) ^ rotl8(q, 4);
    SBOX[p] = (x ^ 0x63) & 0xff;
  } while (p !== 1);
  SBOX[0] = 0x63;
  for (let i = 0; i < 256; i++) INV_SBOX[SBOX[i]] = i;
})();

const RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36];

// AES-256 密钥扩展：Nk=8, Nr=14 → 60 words
function expandKey(key: Uint8Array): Uint8Array {
  const nr = 14;
  const w = new Uint8Array(4 * 4 * (nr + 1));
  w.set(key);
  let bytes = 32;
  let rconIdx = 0;
  while (bytes < w.length) {
    const t = w.slice(bytes - 4, bytes);
    if (bytes % 32 === 0) {
      t.set([SBOX[t[1]] ^ RCON[rconIdx++], SBOX[t[2]], SBOX[t[3]], SBOX[t[0]]]);
    } else if (bytes % 32 === 16) {
      t.set([SBOX[t[0]], SBOX[t[1]], SBOX[t[2]], SBOX[t[3]]]);
    }
    for (let i = 0; i < 4; i++) w[bytes + i] = w[bytes - 32 + i] ^ t[i];
    bytes += 4;
  }
  return w;
}

function addRoundKey(s: Uint8Array, rk: Uint8Array, round: number) {
  for (let i = 0; i < 16; i++) s[i] ^= rk[round * 16 + i];
}

function subBytes(s: Uint8Array) {
  for (let i = 0; i < 16; i++) s[i] = SBOX[s[i]];
}

function invSubBytes(s: Uint8Array) {
  for (let i = 0; i < 16; i++) s[i] = INV_SBOX[s[i]];
}

function shiftRows(s: Uint8Array) {
  // 状态布局 column-major：s[r + 4c]
  const t = s.slice();
  for (let r = 1; r < 4; r++) {
    for (let c = 0; c < 4; c++) s[r + 4 * c] = t[r + 4 * ((c + r) % 4)];
  }
}

function invShiftRows(s: Uint8Array) {
  const t = s.slice();
  for (let r = 1; r < 4; r++) {
    for (let c = 0; c < 4; c++) s[r + 4 * c] = t[r + 4 * ((c + 4 - r) % 4)];
  }
}

function xtime(x: number): number {
  return ((x << 1) ^ (x & 0x80 ? 0x1b : 0)) & 0xff;
}

function mul(a: number, b: number): number {
  let r = 0;
  for (let i = 0; i < 8; i++) {
    if (b & 1) r ^= a;
    a = xtime(a);
    b >>= 1;
  }
  return r & 0xff;
}

function mixColumns(s: Uint8Array) {
  for (let c = 0; c < 4; c++) {
    const a = s.slice(4 * c, 4 * c + 4);
    s[4 * c + 0] = mul(a[0], 2) ^ mul(a[1], 3) ^ a[2] ^ a[3];
    s[4 * c + 1] = a[0] ^ mul(a[1], 2) ^ mul(a[2], 3) ^ a[3];
    s[4 * c + 2] = a[0] ^ a[1] ^ mul(a[2], 2) ^ mul(a[3], 3);
    s[4 * c + 3] = mul(a[0], 3) ^ a[1] ^ a[2] ^ mul(a[3], 2);
  }
}

function invMixColumns(s: Uint8Array) {
  for (let c = 0; c < 4; c++) {
    const a = s.slice(4 * c, 4 * c + 4);
    s[4 * c + 0] = mul(a[0], 14) ^ mul(a[1], 11) ^ mul(a[2], 13) ^ mul(a[3], 9);
    s[4 * c + 1] = mul(a[0], 9) ^ mul(a[1], 14) ^ mul(a[2], 11) ^ mul(a[3], 13);
    s[4 * c + 2] = mul(a[0], 13) ^ mul(a[1], 9) ^ mul(a[2], 14) ^ mul(a[3], 11);
    s[4 * c + 3] = mul(a[0], 11) ^ mul(a[1], 13) ^ mul(a[2], 9) ^ mul(a[3], 14);
  }
}

function encryptBlock(rk: Uint8Array, block: Uint8Array): Uint8Array {
  const s = block.slice();
  const nr = 14;
  addRoundKey(s, rk, 0);
  for (let round = 1; round < nr; round++) {
    subBytes(s);
    shiftRows(s);
    mixColumns(s);
    addRoundKey(s, rk, round);
  }
  subBytes(s);
  shiftRows(s);
  addRoundKey(s, rk, nr);
  return s;
}

function decryptBlock(rk: Uint8Array, block: Uint8Array): Uint8Array {
  const s = block.slice();
  const nr = 14;
  addRoundKey(s, rk, nr);
  for (let round = nr - 1; round >= 1; round--) {
    invShiftRows(s);
    invSubBytes(s);
    addRoundKey(s, rk, round);
    invMixColumns(s);
  }
  invShiftRows(s);
  invSubBytes(s);
  addRoundKey(s, rk, 0);
  return s;
}

function pkcs7Pad(data: Uint8Array): Uint8Array {
  const pad = 16 - (data.length % 16);
  const out = new Uint8Array(data.length + pad);
  out.set(data);
  out.fill(pad, data.length);
  return out;
}

function pkcs7Unpad(data: Uint8Array): Uint8Array {
  const pad = data[data.length - 1];
  if (pad < 1 || pad > 16) throw new Error('bad padding');
  return data.slice(0, data.length - pad);
}

function xorBlock(a: Uint8Array, b: Uint8Array): Uint8Array {
  const out = new Uint8Array(16);
  for (let i = 0; i < 16; i++) out[i] = a[i] ^ b[i];
  return out;
}

function bytesToBase64(bytes: Uint8Array): string {
  let bin = '';
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin);
}

function base64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

/** AES-256-CBC 加密：入参 key(32B)/iv(16B)，返回 base64 密文。 */
export function aesCbcEncrypt(key: Uint8Array, iv: Uint8Array, plain: string): string {
  const rk = expandKey(key);
  const data = pkcs7Pad(new TextEncoder().encode(plain));
  const out = new Uint8Array(data.length);
  let prev = iv;
  for (let off = 0; off < data.length; off += 16) {
    const block = xorBlock(data.slice(off, off + 16), prev);
    const enc = encryptBlock(rk, block);
    out.set(enc, off);
    prev = enc;
  }
  return bytesToBase64(out);
}

/** AES-256-CBC 解密：入参 key(32B)/iv(16B)/base64 密文，返回明文（失败抛错）。 */
export function aesCbcDecrypt(key: Uint8Array, iv: Uint8Array, b64: string): string {
  const rk = expandKey(key);
  const data = base64ToBytes(b64);
  const out = new Uint8Array(data.length);
  let prev = iv;
  for (let off = 0; off < data.length; off += 16) {
    const block = data.slice(off, off + 16);
    const dec = decryptBlock(rk, block);
    out.set(xorBlock(dec, prev), off);
    prev = block;
  }
  return new TextDecoder().decode(pkcs7Unpad(out));
}

export function randomBytes(n: number): Uint8Array {
  const out = new Uint8Array(n);
  if (typeof crypto !== 'undefined' && crypto.getRandomValues) crypto.getRandomValues(out);
  else for (let i = 0; i < n; i++) out[i] = Math.floor(Math.random() * 256);
  return out;
}

export { bytesToBase64, base64ToBytes };
