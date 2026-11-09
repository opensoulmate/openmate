'use client';

// 分类视图体系（会话/群组共用唯一真源）——用户拍板：两页分类必须同一份代码
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { getApiBaseUrl } from '@/lib/api-client';

const getApiUrl = () => getApiBaseUrl();
import { SkirtTabs } from '@opensoulmate/openface';
import { useAppStore } from '@/stores/app-store';
import {
  Bot, FileText, Image as ImageIcon, Globe, Download, Trash2,
  ChevronDown, ChevronRight, ChevronLeft, Clock, User,
  ListChecks, Activity, RotateCcw, Loader2, RefreshCw, X, AlertTriangle,
  Award, LocateFixed, Copy, MessageSquare, Video, Music, Link2, CalendarDays, Paperclip,
} from 'lucide-react';

export interface ViewMsg { id: string; role: 'user' | 'agent'; timestamp: Date | null; text?: string; parts?: any[]; }
export interface ViewFile { name: string; filePath?: string; mimeType?: string; data?: string; messageId: string; role: 'user' | 'agent'; timestamp: Date | null; }
export interface ViewLink { url: string; title?: string; messageId: string; role: 'user' | 'agent'; timestamp: Date | null; }
export interface ViewDateGroup { date: string; label: string; count: number; messages: ViewMsg[]; }
export interface ViewData {
  fileAttachments: ViewFile[];
  imageAttachments: ViewFile[];
  videoAttachments: ViewFile[];
  audioAttachments: ViewFile[];
  docAttachments: ViewFile[];
  otherAttachments: ViewFile[];
  linkItems: ViewLink[];
  dateGroups: ViewDateGroup[];
}

const getFileDirectUrl = (filePath: string) =>
  `http://${window.location.hostname}:8092/api/file?path=${encodeURIComponent(filePath)}`;

async function fetchFileBlob(filePath: string, mimeType?: string): Promise<Blob | undefined> {
  try {
    const resp = await fetch(getFileDirectUrl(filePath));
    const ct = resp.headers.get('content-type') || '';
    if (resp.ok && !ct.includes('application/json')) return await resp.blob();
  } catch (err) { console.warn('[file-fetch] direct failed:', err); }
  // 兜底：原 JSON 接口（纯文本可用；二进制经文本转码会损坏，仅作最后手段）
  try {
    const apiBase = getApiUrl();
    const resp = await fetch(`${apiBase}/api/file?path=${encodeURIComponent(filePath)}`);
    if (resp.ok) {
      const ct = resp.headers.get('content-type') || '';
      if (ct.includes('application/json')) {
        const data = await resp.json();
        if (typeof data.content !== 'string') return undefined;
        return new Blob([data.content], { type: mimeType || 'text/plain' });
      }
      return await resp.blob();
    }
  } catch (err) { console.warn('[file-fetch] fallback failed:', err); }
  return undefined;
}

async function fetchFileDataUrl(filePath: string, mimeType?: string): Promise<string | undefined> {
  const blob = await fetchFileBlob(filePath, mimeType);
  if (!blob) return undefined;
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let bin = '';
  for (let i = 0; i < bytes.length; i += 0x8000) {
    bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return `data:${mimeType || blob.type || 'application/octet-stream'};base64,${btoa(bin)}`;
}

// 跳转上下文：切到会话视图+滚动定位到目标消息（各视图功能按钮+日期日历共用）
  export function jumpToMessage(messageId: string, onViewChange?: (id: string) => void) {
    onViewChange?.('messages');
    setTimeout(() => {
      const el = document.getElementById(`msg-${messageId}`);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        el.classList.add('ring-2', 'ring-primary');
        setTimeout(() => el.classList.remove('ring-2', 'ring-primary'), 2000);
      }
    }, 150);
}



