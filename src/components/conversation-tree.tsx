"use client";

import { useCallback, useMemo } from "react";
import { cn } from "@/lib/utils";
import { useTranslation } from "react-i18next";
import { MessageSquare, ChevronDown, ChevronRight, Plus, Trash2, Loader2 } from "lucide-react";
import { useIsMobile } from "@/hooks/use-mobile";

// ── Types ────────────────────────────────────────────────────────

interface Session {
  id: string;
  name?: string;
  title?: string;
  platform?: string;
  chat_id?: string;
  last_message?: string;
  unread?: number;
  workspace?: string;
  last_active?: string;
  updated_at?: string;
  created_at?: string;
  message_count?: number;
  source?: string;
  tags?: string[];
}

interface SourceGroup {
  source: string;
  label: string;
  icon: string;
  sessions: Session[];
  expanded: boolean;
}

export interface AgentInfo {
  id: string;
  name: string;
  icon: string;
  logo?: string;
  description: string;
  installed: boolean;
  available?: boolean;
  category?: string;
  path?: string;
  sessions: Session[];
  expanded: boolean;
  sourceGroups?: SourceGroup[];
}

// ── Unread badge ─────────────────────────────────────────────────

function UnreadBadge({ count }: { count: number }) {
  if (count <= 0) return null;
  return (
    <span className="inline-flex items-center justify-center min-w-[16px] h-4 px-1 rounded-full bg-red-500 text-white text-[9px] font-bold leading-none shrink-0">
      {count > 99 ? '99+' : count}
    </span>
  );
}

// ── Props ────────────────────────────────────────────────────────

interface ConversationTreeProps {
  agents: AgentInfo[];
  activeSessionId: string | null;
  getUnread: (session: Session) => number;
  onToggleAgent: (agentId: string) => void;
  onToggleSourceGroup: (agentId: string, source: string) => void;
  onSelectSession: (session: Session, agent: AgentInfo) => void;
  onNewSession?: (agentId: string) => void;
  onDeleteSession?: (sessionId: string) => void;
  /** Optional search query — filters agents/sessions when non-empty */
  search?: string;
  /** Optional class for the root container */
  className?: string;
}

// ── Component ────────────────────────────────────────────────────

