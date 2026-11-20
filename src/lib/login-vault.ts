// 本地凭据保险箱 —— 密码本地存储加密（明文禁令）。
//
// 主路（安全上下文/https/tauri/localhost）：WebCrypto AES-GCM，密钥为
// IndexedDB 里不可导出（extractable=false）的 CryptoKey；
// 兜底（http://LAN 明文源无 crypto.subtle）：纯 JS AES-256-CBC（aes-cbc.ts，
// 已与 Node crypto 对拍验证），随机 32B 密钥存 IndexedDB。
// 两路都禁止明文落盘；保险箱不可用时调用方直接不存密码（fail-closed）。
//
// ── 密钥分槽（1.3.9 桌面壳线上事故根修）─────────────────────────────
// 事故链：1.3.8 及以前桌面壳在 http 源（无 crypto.subtle）走 CBC 兜底，
// 密钥是 IndexedDB 'vault-key' 里的 32B base64 字符串；1.3.9 起壳用
// unsafely-treat-insecure-origin-as-secure 把 http 回退源标成安全上下文，
// crypto.subtle 出现 → 老实现见「key.type !== 'secret'」就地重新生成并
// 覆写 'vault-key' → 全部已存密码（v0 密文）静默解不开 → 登录页密码
// 空白、点登录报「不能为空」不跳转。反向同理（v1 密文落到无 subtle
// 环境时字符串键覆写 CryptoKey）。
// 根修：GCM/CBC 密钥分槽（vault-key-gcm / vault-key-cbc），老槽
// 'vault-key' 只读迁移、绝不覆写、绝不删除（兼容回滚）；解密按密文
// 版本号路由而非当前模式——v0 密文在 subtle 环境照样纯 JS 解出（跨
// 模式可读）。密钥缺失/版本不支持/解密失败 → 结构化失败码，调用方
// 显性降级（重输密码框+勾选态保留），绝不静默空值。

import { aesCbcEncrypt, aesCbcDecrypt, randomBytes, bytesToBase64, base64ToBytes } from './aes-cbc';

const DB_NAME = 'openmate-login-vault';
const STORE_NAME = 'keys';
const KEY_ID = 'vault-key';       // 老混合槽：只读迁移源，本文件不再写入
const KEY_ID_GCM = 'vault-key-gcm'; // AES-GCM CryptoKey（subtle 主路）
const KEY_ID_CBC = 'vault-key-cbc'; // AES-256-CBC 原始 32B 密钥 base64（纯 JS 兜底）

interface VaultKeys {
  gcm: CryptoKey | null;
  cbc: Uint8Array | null;
}

