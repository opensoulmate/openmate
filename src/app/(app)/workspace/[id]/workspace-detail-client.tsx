"use client";

import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { useParams } from "next/navigation";
import { cn } from "@/lib/utils";
import { useAppStore, type FileNode, type TerminalLine } from "@/stores/app-store";
import { listDir, readFile, writeFile, executeCommand } from "@/lib/tauri-bridge";
import { FileViewer as OpenFaceFileViewer } from "@opensoulmate/openface";
import { SCMPanel } from "@/components/scm-panel";
import { useIsMobile } from "@/hooks/use-mobile";
import {
  FolderOpen,
  File,
  ChevronRight,
  ChevronDown,
  Terminal,
  Code,
  Maximize2,
  Minimize2,
  Send,
  GitBranch,
  PanelRightOpen,
  PanelRightClose,
  PanelLeft,
  ArrowLeft,
} from "lucide-react";
import Link from "next/link";

// ─── File Tree ──────────────────────────────────────────────────────────────

function FileTreeNode({
  node,
  depth,
  onSelect,
  selected,
}: {
  node: FileNode;
  depth: number;
  onSelect: (path: string) => void;
  selected: string | null;
}) {
  const [expanded, setExpanded] = useState(depth < 2);

  if (node.type === "directory") {
    return (
      <div>
        <button
          onClick={() => setExpanded(!expanded)}
          className={cn(
            "flex w-full items-center gap-1.5 rounded-md px-2 py-1 text-xs transition-colors hover:bg-accent",
          )}
          style={{ paddingLeft: `${depth * 12 + 8}px` }}
        >
          {expanded ? (
            <ChevronDown size={12} className="shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight size={12} className="shrink-0 text-muted-foreground" />
          )}
          <FolderOpen size={14} className="shrink-0 text-amber-400" />
          <span className="truncate text-foreground">{node.name}</span>
        </button>
        {expanded &&
          node.children?.map((child) => (
            <FileTreeNode
              key={child.path}
              node={child}
              depth={depth + 1}
              onSelect={onSelect}
              selected={selected}
            />
          ))}
      </div>
    );
  }

  return (
    <button
      onClick={() => onSelect(node.path)}
      className={cn(
        "flex w-full items-center gap-1.5 rounded-md px-2 py-1 text-xs transition-colors hover:bg-accent",
        selected === node.path && "bg-[rgba(124,58,237,0.12)] text-[#7c3aed]",
      )}
      style={{ paddingLeft: `${depth * 12 + 24}px` }}
    >
      <File size={14} className="shrink-0 text-muted-foreground" />
      <span className="truncate">{node.name}</span>
    </button>
  );
}

// ─── Highlighted Code (lazy-loaded) ────────────────────────────────────────