export function ConversationTree({
  agents,
  activeSessionId,
  getUnread,
  onToggleAgent,
  onToggleSourceGroup,
  onSelectSession,
  onNewSession,
  onDeleteSession,
  search,
  className,
}: ConversationTreeProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();

  // Filter agents/sessions when search is active
  const displayAgents = useMemo(() => {
    const q = (search || '').trim().toLowerCase();
    if (!q) return agents;
    return agents.filter(a => {
      const nameMatch = a.name.toLowerCase().includes(q);
      const sessionMatch = a.sessions.some(s =>
        (s.title || s.name || '').toLowerCase().includes(q) ||
        (s.last_message || '').toLowerCase().includes(q)
      );
      return nameMatch || sessionMatch;
    }).map(a => ({
      ...a,
      expanded: true, // auto-expand when searching
      sourceGroups: a.sourceGroups?.map(g => ({
        ...g,
        expanded: true,
        sessions: g.sessions.filter(s =>
          a.name.toLowerCase().includes(q) ||
          (s.title || s.name || '').toLowerCase().includes(q) ||
          (s.last_message || '').toLowerCase().includes(q)
        ),
      })).filter(g => g.sessions.length > 0),
    }));
  }, [agents, search]);

  // Sort sessions: unread first, then by last_active desc
  const sortSessions = (sessions: Session[]) =>
    sessions.slice().sort((a, b) => {
      const ua = getUnread(a), ub = getUnread(b);
      if (ua > 0 && ub === 0) return -1;
      if (ua === 0 && ub > 0) return 1;
      const ta = a.last_active || a.updated_at || '';
      const tb = b.last_active || b.updated_at || '';
      return tb.localeCompare(ta);
    });

  return (
    <div className={cn("flex-1 overflow-y-auto", className)}>


      {displayAgents.length === 0 ? (
        <div className="flex-1 flex items-center justify-center min-h-[200px]">
          <div className="flex items-center gap-2 text-xs text-muted-foreground animate-pulse">
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
            <span>{t("common.loading", "加载中...")}</span>
          </div>
        </div>
      ) : (
        displayAgents.map(agent => (
          <div key={agent.id}>
            {/* Agent header */}
            <div className="flex items-center justify-between px-2 py-2 lg:py-1.5 hover:bg-muted/50 active:bg-muted/70 group touch-manipulation">
              <button onClick={() => onToggleAgent(agent.id)} className="flex items-center gap-1.5 flex-1 min-w-0">
                {agent.expanded
                  ? <ChevronDown className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                  : <ChevronRight className="w-3.5 h-3.5 text-muted-foreground shrink-0" />}
                {agent.logo
                  ? <img src={agent.logo} alt={agent.name} className="w-5 h-5 rounded object-contain" onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }} />
                  : null}
                <span className={cn("text-sm", agent.logo ? "hidden" : "")}>{agent.icon}</span>
                <span className="text-sm font-medium truncate">{agent.name}</span>
              </button>
              {(() => {
                const agentUnread = agent.sessions.reduce((s, session) => s + getUnread(session), 0);
                return agentUnread > 0 ? (
                  <UnreadBadge count={agentUnread} />
                ) : (
                  <span className="text-[10px] text-muted-foreground shrink-0">{agent.sessions.length}</span>
                );
              })()}
              <button
                onClick={(e) => { e.stopPropagation(); onNewSession?.(agent.id); }}
                className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-primary/10 text-muted-foreground hover:text-primary transition-all shrink-0"
                title={t("sidebar.newChat", "新建会话")}
              >
                <Plus size={13} />
              </button>
            </div>

            {/* Source groups or flat sessions — mobile: always flat (2-level), desktop: 3-level tree */}
            {agent.expanded && !isMobile && agent.sourceGroups && agent.sourceGroups.length > 0 ? (
              agent.sourceGroups.map(group => (
                <div key={group.source}>
                  <button onClick={() => onToggleSourceGroup(agent.id, group.source)}
                    className="w-full flex items-center gap-1.5 pl-6 pr-3 py-2 lg:py-1.5 hover:bg-muted/40 active:bg-muted/60 touch-manipulation transition-colors">
                    {group.expanded
                      ? <ChevronDown className="w-3 h-3 text-muted-foreground shrink-0" />
                      : <ChevronRight className="w-3 h-3 text-muted-foreground shrink-0" />}
                    <span className="text-xs">{group.icon}</span>
                    <span className="text-xs font-medium text-muted-foreground">{group.label}</span>
                    {(() => {
                      const groupUnread = group.sessions.reduce((s, session) => s + getUnread(session), 0);
                      return groupUnread > 0 ? (
                        <span className="ml-auto"><UnreadBadge count={groupUnread} /></span>
                      ) : (
                        <span className="text-[10px] text-muted-foreground ml-auto">{group.sessions.length}</span>
                      );
                    })()}
                  </button>
                  {group.expanded && sortSessions(group.sessions).map(session => {
                    const unread = getUnread(session);
                    const isActive = activeSessionId === session.id;
                    return (
                      <div key={session.id}
                        role="button" tabIndex={0}
                        onClick={() => onSelectSession(session, agent)}
                        onKeyDown={(e) => { if (e.key === 'Enter') onSelectSession(session, agent); }}
                        className={cn(
                          "group w-full text-left pl-8 lg:pl-12 pr-3 py-2.5 lg:py-2 hover:bg-muted/80 active:bg-muted transition-colors cursor-pointer touch-manipulation",
                          isActive && "bg-primary/12 text-primary"
                        )}>
                        <div className="flex items-center gap-1.5">
                          <MessageSquare className={cn("w-3 h-3 shrink-0", isActive ? "text-primary" : "text-muted-foreground")} />
                          <span className={cn("text-xs truncate flex-1", unread > 0 ? "font-bold" : "", isActive && "text-primary")}>{session.name || session.title || "Untitled"}</span>
                          <UnreadBadge count={unread} />
                          {onDeleteSession && (
                            <button
                              onClick={(e) => { e.stopPropagation(); onDeleteSession(session.id); }}
                              className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-red-500/10 text-muted-foreground hover:text-red-500 transition-all"
                              title="删除会话"
                            >
                              <Trash2 size={12} />
                            </button>
                          )}
                        </div>
                        {isMobile && session.last_message ? (
                          <div className="text-[10px] text-muted-foreground/70 truncate ml-4.5 mt-0.5">
                            {session.last_message}
                          </div>
                        ) : (session.last_active || session.updated_at) ? (
                          <div className="text-[10px] text-muted-foreground ml-4.5 mt-0.5">
                            {new Date(session.last_active || session.updated_at!).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}
                          </div>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              ))
            ) : (
              agent.expanded && <>
              {sortSessions(agent.sessions).map(session => {
                const unread = getUnread(session);
                const isActive = activeSessionId === session.id;
                return (
                  <div key={session.id}
                    role="button" tabIndex={0}
                    onClick={() => onSelectSession(session, agent)}
                    onKeyDown={(e) => { if (e.key === 'Enter') onSelectSession(session, agent); }}
                    className={cn(
                      "group w-full text-left pl-8 pr-3 py-2.5 lg:py-2 hover:bg-muted/80 active:bg-muted transition-colors cursor-pointer touch-manipulation",
                      isActive && "bg-primary/12 text-primary"
                    )}>
                    <div className="flex items-center gap-1.5">
                      <MessageSquare className={cn("w-3 h-3 shrink-0", isActive ? "text-primary" : "text-muted-foreground")} />
                      <span className={cn("text-xs truncate flex-1", unread > 0 ? "font-bold" : "", isActive && "text-primary")}>{session.name || session.title || "Untitled"}</span>
                      <UnreadBadge count={unread} />
                      {onDeleteSession && (
                        <button
                          onClick={(e) => { e.stopPropagation(); onDeleteSession(session.id); }}
                          className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-red-500/10 text-muted-foreground hover:text-red-500 transition-all"
                          title="删除会话"
                        >
                          <Trash2 size={12} />
                        </button>
                      )}
                    </div>
                    {isMobile && session.last_message ? (
                      <div className="text-[10px] text-muted-foreground/70 truncate ml-4.5 mt-0.5">
                        {session.last_message}
                      </div>
                    ) : (session.last_active || session.updated_at) ? (
                      <div className="text-[10px] text-muted-foreground ml-4.5 mt-0.5">
                        {new Date(session.last_active || session.updated_at!).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}
                      </div>
                    ) : null}
                  </div>
                );
              })}
              </>
            )}

            {agent.expanded && agent.sessions.length === 0 && (
              <div className="pl-8 pr-3 py-1">
                <div className="py-1 text-xs text-muted-foreground italic">
                  {t("chat.noSessions")}
                </div>

              </div>
            )}
          </div>
        ))
      )}
    </div>
  );
}
