"use client";

/**
 * 统一文档浏览器组件
 * 
 * 功能：
 * - 左侧：目录树（按分组折叠展示，每组有图标）
 * - 主区域：Markdown 内容渲染（使用 FileViewer）
 * - 支持搜索（跨分组搜索文件名）
 * - 点击文件加载内容
 * - 显示当前路径（面包屑）
 * - 三栏布局（复用 PageLayout + LeftPanel + DetailPanel）
 *
 * 数据来源：
 * - GET /api/docs/list → { groups: DocGroup[] }
 * - GET /api/docs/content?path=... → { content, path }
 */

import { useState, useEffect, useCallback, useMemo } from "react";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/stores/app-store";
import {
  FileText, Search, Layers, Layout, BookOpen, Settings, Folder,
  ChevronRight, ChevronDown, X,
} from "lucide-react";
import { LeftPanel } from "@/components/left-panel";
import { DetailPanel } from "@/components/detail-panel";
import { PageLayout } from "@/components/page-layout";
import { FileViewer } from "@opensoulmate/openface";

/* ── 数据模型 ────────────────────────────────────────── */

/** 单个文档文件 */
interface DocFile {
  path: string;   // 相对于 docs/ 的路径
  name: string;   // 显示名称
  group: string;  // 所属分组
  date: string;   // 修改日期
}

/** 文档分组 */
interface DocGroup {
  name: string;     // 分组名
  icon: string;     // 图标名
  files: DocFile[]; // 文件列表
}

/* ── 图标映射 ────────────────────────────────────────── */

/** 将图标名映射到 lucide-react 组件 */
const ICON_MAP: Record<string, React.ElementType> = {
  layers: Layers,
  layout: Layout,
  "file-text": FileText,
  "book-open": BookOpen,
  settings: Settings,
  folder: Folder,
};

/** 获取分组图标组件 */
function getGroupIcon(iconName: string): React.ElementType {
  return ICON_MAP[iconName] || Folder;
}

/* ── 页面组件 ────────────────────────────────────────── */

