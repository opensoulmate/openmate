'use client';
// QQ 登录框式账号输入框 + 历史账号下拉。
// 交互逐条对齐 QQ 登录框：点输入框/右侧箭头弹历史列表（头像位/账号名/密码已记标记，
// 每条可删 ×）；无历史时无下拉也无箭头；键盘 ↑↓ 选择、Enter 填入、Esc 关闭；
// 点击条目即填（账号+已记密码由父级回填）；点外部/失焦即收。

import { useEffect, useRef, useState } from 'react';
import { ChevronDown, KeyRound, X, User } from 'lucide-react';
import type { SavedAccount } from '@/lib/login-accounts';

interface Props {
  value: string;
  onChange: (v: string) => void;
  accounts: SavedAccount[];
  /** 点选账号：父级回填账号+已记密码 */
  onSelect: (account: SavedAccount) => void;
  onDelete: (username: string) => void;
  onSubmit: () => void;
  placeholder: string;
  disabled?: boolean;
  autoFocus?: boolean;
  /** 密码已记标记文案（i18n 由父级注入） */
  passwordSavedMark: string;
  deleteAccountLabel: string;
}

export function LoginAccountField({
  value,
  onChange,
  accounts,
  onSelect,
  onDelete,
  onSubmit,
  placeholder,
  disabled,
  autoFocus,
  passwordSavedMark,
  deleteAccountLabel,
}: Props) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const wrapRef = useRef<HTMLDivElement>(null);
  const hasHistory = accounts.length > 0;

  // 点外部关闭（QQ 下拉消失时机）
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  const select = (acc: SavedAccount) => {
    setOpen(false);
    setActive(-1);
    onSelect(acc);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (open && hasHistory) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setActive((i) => (i + 1) % accounts.length);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setActive((i) => (i <= 0 ? accounts.length - 1 : i - 1));
        return;
      }
      if (e.key === 'Enter') {
        // Enter 填入高亮条目（下拉开着不提交表单）
        if (active >= 0 && active < accounts.length) {
          e.preventDefault();
          select(accounts[active]);
          return;
        }
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setOpen(false);
        setActive(-1);
        return;
      }
    }
    if (e.key === 'Enter') onSubmit();
  };

  return (
    <div ref={wrapRef} className="relative w-full mb-3">
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onFocus={() => {
          if (hasHistory) {
            setOpen(true);
            setActive(-1);
          }
        }}
        onBlur={(e) => {
          // 焦点离开整个容器才收起（点下拉行不触发）
          if (!wrapRef.current?.contains(e.relatedTarget as Node)) setOpen(false);
        }}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        className={`w-full px-4 py-3 rounded-lg border bg-background text-sm ${hasHistory ? 'pr-12' : ''}`}
        autoFocus={autoFocus}
        disabled={disabled}
        autoComplete="username"
        aria-haspopup={hasHistory ? 'listbox' : undefined}
        aria-expanded={hasHistory ? open : undefined}
      />
      {hasHistory && (
        <button
          type="button"
          tabIndex={-1}
          aria-label={placeholder}
          onClick={() => {
            setOpen((o) => {
              if (!o) setActive(-1);
              return !o;
            });
          }}
          className="absolute right-1 top-1/2 -translate-y-1/2 w-11 h-11 flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground transition-colors"
        >
          <ChevronDown className={`w-4 h-4 transition-transform ${open ? 'rotate-180' : ''}`} />
        </button>
      )}

      {open && hasHistory && (
        <ul
          role="listbox"
          className="absolute left-0 right-0 top-full mt-1 z-50 max-h-56 overflow-y-auto rounded-lg border bg-popover shadow-lg py-1"
          onMouseDown={(e) => e.preventDefault()}
        >
          {accounts.map((acc, i) => (
            <li
              key={acc.username}
              role="option"
              aria-selected={i === active}
              className={`flex items-center gap-2 px-2 py-1.5 cursor-pointer transition-colors ${i === active ? 'bg-accent text-accent-foreground' : 'hover:bg-muted'}`}
              onMouseEnter={() => setActive(i)}
              onClick={() => select(acc)}
            >
              {/* 头像位 */}
              <span className="w-8 h-8 shrink-0 rounded-full bg-primary/10 text-primary flex items-center justify-center text-sm font-medium overflow-hidden">
                {acc.username.slice(0, 1).toUpperCase() || <User className="w-4 h-4" />}
              </span>
              <span className="flex-1 min-w-0 truncate text-sm">{acc.username}</span>
              {acc.hasPassword && (
                <span className="shrink-0 flex items-center gap-1 text-xs text-muted-foreground" title={passwordSavedMark}>
                  <KeyRound className="w-3 h-3" />
                  {passwordSavedMark}
                </span>
              )}
              <button
                type="button"
                aria-label={deleteAccountLabel}
                title={deleteAccountLabel}
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete(acc.username);
                }}
                className="shrink-0 w-7 h-7 flex items-center justify-center rounded-md text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
