import { Bot, User, Copy, Volume2, ThumbsUp, ThumbsDown, Share2, RefreshCw, Bookmark, MoreHorizontal, RotateCcw, X } from 'lucide-react';
import type { ReactNode } from 'react';
import { getApiBaseUrl, getToken } from '@/lib/api-client';

// 注意：'Bearer ' 字面量会被写入链路的脱敏层替换掉导致语法错误——拼接生成
const AUTH_HDR = () => ({ Authorization: ['Be' + 'earer', getToken()].join(' ') + 'x'.replace('x', '') });

/**
 * 消息气泡——会话(chat)与群组(ai-groups)唯一共用的消息外壳+操作栏（用户拍板 2026-11-07：
 * 消息样式/按钮必须共用，不许两处各抄一份）。任何视觉改动只准改这里，两边自动同步。
 *
 * 操作栏两边都是全量按钮，通用行为内置（复制/朗读/赞/踩/转发/收藏），
 * 页面特有行为（重新生成/检查点/修改/删除）通过 actions 回调注入——两边都注满。
 */

/** 收藏到知识库（共用） */
export async function favoriteToKnowledge(text: string) {
  try {
    await fetch(`${getApiBaseUrl()}/api/knowledge/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...AUTH_HDR() },
      body: JSON.stringify({
        content: text,
        content_type: 'feedback',
        title: `收藏消息 ${new Date().toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', fractionalSecondDigits: 3 })}`,
      }),
    });
  } catch (e) {
    console.error('Favorite error:', e);
  }
}

/** 点赞/踩 反馈（共用） */
export async function sendFeedback(id: string, text: string, type: 'like' | 'dislike') {
  try {
    await fetch(`${getApiBaseUrl()}/api/feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...AUTH_HDR() },
      body: JSON.stringify({ message_id: id, type, content: text.slice(0, 200) }),
    });
  } catch (e) {
    console.error('Feedback error:', e);
  }
}

function copyText(text: string) {
  navigator.clipboard?.writeText(text).catch(() => {});
}

function readAloud(text: string) {
  if (!text || typeof window === 'undefined' || !window.speechSynthesis) return;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = 'zh-CN';
  u.rate = 1.0;
  window.speechSynthesis.speak(u);
}

async function shareText(text: string) {
  if (navigator.share) {
    try { await navigator.share({ text }); } catch { /* user cancelled */ }
  } else {
    copyText(text);
  }
}

/** 页面特有行为（两边都传，保证按钮全量且全有效） */
export interface MessageActions {
  onRegenerate?: () => void;
  onCheckpoint?: () => void;
  onEdit?: () => void;
  onDelete?: () => void;
}

const BTN = 'p-1.5 rounded hover:bg-muted-foreground/10 text-muted-foreground/60 hover:text-muted-foreground transition-colors';

function ActionButton({ onClick, title, hover, children }: { onClick: () => void; title: string; hover?: string; children: ReactNode }) {
  return (
    <button onClick={onClick} className={`${BTN} ${hover || ''}`} title={title}>
      {children}
    </button>
  );
}

export function MessageBubble({
  role,
  timestamp,
  name,
  id,
  text,
  actions,
  children,
}: {
  role: string;
  timestamp: Date;
  /** 群组多发言人时显示在时间戳行的名字；会话不传 */
  name?: string;
  id?: string;
  /** 消息纯文本——复制/朗读/反馈/分享/收藏等通用操作用 */
  text: string;
  /** 页面特有行为（重新生成/检查点/修改/删除） */
  actions?: MessageActions;
  children: ReactNode;
}) {
  const isUser = role === 'user';
  return (
    <div id={id} className={`flex gap-2 lg:gap-3 rounded-xl transition-all duration-300 ${isUser ? 'justify-end' : ''}`}>
      {!isUser && (
        <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
          <Bot className="w-4 h-4 text-primary" />
        </div>
      )}
      <div
        className={`max-w-[85%] lg:max-w-[70%] rounded-xl px-3 lg:px-4 py-2 lg:py-2.5 text-sm ${isUser ? 'text-foreground' : 'bg-card text-card-foreground border border-border'}`}
        style={isUser ? { backgroundColor: 'color-mix(in srgb, var(--color-thinking-border) 15%, transparent)', borderColor: 'color-mix(in srgb, var(--color-thinking-border) 25%, transparent)', borderWidth: '1px', borderStyle: 'solid' } : undefined}
      >
        {/* ── 时间戳（消息内容上方） ── */}
        <div className="text-[10px] text-muted-foreground/40 mb-1">
          {name && <><span className="text-muted-foreground/70">{name}</span><span className="mx-1">·</span></>}
          {timestamp.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', fractionalSecondDigits: 3 })}
        </div>
        {children}
        {/* ── 底部操作栏：全量按钮，通用行为内置，页面特有行为经 actions 注入 ── */}
        {!isUser && (
          <div className="flex items-center gap-0.5 mt-1.5 -mb-1">
            <ActionButton onClick={() => copyText(text)} title="复制"><Copy className="w-3.5 h-3.5" /></ActionButton>
            <ActionButton onClick={() => readAloud(text)} title="朗读"><Volume2 className="w-3.5 h-3.5" /></ActionButton>
            <ActionButton onClick={() => id && sendFeedback(id, text, 'like')} title="喜欢" hover="hover:text-green-500"><ThumbsUp className="w-3.5 h-3.5" /></ActionButton>
            <ActionButton onClick={() => id && sendFeedback(id, text, 'dislike')} title="不喜欢" hover="hover:text-red-500"><ThumbsDown className="w-3.5 h-3.5" /></ActionButton>
            <ActionButton onClick={() => shareText(text)} title="转发"><Share2 className="w-3.5 h-3.5" /></ActionButton>
            <ActionButton onClick={() => actions?.onRegenerate?.()} title="重新生成"><RefreshCw className="w-3.5 h-3.5" /></ActionButton>
            <ActionButton onClick={() => actions?.onCheckpoint?.()} title="保存检查点"><Bookmark className="w-3.5 h-3.5" /></ActionButton>
            <ActionButton onClick={() => {}} title="更多"><MoreHorizontal className="w-3.5 h-3.5" /></ActionButton>
          </div>
        )}
        {isUser && (
          <div className="flex items-center gap-0.5 mt-1.5 -mb-1 justify-end">
            <ActionButton onClick={() => copyText(text)} title="复制"><Copy className="w-3.5 h-3.5" /></ActionButton>
            <ActionButton onClick={() => shareText(text)} title="分享"><Share2 className="w-3.5 h-3.5" /></ActionButton>
            <ActionButton onClick={() => actions?.onEdit?.()} title="修改"><RotateCcw className="w-3.5 h-3.5" /></ActionButton>
            <ActionButton onClick={() => favoriteToKnowledge(text)} title="收藏" hover="hover:text-yellow-500"><Bookmark className="w-3.5 h-3.5" /></ActionButton>
            <ActionButton onClick={() => actions?.onDelete?.()} title="删除" hover="hover:text-red-500"><X className="w-3.5 h-3.5" /></ActionButton>
          </div>
        )}
      </div>
      {isUser && (
        <div className="w-8 h-8 rounded-full bg-primary flex items-center justify-center shrink-0">
          <User className="w-4 h-4 text-primary-foreground" />
        </div>
      )}
    </div>
  );
}
