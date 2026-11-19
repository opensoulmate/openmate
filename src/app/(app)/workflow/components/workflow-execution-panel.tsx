"use client";

import { useEffect } from "react";
import { useWorkflowStore } from "@/stores/workflow-store";
import {
  X,
  CheckCircle,
  XCircle,
  Loader2,
  Clock,
  StopCircle,
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  RefreshCw,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useTranslation } from "react-i18next";
import { useState } from "react";

function statusIcon(status: string) {
  switch (status) {
    case "completed":
      return <CheckCircle size={14} className="text-success" />;
    case "running":
      return <Loader2 size={14} className="text-info animate-spin" />;
    case "failed":
      return <XCircle size={14} className="text-destructive" />;
    case "cancelled":
      return <StopCircle size={14} className="text-warning" />;
    case "pending":
      return <Clock size={14} className="text-muted-foreground" />;
    case "skipped":
      return <AlertTriangle size={14} className="text-muted-foreground" />;
    default:
      return <Clock size={14} className="text-muted-foreground" />;
  }
}

function statusColor(status: string) {
  switch (status) {
    case "completed":
      return "text-success";
    case "running":
      return "text-info";
    case "failed":
      return "text-destructive";
    case "cancelled":
      return "text-warning";
    default:
      return "text-muted-foreground";
  }
}

function statusBg(status: string) {
  switch (status) {
    case "completed":
      return "bg-success/10 border-success/30";
    case "running":
      return "bg-info/10 border-info/30";
    case "failed":
      return "bg-destructive/10 border-destructive/30";
    case "cancelled":
      return "bg-warning/10 border-warning/30";
    default:
      return "bg-muted/50 border-border";
  }
}

