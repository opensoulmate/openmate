const DEFAULT_API_URL = typeof window !== 'undefined'
  ? `http://${window.location.hostname}:8090`
  : 'http://127.0.0.1:8090';

export function getApiBaseUrl(): string {
  if (typeof window === 'undefined') return DEFAULT_API_URL;
  return localStorage.getItem('openmate-api-url') || DEFAULT_API_URL;
}

export function setApiBaseUrl(url: string) {
  localStorage.setItem('openmate-api-url', url);
}

// 账号角色：解 JWT payload（登录时已带真角色），无 token/解析失败默认 user
export function getUserRole(): string {
  try {
    const token = typeof window !== 'undefined' ? localStorage.getItem('openmate-token') : null;
    if (!token) return 'user';
    const payload = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
    return payload.role || 'user';
  } catch {
    return 'user';
  }
}

async function request(path: string, options: RequestInit = {}) {
  const base = getApiBaseUrl();
  const headers: Record<string, string> = { 'Content-Type': 'application/json', ...(options.headers as Record<string, string> || {}) };
  const token = getToken();
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(`${base}${path}`, { ...options, headers });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export const api = {
  // Auth
  login: (username: string, password: string) => {
    const body = new URLSearchParams({ username, password });
    return request('/api/user/login', { method: 'POST', body: body.toString(), headers: { 'Content-Type': 'application/x-www-form-urlencoded' } as Record<string, string> });
  },
  register: (username: string, password: string, email: string) =>
    request('/api/user/register', { method: 'POST', body: JSON.stringify({ username, password, email }) }),

  // Chat
  chat: (message: string, userId: string) =>
    request('/api/chat/', { method: 'POST', body: JSON.stringify({ message, user_id: userId }) }),

  // Knowledge
  getKnowledge: (userId: string) => request(`/api/knowledge/?user_id=${userId}`),
  createKnowledge: (userId: string, data: { title: string; content: string; tags?: string[] }) =>
    request(`/api/knowledge/?user_id=${userId}`, { method: 'POST', body: JSON.stringify(data) }),
  deleteKnowledge: (id: string) => request(`/api/knowledge/${id}`, { method: 'DELETE' }),
  uploadKnowledge: async (userId: string, file: File, title?: string, tags?: string) => {
    const base = getApiBaseUrl();
    const form = new FormData();
    form.append('file', file);
    if (title) form.append('title', title);
    if (tags) form.append('tags', tags);
    const headers: Record<string, string> = {};
    const token = getToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;
    const res = await fetch(`${base}/api/knowledge/upload?user_id=${userId}`, {
      method: 'POST', body: form, headers,
    });
    if (!res.ok) throw new Error(`Upload error: ${res.status}`);
    return res.json();
  },
  uploadKnowledgeBulk: async (userId: string, files: File[], tags?: string) => {
    const base = getApiBaseUrl();
    const form = new FormData();
    for (const f of files) form.append('files', f);
    if (tags) form.append('tags', tags);
    const headers: Record<string, string> = {};
    const token = getToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;
    const res = await fetch(`${base}/api/knowledge/upload/bulk?user_id=${userId}`, {
      method: 'POST', body: form, headers,
    });
    if (!res.ok) throw new Error(`Upload error: ${res.status}`);
    return res.json();
  },

  // Knowledge Requests
  createKbRequest: (data: { kb_name: string; kb_description: string }) =>
    request('/api/knowledge-requests/', { method: 'POST', body: JSON.stringify(data) }),
  getMyKbRequests: () => request('/api/knowledge-requests/my'),
  listKbRequests: (status?: string) => request(`/api/knowledge-requests/${status ? `?status=${status}` : ''}`),
  reviewKbRequest: (id: string, data: { status: string; review_note: string }) =>
    request(`/api/knowledge-requests/${id}/review`, { method: 'POST', body: JSON.stringify(data) }),

  // KB Sharing
  createSharingRequest: (data: { kb_id: string; kb_name: string }) =>
    request('/api/kb-sharing/', { method: 'POST', body: JSON.stringify(data) }),
  getMySharingRequests: () => request('/api/kb-sharing/my'),
  listSharingRequests: (status?: string) => request(`/api/kb-sharing/${status ? `?status=${status}` : ''}`),
  reviewSharingRequest: (id: string, data: { status: string; review_note: string }) =>
    request(`/api/kb-sharing/${id}/review`, { method: 'POST', body: JSON.stringify(data) }),

  // Search
  search: (query: string, userId: string) => request(`/api/search/?q=${encodeURIComponent(query)}&user_id=${userId}`),

  // Graph
  getGraph: (userId: string) => request(`/api/graph/full?user_id=${userId}`),
  getEntities: (userId: string) => request(`/api/graph/entities?user_id=${userId}`),
  getRelations: () => request('/api/graph/relations'),

  // Entity CRUD
  createEntity: (userId: string, data: { name: string; type: string; description?: string; properties?: Record<string, unknown> }) =>
    request(`/api/entity/?user_id=${userId}`, { method: 'POST', body: JSON.stringify(data) }),
  deleteEntity: (entityId: string, userId: string) =>
    request(`/api/entity/${entityId}?user_id=${userId}`, { method: 'DELETE' }),

  // Relation CRUD
  createRelation: (data: { source_id: string; target_id: string; type: string }) =>
    request('/api/graph/relations', { method: 'POST', body: JSON.stringify(data) }),

  // Health
  health: () => request('/api/health'),
};

// User session management
export function getUserId(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem('openmate-user-id');
}

export function setUserId(id: string) {
  localStorage.setItem('openmate-user-id', id);
}

export function getUserName(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem('openmate-username');
}

export function setUserName(name: string) {
  localStorage.setItem('openmate-username', name);
}

export function clearUser() {
  localStorage.removeItem('openmate-user-id');
  localStorage.removeItem('openmate-username');
  localStorage.removeItem('openmate-token');
}

export function setToken(token: string) {
  localStorage.setItem('openmate-token', token);
}

export function getToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem('openmate-token');
}

export function isLoggedIn(): boolean {
  return !!getUserId();
}
