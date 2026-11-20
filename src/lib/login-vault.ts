// 本地凭据保险箱 —— 密码本地存储加密（明文禁令）。
//
// 主路（安全上下文/https/tauri/localhost）：WebCrypto AES-GCM，
//   密钥为 IndexedDB 里不可导出（extractable=false）的 CryptoKey；
// 兜底（http://LAN 明文源无 crypto.subtle）：纯 JS AES-256-CBC
//   （aes-cbc.ts，已与 Node crypto 对拍验证），随机 32B 密钥存 IndexedDB。
// 两路都禁止明文落盘；保险箱不可用时调用方直接不存密码（fail-closed）。

import { aesCbcEncrypt, aesCbcDecrypt, randomBytes, bytesToBase64, base64ToBytes } from './aes-cbc';

const DB_NAME = 'openmate-login-vault';
const STORE_NAME = 'keys';
const KEY_ID = 'vault-key';

type VaultMode = 'subtle' | 'fallback' | 'none';

let cachedKey: CryptoKey | Uint8Array | null = null;
let cachedMode: VaultMode | null = null;

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

async function getVault(): Promise<{ mode: Exclude<VaultMode, 'none'>; key: CryptoKey | Uint8Array } | null> {
  if (typeof window === 'undefined') return null;
  if (cachedMode && cachedKey && cachedMode !== 'none') return { mode: cachedMode as Exclude<VaultMode, 'none'>, key: cachedKey as CryptoKey | Uint8Array };
  try {
    if (hasSubtle()) {
      let key = (await idbGet(KEY_ID)) as CryptoKey | undefined;
      if (!key || key.type !== 'secret') {
        key = await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
        await idbSet(KEY_ID, key);
      }
      cachedKey = key;
      cachedMode = 'subtle';
      return { mode: 'subtle', key };
    }
    // 兜底：纯 JS AES-256-CBC（非安全上下文）
    let raw = (await idbGet(KEY_ID)) as string | undefined;
    if (typeof raw !== 'string' || raw.length < 40) {
      raw = bytesToBase64(randomBytes(32));
      await idbSet(KEY_ID, raw);
    }
    const key = base64ToBytes(raw);
    if (key.length !== 32) throw new Error('bad vault key');
    cachedKey = key;
    cachedMode = 'fallback';
    return { mode: 'fallback', key };
  } catch {
    cachedMode = 'none';
    return null;
  }
}

function b64FromBuf(buf: ArrayBuffer | Uint8Array): string {
  return buf instanceof Uint8Array ? bytesToBase64(buf) : bytesToBase64(new Uint8Array(buf));
}

/** 加密文本 → base64(版本头+密文)；保险箱不可用返回 null（调用方禁存明文）。 */
export async function encryptSecret(plain: string): Promise<string | null> {
  const vault = await getVault();
  if (!vault) return null;
  try {
    if (vault.mode === 'subtle') {
      const iv = randomBytes(12);
      const pt = new TextEncoder().encode(plain);
      const ct = await crypto.subtle.encrypt(
        { name: 'AES-GCM', iv: iv as unknown as BufferSource },
        vault.key as CryptoKey,
        pt as unknown as BufferSource,
      );
      return 'v1.' + b64FromBuf(iv) + '.' + b64FromBuf(ct);
    }
    const iv = randomBytes(16);
    return 'v0.' + bytesToBase64(iv) + '.' + aesCbcEncrypt(vault.key as Uint8Array, iv, plain);
  } catch {
    return null;
  }
}

/** 解密 encryptSecret 的产物；失败/保险箱不可用返回 null（禁半残明文外泄）。 */
export async function decryptSecret(enc: string): Promise<string | null> {
  const vault = await getVault();
  if (!vault) return null;
  try {
    const [ver, ivB64, ctB64] = enc.split('.');
    if (ver === 'v1' && vault.mode === 'subtle') {
      const pt = await crypto.subtle.decrypt(
        { name: 'AES-GCM', iv: base64ToBytes(ivB64) as unknown as BufferSource },
        vault.key as CryptoKey,
        base64ToBytes(ctB64) as unknown as BufferSource,
      );
      return new TextDecoder().decode(pt);
    }
    if (ver === 'v0' && vault.mode === 'fallback') {
      return aesCbcDecrypt(vault.key as Uint8Array, base64ToBytes(ivB64), ctB64);
    }
    return null;
  } catch {
    return null;
  }
}