function HighlightedCode({ content, language }: { content: string; language: string }) {
  const [Component, setComponent] = useState<React.ComponentType<{ children: string }> | null>(null);

  useEffect(() => {
    let cancelled = false;
    import("react-syntax-highlighter").then((mod) => {
      if (cancelled) return;
      const SyntaxHighlighter = mod.Prism;
      import("react-syntax-highlighter/dist/cjs/styles/prism").then((styles) => {
        if (cancelled) return;
        const Wrapped = ({ children }: { children: string }) => (
          <SyntaxHighlighter
            language={language}
            style={styles.oneDark}
            showLineNumbers
            wrapLines
            customStyle={{ margin: 0, borderRadius: 0, background: "transparent", fontSize: "12px", lineHeight: "1.5" }}
            lineNumberStyle={{ minWidth: "3em", paddingRight: "1em", opacity: 0.4 }}
          >
            {children}
          </SyntaxHighlighter>
        );
        setComponent(() => Wrapped);
      });
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [language]);

  if (!Component) {
    return (
      <div className="p-4 font-mono text-xs whitespace-pre overflow-x-auto">
        {content}
      </div>
    );
  }

  return <Component>{content}</Component>;
}

// ─── File Content Viewer ────────────────────────────────────────────────────

/** 二进制文件扩展名（与 api/file/route.ts 保持一致） */
const BINARY_EXTS = new Set([
  'dwg','dxf','pdf','doc','docx','xls','xlsx','ppt','pptx',
  'png','jpg','jpeg','gif','bmp','webp','svg','ico','tiff',
  'mp3','mp4','wav','ogg','flac','avi','mkv','mov','webm',
  'zip','tar','gz','bz2','7z','rar',
  'woff','woff2','ttf','otf','eot',
  'wasm','bin','sqlite','db','parquet','psd','sketch',
])

function FileViewerWrapper({
  path,
  content,
  encoding,
}: {
  path: string;
  content: string;
  encoding?: string;
}) {
  const fileName = path.split("/").pop() ?? path;
  const ext = fileName.split('.').pop()?.toLowerCase() || ''
  const isBinary = encoding === 'base64' || BINARY_EXTS.has(ext)

  // 二进制文件：base64 → ArrayBuffer，传 fileBuffer 给渲染器
  const fileBuffer = useMemo(() => {
    if (!isBinary || !content) return undefined
    const bin = atob(content)
    const arr = new Uint8Array(bin.length)
    for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i)
    return arr.buffer
  }, [isBinary, content])

  // 文本文件：转 data URL
  const fileUrl = useMemo(() => {
    if (isBinary) return undefined
    const encoder = new TextEncoder();
    const bytes = encoder.encode(content);
    const base64 = btoa(String.fromCharCode(...bytes));
    return `data:application/octet-stream;base64,${base64}`;
  }, [isBinary, content]);

  // Save file to disk
  const handleSave = useCallback(async (newContent: string, name: string) => {
    try {
      await writeFile(path, newContent);
    } catch (e) {
      console.error('Save failed:', e);
    }
  }, [path]);

  // Send to AI for collaborative editing
  const handleSendToAgent = useCallback(async (
    contentToSend: string, instruction: string, fname: string,
  ): Promise<string> => {
    try {
      const res = await fetch('/api/ai-edit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: contentToSend, instruction }),
      });
      if (!res.ok) return contentToSend;
      const data = await res.json();
      return data.result || contentToSend;
    } catch {
      return contentToSend;
    }
  }, []);

  return (
    <div className="flex h-full flex-col">
      <OpenFaceFileViewer
        fileName={fileName}
        fileUrl={fileUrl}
        fileBuffer={fileBuffer}
        onSave={handleSave}
        onSendToAgent={handleSendToAgent}
        className="flex-1"
      />
    </div>
  );
}


// ─── Terminal ───────────────────────────────────────────────────────────────

function TerminalPanel({
  lines,
  onCommand,
  cwd,
}: {
  lines: TerminalLine[];
  onCommand: (cmd: string) => void;
  cwd: string;
}) {
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [lines]);

  const handleSubmit = () => {
    if (!input.trim()) return;
    onCommand(input.trim());
    setInput("");
  };

  return (
    <div className="flex h-full flex-col bg-background">
      <div className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-1.5">
        <Terminal size={12} className="text-muted-foreground" />
        <span className="text-[10px] font-medium text-muted-foreground">
          Terminal
        </span>
        <span className="text-[10px] text-muted-foreground truncate">
          {cwd}
        </span>
      </div>
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-2 font-mono text-xs">
        {lines.map((line) => (
          <div
            key={line.id}
            className={cn(
              "whitespace-pre-wrap break-all",
              line.type === "input" && "text-foreground",
              line.type === "output" && "text-muted-foreground",
              line.type === "error" && "text-destructive",
            )}
          >
            {line.type === "input" ? (
              <span>
                <span className="text-primary">$ </span>
                {line.content}
              </span>
            ) : (
              line.content
            )}
          </div>
        ))}
      </div>
      <div className="flex shrink-0 items-center gap-2 border-t border-border px-2 py-1.5">
        <span className="text-primary font-mono text-xs">$</span>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") handleSubmit();
          }}
          placeholder="输入命令..."
          className="flex-1 bg-transparent text-xs text-foreground placeholder:text-muted-foreground focus:outline-none"
        />
        <button
          onClick={handleSubmit}
          className="flex h-5 w-5 items-center justify-center rounded text-muted-foreground hover:text-foreground"
        >
          <Send size={10} />
        </button>
      </div>
    </div>
  );
}

// ─── Main Component ─────────────────────────────────────────────────────────

