"use client";
/**
 * 智能投标页面 — AI招投标文档生成系统
 * 使用openface标准三栏布局：setPageSidebar(LeftPanel) + MainPanel + 页内 detail 右列（workspace prop）
 * 集成bidding后端引擎（12个API端点）
 */
import { useState, useEffect, useCallback } from "react";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/stores/app-store";
import {
  FileText, Upload, ListTree, PenTool, ShieldCheck, Download,
  Plus, ChevronRight, ChevronDown, Loader2, CheckCircle,
  AlertTriangle, AlertCircle, Clock, Trash2, Eye,
} from "lucide-react";
import { LeftPanel } from "@/components/left-panel";
import { DetailPanel } from "@/components/detail-panel";
import { PageLayout } from "@/components/page-layout";

/* ── 类型定义 ─────────────────────────────────────── */

interface Project {
  id: string;
  name: string;
  status: "draft" | "parsing" | "generating" | "completed";
  created_at: string;
  updated_at?: string;
  file_path?: string;
}

interface ScoringItem {
  item: string;
  score: number;
  type: string;
  category?: string;
}

interface TechParam {
  product: string;
  param: string;
  value: string;
  level: "★" | "▲" | "一般";
}

interface ParseResult {
  project_info: {
    name: string;
    budget?: string;
    deadline?: string;
    method?: string;
  };
  scoring: ScoringItem[];
  tech_params: TechParam[];
  clauses: string[];
  templates: string[];
}

interface OutlineNode {
  id: string;
  title: string;
  description: string;
  children?: OutlineNode[];
  content?: string;
}

interface CheckResult {
  rule_id: string;
  severity: "致命" | "高" | "中" | "低";
  description: string;
  suggestion: string;
  status: "待修复" | "已修复";
}

type TabId = "parse" | "outline" | "generate" | "check" | "export";

/* ── 常量 ──────────────────────────────────────────── */

const TABS: { id: TabId; label: string; icon: typeof FileText }[] = [
  { id: "parse", label: "解析", icon: Upload },
  { id: "outline", label: "提纲", icon: ListTree },
  { id: "generate", label: "生成", icon: PenTool },
  { id: "check", label: "检查", icon: ShieldCheck },
  { id: "export", label: "导出", icon: Download },
];

const STATUS_LABEL: Record<Project["status"], string> = {
  draft: "草稿", parsing: "解析中", generating: "生成中", completed: "已完成",
};

const STATUS_STYLE: Record<Project["status"], string> = {
  draft: "bg-gray-500/20 text-gray-400",
  parsing: "bg-blue-500/20 text-blue-400",
  generating: "bg-yellow-500/20 text-yellow-400",
  completed: "bg-green-500/20 text-green-400",
};

const SEVERITY_STYLE: Record<string, string> = {
  "致命": "bg-red-500/20 text-red-400 border-red-500/30",
  "高": "bg-orange-500/20 text-orange-400 border-orange-500/30",
  "中": "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
  "低": "bg-blue-500/20 text-blue-400 border-blue-500/30",
};

/* ── API基地址 ─────────────────────────────────────── */

function getApiBase() {
  if (typeof window === "undefined") return "";
  const h = window.location.hostname;
  return `http://${h}:8092`;
}

/* ── 主组件 ────────────────────────────────────────── */