// 通用附件操作按钮（跳转/预览/下载/复制）— 文档/其他/图片/视频/音频视图共用
  export function renderAttachmentActions(f: { name: string; messageId?: string; data?: string; filePath?: string; mimeType?: string }, onViewChange?: (id: string) => void) {
  return (
    <>
      {f.messageId && <button onClick={(e) => { e.stopPropagation(); jumpToMessage(f.messageId!, onViewChange); }} className="flex items-center gap-1 px-2 py-1 rounded text-xs text-muted-foreground hover:bg-muted/50 transition-colors" title="跳转到会话中此消息">
        <LocateFixed className="w-3 h-3" />
        跳转
      </button>}
      <button onClick={async (e) => {
        e.stopPropagation();
        const store = useAppStore.getState();
        let dataUrl: string | undefined;
        if (f.data) {
          dataUrl = `data:${f.mimeType || 'application/octet-stream'};base64,${f.data}`;
        } else if (f.filePath) {
          dataUrl = await fetchFileDataUrl(f.filePath, f.mimeType);
        }
        if (dataUrl) {
          store.setPendingFilePreview({ url: dataUrl, name: f.name, mimeType: f.mimeType });
          store.setRightPanelOpen(true);
        }
      }} className="flex items-center gap-1 px-2 py-1 rounded text-xs text-primary hover:bg-primary/10 transition-colors">
        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" /></svg>
        预览
      </button>
      {(f.data || f.filePath) && <button onClick={async (e) => {
        e.stopPropagation();
        if (f.data) {
          const byteChars = atob(f.data);
          const bytes = new Uint8Array(byteChars.length);
          for (let j = 0; j < byteChars.length; j++) bytes[j] = byteChars.charCodeAt(j);
          const blob = new Blob([bytes], { type: f.mimeType || 'application/octet-stream' });
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a'); a.href = url; a.download = f.name; a.click(); URL.revokeObjectURL(url);
        } else if (f.filePath) {
          const blob = await fetchFileBlob(f.filePath, f.mimeType);
          if (blob) {
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a'); a.href = url; a.download = f.name; a.click(); URL.revokeObjectURL(url);
          } else {
            console.warn('[file-download] Failed:', f.filePath);
          }
        }
      }} className="flex items-center gap-1 px-2 py-1 rounded text-xs text-muted-foreground hover:bg-muted/50 transition-colors">
        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
        下载
      </button>}
      {f.data && <button onClick={(e) => { e.stopPropagation(); navigator.clipboard.writeText(atob(f.data!)); }} className="flex items-center gap-1 px-2 py-1 rounded text-xs text-muted-foreground hover:bg-muted/50 transition-colors">
        <Copy className="w-3 h-3" />
        复制
      </button>}
    </>
  );
}


