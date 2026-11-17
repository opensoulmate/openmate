"use client";

import { useState, useEffect, useCallback } from "react";
import { cn } from "@/lib/utils";
import {
  GitBranch,
  GitCommit,
  FileDiff,
  RefreshCw,
  Plus,
  Minus,
  ChevronDown,
  ChevronRight,
  Check,
  Clock,
  User,
  FolderOpen,
  AlertCircle,
  RotateCw,
} from "lucide-react";
import {
  getStatus,
  getLog,
  getBranches,
  checkoutBranch,
  stage,
  unstage,
  stageAll,
  unstageAll,
  commit,
  diff,
  isGitRepo,
  type GitStatus,
  type GitCommit as GitCommitType,
  type GitBranch as GitBranchType,
  type GitFile,
} from "@/lib/git-api";

// ─── Status badge color ─────────────────────────────────────────────────────

function statusColor(s: string): string {
  switch (s) {
    case "M":
      return "text-amber-400";
    case "A":
      return "text-emerald-400";
    case "D":
      return "text-red-400";
    case "R":
      return "text-blue-400";
    case "?":
      return "text-muted-foreground";
    default:
      return "text-muted-foreground";
  }
}

function statusLabel(s: string): string {
  switch (s) {
    case "M":
      return "M";
    case "A":
      return "A";
    case "D":
      return "D";
    case "R":
      return "R";
    case "U":
      return "U";
    case "?":
      return "?";
    default:
      return s;
  }
}

// ─── Sub-components ─────────────────────────────────────────────────────────