export function DocsClient() {
  /* 状态管理 */
  const [groups, setGroups] = useState<DocGroup[]>([]);           // 分组列表
  const [loading, setLoading] = useState(true);                    // 列表加载状态
  const [selectedPath, setSelectedPath] = useState<string | null>(null); // 当前选中文件路径
  const [query, setQuery] = useState("");                          // 搜索关键词
  const [docContent, setDocContent] = useState<string>("");        // 文档内容
  const [loadingContent, setLoadingContent] = useState(false);     // 内容加载状态
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set()); // 折叠的分组

  const setPageSidebar = useAppStore((s) => s.setPageSidebar);
  const setPageWorkspace = useAppStore((s) => s.setPageWorkspace);

  /* ── 从 API 加载文档列表 ── */
  useEffect(() => {
    setLoading(true);
    fetch("/api/docs/list")
      .then((res) => res.json())
      .then((data) => {
        setGroups(data.groups || []);
      })
      .catch(() => {
        setGroups([]);
      })
      .finally(() => {
        setLoading(false);
      });
  }, []);

  /* ── 搜索过滤 ── */
  const filteredGroups = useMemo(() => {
    if (!query.trim()) return groups;
    const q = query.toLowerCase();
    return groups
      .map((group) => ({
        ...group,
        files: group.files.filter(
          (f) =>
            f.name.toLowerCase().includes(q) ||
            f.path.toLowerCase().includes(q)
        ),
      }))
      .filter((group) => group.files.length > 0);
  }, [groups, query]);

  /* ── 统计总文件数 ── */
  const totalFiles = useMemo(
    () => groups.reduce((sum, g) => sum + g.files.length, 0),
    [groups]
  );

  /* ── 当前选中的文件信息 ── */
  const selectedFile = useMemo(() => {
    if (!selectedPath) return null;
    for (const group of groups) {
      const found = group.files.find((f) => f.path === selectedPath);
      if (found) return found;
    }
    return null;
  }, [groups, selectedPath]);

  /* ── 切换分组折叠状态 ── */
  const toggleGroup = useCallback((groupName: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(groupName)) {
        next.delete(groupName);
      } else {
        next.add(groupName);
      }
      return next;
    });
  }, []);

  /* ── 加载文档内容 ── */
  const loadDocContent = useCallback(async (path: string) => {
    setLoadingContent(true);
    try {
      const resp = await fetch(
        `/api/docs/content?path=${encodeURIComponent(path)}`
      );
      if (resp.ok) {
        const data = await resp.json();
        setDocContent(data.content || "加载失败");
      } else {
        setDocContent("无法加载文档文件");
      }
    } catch {
      setDocContent("网络错误");
    } finally {
      setLoadingContent(false);
    }
  }, []);

  /* ── 注册左侧 sidebar（LeftPanel） ── */
  useEffect(() => {
    setPageSidebar(
      <LeftPanel
        items={filteredGroups.flatMap((g) => g.files)}
        filter={(file: DocFile, q: string) => {
          const ql = q.toLowerCase();
          return (
            file.name.toLowerCase().includes(ql) ||
            file.path.toLowerCase().includes(ql)
          );
        }}
        placeholder="搜索文档..."
        header={
          <div className="px-2 pb-2 space-y-2">
            {/* 标题区域 */}
            <div className="flex items-center gap-2 px-2">
              <FileText size={16} className="text-primary" />
              <span className="text-sm font-semibold">文档中心</span>
            </div>
            {/* 统计标签 */}
            <div className="flex gap-1 px-2 flex-wrap">
              <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground">
                {totalFiles} 篇文档
              </span>
              <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground">
                {groups.length} 个分组
              </span>
            </div>
          </div>
        }
        renderItem={(file: DocFile) => {
          const isSelected = selectedPath === file.path;
          /* 查找该文件所属分组的图标 */
          const group = groups.find((g) => g.name === file.group);
          const Icon = group ? getGroupIcon(group.icon) : FileText;

          return (
            <div
              key={file.path}
              className={cn(
                "px-3 py-2.5 cursor-pointer hover:bg-muted/80 transition-colors border-b border-border/30",
                isSelected && "bg-primary/12 text-primary"
              )}
              onClick={() => setSelectedPath(file.path)}
            >
              <div className="flex items-center gap-2 min-w-0">
                <Icon size={14} className="shrink-0 text-muted-foreground" />
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-medium truncate">{file.name}</p>
                  <div className="flex items-center gap-2 mt-0.5">
                    <span className="text-[9px] text-muted-foreground">
                      {file.group}
                    </span>
                    {file.date && (
                      <span className="text-[9px] text-muted-foreground">
                        {file.date}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            </div>
          );
        }}
        emptyState={
          <div className="flex flex-col items-center justify-center h-40 text-muted-foreground">
            <FileText className="w-8 h-8 mb-2 opacity-40" />
            <p className="text-xs">未找到文档</p>
          </div>
        }
      />
    );
    return () => setPageSidebar(null);
  }, [filteredGroups, selectedPath, groups, totalFiles, setPageSidebar]);

  /* ── 注册右侧 workspace（DetailPanel） ── */
  useEffect(() => {
    if (!selectedFile) {
      setPageWorkspace(null);
      return;
    }
    loadDocContent(selectedFile.path);

    /* 面包屑路径 */
    const pathParts = selectedFile.path.split("/");
    const breadcrumb =
      pathParts.length > 1
        ? pathParts.slice(0, -1).join(" / ")
        : "根目录";

    /* markdown 内容转 data URL 供 FileViewer 渲染 */
    const fileUrl = docContent
      ? `data:application/octet-stream;base64,${btoa(unescape(encodeURIComponent(docContent)))}`
      : "";

    setPageWorkspace(
      <DetailPanel
        title={selectedFile.name}
        subtitle={selectedFile.group}
        icon={<FileText className="w-5 h-5 text-primary" />}
        badge={selectedFile.date}
        onClose={() => setSelectedPath(null)}
      >
        {/* 面包屑导航 */}
        <div className="flex items-center gap-1 px-3 py-2 text-xs text-muted-foreground border-b border-border">
          <span>docs</span>
          {pathParts.map((part, i) => (
            <span key={i} className="flex items-center gap-1">
              <ChevronRight size={10} />
              <span
                className={cn(
                  i === pathParts.length - 1
                    ? "text-foreground font-medium"
                    : "text-muted-foreground"
                )}
              >
                {part}
              </span>
            </span>
          ))}
        </div>

        {/* FileViewer 渲染文档正文 */}
        <div className="h-[60vh]">
          {loadingContent ? (
            <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
              加载中...
            </div>
          ) : fileUrl ? (
            <FileViewer
              fileName={selectedFile.path.split("/").pop() || "doc.md"}
              fileUrl={fileUrl}
              className="h-full"
            />
          ) : (
            <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
              无法加载文档
            </div>
          )}
        </div>
      </DetailPanel>
    );
    return () => setPageWorkspace(null);
  }, [selectedFile, docContent, setPageWorkspace, loadDocContent]);

  /* ── MainPanel: 中间主内容区 ── */
  return (
    <PageLayout
      title="文档中心"
      icon={<FileText className="w-5 h-5" />}
      badge={loading ? "加载中..." : `${totalFiles} 篇`}
      headerActions={
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground">
            {loading ? "加载中..." : `共 ${totalFiles} 篇文档 · ${groups.length} 个分组`}
          </span>
        </div>
      }
    >
      <div className="h-full overflow-y-auto px-3 lg:px-6 py-4 lg:py-6">
      {/* 加载状态 */}
      {loading && (
        <div className="flex items-center justify-center py-20 text-muted-foreground text-sm">
          加载文档列表...
        </div>
      )}

      {/* 搜索栏 + 分组目录树 */}
      {!loading && (
        <>
          {/* 搜索栏 */}
          <div className="relative max-w-sm mb-4">
            <Search
              size={14}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground"
            />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索文档..."
              className="w-full pl-9 pr-8 py-2 rounded-lg border border-border bg-muted text-xs lg:text-sm outline-none focus:ring-2 focus:ring-primary/30"
            />
            {query && (
              <button
                onClick={() => setQuery("")}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              >
                <X size={14} />
              </button>
            )}
          </div>

          {/* 分组目录树 */}
          {filteredGroups.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
              <FileText size={48} className="mb-4 opacity-30" />
              <p className="text-xs lg:text-sm">未找到文档</p>
            </div>
          ) : (
            <div className="space-y-1">
              {filteredGroups.map((group) => {
                const GroupIcon = getGroupIcon(group.icon);
                const isCollapsed = collapsedGroups.has(group.name);
                return (
                  <div key={group.name} className="rounded-lg overflow-hidden">
                    {/* 分组标题（可折叠） */}
                    <button
                      onClick={() => toggleGroup(group.name)}
                      className="flex items-center gap-2 w-full px-3 py-2 hover:bg-muted/50 transition-colors"
                    >
                      {isCollapsed ? (
                        <ChevronRight size={14} className="text-muted-foreground" />
                      ) : (
                        <ChevronDown size={14} className="text-muted-foreground" />
                      )}
                      <GroupIcon size={14} className="text-primary" />
                      <span className="text-xs font-medium">{group.name}</span>
                      <span className="text-[10px] text-muted-foreground ml-auto">
                        {group.files.length}
                      </span>
                    </button>

                    {/* 分组内文件列表 */}
                    {!isCollapsed && (
                      <div className="pl-6">
                        {group.files.map((file) => (
                          <div
                            key={file.path}
                            onClick={() => setSelectedPath(file.path)}
                            className={cn(
                              "flex items-center gap-2 px-3 py-1.5 cursor-pointer hover:bg-muted/50 transition-colors rounded-md",
                              selectedPath === file.path &&
                                "bg-primary/12 text-primary"
                            )}
                          >
                            <FileText
                              size={12}
                              className="shrink-0 text-muted-foreground"
                            />
                            <span className="text-xs truncate flex-1">
                              {file.name}
                            </span>
                            <span className="text-[9px] text-muted-foreground shrink-0">
                              {file.date}
                            </span>
                          </div>
                        ))}
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
