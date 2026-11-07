import { Bot, User, Copy, Volume2, ThumbsUp, ThumbsDown, Share2, RefreshCw, Bookmark, MoreHorizontal, RotateCcw, X } from 'lucide-react';
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
 * - 底部操作栏：复制/朗读/赞/踩/转发/重新生成/检查点（agent）· 复制/分享/修改/收藏/删除（user）
 *   —— 传了哪个回调就渲染哪个按钮，样式一处维护
 */

export interface MessageActions {
  onCopy?: () => void;
  onReadAloud?: () => void;
  onLike?: () => void;
  onDislike?: () => void;
  onShare?: () => void;
  onRegenerate?: () => void;
  onCheckpoint?: () => void;
  onEdit?: () => void;
  onFavorite?: () => void;
  onDelete?: () => void;
  onMore?: () => void;
}

const BTN = 'p-1.5 rounded hover:bg-muted-foreground/10 text-muted-foreground/60 hover:text-muted-foreground transition-colors';

function ActionButton({ onClick, title, hover, children }: { onClick?: () => void; title: string; hover?: string; children: ReactNode }) {
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
  actions,
  children,
}: {
  role: string;
  timestamp: Date;
  /** 群组多发言人时显示在时间戳行的名字；会话不传 */
  name?: string;
  id?: string;
  /** 底部操作栏回调——传了哪个就渲染哪个按钮 */
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
        {/* ── 底部操作栏（agent 左对齐 / user 右对齐） ── */}
        {actions && !isUser && (
          <div className="flex items-center gap-0.5 mt-1.5 -mb-1">
            {actions.onCopy && <ActionButton onClick={actions.onCopy} title="复制"><Copy className="w-3.5 h-3.5" /></ActionButton>}
            {actions.onReadAloud && <ActionButton onClick={actions.onReadAloud} title="朗读"><Volume2 className="w-3.5 h-3.5" /></ActionButton>}
            {actions.onLike && <ActionButton onClick={actions.onLike} title="喜欢" hover="hover:text-green-500"><ThumbsUp className="w-3.5 h-3.5" /></ActionButton>}
            {actions.onDislike && <ActionButton onClick={actions.onDislike} title="不喜欢" hover="hover:text-red-500"><ThumbsDown className="w-3.5 h-3.5" /></ActionButton>}
            {actions.onShare && <ActionButton onClick={actions.onShare} title="转发"><Share2 className="w-3.5 h-3.5" /></ActionButton>}
            {actions.onRegenerate && <ActionButton onClick={actions.onRegenerate} title="重新生成"><RefreshCw className="w-3.5 h-3.5" /></ActionButton>}
            {actions.onCheckpoint && <ActionButton onClick={actions.onCheckpoint} title="保存检查点"><Bookmark className="w-3.5 h-3.5" /></ActionButton>}
            {actions.onMore && <ActionButton onClick={actions.onMore} title="更多"><MoreHorizontal className="w-3.5 h-3.5" /></ActionButton>}
          </div>
        )}
        {actions && isUser && (
          <div className="flex items-center gap-0.5 mt-1.5 -mb-1 justify-end">
            {actions.onCopy && <ActionButton onClick={actions.onCopy} title="复制"><Copy className="w-3.5 h-3.5" /></ActionButton>}
            {actions.onShare && <ActionButton onClick={actions.onShare} title="分享"><Share2 className="w-3.5 h-3.5" /></ActionButton>}
            {actions.onEdit && <ActionButton onClick={actions.onEdit} title="修改"><RotateCcw className="w-3.5 h-3.5" /></ActionButton>}
            {actions.onFavorite && <ActionButton onClick={actions.onFavorite} title="收藏" hover="hover:text-yellow-500"><Bookmark className="w-3.5 h-3.5" /></ActionButton>}
            {actions.onDelete && <ActionButton onClick={actions.onDelete} title="删除" hover="hover:text-red-500"><X className="w-3.5 h-3.5" /></ActionButton>}
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
