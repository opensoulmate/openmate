'use client';

import { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Users, Plus, Bot, Shield, Zap, User, Trash2, Check, Edit3,
} from 'lucide-react';
import { useAIGroupsStore, type AgentRole } from '@/stores/ai-groups-store';
import { LeftPanel } from '@/components/left-panel';

const ROLE_ICONS: Record<string, any> = { advisor: Shield, executor: Zap, verifier: Bot, human: User };

const AGENT_AVATAR_COLORS = [
  'bg-rose-500/20 text-rose-400', 'bg-sky-500/20 text-sky-400', 'bg-emerald-500/20 text-emerald-400',
  'bg-amber-500/20 text-amber-400', 'bg-violet-500/20 text-violet-400', 'bg-pink-500/20 text-pink-400',
  'bg-teal-500/20 text-teal-400', 'bg-orange-500/20 text-orange-400',
];

function getAgentAvatarColor(index: number) {
  return AGENT_AVATAR_COLORS[index % AGENT_AVATAR_COLORS.length];
}

export function AIGroupsSidebar() {
  const { t } = useTranslation();

  const groups = useAIGroupsStore((s) => s.groups);
  const selectedGroup = useAIGroupsStore((s) => s.selectedGroup);
  const showCreate = useAIGroupsStore((s) => s.showCreate);
  const setShowCreate = useAIGroupsStore((s) => s.setShowCreate);
  const newName = useAIGroupsStore((s) => s.newName);
  const setNewName = useAIGroupsStore((s) => s.setNewName);
  const newDesc = useAIGroupsStore((s) => s.newDesc);
  const setNewDesc = useAIGroupsStore((s) => s.setNewDesc);
  const editingGroupId = useAIGroupsStore((s) => s.editingGroupId);
  const setEditingGroupId = useAIGroupsStore((s) => s.setEditingGroupId);
  const editingName = useAIGroupsStore((s) => s.editingName);
  const setEditingName = useAIGroupsStore((s) => s.setEditingName);
  const loading = useAIGroupsStore((s) => s.loading);
  const selectGroup = useAIGroupsStore((s) => s.selectGroup);
  const createGroup = useAIGroupsStore((s) => s.createGroup);
  const availableAgents = useAIGroupsStore((s) => s.availableAgents);
  const newMembers = useAIGroupsStore((s) => s.newMembers);
  const toggleNewMember = useAIGroupsStore((s) => s.toggleNewMember);
  const fetchAvailableAgents = useAIGroupsStore((s) => s.fetchAvailableAgents);
  const setMemberRole = useAIGroupsStore((s) => s.setMemberRole);
  const deleteGroup = useAIGroupsStore((s) => s.deleteGroup);
  const renameGroup = useAIGroupsStore((s) => s.renameGroup);

  return (
    <LeftPanel
      items={groups}
      filter={(group, q) => group.name.toLowerCase().includes(q) || (group.description || '').toLowerCase().includes(q)}
      placeholder={t("aiGroups.searchPlaceholder") || "搜索群组..."}
      header={
        <div className="px-2 pb-2">
          {(
            <div className="space-y-2 p-2 rounded-lg bg-muted/50 mb-2">
              <input value={newName} onChange={e => setNewName(e.target.value)} placeholder={t("aiGroups.groupNamePlaceholder")}
                className="w-full px-2.5 py-1.5 bg-background border border-border rounded text-xs focus:outline-none focus:ring-1 focus:ring-primary/50" />
              <input value={newDesc} onChange={e => setNewDesc(e.target.value)} placeholder={t("aiGroups.descriptionOptional")}
                className="w-full px-2.5 py-1.5 bg-background border border-border rounded text-xs focus:outline-none focus:ring-1 focus:ring-primary/50" />
              {/* 参与成员——创建群组即选人（用户拍板：建组必须能选择参与的 agent） */}
              <div className="space-y-1">
                <div className="flex items-center justify-between">
                  <span className="text-[11px] text-muted-foreground">参与成员</span>
                  <span className="text-[10px] text-muted-foreground">已选 {newMembers.length}</span>
                </div>
                <div className="max-h-36 overflow-y-auto space-y-0.5">
                  {availableAgents.length === 0 ? (
                    <p className="text-[10px] text-muted-foreground px-1">接入的 Agent 加载中…</p>
                  ) : availableAgents.map((a) => {
                    const picked = newMembers.find((m) => (a.agent_id && m.agent_id === a.agent_id) || (!a.agent_id && m.name === a.name));
                    return (
                      <div key={a.agent_id || a.name} className="flex items-center gap-1.5 px-1.5 py-1 rounded hover:bg-muted">
                        <input type="checkbox" checked={!!picked} onChange={() => toggleNewMember(a)} className="accent-primary" />
                        <span className="text-xs truncate flex-1">{a.name}</span>
                        {picked && (
                          <select value={picked.role}
                            onChange={(e) => setMemberRole(picked.agent_id || picked.name, e.target.value)}
                            className="px-1 py-0.5 bg-background border border-border rounded text-[10px] focus:outline-none">
                            <option value="advisor">顾问</option>
                            <option value="executor">执行</option>
                            <option value="verifier">验证</option>
                            <option value="human">人工</option>
                          </select>
                        )}
                      </div>
                    );
                  })}
                </div>
                <p className="text-[10px] text-muted-foreground px-1">勾选入组，每个成员可指派角色</p>
              </div>
              <div className="flex gap-1.5">
                <button onClick={createGroup} disabled={loading}
                  className="flex-1 px-2.5 py-1.5 bg-primary text-primary-foreground rounded text-xs hover:bg-primary/90 disabled:opacity-50">
                  {loading ? t('aiGroups.creating') : t('aiGroups.create')}
                </button>
                <button onClick={() => setShowCreate(false)}
                  className="px-2.5 py-1.5 bg-muted border border-border rounded text-xs hover:bg-accent">{t("aiGroups.cancel")}</button>
              </div>
            </div>
          )}

        </div>
      }
      renderItem={(group) => (
        <div key={group.id}
          className={`group px-3 py-2.5 cursor-pointer hover:bg-muted/80 transition-colors border-b border-border/30 ${selectedGroup?.id === group.id ? 'bg-primary/12 text-primary' : ''}`}
          onClick={() => selectGroup(group)}>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 flex-1 min-w-0">
              <div className="w-8 h-8 rounded-lg bg-primary/20 flex items-center justify-center shrink-0">
                <Users className="w-4 h-4 text-primary" />
              </div>
              <div className="flex-1 min-w-0">
                {editingGroupId === group.id ? (
                  <div className="flex items-center gap-1" onClick={e => e.stopPropagation()}>
                    <input value={editingName} onChange={e => setEditingName(e.target.value)}
                      className="w-full px-1.5 py-0.5 bg-background border border-border rounded text-xs focus:outline-none"
                      onKeyDown={e => { if (e.key === 'Enter') renameGroup(group.id); if (e.key === 'Escape') setEditingGroupId(null); }}
                      autoFocus />
                    <button onClick={() => renameGroup(group.id)} className="p-0.5 rounded hover:bg-muted"><Check className="w-3 h-3 text-primary" /></button>
                  </div>
                ) : (
                  <div className="flex items-center gap-1">
                    <span className="text-xs lg:text-sm font-medium truncate">{group.name}</span>
                    <button className="lg:opacity-0 lg:group-hover:opacity-100 p-1 lg:p-0.5 rounded hover:bg-muted/50 touch-manipulation transition-opacity"
                      onClick={e => { e.stopPropagation(); setEditingGroupId(group.id); setEditingName(group.name); }}>
                      <Edit3 className="w-3 h-3 lg:w-2.5 lg:h-2.5 text-muted-foreground" />
                    </button>
                  </div>
                )}
                <p className="text-[11px] text-muted-foreground truncate">
                  {group.description || t('aiGroups.agentCount', { count: group.agents?.length || 0 })}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-1">
              <div className="flex -space-x-1.5 mr-1">
                {(group.agents || []).slice(0, 3).map((a, i) => {
                  const Icon = ROLE_ICONS[a.role] || Bot;
                  return <div key={i} className={`w-5 h-5 rounded-full flex items-center justify-center border border-card ${getAgentAvatarColor(i)}`}><Icon className="w-2.5 h-2.5" /></div>;
                })}
              </div>
              <button className="lg:opacity-0 lg:group-hover:opacity-100 p-1 lg:p-0.5 rounded hover:bg-red-500/10 text-red-500 touch-manipulation transition-opacity"
                onClick={(e) => deleteGroup(group.id, e)} title={t("aiGroups.delete")}>
                <Trash2 className="w-3 h-3" />
              </button>
            </div>
          </div>
        </div>
      )}
      emptyState={
        <div className="flex flex-col items-center justify-center h-40 text-muted-foreground">
          <Users className="w-8 h-8 mb-2 opacity-40" />
          <p className="text-xs">{t("aiGroups.noGroups")}</p>
        </div>
      }
    />
  );
}