export default function BiddingClient() {
  const { setPageSidebar } = useAppStore();

  /* 状态 */
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<TabId>("parse");
  const [parseResult, setParseResult] = useState<ParseResult | null>(null);
  const [outline, setOutline] = useState<OutlineNode | null>(null);
  const [checkResults, setCheckResults] = useState<CheckResult[]>([]);
  const [selectedChapter, setSelectedChapter] = useState<OutlineNode | null>(null);
  const [isGenerating, setIsGenerating] = useState(false);
  const [isParsing, setIsParsing] = useState(false);
  const [isChecking, setIsChecking] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [exportUrl, setExportUrl] = useState<string | null>(null);
  const [genProgress, setGenProgress] = useState<Record<string, boolean>>({});
  const [newProjectName, setNewProjectName] = useState("");
  const [showNewProject, setShowNewProject] = useState(false);

  const selected = projects.find(p => p.id === selectedId) || null;

  /* ── 加载项目列表 ────────────────────────────────── */
  const fetchProjects = useCallback(async () => {
    try {
      const res = await fetch(`${getApiBase()}/bidding/projects`);
      if (res.ok) {
        const data = await res.json();
        setProjects(data.projects || []);
      }
    } catch { /* 静默失败 */ }
  }, []);

  useEffect(() => { fetchProjects(); }, [fetchProjects]);

  /* ── 创建项目 ────────────────────────────────────── */
  const createProject = async () => {
    if (!newProjectName.trim()) return;
    try {
      const res = await fetch(`${getApiBase()}/bidding/project`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newProjectName }),
      });
      if (res.ok) {
        setNewProjectName("");
        setShowNewProject(false);
        fetchProjects();
      }
    } catch { /* 静默失败 */ }
  };

  /* ── 删除项目 ────────────────────────────────────── */
  const deleteProject = async (id: string) => {
    try {
      await fetch(`${getApiBase()}/bidding/project/${id}`, { method: "DELETE" });
      if (selectedId === id) setSelectedId(null);
      fetchProjects();
    } catch { /* 静默失败 */ }
  };

  /* ── 解析招标文件 ────────────────────────────────── */
  const parseDocument = async (file: File) => {
    if (!selectedId) return;
    setIsParsing(true);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("project_id", selectedId);
      const res = await fetch(`${getApiBase()}/bidding/parse`, { method: "POST", body: form });
      if (res.ok) {
        const data = await res.json();
        setParseResult(data);
      }
    } catch { /* 静默失败 */ }
    finally { setIsParsing(false); }
  };

  /* ── 生成提纲 ────────────────────────────────────── */
  const generateOutline = async () => {
    if (!selectedId) return;
    try {
      const res = await fetch(`${getApiBase()}/bidding/outline`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_id: selectedId }),
      });
      if (res.ok) {
        const data = await res.json();
        setOutline(data.outline || data);
      }
    } catch { /* 静默失败 */ }
  };

  /* ── 生成章节（流式） ───────────────────────────── */
  const generateChapter = async (chapterId: string) => {
    if (!selectedId) return;
    setIsGenerating(true);
    setGenProgress(prev => ({ ...prev, [chapterId]: false }));
    try {
      const res = await fetch(`${getApiBase()}/bidding/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_id: selectedId, chapter_id: chapterId }),
      });
      if (res.ok) {
        const data = await res.json();
        // 更新outline中对应章节的内容
        setOutline(prev => updateOutlineContent(prev, chapterId, data.content || ""));
        setGenProgress(prev => ({ ...prev, [chapterId]: true }));
      }
    } catch { /* 静默失败 */ }
    finally { setIsGenerating(false); }
  };

  /* ── 合规检查 ────────────────────────────────────── */
  const runComplianceCheck = async () => {
    if (!selectedId) return;
    setIsChecking(true);
    try {
      const res = await fetch(`${getApiBase()}/bidding/check`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_id: selectedId }),
      });
      if (res.ok) {
        const data = await res.json();
        setCheckResults(data.results || []);
      }
    } catch { /* 静默失败 */ }
    finally { setIsChecking(false); }
  };

  /* ── 导出Word ────────────────────────────────────── */
  const exportToWord = async () => {
    if (!selectedId) return;
    setIsExporting(true);
    try {
      const res = await fetch(`${getApiBase()}/bidding/export`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_id: selectedId }),
      });
      if (res.ok) {
        const data = await res.json();
        setExportUrl(data.download_url || null);
      }
    } catch { /* 静默失败 */ }
    finally { setIsExporting(false); }
  };

  /* ── 左侧栏：项目列表 ──────────────────────────── */
  useEffect(() => {
    setPageSidebar(
      <LeftPanel
        items={projects}
        filter={(p, q) => p.name.toLowerCase().includes(q.toLowerCase())}
        placeholder="搜索项目..."
        header={
          <div className="px-2 pb-2 space-y-2">
            <div className="flex items-center gap-2 px-2">
              <FileText size={16} className="text-primary" />
              <span className="text-sm font-semibold">智能投标</span>
            </div>
            {/* 新建项目 */}
            {showNewProject ? (
              <div className="flex gap-2 px-2 items-center">
                <input
                  value={newProjectName}
                  onChange={e => setNewProjectName(e.target.value)}
                  onKeyDown={e => e.key === "Enter" && createProject()}
                  placeholder="项目名称..."
                  className="flex-1 min-w-0 px-2 py-1.5 text-xs rounded border border-border bg-muted outline-none focus:ring-1 focus:ring-primary/30"
                  autoFocus
                />
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); createProject(); }}
                  className="shrink-0 px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded hover:bg-primary/80 active:scale-95 transition-all cursor-pointer"
                >确定</button>
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); setShowNewProject(false); }}
                  className="shrink-0 px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground rounded hover:bg-accent transition-colors cursor-pointer"
                >取消</button>
              </div>
            ) : (
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); setShowNewProject(true); }}
                className="flex items-center gap-1.5 w-full px-3 py-2 text-xs text-muted-foreground hover:text-foreground hover:bg-accent rounded transition-colors cursor-pointer"
              >
                <Plus size={14} /> 新建项目
              </button>
            )}
          </div>
        }
        renderItem={(p: Project) => (
          <div
            key={p.id}
            onClick={() => setSelectedId(p.id)}
            className={cn(
              "px-3 py-2.5 cursor-pointer hover:bg-muted/80 transition-colors border-b border-border/30",
              selectedId === p.id && "bg-primary/12 text-primary"
            )}
          >
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium truncate">{p.name}</span>
              <span className={cn("text-[10px] px-1.5 py-0.5 rounded-full shrink-0", STATUS_STYLE[p.status])}>
                {STATUS_LABEL[p.status]}
              </span>
            </div>
            <p className="text-[10px] text-muted-foreground mt-0.5">{new Date(p.created_at).toLocaleDateString("zh-CN")}</p>
          </div>
        )}
      />
    );
    return () => setPageSidebar(null);
  }, [projects, selectedId, showNewProject, newProjectName, setPageSidebar]);

  /* ── 右侧栏：详情面板 ──────────────────────────── */
    /* ── 页内 detail 右列（PageLayout workspace prop）──
     菜单点击劫持全局根修（2026-11-19）：项目详情渲染在页面自身右列，绝不再
     塞进全局 workspace 面板；selected/selectedId（纯展示型 detail 状态）原样保留。 */
  const pageDetail = selected ? (
    <DetailPanel
        title={selected.name}
        subtitle={STATUS_LABEL[selected.status]}
        icon={<FileText className="w-5 h-5 text-primary" />}
        onClose={() => setSelectedId(null)}
      >
        <div className="space-y-3 p-3">
          <div className="flex items-center justify-between text-xs">
            <span className="text-muted-foreground">状态</span>
            <span className={cn("px-2 py-0.5 rounded-full text-[10px]", STATUS_STYLE[selected.status])}>
              {STATUS_LABEL[selected.status]}
            </span>
          </div>
          <div className="flex items-center justify-between text-xs">
            <span className="text-muted-foreground">创建时间</span>
            <span>{new Date(selected.created_at).toLocaleString("zh-CN")}</span>
          </div>
          {selected.file_path && (
            <div className="flex items-center justify-between text-xs">
              <span className="text-muted-foreground">文件</span>
              <span className="truncate max-w-[150px]">{selected.file_path}</span>
            </div>
          )}
          {/* 解析结果摘要 */}
          {parseResult && (
            <div className="mt-4 space-y-2">
              <h4 className="text-xs font-medium text-muted-foreground">解析结果</h4>
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground">评分项</span>
                <span>{parseResult.scoring?.length || 0} 项</span>
              </div>
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground">技术参数</span>
                <span>{parseResult.tech_params?.length || 0} 项</span>
              </div>
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground">废标条款</span>
                <span>{parseResult.clauses?.length || 0} 条</span>
              </div>
            </div>
          )}
          {/* 合规检查摘要 */}
          {checkResults.length > 0 && (
            <div className="mt-4 space-y-2">
              <h4 className="text-xs font-medium text-muted-foreground">合规检查</h4>
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground">致命问题</span>
                <span className="text-red-400">{checkResults.filter(r => r.severity === "致命").length}</span>
              </div>
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground">高风险</span>
                <span className="text-orange-400">{checkResults.filter(r => r.severity === "高").length}</span>
              </div>
            </div>
          )}
        </div>
      </DetailPanel>
  ) : null;

  /* ── MainPanel ──────────────────────────────────── */
  if (!selected) {
    return (
      <PageLayout title="智能投标" icon={<FileText className="w-5 h-5" />} workspace={pageDetail} badge="AI">
        <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
          <FileText size={64} className="mb-4 opacity-20" />
          <p className="text-sm">选择或创建一个投标项目</p>
          <p className="text-xs mt-1">支持招标文件解析、标书生成、合规检查、Word导出</p>
        </div>
      </PageLayout>
    );
  }

  return (
    <PageLayout
      title={selected.name}
      icon={<FileText className="w-5 h-5" />}
      badge={STATUS_LABEL[selected.status]}
    >
      {/* Tab栏 */}
      <div className="flex items-center gap-1 border-b border-border mb-4 pb-1 overflow-x-auto">
        {TABS.map(tab => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={cn(
                "flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-t-lg transition-colors whitespace-nowrap",
                activeTab === tab.id
                  ? "text-primary bg-primary/10 border-b-2 border-primary"
                  : "text-muted-foreground hover:text-foreground hover:bg-accent"
              )}
            >
              <Icon size={14} />
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* Tab内容 */}
      {activeTab === "parse" && (
        <ParseTab
          parseResult={parseResult}
          isParsing={isParsing}
          onParse={parseDocument}
        />
      )}
      {activeTab === "outline" && (
        <OutlineTab
          outline={outline}
          onGenerate={generateOutline}
          onSelectChapter={setSelectedChapter}
        />
      )}
      {activeTab === "generate" && (
        <GenerateTab
          outline={outline}
          selectedChapter={selectedChapter}
          onSelectChapter={setSelectedChapter}
          onGenerate={generateChapter}
          isGenerating={isGenerating}
          genProgress={genProgress}
        />
      )}
      {activeTab === "check" && (
        <CheckTab
          results={checkResults}
          isChecking={isChecking}
          onCheck={runComplianceCheck}
          onSelectResult={() => {}}
        />
      )}
      {activeTab === "export" && (
        <ExportTab
          isExporting={isExporting}
          exportUrl={exportUrl}
          onExport={exportToWord}
        />
      )}
    </PageLayout>
  );
}

/* ── 辅助函数：更新outline中指定章节内容 ──────────── */

function updateOutlineContent(node: OutlineNode | null, chapterId: string, content: string): OutlineNode | null {
  if (!node) return null;
  if (node.id === chapterId) return { ...node, content };
  if (node.children) {
    return { ...node, children: node.children.map(c => updateOutlineContent(c, chapterId, content) || c) };
  }
  return node;
}

/* ── 解析Tab ──────────────────────────────────────── */

function ParseTab({ parseResult, isParsing, onParse }: {
  parseResult: ParseResult | null;
  isParsing: boolean;
  onParse: (file: File) => void;
}) {
  const [dragOver, setDragOver] = useState(false);

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) onParse(file);
  };

  const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) onParse(file);
  };

  return (
    <div className="space-y-4">
      {/* 上传区域 */}
      <div
        onDragOver={e => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        className={cn(
          "flex flex-col items-center justify-center p-8 border-2 border-dashed rounded-xl transition-colors cursor-pointer",
          dragOver ? "border-primary bg-primary/5" : "border-border hover:border-primary/30"
        )}
        onClick={() => document.getElementById("bid-file-input")?.click()}
      >
        <input id="bid-file-input" type="file" accept=".pdf,.docx,.doc" className="hidden" onChange={handleFileInput} />
        {isParsing ? (
          <Loader2 size={40} className="text-primary animate-spin mb-2" />
        ) : (
          <Upload size={40} className="text-muted-foreground mb-2 opacity-50" />
        )}
        <p className="text-sm text-muted-foreground">{isParsing ? "正在解析..." : "拖放招标文件到此处，或点击选择"}</p>
        <p className="text-xs text-muted-foreground/60 mt-1">支持 PDF、Word 格式</p>
      </div>

      {/* 解析结果 */}
      {parseResult && (
        <div className="space-y-4">
          {/* 项目信息 */}
          <div className="p-4 rounded-xl border bg-card">
            <h3 className="text-sm font-medium mb-3 flex items-center gap-2"><Eye size={14} /> 项目信息</h3>
            <div className="grid grid-cols-2 gap-2 text-xs">
              <div className="flex justify-between"><span className="text-muted-foreground">名称</span><span>{parseResult.project_info?.name}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">预算</span><span>{parseResult.project_info?.budget || "-"}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">截止</span><span>{parseResult.project_info?.deadline || "-"}</span></div>
              <div className="flex justify-between"><span className="text-muted-foreground">评标方法</span><span>{parseResult.project_info?.method || "-"}</span></div>
            </div>
          </div>

          {/* 评分标准 */}
          {parseResult.scoring?.length > 0 && (
            <div className="p-4 rounded-xl border bg-card">
              <h3 className="text-sm font-medium mb-3">评分标准</h3>
              <table className="w-full text-xs">
                <thead><tr className="text-muted-foreground border-b border-border">
                  <th className="text-left py-1.5">项目</th><th className="text-right py-1.5">分值</th><th className="text-right py-1.5">类型</th>
                </tr></thead>
                <tbody>
                  {parseResult.scoring.map((s, i) => (
                    <tr key={i} className="border-b border-border/50">
                      <td className="py-1.5">{s.item}</td>
                      <td className="text-right">{s.score}</td>
                      <td className="text-right">{s.type}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* 技术参数 */}
          {parseResult.tech_params?.length > 0 && (
            <div className="p-4 rounded-xl border bg-card">
              <h3 className="text-sm font-medium mb-3">技术参数</h3>
              <table className="w-full text-xs">
                <thead><tr className="text-muted-foreground border-b border-border">
                  <th className="text-left py-1.5">产品</th><th className="text-left py-1.5">参数</th><th className="text-left py-1.5">要求</th><th className="text-center py-1.5">级别</th>
                </tr></thead>
                <tbody>
                  {parseResult.tech_params.map((t, i) => (
                    <tr key={i} className="border-b border-border/50">
                      <td className="py-1.5">{t.product}</td><td>{t.param}</td><td>{t.value}</td>
                      <td className={cn("text-center", t.level === "★" ? "text-red-400" : t.level === "▲" ? "text-orange-400" : "text-muted-foreground")}>{t.level}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* 废标条款 */}
          {parseResult.clauses?.length > 0 && (
            <div className="p-4 rounded-xl border bg-card">
              <h3 className="text-sm font-medium mb-3 flex items-center gap-2">
                <AlertTriangle size={14} className="text-red-400" /> 废标条款 ({parseResult.clauses.length}条)
              </h3>
              <ul className="space-y-1.5">
                {parseResult.clauses.map((c, i) => (
                  <li key={i} className="text-xs text-muted-foreground flex items-start gap-2">
                    <span className="text-red-400 mt-0.5">•</span> {c}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ── 提纲Tab ──────────────────────────────────────── */

function OutlineTab({ outline, onGenerate, onSelectChapter }: {
  outline: OutlineNode | null;
  onGenerate: () => void;
  onSelectChapter: (node: OutlineNode) => void;
}) {
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">标书提纲</h3>
        <button
          onClick={onGenerate}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors"
        >
          <ListTree size={14} /> 生成提纲
        </button>
      </div>
      {outline ? (
        <OutlineTreeNode node={outline} depth={0} onSelect={onSelectChapter} />
      ) : (
        <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
          <ListTree size={48} className="mb-3 opacity-20" />
          <p className="text-sm">点击"生成提纲"自动分析招标文件</p>
        </div>
      )}
    </div>
  );
}

function OutlineTreeNode({ node, depth, onSelect }: { node: OutlineNode; depth: number; onSelect: (n: OutlineNode) => void }) {
  const [expanded, setExpanded] = useState(depth < 2);
  const hasChildren = node.children && node.children.length > 0;

  return (
    <div style={{ marginLeft: depth > 0 ? 16 : 0 }}>
      <div
        onClick={() => { if (hasChildren) setExpanded(!expanded); onSelect(node); }}
        className={cn(
          "flex items-center gap-1.5 px-2 py-1.5 rounded text-xs cursor-pointer hover:bg-accent transition-colors",
          hasChildren ? "font-medium" : "text-muted-foreground"
        )}
      >
        {hasChildren ? (
          expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />
        ) : (
          <span className="w-3" />
        )}
        <span>{node.title}</span>
        {node.content && <CheckCircle size={10} className="text-green-400 ml-auto" />}
      </div>
      {expanded && hasChildren && node.children!.map(child => (
        <OutlineTreeNode key={child.id} node={child} depth={depth + 1} onSelect={onSelect} />
      ))}
    </div>
  );
}

/* ── 生成Tab ──────────────────────────────────────── */

function GenerateTab({ outline, selectedChapter, onSelectChapter, onGenerate, isGenerating, genProgress }: {
  outline: OutlineNode | null;
  selectedChapter: OutlineNode | null;
  onSelectChapter: (n: OutlineNode) => void;
  onGenerate: (chapterId: string) => void;
  isGenerating: boolean;
  genProgress: Record<string, boolean>;
}) {
  if (!outline) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
        <PenTool size={48} className="mb-3 opacity-20" />
        <p className="text-sm">请先在"提纲"Tab生成提纲</p>
      </div>
    );
  }

  return (
    <div className="flex gap-4 h-[60vh]">
      {/* 左侧：提纲树 */}
      <div className="w-64 border rounded-xl overflow-y-auto bg-card shrink-0">
        <div className="p-2 border-b border-border">
          <h4 className="text-xs font-medium text-muted-foreground">章节目录</h4>
        </div>
        <OutlineTreeNode node={outline} depth={0} onSelect={onSelectChapter} />
      </div>

      {/* 右侧：内容编辑 */}
      <div className="flex-1 flex flex-col border rounded-xl bg-card">
        {selectedChapter ? (
          <>
            <div className="flex items-center justify-between p-3 border-b border-border">
              <h4 className="text-sm font-medium">{selectedChapter.title}</h4>
              <div className="flex items-center gap-2">
                {genProgress[selectedChapter.id] && (
                  <span className="text-[10px] text-green-400 flex items-center gap-1"><CheckCircle size={10} /> 已生成</span>
                )}
                <button
                  onClick={() => onGenerate(selectedChapter.id)}
                  disabled={isGenerating}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 disabled:opacity-50 transition-colors"
                >
                  {isGenerating ? <Loader2 size={12} className="animate-spin" /> : <PenTool size={12} />}
                  生成当前章节
                </button>
              </div>
            </div>
            <div className="flex-1 p-4 overflow-y-auto">
              {selectedChapter.content ? (
                <pre className="text-xs whitespace-pre-wrap font-mono">{selectedChapter.content}</pre>
              ) : (
                <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
                  <PenTool size={32} className="mb-2 opacity-20" />
                  <p className="text-xs">点击"生成当前章节"开始生成</p>
                </div>
              )}
            </div>
          </>
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
            <ListTree size={32} className="mb-2 opacity-20" />
            <p className="text-xs">选择一个章节开始生成</p>
          </div>
        )}
      </div>
    </div>
  );
}

/* ── 检查Tab ──────────────────────────────────────── */

function CheckTab({ results, isChecking, onCheck, onSelectResult }: {
  results: CheckResult[];
  isChecking: boolean;
  onCheck: () => void;
  onSelectResult: (r: CheckResult) => void;
}) {
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">合规检查</h3>
        <button
          onClick={onCheck}
          disabled={isChecking}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 disabled:opacity-50 transition-colors"
        >
          {isChecking ? <Loader2 size={12} className="animate-spin" /> : <ShieldCheck size={12} />}
          运行合规检查
        </button>
      </div>

      {results.length > 0 ? (
        <div className="border rounded-xl overflow-hidden">
          <table className="w-full text-xs">
            <thead>
              <tr className="bg-muted/50 text-muted-foreground">
                <th className="text-left py-2 px-3">规则</th>
                <th className="text-center py-2 px-3">严重程度</th>
                <th className="text-left py-2 px-3">问题描述</th>
                <th className="text-left py-2 px-3">修复建议</th>
                <th className="text-center py-2 px-3">状态</th>
              </tr>
            </thead>
            <tbody>
              {results.map((r, i) => (
                <tr
                  key={i}
                  onClick={() => onSelectResult(r)}
                  className="border-t border-border/50 cursor-pointer hover:bg-accent/50 transition-colors"
                >
                  <td className="py-2 px-3 font-mono text-[10px]">{r.rule_id}</td>
                  <td className="py-2 px-3 text-center">
                    <span className={cn("px-1.5 py-0.5 rounded-full text-[10px] border", SEVERITY_STYLE[r.severity])}>
                      {r.severity}
                    </span>
                  </td>
                  <td className="py-2 px-3">{r.description}</td>
                  <td className="py-2 px-3 text-muted-foreground">{r.suggestion}</td>
                  <td className="py-2 px-3 text-center">
                    <span className={cn("px-1.5 py-0.5 rounded-full text-[10px]",
                      r.status === "已修复" ? "bg-green-500/20 text-green-400" : "bg-red-500/20 text-red-400"
                    )}>
                      {r.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
          <ShieldCheck size={48} className="mb-3 opacity-20" />
          <p className="text-sm">点击"运行合规检查"检查投标文件</p>
          <p className="text-xs mt-1">自动检查废标风险、评分覆盖、格式规范</p>
        </div>
      )}
    </div>
  );
}

/* ── 导出Tab ──────────────────────────────────────── */

function ExportTab({ isExporting, exportUrl, onExport }: {
  isExporting: boolean;
  exportUrl: string | null;
  onExport: () => void;
}) {
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">导出Word文档</h3>
        <button
          onClick={onExport}
          disabled={isExporting}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 disabled:opacity-50 transition-colors"
        >
          {isExporting ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />}
          导出Word
        </button>
      </div>

      {exportUrl ? (
        <div className="p-6 rounded-xl border bg-card text-center">
          <CheckCircle size={48} className="mx-auto mb-3 text-green-400" />
          <p className="text-sm font-medium mb-2">导出完成</p>
          <a
            href={exportUrl}
            download
            className="inline-flex items-center gap-1.5 px-4 py-2 text-xs bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors"
          >
            <Download size={14} /> 下载文件
          </a>
        </div>
      ) : (
        <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
          <Download size={48} className="mb-3 opacity-20" />
          <p className="text-sm">点击"导出Word"生成投标文件</p>
          <p className="text-xs mt-1">自动生成标准格式的Word文档</p>
        </div>
      )}
    </div>
  );
}
