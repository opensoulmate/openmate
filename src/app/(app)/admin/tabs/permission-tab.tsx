'use client';
import { useState, useEffect, useCallback } from 'react';
import { Shield, Loader2, Plus, Trash2, Users, FileText, Search, X, Save } from 'lucide-react';
import { getApiBaseUrl, getToken } from '@/lib/api-client';
import { useTranslation } from 'react-i18next';

interface Policy {
  id?: string;
  role: string;
  resource: string;
  action: string;
  effect: string;
  created_at?: string;
}

interface RoleInfo {
  username: string;
  roles: string[];
}

export function PermissionTab() {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<'policies' | 'roles'>('policies');
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');

  // Create policy form
  const [showCreate, setShowCreate] = useState(false);
  const [newRole, setNewRole] = useState('');
  const [newResource, setNewResource] = useState('');
  const [newAction, setNewAction] = useState('read');
  const [newEffect, setNewEffect] = useState('allow');
  const [creating, setCreating] = useState(false);

  // Role lookup
  const [lookupUser, setLookupUser] = useState('');
  const [userRoles, setUserRoles] = useState<RoleInfo | null>(null);
  const [looking, setLooking] = useState(false);

  // Assign role
  const [assignUser, setAssignUser] = useState('');
  const [assignRole, setAssignRole] = useState('');
  const [assigning, setAssigning] = useState(false);

  // Delete confirmation
  const [deleteTarget, setDeleteTarget] = useState<Policy | null>(null);

  const apiHeaders = useCallback((): Record<string, string> => {
    const token = getToken();
    const h: Record<string, string> = { 'Content-Type': 'application/json' };
    if (token) h['Authorization'] = `Bearer ${token}`;
    return h;
  }, []);

  const loadPolicies = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const base = getApiBaseUrl();
      const res = await fetch(`${base}/api/permission/policies`, { headers: apiHeaders() });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setPolicies(Array.isArray(data) ? data : data.policies || data.items || []);
    } catch (e) { setError(`${t('common.error', 'Error')}: ${(e as Error).message}`); }
    setLoading(false);
  }, [apiHeaders, t]);

  useEffect(() => { loadPolicies(); }, [loadPolicies]);

  const handleCreatePolicy = async () => {
    if (!newRole.trim() || !newResource.trim()) return;
    setCreating(true);
    try {
      const base = getApiBaseUrl();
      const res = await fetch(`${base}/api/permission/policy`, {
        method: 'POST',
        headers: apiHeaders(),
        body: JSON.stringify({ role: newRole, resource: newResource, action: newAction, effect: newEffect }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setShowCreate(false);
      setNewRole('');
      setNewResource('');
      setNewAction('read');
      setNewEffect('allow');
      loadPolicies();
    } catch (e) { setError(`${t('common.error', 'Error')}: ${(e as Error).message}`); }
    setCreating(false);
  };

  const handleDeletePolicy = async (policy: Policy) => {
    try {
      const base = getApiBaseUrl();
      const res = await fetch(`${base}/api/permission/policy`, {
        method: 'DELETE',
        headers: apiHeaders(),
        body: JSON.stringify({ role: policy.role, resource: policy.resource, action: policy.action }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setDeleteTarget(null);
      loadPolicies();
    } catch (e) { setError(`${t('common.error', 'Error')}: ${(e as Error).message}`); }
  };

  const handleLookupRoles = async () => {
    if (!lookupUser.trim()) return;
    setLooking(true);
    try {
      const base = getApiBaseUrl();
      const res = await fetch(`${base}/api/permission/roles/${lookupUser}`, { headers: apiHeaders() });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setUserRoles(data);
    } catch (e) { setError(`${t('common.error', 'Error')}: ${(e as Error).message}`); }
    setLooking(false);
  };

  const handleAssignRole = async () => {
    if (!assignUser.trim() || !assignRole.trim()) return;
    setAssigning(true);
    try {
      const base = getApiBaseUrl();
      const res = await fetch(`${base}/api/permission/role`, {
        method: 'POST',
        headers: apiHeaders(),
        body: JSON.stringify({ username: assignUser, role: assignRole }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setAssignUser('');
      setAssignRole('');
      if (lookupUser) handleLookupRoles();
    } catch (e) { setError(`${t('common.error', 'Error')}: ${(e as Error).message}`); }
    setAssigning(false);
  };

  const handleDeleteRole = async (username: string, role: string) => {
    try {
      const base = getApiBaseUrl();
      const res = await fetch(`${base}/api/permission/role`, {
        method: 'DELETE',
        headers: apiHeaders(),
        body: JSON.stringify({ username, role }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      if (lookupUser) handleLookupRoles();
    } catch (e) { setError(`${t('common.error', 'Error')}: ${(e as Error).message}`); }
  };

  const filtered = policies.filter((p) =>
    !search || p.role.toLowerCase().includes(search.toLowerCase()) ||
    p.resource.toLowerCase().includes(search.toLowerCase()) ||
    p.action.toLowerCase().includes(search.toLowerCase())
  );

  const effectColor = (e: string) => e === 'allow' ? 'bg-green-500/10 text-green-600 border-green-500/20' : 'bg-red-500/10 text-red-600 border-red-500/20';
  const ACTIONS = ['read', 'write', 'delete', 'admin', '*'];
  const EFFECTS = ['allow', 'deny'];

  return (
    <div className="px-3 lg:px-6 py-4 lg:py-6 space-y-4 lg:space-y-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl lg:text-2xl font-bold flex items-center gap-2">
            <Shield className="w-6 h-6" /> {t('nav.permission', '权限管理')}
          </h1>
          <p className="text-xs lg:text-sm text-muted-foreground mt-1">{t('permission.description', '管理角色和访问控制策略')}</p>
        </div>
      </div>

      {error && <div className="p-3 bg-red-500/10 border border-red-500/20 rounded-md text-red-600 text-xs lg:text-sm">{error}</div>}

      {/* Tabs */}
      <div className="flex gap-1 border-b">
        <button onClick={() => setActiveTab('policies')} className={`px-2 lg:px-4 py-2 text-xs lg:text-sm font-medium rounded-lg transition-colors ${activeTab === 'policies' ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:bg-muted hover:text-foreground'}`}>
          <FileText className="w-4 h-4 inline mr-1.5" />{t('permission.policies', '访问策略')}
        </button>
        <button onClick={() => setActiveTab('roles')} className={`px-2 lg:px-4 py-2 text-xs lg:text-sm font-medium rounded-lg transition-colors ${activeTab === 'roles' ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:bg-muted hover:text-foreground'}`}>
          <Users className="w-4 h-4 inline mr-1.5" />{t('permission.roles', '角色管理')}
        </button>
      </div>

      {activeTab === 'policies' && (
        <div className="space-y-4">
          <div className="flex items-center gap-2 lg:gap-4">
            <div className="relative flex-1 max-w-sm">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
              <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t('permission.searchPolicies', '搜索策略...')} className="w-full pl-9 pr-3 py-2 border rounded-md bg-background" />
            </div>
            <button onClick={() => setShowCreate(!showCreate)} className="px-2 lg:px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 flex items-center gap-2">
              <Plus className="w-4 h-4" /> {t('permission.newPolicy', '新建策略')}
            </button>
            <button onClick={loadPolicies} className="text-xs lg:text-sm text-muted-foreground hover:text-foreground">{t('common.refresh', '刷新')}</button>
          </div>

          {showCreate && (
            <div className="p-4 border rounded-lg bg-card space-y-4">
              <h3 className="font-medium">{t('permission.createPolicy', '创建访问策略')}</h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2 lg:gap-4">
                <div>
                  <label className="text-xs lg:text-sm font-medium mb-1 block">{t('permission.role', '角色')}</label>
                  <input value={newRole} onChange={(e) => setNewRole(e.target.value)} className="w-full px-3 py-2 border rounded-md bg-background" placeholder="admin" />
                </div>
                <div>
                  <label className="text-xs lg:text-sm font-medium mb-1 block">{t('permission.resource', '资源')}</label>
                  <input value={newResource} onChange={(e) => setNewResource(e.target.value)} className="w-full px-3 py-2 border rounded-md bg-background" placeholder="/api/knowledge/*" />
                </div>
                <div>
                  <label className="text-xs lg:text-sm font-medium mb-1 block">{t('permission.action', '操作')}</label>
                  <select value={newAction} onChange={(e) => setNewAction(e.target.value)} className="w-full px-3 py-2 border rounded-md bg-background">
                    {ACTIONS.map((a) => <option key={a} value={a}>{a}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-xs lg:text-sm font-medium mb-1 block">{t('permission.effect', '效果')}</label>
                  <select value={newEffect} onChange={(e) => setNewEffect(e.target.value)} className="w-full px-3 py-2 border rounded-md bg-background">
                    {EFFECTS.map((e) => <option key={e} value={e}>{e}</option>)}
                  </select>
                </div>
              </div>
              <div className="flex gap-2">
                <button onClick={handleCreatePolicy} disabled={creating || !newRole.trim() || !newResource.trim()} className="px-2 lg:px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50 flex items-center gap-2">
                  {creating ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />} {t('common.create', '创建')}
                </button>
                <button onClick={() => setShowCreate(false)} className="px-2 lg:px-4 py-2 border rounded-md hover:bg-accent">{t('common.cancel', '取消')}</button>
              </div>
            </div>
          )}

          {loading ? (
            <div className="flex items-center justify-center py-12"><Loader2 className="w-6 h-6 animate-spin text-muted-foreground" /></div>
          ) : filtered.length === 0 ? (
            <div className="text-center py-12 text-muted-foreground">
              <FileText className="w-12 h-12 mx-auto mb-3 opacity-30" />
              <p>{t('permission.emptyPolicies', '暂无访问策略')}</p>
            </div>
          ) : (
            <div className="border rounded-lg overflow-x-auto">
              <table className="w-full min-w-[450px]">
                <thead>
                  <tr className="bg-muted/50">
                    <th className="px-2 lg:px-4 py-2 lg:py-3 text-left text-xs lg:text-sm font-medium">{t('permission.role', '角色')}</th>
                    <th className="px-2 lg:px-4 py-2 lg:py-3 text-left text-xs lg:text-sm font-medium">{t('permission.resource', '资源')}</th>
                    <th className="px-2 lg:px-4 py-2 lg:py-3 text-left text-xs lg:text-sm font-medium">{t('permission.action', '操作')}</th>
                    <th className="px-2 lg:px-4 py-2 lg:py-3 text-left text-xs lg:text-sm font-medium">{t('permission.effect', '效果')}</th>
                    <th className="px-2 lg:px-4 py-2 lg:py-3 w-12"></th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((p, i) => (
                    <tr key={i} className="border-t hover:bg-muted/30">
                      <td className="px-2 lg:px-4 py-2 lg:py-3 text-xs lg:text-sm font-medium">{p.role}</td>
                      <td className="px-2 lg:px-4 py-2 lg:py-3 text-xs lg:text-sm font-mono">{p.resource}</td>
                      <td className="px-2 lg:px-4 py-2 lg:py-3 text-xs lg:text-sm">{p.action}</td>
                      <td className="px-2 lg:px-4 py-2 lg:py-3">
                        <span className={`px-2 py-0.5 rounded-full text-xs border ${effectColor(p.effect)}`}>{p.effect}</span>
                      </td>
                      <td className="px-2 lg:px-4 py-2 lg:py-3">
                        {deleteTarget === p ? (
                          <div className="flex gap-1">
                            <button onClick={() => handleDeletePolicy(p)} className="px-1.5 py-0.5 bg-red-500 text-white rounded text-xs">{t('common.confirm', '确认')}</button>
                            <button onClick={() => setDeleteTarget(null)} className="px-1.5 py-0.5 border rounded text-xs"><X className="w-3 h-3" /></button>
                          </div>
                        ) : (
                          <button onClick={() => setDeleteTarget(p)} className="p-1 text-muted-foreground hover:text-red-500"><Trash2 className="w-3.5 h-3.5" /></button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {activeTab === 'roles' && (
        <div className="space-y-4">
          {/* Lookup user roles */}
          <div className="p-4 border rounded-lg bg-card space-y-4">
            <h3 className="font-medium">{t('permission.lookupRoles', '查询用户角色')}</h3>
            <div className="flex gap-2">
              <input value={lookupUser} onChange={(e) => setLookupUser(e.target.value)} className="flex-1 px-3 py-2 border rounded-md bg-background" placeholder={t('permission.usernamePlaceholder', '输入用户名')} onKeyDown={(e) => e.key === 'Enter' && handleLookupRoles()} />
              <button onClick={handleLookupRoles} disabled={looking || !lookupUser.trim()} className="px-2 lg:px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50 flex items-center gap-2">
                {looking ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />} {t('common.search', '查询')}
              </button>
            </div>
            {userRoles && (
              <div className="p-3 bg-muted rounded-lg">
                <div className="flex items-center gap-2 mb-2">
                  <Users className="w-4 h-4 text-muted-foreground" />
                  <span className="font-medium">{userRoles.username}</span>
                </div>
                {userRoles.roles.length === 0 ? (
                  <p className="text-xs lg:text-sm text-muted-foreground">{t('permission.noRoles', '该用户暂无角色')}</p>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    {userRoles.roles.map((role, i) => (
                      <span key={i} className="px-2 py-1 bg-primary/10 text-primary rounded-md text-xs lg:text-sm flex items-center gap-1">
                        {role}
                        <button onClick={() => handleDeleteRole(userRoles.username, role)} className="ml-1 hover:text-red-500"><X className="w-3 h-3" /></button>
                      </span>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Assign role */}
          <div className="p-4 border rounded-lg bg-card space-y-4">
            <h3 className="font-medium">{t('permission.assignRole', '分配角色')}</h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2 lg:gap-4">
              <input value={assignUser} onChange={(e) => setAssignUser(e.target.value)} className="px-3 py-2 border rounded-md bg-background" placeholder={t('permission.username', '用户名')} />
              <input value={assignRole} onChange={(e) => setAssignRole(e.target.value)} className="px-3 py-2 border rounded-md bg-background" placeholder={t('permission.roleName', '角色名')} />
              <button onClick={handleAssignRole} disabled={assigning || !assignUser.trim() || !assignRole.trim()} className="px-2 lg:px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50 flex items-center gap-2">
                {assigning ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />} {t('permission.assign', '分配')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
