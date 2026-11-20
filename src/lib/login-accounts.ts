// 登录账号记录（QQ 登录框式账号下拉）+ 记住密码 + 自动登录。
//
// 存储口径：
// - 账号列表 localStorage 'openmate-login-accounts'（账号名+密码已记标记+最后登录时间）；
// - 密码一律经 login-vault 加密后才落盘（明文禁令；保险箱不可用=不存密码，账号照记）；
// - 自动登录偏好 'openmate-auto-login'=账号名；长效 token（30 天）经保险箱加密存
//   'openmate-auto-token'（{username, encToken}）——自动登录长效 token 优先，
//   无效再回落加密密码静默登录；全部失败回落登录页，绝不锁人。
// - 显式登出必须 clearAutoLogin()（否则登出即被自动登回来）；401 过期接管不清
//   自动登录（过期≠登出，自动登录就是要救这个场景）。

import { encryptSecret, decryptSecret, decryptSecretDetailed, type DecryptFailReason } from './login-vault';

export interface SavedAccount {
  username: string;
  /** 密码已记标记（下拉 UI 显示），不代表明文存在 */
  hasPassword: boolean;
  /** 加密密码（vault 产物）；无则未记密码 */
  encPassword?: string;
  lastLogin: number;
}

const ACCOUNTS_KEY = 'openmate-login-accounts';
const AUTO_LOGIN_KEY = 'openmate-auto-login';
const AUTO_TOKEN_KEY = 'openmate-auto-token';
const LAST_ACCOUNT_KEY = 'openmate-last-account';

export function listAccounts(): SavedAccount[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = localStorage.getItem(ACCOUNTS_KEY);
    const arr = raw ? (JSON.parse(raw) as SavedAccount[]) : [];
    return arr.filter((a) => a && typeof a.username === 'string').sort((a, b) => b.lastLogin - a.lastLogin);
  } catch {
    return [];
  }
}

function writeAccounts(accounts: SavedAccount[]) {
  localStorage.setItem(ACCOUNTS_KEY, JSON.stringify(accounts));
}

/** 登录成功后记录账号；记住密码勾选才存（加密），未勾选=清除该账号已存密码。 */
export async function saveLoginAccount(username: string, password: string | null): Promise<void> {
  if (typeof window === 'undefined') return;
  const accounts = listAccounts().filter((a) => a.username !== username);
  let encPassword: string | undefined;
  let hasPassword = false;
  if (password) {
    const enc = await encryptSecret(password);
    if (enc) {
      encPassword = enc;
      hasPassword = true;
    }
    // 保险箱不可用 → 不存密码（fail-closed，明文禁令），账号仍记录
  }
  accounts.unshift({ username, hasPassword, encPassword, lastLogin: Date.now() });
  writeAccounts(accounts.slice(0, 20));
  localStorage.setItem(LAST_ACCOUNT_KEY, username);
}

/** 删除一条账号记录（含密码）；删的是自动登录账号则一并清自动登录。 */
export function removeLoginAccount(username: string): void {
  if (typeof window === 'undefined') return;
  writeAccounts(listAccounts().filter((a) => a.username !== username));
  if (getAutoLoginUser() === username) clearAutoLogin();
  if (getLastAccount() === username) localStorage.removeItem(LAST_ACCOUNT_KEY);
}

export type SavedPasswordResult =
  | { status: 'none' }                                        // 该账号没记密码
  | { status: 'ok'; password: string }                        // 解密成功
  | { status: 'failed'; reason: DecryptFailReason };          // 记了但解不开（存量密钥丢失/跨模式）

/**
 * 取已存密码（带失败区分）：'none' vs 'failed' 必须分清——
 * 'failed' 时 UI 要显性降级（重输密码框+勾选态保留），绝不静默空值。
 */
export async function getSavedPasswordDetailed(username: string): Promise<SavedPasswordResult> {
  const acc = listAccounts().find((a) => a.username === username);
  if (!acc?.encPassword) return { status: 'none' };
  const r = await decryptSecretDetailed(acc.encPassword);
  return r.ok ? { status: 'ok', password: r.plain } : { status: 'failed', reason: r.reason };
}

/** 取解密后的已存密码（未记/解密失败=null，调用方回落手输）。 */
export async function getSavedPassword(username: string): Promise<string | null> {
  const acc = listAccounts().find((a) => a.username === username);
  if (!acc?.encPassword) return null;
  return decryptSecret(acc.encPassword);
}

export function getLastAccount(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem(LAST_ACCOUNT_KEY);
}

// ── 自动登录 ──────────────────────────────────────────────────────────

export function getAutoLoginUser(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem(AUTO_LOGIN_KEY);
}

/** 设置自动登录账号（同时收/发长效 token）；传 null = 关闭自动登录。 */
export async function setAutoLogin(username: string | null, rememberToken?: string | null): Promise<void> {
  if (typeof window === 'undefined') return;
  if (!username) {
    clearAutoLogin();
    return;
  }
  localStorage.setItem(AUTO_LOGIN_KEY, username);
  if (rememberToken) {
    const enc = await encryptSecret(rememberToken);
    if (enc) localStorage.setItem(AUTO_TOKEN_KEY, JSON.stringify({ username, encToken: enc }));
  }
}

/** 自动登录：长效 token 优先（30 天 remember_token，加密保存）。 */
export async function getAutoToken(username: string): Promise<string | null> {
  if (typeof window === 'undefined') return null;
  if (getAutoLoginUser() !== username) return null;
  try {
    const raw = localStorage.getItem(AUTO_TOKEN_KEY);
    if (!raw) return null;
    const rec = JSON.parse(raw) as { username: string; encToken: string };
    if (rec.username !== username) return null;
    return decryptSecret(rec.encToken);
  } catch {
    return null;
  }
}

/** 显式登出调用：关自动登录+清长效 token（401 过期接管禁调——过期≠登出）。 */
export function clearAutoLogin(): void {
  if (typeof window === 'undefined') return;
  localStorage.removeItem(AUTO_LOGIN_KEY);
  localStorage.removeItem(AUTO_TOKEN_KEY);
}