let cached: VaultKeys | null = null;
let loading: Promise<VaultKeys> | null = null;

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(STORE_NAME)) req.result.createObjectStore(STORE_NAME);
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function idbGet(key: string): Promise<unknown> {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readonly');
    const req = tx.objectStore(STORE_NAME).get(key);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function idbSet(key: string, value: unknown): Promise<void> {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite');
    tx.objectStore(STORE_NAME).put(value, key);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

function hasSubtle(): boolean {
  return typeof crypto !== 'undefined' && !!crypto.subtle && typeof indexedDB !== 'undefined';
}

function isGcmKey(v: unknown): v is CryptoKey {
  return !!v && typeof v === 'object' && (v as CryptoKey).type === 'secret'
    && typeof (v as CryptoKey).algorithm === 'object' && (v as CryptoKey).algorithm.name === 'AES-GCM';
}

function parseCbcKey(v: unknown): Uint8Array | null {
  if (typeof v !== 'string' || v.length < 40) return null;
  try {
    const bytes = base64ToBytes(v);
    return bytes.length === 32 ? bytes : null;
  } catch {
    return null;
  }
}

// 读槽 + 老槽只读迁移（绝不覆写既有槽、绝不写老槽）。
async function loadKeys(): Promise<VaultKeys> {
  const [legacy, gcmRaw, cbcRaw] = await Promise.all([
    idbGet(KEY_ID), idbGet(KEY_ID_GCM), idbGet(KEY_ID_CBC),
  ]);
  let gcm = isGcmKey(gcmRaw) ? gcmRaw : null;
  let cbc = parseCbcKey(cbcRaw);
  // 老混合槽迁移：按类型归位到对应空槽（槽已有值=信任槽内密钥，不动）
  if (!gcm && isGcmKey(legacy)) {
    gcm = legacy;
    await idbSet(KEY_ID_GCM, legacy);
  }
  if (!cbc) {
    const fromLegacy = parseCbcKey(legacy);
    if (fromLegacy) {
      cbc = fromLegacy;
      await idbSet(KEY_ID_CBC, legacy);
    }
  }
  return { gcm, cbc };
}

// 并发调用合并（React StrictMode 双 effect / 同时解密码+token 时只初始化一次）。
// 失败不缓存——瞬时 IDB 故障下次调用重试，不永久中毒。
function getKeys(): Promise<VaultKeys> {
  if (cached) return Promise.resolve(cached);
  if (!loading) {
    loading = loadKeys()
      .then((k) => { cached = k; return k; })
      .finally(() => { loading = null; });
  }
  return loading;
}

async function ensureGcmKey(): Promise<CryptoKey | null> {
  if (!hasSubtle()) return null;
  const keys = await getKeys();
  if (keys.gcm) return keys.gcm;
  try {
    const key = await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
    await idbSet(KEY_ID_GCM, key);
    keys.gcm = key;
    return key;
  } catch {
    return null;
  }
}

async function ensureCbcKey(): Promise<Uint8Array | null> {
  const keys = await getKeys();
  if (keys.cbc) return keys.cbc;
  try {
    const key = randomBytes(32);
    await idbSet(KEY_ID_CBC, bytesToBase64(key));
    keys.cbc = key;
    return key;
  } catch {
    return null;
  }
}

function b64FromBuf(buf: ArrayBuffer | Uint8Array): string {
  return buf instanceof Uint8Array ? bytesToBase64(buf) : bytesToBase64(new Uint8Array(buf));
}

/** 加密文本 → base64(版本头+密文)；保险箱不可用返回 null（调用方禁存明文）。 */
export async function encryptSecret(plain: string): Promise<string | null> {
  if (typeof window === 'undefined') return null;
  try {
    // 主路：subtle 可用且 GCM 密钥在/可生成 → v1
    if (hasSubtle()) {
      const key = await ensureGcmKey();
      if (key) {
        const iv = randomBytes(12);
        const pt = new TextEncoder().encode(plain);
        const ct = await crypto.subtle.encrypt(
          { name: 'AES-GCM', iv: iv as unknown as BufferSource },
          key,
          pt as unknown as BufferSource,
        );
        return 'v1.' + b64FromBuf(iv) + '.' + b64FromBuf(ct);
      }
    }
    // 兜底：纯 JS AES-256-CBC（非安全上下文 / subtle 不可用）
    const key = await ensureCbcKey();
    if (!key) return null;
    const iv = randomBytes(16);
    return 'v0.' + bytesToBase64(iv) + '.' + aesCbcEncrypt(key, iv, plain);
  } catch {
    return null;
  }
}

/** 解密失败的结构化原因（调用方据此显性降级，禁静默空值）。 */
export type DecryptFailReason = 'vault-unavailable' | 'key-missing' | 'bad-format' | 'unsupported-version' | 'decrypt-failed';

export type DecryptResult =
  | { ok: true; plain: string }
  | { ok: false; reason: DecryptFailReason };

/**
 * 解密 encryptSecret 的产物（带失败原因）。
 * 按密文版本号路由而非当前模式：v0 密文即使在 subtle 环境也走纯 JS CBC
 * 解（跨模式可读——1.3.9 事故里 v0 存量密码的活路）；v1 密文需要 subtle
 * + GCM 密钥，缺哪个如实报哪个。绝不半残明文外泄、绝不静默吞错。
 */
export async function decryptSecretDetailed(enc: string): Promise<DecryptResult> {
  if (typeof window === 'undefined') return { ok: false, reason: 'vault-unavailable' };
  if (typeof enc !== 'string' || !enc.includes('.')) return { ok: false, reason: 'bad-format' };
  let keys: VaultKeys;
  try {
    keys = await getKeys();
  } catch {
    return { ok: false, reason: 'vault-unavailable' };
  }
  const [ver, ivB64, ctB64] = enc.split('.');
  if (!ivB64 || !ctB64) return { ok: false, reason: 'bad-format' };
  try {
    if (ver === 'v1') {
      if (!hasSubtle()) return { ok: false, reason: 'unsupported-version' };
      if (!keys.gcm) return { ok: false, reason: 'key-missing' };
      const pt = await crypto.subtle.decrypt(
        { name: 'AES-GCM', iv: base64ToBytes(ivB64) as unknown as BufferSource },
        keys.gcm,
        base64ToBytes(ctB64) as unknown as BufferSource,
      );
      return { ok: true, plain: new TextDecoder().decode(pt) };
    }
    if (ver === 'v0') {
      if (!keys.cbc) return { ok: false, reason: 'key-missing' };
      return { ok: true, plain: aesCbcDecrypt(keys.cbc, base64ToBytes(ivB64), ctB64) };
    }
    return { ok: false, reason: 'unsupported-version' };
  } catch {
    return { ok: false, reason: 'decrypt-failed' };
  }
}

/** 解密 encryptSecret 的产物；失败/保险箱不可用返回 null（禁半残明文外泄）。 */
export async function decryptSecret(enc: string): Promise<string | null> {
  const r = await decryptSecretDetailed(enc);
  return r.ok ? r.plain : null;
}