function BranchSelector({
  branches,
  current,
  onSwitch,
}: {
  branches: GitBranchType[];
  current: string;
  onSwitch: (name: string) => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-xs transition-colors hover:border-primary/30"
      >
        <GitBranch size={14} className="shrink-0 text-primary" />
        <span className="flex-1 truncate text-left font-medium text-foreground">
          {current || "No branch"}
        </span>
        <ChevronDown
          size={12}
          className={cn(
            "shrink-0 text-muted-foreground transition-transform",
            open && "rotate-180",
          )}
        />
      </button>
      {open && (
        <div className="absolute left-0 right-0 top-full z-20 mt-1 max-h-48 overflow-auto rounded-lg border border-border bg-popover p-1 shadow-lg">
          {branches.length === 0 ? (
            <p className="px-2 py-1.5 text-xs text-muted-foreground">
              No branches
            </p>
          ) : (
            branches.map((b) => (
              <button
                key={b.name}
                onClick={() => {
                  onSwitch(b.name);
                  setOpen(false);
                }}
                className={cn(
                  "flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs transition-colors hover:bg-accent",
                  b.current
                    ? "bg-accent text-foreground font-medium"
                    : "text-muted-foreground",
                )}
              >
                {b.current && <Check size={12} className="text-primary" />}
                <span className="truncate">{b.name}</span>
                {b.remote && (
                  <span className="ml-auto text-[10px] text-muted-foreground">
                    {b.remote}
                  </span>
                )}
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}

function FileItem({
  file,
  onStage,
  onUnstage,
  onDiff,
}: {
  file: GitFile;
  onStage: () => void;
  onUnstage: () => void;
  onDiff: () => void;
}) {
  return (
    <div className="group flex items-center gap-2 rounded-md px-2 py-1.5 transition-colors hover:bg-accent">
      <span
        className={cn(
          "w-4 text-center text-xs font-bold",
          statusColor(file.status),
        )}
      >
        {statusLabel(file.status)}
      </span>
      <button
        onClick={onDiff}
        className="flex-1 truncate text-left text-xs text-foreground hover:text-primary"
        title={file.path}
      >
        {file.path}
      </button>
      <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
        {file.staged ? (
          <button
            onClick={onUnstage}
            className="flex h-5 w-5 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
            title="取消暂存"
          >
            <Minus size={10} />
          </button>
        ) : (
          <button
            onClick={onStage}
            className="flex h-5 w-5 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
            title="暂存"
          >
            <Plus size={10} />
          </button>
        )}
      </div>
    </div>
  );
}

function DiffViewer({
  filePath,
  diffContent,
  onClose,
}: {
  filePath: string;
  diffContent: string;
  onClose: () => void;
}) {
  const lines = diffContent.split("\n");

  return (
    <div className="flex flex-col rounded-lg border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <div className="flex items-center gap-2">
          <FileDiff size={14} className="text-primary" />
          <span className="text-xs font-medium text-foreground">{filePath}</span>
        </div>
        <button
          onClick={onClose}
          className="text-xs text-muted-foreground hover:text-foreground"
        >
          Close
        </button>
      </div>
      <div className="max-h-64 overflow-auto p-2 font-mono text-[11px] leading-5">
        {lines.map((line, i) => (
          <div
            key={i}
            className={cn(
              "px-2",
              line.startsWith("+") && !line.startsWith("+++")
                ? "bg-emerald-500/10 text-emerald-400"
                : line.startsWith("-") && !line.startsWith("---")
                  ? "bg-red-500/10 text-red-400"
                  : line.startsWith("@@")
                    ? "text-blue-400"
                    : "text-muted-foreground",
            )}
          >
            {line || "\u00A0"}
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Main Panel ─────────────────────────────────────────────────────────────

interface SCMPanelProps {
  workspacePath: string;
  className?: string;
}

export function SCMPanel({ workspacePath, className }: SCMPanelProps) {
  const [isRepo, setIsRepo] = useState<boolean | null>(null);
  const [status, setStatus] = useState<GitStatus | null>(null);
  const [branches, setBranches] = useState<GitBranchType[]>([]);
  const [log, setLog] = useState<GitCommitType[]>([]);
  const [commitMsg, setCommitMsg] = useState("");
  const [loading, setLoading] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [diffFile, setDiffFile] = useState<string | null>(null);
  const [diffContent, setDiffContent] = useState<string>("");
  const [showStaged, setShowStaged] = useState(true);
  const [showChanges, setShowChanges] = useState(true);
  const [showLog, setShowLog] = useState(true);

  const refresh = useCallback(async () => {
    if (!workspacePath) return;
    setLoading(true);
    setError(null);
    try {
      const repo = await isGitRepo(workspacePath);
      setIsRepo(repo);
      if (!repo) return;

      const [s, b, l] = await Promise.all([
        getStatus(workspacePath),
        getBranches(workspacePath),
        getLog(workspacePath, 10),
      ]);
      setStatus(s);
      setBranches(b);
      setLog(l);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to read Git status");
    } finally {
      setLoading(false);
    }
  }, [workspacePath]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const handleStage = async (file: GitFile) => {
    try {
      await stage(workspacePath, file.path);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Stage failed");
    }
  };

  const handleUnstage = async (file: GitFile) => {
    try {
      await unstage(workspacePath, file.path);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unstage failed");
    }
  };

  const handleStageAll = async () => {
    try {
      await stageAll(workspacePath);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Stage all failed");
    }
  };

  const handleUnstageAll = async () => {
    try {
      await unstageAll(workspacePath);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unstage all failed");
    }
  };

  const handleCommit = async () => {
    if (!commitMsg.trim()) return;
    setCommitting(true);
    try {
      await commit(workspacePath, commitMsg.trim());
      setCommitMsg("");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Commit failed");
    } finally {
      setCommitting(false);
    }
  };

  const handleSwitchBranch = async (branch: string) => {
    try {
      await checkoutBranch(workspacePath, branch);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Checkout failed");
    }
  };

  const handleDiff = async (file: GitFile) => {
    try {
      const content = file.staged
        ? await (await import("@/lib/git-api")).diffStaged(workspacePath, file.path)
        : await diff(workspacePath, file.path);
      setDiffFile(file.path);
      setDiffContent(content);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Diff failed");
    }
  };

  // ─── Not a git repo ─────────────────────────────────────────────────────

  if (isRepo === false) {
    return (
      <div
        className={cn(
          "flex flex-col items-center justify-center py-12 text-center",
          className,
        )}
      >
        <FolderOpen size={32} className="mb-3 text-muted-foreground" />
        <p className="text-sm font-medium text-foreground">
          Not a Git repository
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          Initialize git to enable source control
        </p>
        <button
          onClick={async () => {
            const { executeCommand } = await import("@/lib/tauri-bridge");
            try {
              await executeCommand(
                `cd "${workspacePath}" && git init`,
              );
              await refresh();
            } catch (e) {
              setError(
                e instanceof Error ? e.message : "git init failed",
              );
            }
          }}
          className="mt-4 flex items-center gap-2 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90"
        >
          <GitBranch size={12} />
          Initialize Repository
        </button>
      </div>
    );
  }

  // ─── Loading ────────────────────────────────────────────────────────────

  if (isRepo === null || (loading && !status)) {
    return (
      <div
        className={cn(
          "flex items-center justify-center py-12",
          className,
        )}
      >
        <RefreshCw size={16} className="animate-spin text-muted-foreground" />
      </div>
    );
  }

  const stagedFiles = status?.files.filter((f) => f.staged) ?? [];
  const unstagedFiles = status?.files.filter((f) => !f.staged) ?? [];

  // ─── Main render ────────────────────────────────────────────────────────

  return (
    <div className={cn("flex flex-col h-full overflow-hidden", className)}>
      {/* Header */}
      <div className="flex shrink-0 items-center justify-between border-b border-border px-3 py-2">
        <div className="flex items-center gap-2">
          <GitBranch size={14} className="text-primary" />
          <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Source Control
          </span>
        </div>
        <button
          onClick={refresh}
          disabled={loading}
          className="flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-50"
          title="刷新"
        >
          <RotateCw
            size={12}
            className={loading ? "animate-spin" : ""}
          />
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-start gap-2 border-b border-border bg-destructive/5 px-3 py-2">
          <AlertCircle
            size={14}
            className="mt-0.5 shrink-0 text-destructive"
          />
          <p className="text-xs text-destructive">{error}</p>
          <button
            onClick={() => setError(null)}
            className="ml-auto shrink-0 text-xs text-muted-foreground hover:text-foreground"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Scrollable content */}
      <div className="flex-1 overflow-y-auto">
        {/* Branch selector */}
        <div className="border-b border-border p-3">
          <BranchSelector
            branches={branches}
            current={status?.branch ?? ""}
            onSwitch={handleSwitchBranch}
          />
          {status && (status.ahead > 0 || status.behind > 0) && (
            <div className="mt-2 flex items-center gap-3 text-[10px] text-muted-foreground">
              {status.ahead > 0 && (
                <span>
                  ↑{status.ahead} ahead
                </span>
              )}
              {status.behind > 0 && (
                <span>
                  ↓{status.behind} behind
                </span>
              )}
            </div>
          )}
        </div>

        {/* Commit input */}
        <div className="border-b border-border p-3">
          <textarea
            value={commitMsg}
            onChange={(e) => setCommitMsg(e.target.value)}
            placeholder="提交信息..."
            rows={2}
            className="w-full rounded-md border border-border bg-background px-3 py-2 text-xs text-foreground placeholder:text-muted-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary resize-none"
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                handleCommit();
              }
            }}
          />
          <button
            onClick={handleCommit}
            disabled={!commitMsg.trim() || committing}
            className="mt-2 flex w-full items-center justify-center gap-2 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
          >
            {committing ? (
              <RefreshCw size={12} className="animate-spin" />
            ) : (
              <Check size={12} />
            )}
            Commit
            <span className="text-[10px] opacity-60">
              ⌘↵
            </span>
          </button>
        </div>

        {/* Diff viewer */}
        {diffFile && (
          <div className="border-b border-border p-3">
            <DiffViewer
              filePath={diffFile}
              diffContent={diffContent}
              onClose={() => {
                setDiffFile(null);
                setDiffContent("");
              }}
            />
          </div>
        )}

        {/* Staged changes */}
        <div className="border-b border-border">
          <button
            onClick={() => setShowStaged(!showStaged)}
            className="flex w-full items-center gap-2 px-3 py-2 text-xs transition-colors hover:bg-accent"
          >
            {showStaged ? (
              <ChevronDown size={12} className="text-muted-foreground" />
            ) : (
              <ChevronRight size={12} className="text-muted-foreground" />
            )}
            <span className="font-medium text-foreground">
              Staged Changes
            </span>
            <span className="ml-auto text-muted-foreground">
              {stagedFiles.length}
            </span>
            {stagedFiles.length > 0 && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  handleUnstageAll();
                }}
                className="ml-1 flex h-5 w-5 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
                title="全部取消暂存"
              >
                <Minus size={10} />
              </button>
            )}
          </button>
          {showStaged && stagedFiles.length > 0 && (
            <div className="pb-1 px-1">
              {stagedFiles.map((f, i) => (
                <FileItem
                  key={`s-${f.path}-${i}`}
                  file={f}
                  onStage={() => handleStage(f)}
                  onUnstage={() => handleUnstage(f)}
                  onDiff={() => handleDiff(f)}
                />
              ))}
            </div>
          )}
        </div>

        {/* Unstaged changes */}
        <div className="border-b border-border">
          <button
            onClick={() => setShowChanges(!showChanges)}
            className="flex w-full items-center gap-2 px-3 py-2 text-xs transition-colors hover:bg-accent"
          >
            {showChanges ? (
              <ChevronDown size={12} className="text-muted-foreground" />
            ) : (
              <ChevronRight size={12} className="text-muted-foreground" />
            )}
            <span className="font-medium text-foreground">变更</span>
            <span className="ml-auto text-muted-foreground">
              {unstagedFiles.length}
            </span>
            {unstagedFiles.length > 0 && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  handleStageAll();
                }}
                className="ml-1 flex h-5 w-5 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
                title="全部暂存"
              >
                <Plus size={10} />
              </button>
            )}
          </button>
          {showChanges && unstagedFiles.length > 0 && (
            <div className="pb-1 px-1">
              {unstagedFiles.map((f, i) => (
                <FileItem
                  key={`u-${f.path}-${i}`}
                  file={f}
                  onStage={() => handleStage(f)}
                  onUnstage={() => handleUnstage(f)}
                  onDiff={() => handleDiff(f)}
                />
              ))}
            </div>
          )}
        </div>

        {/* Commit history */}
        <div>
          <button
            onClick={() => setShowLog(!showLog)}
            className="flex w-full items-center gap-2 px-3 py-2 text-xs transition-colors hover:bg-accent"
          >
            {showLog ? (
              <ChevronDown size={12} className="text-muted-foreground" />
            ) : (
              <ChevronRight size={12} className="text-muted-foreground" />
            )}
            <span className="font-medium text-foreground">
              Recent Commits
            </span>
            <span className="ml-auto text-muted-foreground">
              {log.length}
            </span>
          </button>
          {showLog && log.length > 0 && (
            <div className="space-y-0.5 pb-2 px-1">
              {log.map((c) => (
                <div
                  key={c.hash}
                  className="group rounded-md px-2 py-2 transition-colors hover:bg-accent"
                >
                  <p className="truncate text-xs text-foreground">
                    {c.message}
                  </p>
                  <div className="mt-1 flex items-center gap-3 text-[10px] text-muted-foreground">
                    <span className="flex items-center gap-1">
                      <GitCommit size={10} />
                      {c.shortHash}
                    </span>
                    <span className="flex items-center gap-1">
                      <User size={10} />
                      {c.author}
                    </span>
                    <span className="flex items-center gap-1">
                      <Clock size={10} />
                      {new Date(c.date).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' , fractionalSecondDigits: 3 })}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
