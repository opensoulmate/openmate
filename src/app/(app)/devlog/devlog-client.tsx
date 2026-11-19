"use client";
/**
 * 开发记录页面 — 动态读取 docs/devlog/ 目录下的开发记录文件
 * 使用openface标准三栏布局：setPageSidebar(LeftPanel) + MainPanel(MainHeader) + 页内 detail 右列（workspace prop）
 *
 * 数据来源：GET /api/devlog/list → { logs: DevlogItem[] }
 * 文件命名规范：{YYYY-MM-DD}-{feature-name}.md
 */
import { useState, useEffect, useCallback } from "react";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/stores/app-store";
import {
  FileText, Search, Calendar, Code2, CheckCircle2, Clock, AlertCircle,
} from "lucide-react";
import { LeftPanel } from "@/components/left-panel";
import { DetailPanel } from "@/components/detail-panel";
import { PageLayout } from "@/components/page-layout";
import { FileViewer } from "@opensoulmate/openface";

/* ── 数据模型 ────────────────────────────────────────── */

/** 开发记录条目 — 只保留核心字段 */
interface DevlogItem {
  id: string;        // 唯一标识（文件名去掉.md）
  name: string;      // 功能名称（从文件标题提取）
  desc: string;      // 概述描述（从文件内容提取）
  version: string;   // 版本号（从文件内容提取）
  fileName: string;  // 文件名（用于加载内容）
  date: string;      // 日期（从文件名解析）
}

/* ── 图标映射 ────────────────────────────────────────── */

/** 根据功能名称关键字映射图标 */
const NAME_ICONS: Record<string, typeof FileText> = {
  ai: Code2,
  group: Code2,
  feature: Code2,
  fix: AlertCircle,
  refactor: Code2,
  test: CheckCircle2,
  deploy: Calendar,
};

/* ── 页面组件 ────────────────────────────────────────── */