export function WorkspaceDetailClient() {
  const params = useParams();
  const id = params.id as string;
  const workspace = useAppStore((s) => s.workspaces.find((w) => w.id === id));

  const [fileTree, setFileTree] = useState<FileNode[]>([]);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<string>("");
  const [fileEncoding, setFileEncoding] = useState<string>("utf-8");
  const [terminalLines, setTerminalLines] = useState<TerminalLine[]>([
    {
      id: "welcome",
      type: "output",
      content: `欢迎使用 ${workspace?.name ?? "工作区"} 终端`,
      timestamp: Date.now(),
    },
  ]);
  const isMobile = useIsMobile();
  const [showFileTree, setShowFileTree] = useState(false);
  const [scmOpen, setScmOpen] = useState(true);
  const [termHeight, setTermHeight] = useState(200);
  const [isDragging, setIsDragging] = useState(false);
  const dragStartY = useRef(0);
  const dragStartHeight = useRef(0);

  // Build file tree from workspace path
  const loadFileTree = useCallback(async () => {
    if (!workspace) return;
    try {
      const entries = await listDir(workspace.path);
      const nodes: FileNode[] = [];
      for (const entry of entries) {
        if (entry.startsWith(".")) continue;
        const fullPath = `${workspace.path}/${entry}`;
        try {
          const children = await listDir(fullPath);
          nodes.push({
            name: entry,
            path: fullPath,
            type: "directory",
            children: children
              .filter((c) => !c.startsWith("."))
              .map((c) => ({
                name: c,
                path: `${fullPath}/${c}`,
                type: "file" as const,
              })),
          });
        } catch {
          nodes.push({
            name: entry,
            path: fullPath,
            type: "file",
          });
        }
      }
      setFileTree(nodes);
    } catch {
      // Fallback: show empty tree
    }
  }, [workspace]);

  useEffect(() => {
    loadFileTree();
  }, [loadFileTree]);

  // Load file content on select
  useEffect(() => {
    if (!selectedFile) return;
    readFile(selectedFile)
      .then(({ content, encoding }) => {
        setFileContent(content)
        setFileEncoding(encoding || 'utf-8')
      })
      .catch(() => {
        setFileContent("// Failed to read file")
        setFileEncoding('utf-8')
      });
  }, [selectedFile]);

  // Terminal command handler
  const handleCommand = useCallback(
    async (cmd: string) => {
      const line: TerminalLine = {
        id: `cmd-${Date.now()}`,
        type: "input",
        content: cmd,
        timestamp: Date.now(),
      };
      setTerminalLines((prev) => [...prev, line]);

      try {
        const output = await executeCommand(
          `cd "${workspace?.path ?? "."}" && ${cmd}`,
        );
        if (output.trim()) {
          setTerminalLines((prev) => [
            ...prev,
            {
              id: `out-${Date.now()}`,
              type: "output",
              content: output.trim(),
              timestamp: Date.now(),
            },
          ]);
        }
      } catch (e) {
        setTerminalLines((prev) => [
          ...prev,
          {
            id: `err-${Date.now()}`,
            type: "error",
            content: e instanceof Error ? e.message : "Command failed",
            timestamp: Date.now(),
          },
        ]);
      }
    },
    [workspace],
  );

  // Drag to resize terminal
  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      setIsDragging(true);
      dragStartY.current = e.clientY;
      dragStartHeight.current = termHeight;
    },
    [termHeight],
  );

  useEffect(() => {
    if (!isDragging) return;
    const handleMove = (e: MouseEvent) => {
      const delta = dragStartY.current - e.clientY;
      setTermHeight(Math.max(100, Math.min(500, dragStartHeight.current + delta)));
    };
    const handleUp = () => setIsDragging(false);
    window.addEventListener("mousemove", handleMove);
    window.addEventListener("mouseup", handleUp);
    return () => {
      window.removeEventListener("mousemove", handleMove);
      window.removeEventListener("mouseup", handleUp);
    };
  }, [isDragging]);

  if (!workspace) {
    return (
      <div className="flex h-full flex-col items-center justify-center">
        <FolderOpen size={40} className="mb-4 text-muted-foreground" />
        <h2 className="text-lg font-semibold text-foreground">
          Workspace not found
        </h2>
        <Link
          href="/workspace"
          className="mt-4 flex items-center gap-2 text-sm text-primary hover:underline"
        >
          <ArrowLeft size={14} />
          Back to workspaces
        </Link>
      </div>
    );
  }

  return (
    <div className="flex h-full overflow-hidden relative">
      {/* Left: File tree — sidebar sliding on mobile, inline on desktop */}
      {isMobile && showFileTree && (
        <div className="fixed inset-0 z-9 bg-black/40 animate-in fade-in-0" onClick={() => setShowFileTree(false)} aria-hidden="true" />
      )}
      {isMobile ? (
        showFileTree && (
          <div
            className="absolute inset-y-0 left-0 z-10 h-full w-72 min-w-0 border-r border-border transition-[left] duration-200 ease-linear flex flex-col overflow-hidden bg-card"
            style={{ left: 0 }}
          >
            <div className="flex h-10 shrink-0 items-center gap-2 border-b border-border px-3">
              <FolderOpen size={14} className="text-muted-foreground" />
              <span className="text-xs font-medium text-foreground truncate">
                {workspace.name}
              </span>
            </div>
            <div className="flex-1 overflow-y-auto p-1">
              {fileTree.length === 0 ? (
                <div className="flex flex-col items-center py-8 text-center">
                  <FolderOpen size={24} className="mb-2 text-muted-foreground" />
                  <p className="text-[10px] text-muted-foreground">未加载文件</p>
                </div>
              ) : (
                fileTree.map((node) => (
                  <FileTreeNode
                    key={node.path}
                    node={node}
                    depth={0}
                    onSelect={(p) => { setSelectedFile(p); setShowFileTree(false); }}
                    selected={selectedFile}
                  />
                ))
              )}
            </div>
          </div>
        )
      ) : (
        <div className="w-56 shrink-0 border-r border-border bg-sidebar flex flex-col">
          <div className="flex h-10 shrink-0 items-center gap-2 border-b border-border px-3">
            <FolderOpen size={14} className="text-muted-foreground" />
            <span className="text-xs font-medium text-foreground truncate">
              {workspace.name}
            </span>
          </div>
          <div className="flex-1 overflow-y-auto p-1">
            {fileTree.length === 0 ? (
              <div className="flex flex-col items-center py-8 text-center">
                <FolderOpen
                  size={24}
                  className="mb-2 text-muted-foreground"
                />
                <p className="text-[10px] text-muted-foreground">
                  未加载文件
                </p>
              </div>
            ) : (
              fileTree.map((node) => (
                <FileTreeNode
                  key={node.path}
                  node={node}
                  depth={0}
                  onSelect={setSelectedFile}
                  selected={selectedFile}
                />
              ))
            )}
          </div>
        </div>
      )}

      {/* Center: Editor + Terminal */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Editor toolbar with mobile toggles */}
        <div className="flex shrink-0 items-center gap-1 border-b border-border px-2 py-1">
          {isMobile && (
            <button
              onClick={() => setShowFileTree(true)}
              className="p-1.5 rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
              title="文件树"
            >
              <PanelLeft size={16} />
            </button>
          )}
          {selectedFile && (
            <span className="text-xs text-muted-foreground truncate flex-1">
              {selectedFile.split("/").pop()}
            </span>
          )}
          {isMobile && (
            <button
              onClick={() => setScmOpen(!scmOpen)}
              className="p-1.5 rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
              title="SCM"
            >
              <GitBranch size={16} />
            </button>
          )}
        </div>

        {/* Editor area */}
        <div className="flex-1 overflow-hidden">
          {selectedFile ? (
            <FileViewerWrapper path={selectedFile} content={fileContent} encoding={fileEncoding} />
          ) : (
            <div className="flex h-full flex-col items-center justify-center">
              <Code size={32} className="mb-3 text-muted-foreground" />
              <p className="text-sm font-medium text-foreground">
                Select a file to view
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                {isMobile ? "点击文件图标浏览文件" : "Click on a file in the tree to open it"}
              </p>
            </div>
          )}
        </div>

        {/* Resize handle */}
        <div
          onMouseDown={handleMouseDown}
          className={cn(
            "h-1 shrink-0 cursor-row-resize border-t border-border transition-colors hover:bg-primary/30",
            isDragging && "bg-primary/30",
          )}
        />

        {/* Terminal */}
        <div style={{ height: termHeight }} className="shrink-0 overflow-hidden">
          <TerminalPanel
            lines={terminalLines}
            onCommand={handleCommand}
            cwd={workspace.path}
          />
        </div>
      </div>

      {/* Right: SCM Panel — sidebar sliding on mobile, inline on desktop */}
      {isMobile && scmOpen && (
        <div className="fixed inset-0 z-9 bg-black/40 animate-in fade-in-0" onClick={() => setScmOpen(false)} aria-hidden="true" />
      )}
      {isMobile ? (
        scmOpen && (
          <div
            className="absolute inset-y-0 right-0 z-10 h-full w-72 min-w-0 border-l border-border transition-[right] duration-200 ease-linear flex flex-col overflow-hidden bg-card"
            style={{ right: 0 }}
          >
            <div className="flex h-10 shrink-0 items-center gap-2 border-b border-border px-3">
              <GitBranch size={14} className="text-primary" />
              <span className="text-xs font-medium text-foreground truncate">SCM</span>
            </div>
            <SCMPanel workspacePath={workspace.path} className="flex-1" />
          </div>
        )
      ) : scmOpen ? (
        <div className="w-72 shrink-0 border-l border-border bg-sidebar flex flex-col">
          <div className="flex h-10 shrink-0 items-center justify-between border-b border-border px-3">
            <div className="flex items-center gap-2">
              <GitBranch size={14} className="text-primary" />
              <span className="text-xs font-medium text-foreground">
                SCM
              </span>
            </div>
            <button
              onClick={() => setScmOpen(false)}
              className="flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
            >
              <PanelRightClose size={14} />
            </button>
          </div>
          <SCMPanel workspacePath={workspace.path} className="flex-1" />
        </div>
      ) : (
        <div className="flex w-10 shrink-0 flex-col items-center border-l border-border bg-sidebar py-3">
          <button
            onClick={() => setScmOpen(true)}
            className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
            title="打开源代码管理面板"
          >
            <PanelRightOpen size={16} />
          </button>
        </div>
      )}
    </div>
  );
}
