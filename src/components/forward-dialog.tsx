'use client';

import { useState, useEffect } from 'react';
import { Dialog } from '@/components/ui/dialog';
import { useAppStore } from '@/stores/app-store';
import { useAIGroupsStore } from '@/stores/ai-groups-store';

/**
 * 转发器（唯一代码源）——消息气泡的转发/分享、输入条的转发按钮共用。
 * 目标三选一：任意会话 / 任意群组（可现场新建群勾参与 agent）/ 外部分享。
 * 转到群组 = 自动把内容发进群并发起讨论（转发即讨论，用户拍板 2026-11-07）。
 */
export function ForwardDialog({ open, onClose, text }: { open: boolean; onClose: () => void; text: string; }) {
  const sidebarAgents = useAppStore((s) => s.sidebarAgents) as any[];
  const groups = useAIGroupsStore((s) => s.groups) as any[];
  const dAgents = useAIGroupsStore((s) => s.availableAgents) as any[];
  const dNewName = useAIGroupsStore((s) => s.newName);
  const dNewMembers = useAIGroupsStore((s) => s.newMembers) as any[];
  const [target, setTarget] = useState(''); // session:<id> | group:<id> | 'new' | 'external'

  useEffect(() => {
    if (!open) return;
    setTarget('');
    const s = useAIGroupsStore.getState();
    s.fetchGroups();
    s.fetchAvailableAgents();
  }, [open]);

  // 会话清单 = 注册表准入的 agent 各自的会话（与侧栏一条真源）
  const sessions = (sidebarAgents || []).flatMap((a: any) =>
    (a.sessions || []).map((s: any) => ({ id: s.id, name: s.name, agentName: a.name })),
  );

  const gotoGroup = (gid: string) => {
    window.location.href = `/ai-groups?group=${encodeURIComponent(gid)}&topic=${encodeURIComponent(text.slice(0, 200))}`;
  };

  const handleConfirm = async () => {
    if (!target) return;
    if (target === 'external') {
      if (navigator.share) {
        try { await navigator.share({ text }); } catch { /* 用户取消 */ }
      } else {
        navigator.clipboard?.writeText(text).catch(() => {});
      }
      onClose();
      return;
    }
    if (target === 'new') {
      if (!dNewName.trim() || dNewMembers.length === 0) return;
      const name = dNewName.trim();
      await useAIGroupsStore.getState().createGroup();
      const gid = (useAIGroupsStore.getState().groups || []).find((x: any) => x.name === name)?.id || '';
      if (gid) gotoGroup(gid);
      onClose();
      return;
    }
    if (target.startsWith('session:')) {
      window.location.href = `/chat?session=${encodeURIComponent(target.slice(8))}&forward=${encodeURIComponent(text.slice(0, 200))}`;
      onClose();
      return;
    }
    if (target.startsWith('group:')) {
      gotoGroup(target.slice(6));
      onClose();
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="转发到"
      description="任意会话 / 任意群组（转到群组=自动发起讨论）/ 外部分享"
      footer={<>
        <button onClick={onClose} className="px-3 py-1.5 rounded-lg text-xs text-muted-foreground hover:bg-muted transition-colors">取消</button>
        <button onClick={handleConfirm} className="px-3 py-1.5 rounded-lg text-xs bg-primary text-primary-foreground hover:bg-primary/90 transition-colors">
          {target === 'new' ? '创建群组并转发' : '转发'}
        </button>
      </>}
    >
      <div className="space-y-2 max-h-[60vh] overflow-y-auto">
        {/* 会话 */}
        {sessions.map((s) => (
          <label key={s.id} className={`flex items-center gap-2.5 p-2.5 rounded-lg border cursor-pointer transition-colors ${target === `session:${s.id}` ? 'border-primary bg-primary/5' : 'border-border hover:bg-muted/50'}`}>
            <input type="radio" className="accent-[var(--color-primary)]" checked={target === `session:${s.id}`} onChange={() => setTarget(`session:${s.id}`)} />
            <span className="text-sm">{s.name || s.id}</span>
            <span className="text-[10px] text-muted-foreground ml-auto">{s.agentName}</span>
          </label>
        ))}
        {/* 群组 */}
        {(groups || []).map((g) => (
          <label key={g.id} className={`flex items-center gap-2.5 p-2.5 rounded-lg border cursor-pointer transition-colors ${target === `group:${g.id}` ? 'border-primary bg-primary/5' : 'border-border hover:bg-muted/50'}`}>
            <input type="radio" className="accent-[var(--color-primary)]" checked={target === `group:${g.id}`} onChange={() => setTarget(`group:${g.id}`)} />
            <span className="text-sm">{g.name}</span>
            <span className="text-[10px] text-muted-foreground ml-auto">{g.agents?.length || 0} Agent</span>
          </label>
        ))}
        {/* 新建群组 */}
        <label className={`flex items-center gap-2.5 p-2.5 rounded-lg border cursor-pointer transition-colors ${target === 'new' ? 'border-primary bg-primary/5' : 'border-border hover:bg-muted/50'}`}>
          <input type="radio" className="accent-[var(--color-primary)]" checked={target === 'new'} onChange={() => setTarget('new')} />
          <span className="text-sm">新建群组</span>
        </label>
        {target === 'new' && (
          <div className="ml-7 space-y-2.5">
            <input
              value={dNewName}
              onChange={(e) => useAIGroupsStore.getState().setNewName(e.target.value)}
              placeholder="群组名称"
              className="w-full px-2.5 py-1.5 rounded-lg border border-border bg-background text-sm focus:outline-none focus:ring-1 focus:ring-primary"
            />
            <div className="flex flex-wrap gap-1.5">
              {(dAgents || []).map((a) => {
                const on = dNewMembers.some((m) => m.agent_id === a.agent_id);
                return (
                  <button
                    key={a.agent_id}
                    onClick={() => {
                      const next = on
                        ? dNewMembers.filter((m) => m.agent_id !== a.agent_id)
                        : [...dNewMembers, { agent_id: a.agent_id, name: a.name, role: a.role || 'executor', model: a.model || '' }];
                      useAIGroupsStore.setState({ newMembers: next });
                    }}
                    className={`px-2.5 py-1 rounded-full text-xs border transition-colors ${on ? 'border-primary bg-primary/10 text-primary' : 'border-border text-muted-foreground hover:bg-muted'}`}
                  >
                    {a.name}
                  </button>
                );
              })}
              {dAgents.length === 0 && (
                <span className="text-xs text-muted-foreground">暂无可用 Agent（设置 → Agent 管理 探测连接）</span>
              )}
            </div>
          </div>
        )}
        {/* 外部 */}
        <label className={`flex items-center gap-2.5 p-2.5 rounded-lg border cursor-pointer transition-colors ${target === 'external' ? 'border-primary bg-primary/5' : 'border-border hover:bg-muted/50'}`}>
          <input type="radio" className="accent-[var(--color-primary)]" checked={target === 'external'} onChange={() => setTarget('external')} />
          <span className="text-sm">外部分享</span>
          <span className="text-[10px] text-muted-foreground ml-auto">系统分享 / 复制</span>
        </label>
      </div>
    </Dialog>
  );
}