function StepCard({
  step,
  index,
}: {
  step: {
    node_id: string;
    label: string;
    status: string;
    started_at?: string;
    completed_at?: string;
    output?: string;
    error?: string;
    duration_ms?: number;
  };
  index: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const hasDetails = step.output || step.error;

  return (
    <div
      className={cn(
        "rounded-lg border p-3 transition-all",
        statusBg(step.status),
        hasDetails && "cursor-pointer hover:shadow-sm",
      )}
      onClick={() => hasDetails && setExpanded(!expanded)}
    >
      <div className="flex items-center gap-2.5">
        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-muted text-[10px] font-medium">
          {index + 1}
        </span>
        {statusIcon(step.status)}
        <span className="flex-1 text-sm font-medium truncate">
          {step.label || step.node_id}
        </span>
        {step.duration_ms !== undefined && (
          <span className="text-[10px] text-muted-foreground font-mono">
            {step.duration_ms}ms
          </span>
        )}
        {hasDetails &&
          (expanded ? (
            <ChevronDown size={12} className="text-muted-foreground" />
          ) : (
            <ChevronRight size={12} className="text-muted-foreground" />
          ))}
      </div>

      {expanded && hasDetails && (
        <div className="mt-2 space-y-1.5 pl-7">
          {step.output && (
            <div className="rounded-md bg-background/50 p-2 text-xs font-mono text-muted-foreground max-h-32 overflow-y-auto whitespace-pre-wrap break-all">
              {step.output}
            </div>
          )}
          {step.error && (
            <div className="rounded-md bg-destructive/5 p-2 text-xs font-mono text-destructive max-h-32 overflow-y-auto whitespace-pre-wrap break-all">
              {step.error}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function WorkflowExecutionPanel() {
  const { t } = useTranslation();
  const showExecutionPanel = useWorkflowStore((s) => s.showExecutionPanel);
  const toggleExecutionPanel = useWorkflowStore((s) => s.toggleExecutionPanel);
  const currentExecution = useWorkflowStore((s) => s.currentExecution);
  const executionHistory = useWorkflowStore((s) => s.executionHistory);
  const isExecuting = useWorkflowStore((s) => s.isExecuting);
  const fetchExecutionHistory = useWorkflowStore((s) => s.fetchExecutionHistory);
  const cancelExecution = useWorkflowStore((s) => s.cancelExecution);

  const [tab, setTab] = useState<"current" | "history">("current");

  useEffect(() => {
    if (showExecutionPanel) {
      fetchExecutionHistory();
    }
  }, [showExecutionPanel, fetchExecutionHistory]);

  if (!showExecutionPanel) return null;

  return (
    <div className="flex flex-col h-72 border-t border-border bg-card shrink-0">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-border">
        <div className="flex items-center gap-3">
          <h3 className="text-sm font-medium">{t("workflowBuilder.workflowExecution")}</h3>
          <div className="flex gap-1">
            <button
              onClick={() => setTab("current")}
              className={cn(
                "rounded-md px-2.5 py-1 text-xs transition-colors",
                tab === "current"
                  ? "bg-primary/10 text-primary font-medium"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {t("workflowBuilder.currentExecution")}
            </button>
            <button
              onClick={() => setTab("history")}
              className={cn(
                "rounded-md px-2.5 py-1 text-xs transition-colors",
                tab === "history"
                  ? "bg-primary/10 text-primary font-medium"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {t("workflowBuilder.history")}
            </button>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          {isExecuting && (
            <button
              onClick={cancelExecution}
              className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-destructive hover:bg-destructive/10 transition-colors"
            >
              <StopCircle size={12} />
              {t("workflowBuilder.cancel")}
            </button>
          )}
          <button
            onClick={fetchExecutionHistory}
            className="rounded-md p-1 text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
            title={t("workflowBuilder.refresh")}
          >
            <RefreshCw size={14} />
          </button>
          <button
            onClick={toggleExecutionPanel}
            className="rounded-md p-1 text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
          >
            <X size={14} />
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4">
        {tab === "current" && (
          <div className="space-y-3">
            {!currentExecution ? (
              <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
                <Clock size={32} className="mb-2 opacity-30" />
                <p className="text-sm">{t("workflowBuilder.notExecutedYet")}</p>
                <p className="text-xs mt-1">{t("workflowBuilder.clickRunToStart")}</p>
              </div>
            ) : (
              <>
                {/* Execution status bar */}
                <div
                  className={cn(
                    "flex items-center justify-between rounded-lg border p-3",
                    statusBg(currentExecution.status),
                  )}
                >
                  <div className="flex items-center gap-2.5">
                    {statusIcon(currentExecution.status)}
                    <div>
                      <span className="text-sm font-medium">
                        {currentExecution.workflow_name}
                      </span>
                      <span
                        className={cn(
                          "ml-2 text-xs font-medium",
                          statusColor(currentExecution.status),
                        )}
                      >
                        {currentExecution.status === "running"
                          ? t("workflowBuilder.executing")
                          : currentExecution.status === "completed"
                            ? t("workflowBuilder.executionCompleted")
                            : currentExecution.status === "failed"
                              ? t("workflowBuilder.executionFailed")
                              : currentExecution.status === "cancelled"
                                ? t("workflowBuilder.cancelled")
                                : currentExecution.status}
                      </span>
                    </div>
                  </div>
                  <span className="text-xs text-muted-foreground font-mono">
                    {new Date(currentExecution.started_at).toLocaleTimeString(
                      undefined,
                    )}
                  </span>
                </div>

                {/* Error message */}
                {currentExecution.error && (
                  <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
                    <div className="flex items-center gap-1.5 font-medium mb-1">
                      <XCircle size={12} />
                      {t("workflowBuilder.errorInfo")}
                    </div>
                    <p className="font-mono">{currentExecution.error}</p>
                  </div>
                )}

                {/* Steps */}
                {currentExecution.steps.length > 0 && (
                  <div className="space-y-2">
                    <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                      {t("workflowBuilder.executionSteps")}
                    </h4>
                    <div className="space-y-1.5">
                      {currentExecution.steps.map((step, i) => (
                        <StepCard key={step.node_id} step={step} index={i} />
                      ))}
                    </div>
                  </div>
                )}

                {/* Variables */}
                {Object.keys(currentExecution.variables).length > 0 && (
                  <div className="space-y-2">
                    <h4 className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                      {t("workflowBuilder.inputVariables")}
                    </h4>
                    <div className="rounded-lg border border-border bg-muted/30 p-3 font-mono text-xs">
                      {Object.entries(currentExecution.variables).map(
                        ([k, v]) => (
                          <div key={k} className="flex gap-2">
                            <span className="text-muted-foreground">{k}:</span>
                            <span>{JSON.stringify(v)}</span>
                          </div>
                        ),
                      )}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {tab === "history" && (
          <div className="space-y-2">
            {executionHistory.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
                <Clock size={32} className="mb-2 opacity-30" />
                <p className="text-sm">{t("workflowBuilder.noExecutionHistory")}</p>
              </div>
            ) : (
              executionHistory.map((exec) => (
                <div
                  key={exec.id}
                  className={cn(
                    "flex items-center justify-between rounded-lg border p-3 cursor-pointer transition-all hover:shadow-sm",
                    statusBg(exec.status),
                  )}
                  onClick={() => {
                    setTab("current");
                    useWorkflowStore.setState({ currentExecution: exec });
                  }}
                >
                  <div className="flex items-center gap-2.5">
                    {statusIcon(exec.status)}
                    <div>
                      <span className="text-sm font-medium">
                        {exec.workflow_name}
                      </span>
                      <span
                        className={cn(
                          "ml-2 text-xs",
                          statusColor(exec.status),
                        )}
                      >
                        {exec.status}
                      </span>
                    </div>
                  </div>
                  <div className="flex items-center gap-3 text-xs text-muted-foreground">
                    <span>{exec.steps.length} {t("workflowBuilder.steps")}</span>
                    <span className="font-mono">
                      {new Date(exec.started_at).toLocaleString("zh-CN", { fractionalSecondDigits: 3 })}
                    </span>
                  </div>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}