export function extractViewData(messages: ViewMsg[]): ViewData {
  // 从消息收集全部文件附件（文件视图）
  const fileAttachments = (() => {
    const files: { name: string; filePath?: string; mimeType?: string; data?: string; messageId: string; role: 'user' | 'agent'; timestamp: Date | null }[] = [];
    for (const msg of messages) {
      // Extract file/image parts
      for (const p of (msg.parts || [])) {
        if (p.type === 'file' && p.name) {
          files.push({ name: p.name, mimeType: p.mime_type, data: p.data, messageId: msg.id, role: msg.role, timestamp: msg.timestamp });
        }
        if (p.type === 'image' && p.data) {
          files.push({ name: p.name || 'image.png', mimeType: p.mime_type || 'image/png', data: p.data, messageId: msg.id, role: msg.role, timestamp: msg.timestamp });
        }
        if (p.type === 'audio' && p.data) {
          files.push({ name: p.name || 'voice', mimeType: p.mime_type || 'audio/wav', data: p.data, messageId: msg.id, role: msg.role, timestamp: msg.timestamp });
        }
      }
      // Also scan text parts for MEDIA: tags
      for (const p of (msg.parts || [])) {
        if (p.type === 'text' && p.text) {
          const mediaMatches = p.text.matchAll(/MEDIA:(\/[^\s\n]+)/g);
          for (const m of mediaMatches) {
            const filePath = m[1];
            const fileName = filePath.split('/').pop() || filePath;
            const ext = fileName.split('.').pop()?.toLowerCase() || '';
            const mimeType = ext === 'html' || ext === 'htm' ? 'text/html' : ext === 'md' ? 'text/markdown' : ext === 'json' ? 'application/json' : 'text/plain';
            if (!files.some(f => f.filePath === filePath)) {
              files.push({ name: fileName, filePath, mimeType, data: undefined, messageId: msg.id, role: msg.role, timestamp: msg.timestamp });
            }
          }
        }
      }
    }
    return files;
  })();

  // 分类视图数据：图片、视频、音频
  const imageAttachments = (() => fileAttachments.filter(f =>
    f.mimeType?.startsWith('image/') || /\.(png|jpe?g|gif|webp|svg|bmp|ico)$/i.test(f.name)
  ))();

  const videoAttachments = (() => fileAttachments.filter(f =>
    f.mimeType?.startsWith('video/') || /\.(mp4|avi|mov|wmv|flv|mkv|webm)$/i.test(f.name)
  ))();

  const audioAttachments = (() => fileAttachments.filter(f =>
    f.mimeType?.startsWith('audio/') || /\.(mp3|wav|ogg|flac|aac|m4a|wma)$/i.test(f.name)
  ))();

  // 文档类（用户规格）：pdf,docx,xlsx,pptx,md,txt — 代码类/其他全部归"其他"兜底
  const docAttachments = (() => fileAttachments.filter(f => {
    if (imageAttachments.includes(f) || videoAttachments.includes(f) || audioAttachments.includes(f)) return false;
    return /pdf|msword|excel|powerpoint|officedocument|opendocument|text\/plain|markdown/.test(f.mimeType || '')
      || /\.(pdf|docx?|xlsx?|pptx?|md|txt)$/i.test(f.name);
  }))();

  // 其他类：排除图片/视频/音频/文档后的剩余（压缩包、二进制、未知类型）
  const otherAttachments = (() => fileAttachments.filter(f =>
    !imageAttachments.includes(f) && !videoAttachments.includes(f) && !audioAttachments.includes(f) && !docAttachments.includes(f)
  ))();

  // 链接提取：从消息文本中提取URL
  const linkItems = (() => {
    const links: { url: string; title?: string; messageId: string; role: 'user' | 'agent'; timestamp: Date | null }[] = [];
    const urlRegex = /https?:\/\/[^\s<>"')\]]+/g;
    for (const msg of messages) {
      for (const p of (msg.parts || [])) {
        if (p.type === 'text' && p.text) {
          const matches = p.text.matchAll(urlRegex);
          for (const m of matches) {
            const url = m[0].replace(/[.,;:!?]+$/, '');
            if (!links.some(l => l.url === url)) {
              // 尝试从URL提取域名作为标题
              let title = '';
              try { title = new URL(url).hostname; } catch { title = url.slice(0, 50); }
              links.push({ url, title, messageId: msg.id, role: msg.role, timestamp: msg.timestamp });
            }
          }
        }
      }
    }
    return links;
  })();

  // 日期分组：按日期分组消息
  const dateGroups = (() => {
    const groups: { date: string; label: string; count: number; messages: ViewMsg[] }[] = [];
    const map = new Map<string, ViewMsg[]>();
    for (const msg of messages) {
      const d = msg.timestamp && !isNaN(msg.timestamp.getTime()) ? msg.timestamp.toISOString().slice(0, 10) : '未知日期';
      if (!map.has(d)) map.set(d, []);
      map.get(d)!.push(msg);
    }
    for (const [date, msgs] of map) {
      const d = new Date(date);
      const now = new Date();
      const diffDays = Math.floor((now.getTime() - d.getTime()) / 86400000);
      let label = date;
      if (diffDays === 0) label = '今天';
      else if (diffDays === 1) label = '昨天';
      else if (diffDays < 7) label = `${diffDays}天前`;
      else label = d.toLocaleDateString('zh-CN', { month: 'long', day: 'numeric', weekday: 'short' });
      groups.push({ date, label, count: msgs.length, messages: msgs });
    }
    return groups.sort((a, b) => b.date.localeCompare(a.date));
  })();


  return { fileAttachments, imageAttachments, videoAttachments, audioAttachments, docAttachments, otherAttachments, linkItems, dateGroups };
}

export function MessageViewTabs({ view, onChange, data }: { view: string; onChange: (id: string) => void; data: ViewData; }) {
  const { t } = useTranslation();
  const { imageAttachments, videoAttachments, audioAttachments, docAttachments, otherAttachments, linkItems } = data;
  return (
          <SkirtTabs
            tabs={[
              { id: 'messages', title: t('chat.viewSessions', '会话') },
              { id: 'images', title: t('chat.viewImages', '图片') },
              { id: 'videos', title: t('chat.viewVideos', '视频') },
              { id: 'audio', title: t('chat.viewAudio', '音频') },
              { id: 'docs', title: t('chat.viewDocs', '文档') },
              { id: 'others', title: t('chat.viewOthers', '其他') },
              { id: 'links', title: t('chat.viewLinks', '链接') },
              { id: 'dates', title: t('chat.viewDates', '日期') },
            ]}
            activeTabId={view}
            onTabChange={(id: string) => onChange(id)}
            renderTabContent={(tab: any, isActive: boolean) => (
              <>
                {tab.id === 'messages' && <MessageSquare className="w-3.5 h-3.5 shrink-0" />}
                {tab.id === 'images' && <ImageIcon className="w-3.5 h-3.5 shrink-0" />}
                {tab.id === 'videos' && <Video className="w-3.5 h-3.5 shrink-0" />}
                {tab.id === 'audio' && <Music className="w-3.5 h-3.5 shrink-0" />}
                {tab.id === 'docs' && <FileText className="w-3.5 h-3.5 shrink-0" />}
                {tab.id === 'others' && <Paperclip className="w-3.5 h-3.5 shrink-0" />}
                {tab.id === 'links' && <Link2 className="w-3.5 h-3.5 shrink-0" />}
                {tab.id === 'dates' && <CalendarDays className="w-3.5 h-3.5 shrink-0" />}
                <span className="hidden lg:inline truncate text-xs">{tab.title}</span>
                {tab.id === 'docs' && docAttachments.length > 0 && (
                  <span className={`shrink-0 text-[10px] px-1.5 py-0 rounded-full font-medium min-w-[18px] text-center ${isActive ? 'bg-primary/15 text-primary' : 'bg-muted text-muted-foreground'}`}>{docAttachments.length}</span>
                )}
                {tab.id === 'others' && otherAttachments.length > 0 && (
                  <span className={`shrink-0 text-[10px] px-1.5 py-0 rounded-full font-medium min-w-[18px] text-center ${isActive ? 'bg-primary/15 text-primary' : 'bg-muted text-muted-foreground'}`}>{otherAttachments.length}</span>
                )}
                {tab.id === 'images' && imageAttachments.length > 0 && (
                  <span className={`shrink-0 text-[10px] px-1.5 py-0 rounded-full font-medium min-w-[18px] text-center ${isActive ? 'bg-primary/15 text-primary' : 'bg-muted text-muted-foreground'}`}>{imageAttachments.length}</span>
                )}
                {tab.id === 'videos' && videoAttachments.length > 0 && (
                  <span className={`shrink-0 text-[10px] px-1.5 py-0 rounded-full font-medium min-w-[18px] text-center ${isActive ? 'bg-primary/15 text-primary' : 'bg-muted text-muted-foreground'}`}>{videoAttachments.length}</span>
                )}
                {tab.id === 'audio' && audioAttachments.length > 0 && (
                  <span className={`shrink-0 text-[10px] px-1.5 py-0 rounded-full font-medium min-w-[18px] text-center ${isActive ? 'bg-primary/15 text-primary' : 'bg-muted text-muted-foreground'}`}>{audioAttachments.length}</span>
                )}
                {tab.id === 'links' && linkItems.length > 0 && (
                  <span className={`shrink-0 text-[10px] px-1.5 py-0 rounded-full font-medium min-w-[18px] text-center ${isActive ? 'bg-primary/15 text-primary' : 'bg-muted text-muted-foreground'}`}>{linkItems.length}</span>
                )}
              </>
            )}
          />

  );
}

export function MessageViews({ view, data, onViewChange }: { view: string; data: ViewData; onViewChange: (id: string) => void; }) {
  const { t } = useTranslation();
  const { imageAttachments, videoAttachments, audioAttachments, docAttachments, otherAttachments, linkItems, dateGroups } = data;
  const [calMonth, setCalMonth] = useState<Date>(() => new Date()); // 日期日历当前查看月份
  return (
    <>
                    {/* Docs view — 文档（pdf/docx/xlsx/pptx/md/txt，三段式卡片） */}
          {view === 'docs' && (
            <div className="space-y-2">
              {docAttachments.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-40 text-muted-foreground">
                  <FileText className="w-8 h-8 mb-2 opacity-40" />
                  <p className="text-sm">暂无文档</p>
                </div>
              ) : (
                docAttachments.map((f, i) => (

                  <div key={i} className={`flex gap-2 lg:gap-3 ${f.role === 'user' ? 'justify-end' : ''}`}>
                    {f.role === 'agent' && (
                      <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
                        <Bot className="w-4 h-4 text-primary" />
                      </div>
                    )}
                    <div className="max-w-[85%] lg:max-w-[70%] rounded-xl border border-border/60 bg-background/80 backdrop-blur-sm overflow-hidden">
                      <div className="px-3 py-1.5 border-b border-border/30 bg-muted/20">
                        <span className="text-[10px] text-muted-foreground/60">
                          {f.timestamp && !isNaN(f.timestamp.getTime()) ? f.timestamp.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '时间未记录'}
                        </span>
                      </div>
                      <div className="px-3 py-2.5 flex items-center gap-2">
                        <FileText className="w-5 h-5 text-muted-foreground shrink-0" />
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium truncate">{f.name}</p>
                          {f.mimeType && <p className="text-[10px] text-muted-foreground/60">{f.mimeType}</p>}
                        </div>
                      </div>
                      <div className="flex items-center gap-1 px-3 py-1.5 border-t border-border/30 bg-muted/20">
                        {renderAttachmentActions(f, onViewChange)}
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          )}

          {/* Others view — 其他文件（zip/7z/代码/二进制/未知兜底，三段式卡片） */}
          {view === 'others' && (
            <div className="space-y-2">
              {otherAttachments.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-40 text-muted-foreground">
                  <Paperclip className="w-8 h-8 mb-2 opacity-40" />
                  <p className="text-sm">暂无其他文件</p>
                </div>
              ) : (
                otherAttachments.map((f, i) => (

                  <div key={i} className={`flex gap-2 lg:gap-3 ${f.role === 'user' ? 'justify-end' : ''}`}>
                    {f.role === 'agent' && (
                      <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
                        <Bot className="w-4 h-4 text-primary" />
                      </div>
                    )}
                    <div className="max-w-[85%] lg:max-w-[70%] rounded-xl border border-border/60 bg-background/80 backdrop-blur-sm overflow-hidden">
                      <div className="px-3 py-1.5 border-b border-border/30 bg-muted/20">
                        <span className="text-[10px] text-muted-foreground/60">
                          {f.timestamp && !isNaN(f.timestamp.getTime()) ? f.timestamp.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '时间未记录'}
                        </span>
                      </div>
                      <div className="px-3 py-2.5 flex items-center gap-2">
                        <Paperclip className="w-5 h-5 text-muted-foreground shrink-0" />
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium truncate">{f.name}</p>
                          {f.mimeType && <p className="text-[10px] text-muted-foreground/60">{f.mimeType}</p>}
                        </div>
                      </div>
                      <div className="flex items-center gap-1 px-3 py-1.5 border-t border-border/30 bg-muted/20">
                        {renderAttachmentActions(f, onViewChange)}
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          )}

          {/* Images view — 图片（三段式卡片：时间在上+内容+功能按钮在下） */}
          {view === 'images' && (
            <div className="space-y-2">
              {imageAttachments.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-40 text-muted-foreground">
                  <ImageIcon className="w-8 h-8 mb-2 opacity-40" />
                  <p className="text-sm">暂无图片</p>
                </div>
              ) : (
                imageAttachments.map((f, i) => (
                  <div key={i} className={`flex gap-2 lg:gap-3 ${f.role === 'user' ? 'justify-end' : ''}`}>
                    {f.role === 'agent' && (
                      <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
                        <Bot className="w-4 h-4 text-primary" />
                      </div>
                    )}
                    <div className="max-w-[85%] lg:max-w-[70%] rounded-xl border border-border/60 bg-background/80 backdrop-blur-sm overflow-hidden">
                      {/* 时间在上 */}
                      <div className="px-3 py-1.5 border-b border-border/30 bg-muted/20">
                        <span className="text-[10px] text-muted-foreground/60">
                          {f.timestamp && !isNaN(f.timestamp.getTime()) ? f.timestamp.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '时间未记录'}
                        </span>
                      </div>
                      {/* 图片内容（点击预览） */}
                      <div className="relative cursor-pointer group" onClick={async () => {
                        const store = useAppStore.getState();
                        let dataUrl: string | undefined;
                        if (f.data) {
                          dataUrl = `data:${f.mimeType || 'image/png'};base64,${f.data}`;
                        } else if (f.filePath) {
                          dataUrl = await fetchFileDataUrl(f.filePath, f.mimeType || 'image/png');
                        }
                        if (dataUrl) {
                          store.setPendingFilePreview({ url: dataUrl, name: f.name, mimeType: f.mimeType });
                          store.setRightPanelOpen(true);
                        }
                      }}>
                        {f.data ? (
                          <img src={`data:${f.mimeType || 'image/png'};base64,${f.data}`} alt={f.name} className="w-full max-h-80 object-contain bg-black/5" />
                        ) : (
                          <div className="w-full h-40 flex items-center justify-center">
                            <ImageIcon className="w-8 h-8 opacity-30" />
                          </div>
                        )}
                        <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/60 to-transparent p-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
                          <p className="text-[10px] text-white truncate">{f.name}</p>
                        </div>
                      </div>
                      {/* 功能按钮在下 */}
                      <div className="flex items-center gap-1 px-3 py-1.5 border-t border-border/30 bg-muted/20">
                        {renderAttachmentActions(f, onViewChange)}
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          )}

          {/* Videos view — 视频（三段式卡片：时间在上+播放器+功能按钮在下） */}
          {view === 'videos' && (
            <div className="space-y-2">
              {videoAttachments.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-40 text-muted-foreground">
                  <Video className="w-8 h-8 mb-2 opacity-40" />
                  <p className="text-sm">暂无视频</p>
                </div>
              ) : (
                videoAttachments.map((f, i) => (
                  <div key={i} className={`flex gap-2 lg:gap-3 ${f.role === 'user' ? 'justify-end' : ''}`}>
                    {f.role === 'agent' && (
                      <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
                        <Bot className="w-4 h-4 text-primary" />
                      </div>
                    )}
                    <div className="max-w-[85%] lg:max-w-[70%] rounded-xl border border-border/60 bg-background/80 backdrop-blur-sm overflow-hidden">
                      <div className="px-3 py-1.5 border-b border-border/30 bg-muted/20">
                        <span className="text-[10px] text-muted-foreground/60">
                          {f.timestamp && !isNaN(f.timestamp.getTime()) ? f.timestamp.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '时间未记录'}
                        </span>
                      </div>
                      <div className="px-3 py-2.5 flex items-center gap-2">
                        <Video className="w-5 h-5 text-muted-foreground shrink-0" />
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium truncate">{f.name}</p>
                          {f.mimeType && <p className="text-[10px] text-muted-foreground/60">{f.mimeType}</p>}
                        </div>
                      </div>
                      {f.data && (
                        <video controls className="w-full max-h-64 bg-black" preload="metadata">
                          <source src={`data:${f.mimeType || 'video/mp4'};base64,${f.data}`} />
                        </video>
                      )}
                      <div className="flex items-center gap-1 px-3 py-1.5 border-t border-border/30 bg-muted/20">
                        {renderAttachmentActions(f, onViewChange)}
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          )}

          {/* Audio view — 音频（三段式卡片：时间在上+播放器+功能按钮在下） */}
          {view === 'audio' && (
            <div className="space-y-2">
              {audioAttachments.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-40 text-muted-foreground">
                  <Music className="w-8 h-8 mb-2 opacity-40" />
                  <p className="text-sm">暂无音频</p>
                </div>
              ) : (
                audioAttachments.map((f, i) => (
                  <div key={i} className={`flex gap-2 lg:gap-3 ${f.role === 'user' ? 'justify-end' : ''}`}>
                    {f.role === 'agent' && (
                      <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
                        <Bot className="w-4 h-4 text-primary" />
                      </div>
                    )}
                    <div className="max-w-[85%] lg:max-w-[70%] rounded-xl border border-border/60 bg-background/80 backdrop-blur-sm overflow-hidden">
                      <div className="px-3 py-1.5 border-b border-border/30 bg-muted/20">
                        <span className="text-[10px] text-muted-foreground/60">
                          {f.timestamp && !isNaN(f.timestamp.getTime()) ? f.timestamp.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '时间未记录'}
                        </span>
                      </div>
                      <div className="px-3 py-2.5 flex items-center gap-2">
                        <Music className="w-5 h-5 text-muted-foreground shrink-0" />
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium truncate">{f.name}</p>
                          {f.mimeType && <p className="text-[10px] text-muted-foreground/60">{f.mimeType}</p>}
                        </div>
                      </div>
                      {f.data && (
                        <div className="px-3 pb-2">
                          <audio controls className="w-full" preload="metadata">
                            <source src={`data:${f.mimeType || 'audio/mpeg'};base64,${f.data}`} />
                          </audio>
                        </div>
                      )}
                      <div className="flex items-center gap-1 px-3 py-1.5 border-t border-border/30 bg-muted/20">
                        {renderAttachmentActions(f, onViewChange)}
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          )}

          {/* Links view — 链接（三段式卡片：时间在上+链接信息+功能按钮在下） */}
          {view === 'links' && (
            <div className="space-y-2">
              {linkItems.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-40 text-muted-foreground">
                  <Link2 className="w-8 h-8 mb-2 opacity-40" />
                  <p className="text-sm">暂无链接</p>
                </div>
              ) : (
                linkItems.map((link, i) => (
                  <div key={i} className={`flex gap-2 lg:gap-3 ${link.role === 'user' ? 'justify-end' : ''}`}>
                    {link.role === 'agent' && (
                      <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
                        <Bot className="w-4 h-4 text-primary" />
                      </div>
                    )}
                    <div className="max-w-[85%] lg:max-w-[70%] rounded-xl border border-border/60 bg-background/80 backdrop-blur-sm overflow-hidden">
                      <div className="px-3 py-1.5 border-b border-border/30 bg-muted/20">
                        <span className="text-[10px] text-muted-foreground/60">
                          {link.timestamp && !isNaN(link.timestamp.getTime()) ? link.timestamp.toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '时间未记录'}
                        </span>
                      </div>
                      <div className="px-3 py-2.5 flex items-center gap-2">
                        <div className="w-8 h-8 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
                          <Link2 className="w-4 h-4 text-primary" />
                        </div>
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium truncate">{link.title || link.url}</p>
                          <p className="text-[10px] text-muted-foreground/60 truncate">{link.url}</p>
                        </div>
                      </div>
                      <div className="flex items-center gap-1 px-3 py-1.5 border-t border-border/30 bg-muted/20">
                        <button onClick={() => jumpToMessage(link.messageId)} className="flex items-center gap-1 px-2 py-1 rounded text-xs text-muted-foreground hover:bg-muted/50 transition-colors" title="跳转到会话中此消息">
                          <LocateFixed className="w-3 h-3" />
                          跳转
                        </button>
                        <a href={link.url} target="_blank" rel="noopener noreferrer" className="flex items-center gap-1 px-2 py-1 rounded text-xs text-primary hover:bg-primary/10 transition-colors">
                          <Link2 className="w-3 h-3" />
                          打开链接
                        </a>
                        <button onClick={() => navigator.clipboard.writeText(link.url)} className="flex items-center gap-1 px-2 py-1 rounded text-xs text-muted-foreground hover:bg-muted/50 transition-colors">
                          <Copy className="w-3 h-3" />
                          复制链接
                        </button>
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          )}

          {/* Dates view — 日期日历（微信式：有会话消息的日期突出显示，点击跳转当天第一条消息） */}
          {view === 'dates' && (() => {
            const dateMap = new Map(dateGroups.map(g => [g.date, g]));
            const y = calMonth.getFullYear();
            const m = calMonth.getMonth();
            const startOffset = (new Date(y, m, 1).getDay() + 6) % 7; // 周一起始
            const daysInMonth = new Date(y, m + 1, 0).getDate();
            const today = new Date().toISOString().slice(0, 10);
            const cells: ({ d: number; dateStr: string; group?: typeof dateGroups[number] } | null)[] = [];
            for (let i = 0; i < startOffset; i++) cells.push(null);
            for (let d = 1; d <= daysInMonth; d++) {
              const dateStr = `${y}-${String(m + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
              cells.push({ d, dateStr, group: dateMap.get(dateStr) });
            }
            return (
              <div className="max-w-sm mx-auto rounded-xl border border-border/60 bg-background/80 backdrop-blur-sm overflow-hidden">
                {/* 月份导航 */}
                <div className="flex items-center justify-between px-4 py-3 border-b border-border/30 bg-muted/20">
                  <button onClick={() => setCalMonth(new Date(y, m - 1, 1))} className="p-1.5 rounded hover:bg-muted/50 text-muted-foreground transition-colors" title="上个月">
                    <ChevronLeft className="w-4 h-4" />
                  </button>
                  <span className="text-sm font-medium">{y}年{m + 1}月</span>
                  <button onClick={() => setCalMonth(new Date(y, m + 1, 1))} className="p-1.5 rounded hover:bg-muted/50 text-muted-foreground transition-colors" title="下个月">
                    <ChevronRight className="w-4 h-4" />
                  </button>
                </div>
                {/* 星期表头 */}
                <div className="grid grid-cols-7 px-2 pt-2 pb-1">
                  {['一', '二', '三', '四', '五', '六', '日'].map(w => (
                    <div key={w} className="text-center text-[10px] text-muted-foreground/60 py-1">{w}</div>
                  ))}
                </div>
                {/* 日期网格 */}
                <div className="grid grid-cols-7 gap-1 p-2">
                  {cells.map((cell, i) => {
                    if (!cell) return <div key={`pad-${i}`} />;
                    const has = !!cell.group;
                    const isToday = cell.dateStr === today;
                    return (
                      <button
                        key={cell.dateStr}
                        disabled={!has}
                        onClick={() => { const first = cell.group?.messages[0]; if (first) jumpToMessage(first.id); }}
                        className={`relative aspect-square rounded-lg flex flex-col items-center justify-center text-xs transition-colors ${has ? 'bg-primary/15 text-primary font-semibold hover:bg-primary/25 cursor-pointer' : 'text-muted-foreground/40'} ${isToday ? 'ring-1 ring-primary' : ''}`}
                        title={has ? `${cell.dateStr}：${cell.group!.count}条消息，点击跳转` : cell.dateStr}
                      >
                        {cell.d}
                        {has && <span className="absolute bottom-1 w-1 h-1 rounded-full bg-primary" />}
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })()}

{/* Messages view */}

    </>
  );
}
