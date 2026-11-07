import { Bot, User } from 'lucide-react';
import type { ReactNode } from 'react';

/**
 * 消息气泡——会话(chat)与群组(ai-groups)唯一共用的消息外壳（用户拍板 2026-11-07：
 * 消息样式必须共用，不许两处各抄一份）。任何视觉改动只准改这里，两边自动同步。
 *
 * 结构（与原 chat-client.tsx 逐像素一致）：
 * - 行：flex gap-2 lg:gap-3，用户右对齐
 * - agent 头像在气泡左（bg-primary/10 + Bot）；用户头像在气泡右（bg-primary + User）
 * - 气泡：用户=thinking-border 色域内联样式；agent=bg-card+border
 * - 时间戳在内容上方：text-[10px] text-muted-foreground/40，全量日期含毫秒
 * - name（仅群组传）：多发言人的名字，跟时间戳同行
 */
export function MessageBubble({
  role,
  timestamp,
  name,
  id,
  children,
}: {
  role: string;
  timestamp: Date;
  /** 群组多发言人时显示在时间戳行的名字；会话不传 */
  name?: string;
  id?: string;
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
      </div>
      {isUser && (
        <div className="w-8 h-8 rounded-full bg-primary flex items-center justify-center shrink-0">
          <User className="w-4 h-4 text-primary-foreground" />
        </div>
      )}
    </div>
  );
}