export function DevlogClient() {
  /* 状态管理 */
  const [logs, setLogs] = useState<DevlogItem[]>([]);           // 开发记录列表
  const [loading, setLoading] = useState(true);                  // 列表加载状态
  const [selectedId, setSelectedId] = useState<string | null>(null); // 当前选中的记录
  const [query, setQuery] = useState("");                        // 搜索关键词
  const [logContent, setLogContent] = useState<string>("");     // 记录文件内容
  const [loadingContent, setLoadingContent] = useState(false);   // 内容加载状态

  const setPageSidebar = useAppStore((s) => s.setPageSidebar);

  /* ── 从 API 加载开发记录列表 ── */
  useEffect(() => {
    setLoading(true);
    fetch("/api/devlog/list")
      .then((res) => res.json())
      .then((data) => {
        setLogs(data.logs || []);
      })
      .catch(() => {
        setLogs([]);
      })
      .finally(() => {
        setLoading(false);
      });
  }, []);

  /* 当前选中的开发记录条目 */
  const selected = logs.find((s) => s.id === selectedId) || null;

  /* 搜索过滤 */
  const filtered = logs.filter(s => {
    if (!query) return true;
    const q = query.toLowerCase();
    return s.name.toLowerCase().includes(q) || s.desc.toLowerCase().includes(q) || s.date.includes(q);
  });

  /* ── 加载开发记录文件内容 ── */
  const loadLogContent = useCallback(async (fileName: string) => {
    setLoadingContent(true);
    try {
      const resp = await fetch(`/api/devlog/content?file=${encodeURIComponent(fileName)}`);
      if (resp.ok) { const data = await resp.json(); setLogContent(data.content || "加载失败"); }
      else { setLogContent("无法加载开发记录文件"); }
    } catch { setLogContent("网络错误"); }
    finally { setLoadingContent(false); }
  }, []);

  /* ── 注册左侧sidebar（LeftPanel） ── */
  useEffect(() => {
    setPageSidebar(
      <LeftPanel
        items={filtered}
        filter={(log: DevlogItem, q: string) => {
          const ql = q.toLowerCase();
          return log.name.toLowerCase().includes(ql) || log.desc.toLowerCase().includes(ql) || log.date.includes(ql);
        }}
        placeholder="搜索开发记录..."
        header={
          <div className="px-2 pb-2 space-y-2">
            <div className="flex items-center gap-2 px-2">
              <FileText size={16} className="text-primary" />
              <span className="text-sm font-semibold">开发记录</span>
            </div>
            <div className="flex gap-1 px-2">
              <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground">{logs.length} 条记录</span>
            </div>
          </div>
        }
        renderItem={(log: DevlogItem) => {
          /* 根据名称关键字选择图标 */
          const Icon = Object.entries(NAME_ICONS).find(([k]) => log.name.toLowerCase().includes(k))?.[1] || FileText;
          return (
            <div
              key={log.id}
              className={cn(
                "px-3 py-2.5 cursor-pointer hover:bg-muted/80 transition-colors border-b border-border/30",
                selectedId === log.id && "bg-primary/12 text-primary"
              )}
              onClick={() => setSelectedId(log.id)}
            >
              <div className="flex items-center gap-2 min-w-0">
                <Icon size={14} className="shrink-0 text-muted-foreground" />
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-medium truncate">{log.name}</p>
                  <p className="text-[10px] text-muted-foreground truncate">{log.desc}</p>
                  <div className="flex items-center gap-2 mt-0.5">
                    <span className="text-[9px] text-muted-foreground">v{log.version}</span>
                    {log.date && <span className="text-[9px] text-muted-foreground">{log.date}</span>}
                  </div>
                </div>
              </div>
            </div>
          );
        }}
        emptyState={<div className="flex flex-col items-center justify-center h-40 text-muted-foreground"><FileText className="w-8 h-8 mb-2 opacity-40" /><p className="text-xs">未找到开发记录</p></div>}
      />
    );
    return () => setPageSidebar(null);
  }, [filtered, selectedId, logs.length, setPageSidebar]);

  /* ── 注册右侧workspace（DetailPanel） ── */
    /* ── 加载选中记录内容（纯展示型 detail 状态） ── */
  useEffect(() => {
    if (!selected) return;
    loadLogContent(selected.fileName);
  }, [selected, loadLogContent]);

  /* ── 页内 detail 右列（PageLayout workspace prop）──
     菜单点击劫持全局根修（2026-11-19）：记录正文渲染在页面自身右列，绝不再
     塞进全局 workspace 面板；selected/logContent（纯展示型 detail 状态）原样保留。 */
  const pageDetail = selected ? (() => {
    

    /* 详情面板的基本信息项 */
    const detailItems = [
      { label: "功能", value: selected.name },
      { label: "版本", value: `v${selected.version}` },
      { label: "文件", value: selected.fileName },
      { label: "日期", value: selected.date || "—" },
    ];

    /* markdown内容转data URL供FileViewer渲染 */
    const fileUrl = logContent
      ? `data:application/octet-stream;base64,${btoa(unescape(encodeURIComponent(logContent)))}`
      : "";
    return (
    <DetailPanel
        title={selected.name}
        subtitle={selected.desc}
        icon={<FileText className="w-5 h-5 text-primary" />}
        badge={`v${selected.version}`}
        onClose={() => setSelectedId(null)}
      >
        {/* 基本信息 */}
        <div className="space-y-2 p-3 border-b border-border">
          {detailItems.map((item, i) => (
            <div key={i} className="flex items-center justify-between text-xs">
              <span className="text-muted-foreground">{item.label}</span>
              <span className="font-medium">{item.value}</span>
            </div>
          ))}
        </div>
        {/* FileViewer渲染开发记录正文markdown */}
        <div className="h-[50vh]">
          {loadingContent ? (
            <div className="flex items-center justify-center h-full text-muted-foreground text-sm">加载中...</div>
          ) : fileUrl ? (
            <FileViewer fileName={selected.fileName} fileUrl={fileUrl} className="h-full" />
          ) : (
            <div className="flex items-center justify-center h-full text-muted-foreground text-sm">无法加载开发记录文件</div>
          )}
        </div>
      </DetailPanel>
    );
  })() : null;

  /* ── MainPanel: 中间主内容区 ── */
  return (
    <PageLayout
      title="开发记录"
      icon={<FileText className="w-5 h-5" />}
      badge={loading ? "加载中..." : `${logs.length} 条`}
      workspace={pageDetail}
      headerActions={
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground">{loading ? "加载中..." : `共 ${logs.length} 条开发记录`}</span>
        </div>
      }
    >
      <div className="px-3 lg:px-6 py-4 lg:py-6 h-full overflow-y-auto">
      {/* 加载状态 */}
      {loading && (
        <div className="flex items-center justify-center py-20 text-muted-foreground text-sm">
          加载开发记录列表...
        </div>
      )}

      {/* 搜索栏 */}
      {!loading && (
        <>
          <div className="relative max-w-sm mb-4">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
            <input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索开发记录..."
              className="w-full pl-9 pr-3 py-2 rounded-lg border border-border bg-muted text-xs lg:text-sm outline-none focus:ring-2 focus:ring-primary/30" />
          </div>

          {/* 开发记录卡片网格 */}
          {filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
              <FileText size={48} className="mb-4 opacity-30" />
              <p className="text-xs lg:text-sm">未找到开发记录</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2 lg:gap-4">
              {filtered.map(log => {
                /* 根据名称关键字选择图标 */
                const Icon = Object.entries(NAME_ICONS).find(([k]) => log.name.toLowerCase().includes(k))?.[1] || FileText;
                return (
                  <div
                    key={log.id}
                    onClick={() => setSelectedId(log.id)}
                    className={cn(
                      "rounded-xl border bg-card p-3 lg:p-4 transition-all hover:border-primary/30 cursor-pointer",
                      selectedId === log.id ? "border-primary ring-1 ring-primary/30" : ""
                    )}
                  >
                    <div className="flex items-start justify-between mb-2">
                      <div className="flex items-center gap-2.5">
                        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/10">
                          <Icon size={18} className="text-primary" />
                        </div>
                        <div>
                          <h3 className="text-xs lg:text-sm font-medium">{log.name}</h3>
                          <span className="text-[10px] text-muted-foreground">v{log.version}</span>
                        </div>
                      </div>
                    </div>
                    <p className="text-xs text-muted-foreground line-clamp-2 mb-3 min-h-[2rem]">{log.desc}</p>
                    {log.date && (
                      <div className="flex items-center gap-1">
                        <Calendar size={10} className="text-muted-foreground" />
                        <span className="text-[9px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground">{log.date}</span>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </>
      )}
      </div>
    </PageLayout>
  );
}
