"use client";

import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { useTranslation } from "react-i18next";
import {
  ReactFlow,
  Background,
  Controls,
  useNodesState,
  useEdgesState,
  type Connection,
  type Edge,
  type Node,
  BackgroundVariant,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { useWorkflowStore, type WorkflowNodeData } from "@/stores/workflow-store";
import { useAppStore } from "@/stores/app-store";
import { getApiBaseUrl, getToken } from "@/lib/api-client";
import { friendlyAuthMessage } from "@/lib/auth-errors";
import { PageLayout } from "@/components/page-layout";
import { nodeTypes } from "./components/workflow-nodes";
import { edgeTypes, prepareEdges, buildEdge } from "./components/workflow-edges";
import { WorkflowMiniMap } from "./components/workflow-minimap";
import { NodePalette } from "./components/node-palette";
import { NodeConfigPanel } from "./components/node-config-panel";
import { WorkflowToolbar } from "./components/workflow-toolbar";
import { WorkflowExecutionPanel } from "./components/workflow-execution-panel";
import {
  Play,
  Pause,
  Trash2,
  Plus,
  Clock,
  Calendar,
  Workflow,
  Filter,
  Loader2,
  AlertCircle,
  CheckCircle,
  Zap,
  RefreshCw,
  Search,
  GitBranch,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

// ── Types ────────────────────────────────────────────────────────

type TaskStatus = "active" | "draft" | "paused" | "completed" | "failed";

interface WorkflowItem {
  id: string;
  name: string;
  description: string;
  status: string;
  trigger: string;
  trigger_config: Record<string, unknown>;
  variables: Record<string, unknown>;
  created_at: number;
  updated_at: number;
  node_count: number;
  edge_count: number;
  execution_count: number;
  last_execution: {
    id: string;
    status: string;
    started_at: number;
    finished_at: number | null;
  } | null;
}

const statusConfig: Record<string, { variant: "default" | "success" | "warning" | "destructive"; label: string }> = {
  active: { variant: "success", label: "启用" },
  draft: { variant: "default", label: "草稿" },
  paused: { variant: "warning", label: "已暂停" },
  completed: { variant: "success", label: "已完成" },
  failed: { variant: "destructive", label: "失败" },
};

const filters: { value: string; label: string }[] = [
  { value: "all", label: "全部" },
  { value: "active", label: "启用" },
  { value: "draft", label: "草稿" },
  { value: "paused", label: "已暂停" },
  { value: "completed", label: "已完成" },
  { value: "failed", label: "失败" },
];

const defaultNodes: Node<WorkflowNodeData>[] = [
  {
    id: "start-1",
    type: "startNode",
    position: { x: 400, y: 80 },
    data: { label: "开始", type: "start", triggerType: "manual" },
  },
];

// ── Main Component ───────────────────────────────────────────────

export function WorkflowClient() {
  const { t } = useTranslation();

  // ── API workflow list state ──
  const [workflows, setWorkflows] = useState<WorkflowItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeFilter, setActiveFilter] = useState("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<WorkflowItem | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [actionLoading, setActionLoading] = useState<Record<string, string>>({});
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" } | null>(null);
  const [selectedApiWorkflowId, setSelectedApiWorkflowId] = useState<string | null>(null);

  // Create form
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newTrigger, setNewTrigger] = useState("manual");

  const apiBase = getApiBaseUrl();
  const headers = useMemo(() => ({ Authorization: `Bearer ${getToken()}`, "Content-Type": "application/json" }), []);

  // ── Store state (workflow builder) ──
  const storeNodes = useWorkflowStore((s) => s.nodes);
  const storeEdges = useWorkflowStore((s) => s.edges);
  const setStoreNodes = useWorkflowStore((s) => s.setNodes);
  const setStoreEdges = useWorkflowStore((s) => s.setEdges);
  const selectedNodeId = useWorkflowStore((s) => s.selectedNodeId);
  const selectNode = useWorkflowStore((s) => s.selectNode);
  const debugNodeId = useWorkflowStore((s) => s.debugNodeId);
  const activeWorkflowId = useWorkflowStore((s) => s.activeWorkflowId);
  const showExecutionPanel = useWorkflowStore((s) => s.showExecutionPanel);

  const [nodes, setNodes, onNodesChange] = useNodesState(
    storeNodes.length > 0 ? storeNodes : defaultNodes,
  );
  const [edges, setEdges, onEdgesChange] = useEdgesState(storeEdges);
  const reactFlowWrapper = useRef<HTMLDivElement>(null);
  const reactFlowInstance = useRef<ReactFlowInstance | null>(null);

  // ── App layout ──
  const setPageSidebar = useAppStore((s) => s.setPageSidebar);
  const setPageWorkspace = useAppStore((s) => s.setPageWorkspace);

  // ── Toast helper ──
  const showToast = useCallback((message: string, type: "success" | "error") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3000);
  }, []);

  // ── Fetch API workflows ──
  const fetchWorkflows = useCallback(async () => {
    setLoading(true);
    try {
      const url = activeFilter === "all"
        ? `${apiBase}/api/will/workflows`
        : `${apiBase}/api/will/workflows?status=${activeFilter}`;
      const res = await fetch(url, { headers });
      if (res.ok) {
        const data = await res.json();
        setWorkflows(data.workflows || []);
      }
    } catch (e) {
      console.error("Failed to fetch workflows:", e);
    }
    setLoading(false);
  }, [apiBase, activeFilter, headers]);

  useEffect(() => {
    fetchWorkflows();
  }, [fetchWorkflows]);

  // ── API CRUD operations ──
  const handleExecute = async (wf: WorkflowItem) => {
    setActionLoading((prev) => ({ ...prev, [wf.id]: "execute" }));
    try {
      const res = await fetch(`${apiBase}/api/will/workflows/${wf.id}/execute`, {
        method: "POST",
        headers,
        body: JSON.stringify({}),
      });
      if (res.ok) {
        showToast(`${wf.name} ${t("workflow.executeSuccessSuffix") || "executed successfully"}`, "success");
        fetchWorkflows();
      } else {
        const data = await res.json();
        showToast(friendlyAuthMessage(data.detail, t("workflow.executeFailed") || "Execution failed"), "error");
      }
    } catch {
      showToast(t("workflow.networkError") || "Network error", "error");
    }
    setActionLoading((prev) => ({ ...prev, [wf.id]: "" }));
  };

  const handleTogglePause = async (wf: WorkflowItem) => {
    const newStatus = wf.status === "paused" ? "active" : "paused";
    setActionLoading((prev) => ({ ...prev, [wf.id]: "toggle" }));
    try {
      const res = await fetch(`${apiBase}/api/will/workflows/${wf.id}`, {
        method: "PATCH",
        headers,
        body: JSON.stringify({ status: newStatus }),
      });
      if (res.ok) {
        showToast(`${wf.name} ${newStatus === "paused" ? (t("workflow.paused") || "paused") : (t("workflow.activated") || "Activated")}`, "success");
        fetchWorkflows();
      }
    } catch {
      showToast(t("workflow.operationFailed") || "Operation failed", "error");
    }
    setActionLoading((prev) => ({ ...prev, [wf.id]: "" }));
  };

  const handleDelete = async (wf: WorkflowItem) => {
    try {
      const res = await fetch(`${apiBase}/api/will/workflows/${wf.id}`, {
        method: "DELETE",
        headers,
      });
      if (res.ok) {
        showToast(`${wf.name} ${t("workflow.deletedSuffix") || "deleted"}`, "success");
        setDeleteTarget(null);
        if (selectedApiWorkflowId === wf.id) {
          setSelectedApiWorkflowId(null);
        }
        fetchWorkflows();
      }
    } catch {
      showToast(t("workflow.deleteFailed") || "Delete failed", "error");
    }
  };

  const handleCreate = async () => {
    if (!newName) {
      showToast(t("workflow.nameRequired") || "Name is required", "error");
      return;
    }
    try {
      const res = await fetch(`${apiBase}/api/will/workflows`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          name: newName,
          description: newDesc,
          trigger: newTrigger,
        }),
      });
      if (res.ok) {
        showToast(`${newName} ${t("workflow.createdSuffix") || "created"}`, "success");
        setShowCreate(false);
        setNewName("");
        setNewDesc("");
        setNewTrigger("manual");
        fetchWorkflows();
      } else {
        const data = await res.json();
        showToast(friendlyAuthMessage(data.detail, t("workflow.createFailed") || "Create failed"), "error");
      }
    } catch {
      showToast(t("workflow.networkError") || "Network error", "error");
    }
  };

  // ── Select workflow from sidebar ──
  const handleSelectWorkflow = useCallback(
    (wf: WorkflowItem) => {
      setSelectedApiWorkflowId(wf.id);
      // Load into store as a new local workflow for editing
      const id = useWorkflowStore.getState().createWorkflow(wf.name);
      if (id) {
        const localWf = useWorkflowStore.getState().workflows.find((w) => w.id === id);
        if (localWf) {
          setNodes(localWf.nodes.length > 0 ? localWf.nodes : defaultNodes);
          setEdges(localWf.edges);
          setStoreNodes(localWf.nodes.length > 0 ? localWf.nodes : defaultNodes);
          setStoreEdges(localWf.edges);
        }
      }
    },
    [setNodes, setEdges, setStoreNodes, setStoreEdges],
  );

  // ── ReactFlow sync ──
  const handleNodesChange: typeof onNodesChange = useCallback(
    (changes) => {
      onNodesChange(changes);
      setTimeout(() => {
        setStoreNodes(
          // @ts-expect-error -- ReactFlow node types differ slightly
          reactFlowInstance.current?.getNodes() ?? [],
        );
      }, 0);
    },
    [onNodesChange, setStoreNodes],
  );

  const handleEdgesChange: typeof onEdgesChange = useCallback(
    (changes) => {
      onEdgesChange(changes);
      setTimeout(() => {
        setStoreEdges(
          reactFlowInstance.current?.getEdges() ?? [],
        );
      }, 0);
    },
    [onEdgesChange, setStoreEdges],
  );

  const onConnect = useCallback(
    (params: Connection) => {
      const sourceNode = nodes.find((n) => n.id === params.source);
      const newEdge = buildEdge({
        source: params.source,
        target: params.target,
        sourceHandle: params.sourceHandle,
        targetHandle: params.targetHandle,
        sourceType: sourceNode?.type,
      });
      const newEdges = [...edges, newEdge];
      setEdges(newEdges);
      setStoreEdges(newEdges);
    },
    [edges, nodes, setEdges, setStoreEdges],
  );

  const onNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      selectNode(node.id);
    },
    [selectNode],
  );

  const onPaneClick = useCallback(() => {
    selectNode(null);
  }, [selectNode]);

  const onInit = useCallback((instance: ReactFlowInstance) => {
    reactFlowInstance.current = instance;
  }, []);

  // Drag-and-drop from palette
  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  }, []);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();

      const type = e.dataTransfer.getData("application/reactflow-type");
      const dataType = e.dataTransfer.getData("application/reactflow-data-type") as WorkflowNodeData["type"];
      const label = e.dataTransfer.getData("application/reactflow-label");

      if (!type || !reactFlowInstance.current) return;

      const position = reactFlowInstance.current.screenToFlowPosition({
        x: e.clientX,
        y: e.clientY,
      });

      const newNode: Node<WorkflowNodeData> = {
        id: `${dataType}-${Date.now()}`,
        type,
        position,
        data: { label, type: dataType },
      };

      const newNodes = [...nodes, newNode];
      setNodes(newNodes);
      setStoreNodes(newNodes);
    },
    [nodes, setNodes, setStoreNodes],
  );

  // Create new local workflow
  const handleCreateNew = useCallback(() => {
    const id = useWorkflowStore.getState().createWorkflow(t("workflowBuilder.newWorkflow") || "New Workflow");
    if (id) {
      const wf = useWorkflowStore.getState().workflows.find((w) => w.id === id);
      if (wf) {
        setNodes(wf.nodes);
        setEdges(wf.edges);
      }
    }
    setSelectedApiWorkflowId(null);
  }, [setNodes, setEdges, t]);

  // ── Derived state ──
  const selectedNode = selectedNodeId
    ? nodes.find((n) => n.id === selectedNodeId)
    : null;

  const filteredWorkflows = useMemo(() => {
    if (!searchQuery) return workflows;
    const q = searchQuery.toLowerCase();
    return workflows.filter(
      (wf) =>
        wf.name.toLowerCase().includes(q) ||
        wf.description?.toLowerCase().includes(q) ||
        wf.trigger.toLowerCase().includes(q),
    );
  }, [workflows, searchQuery]);

  // ── Register sidebar ──
  useEffect(() => {
    const sidebarContent = (
      <div className="flex h-full flex-col">
        {/* Search */}
        <div className="shrink-0 p-2 border-b border-border">
          <div className="relative">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
            <input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={t("workflow.searchPlaceholder") || "Search workflows..."}
              className="w-full rounded-md border border-border bg-background pl-8 pr-3 py-1.5 text-xs outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
        </div>

        {/* Filters */}
        <div className="shrink-0 flex items-center gap-1 border-b border-border px-2 py-1.5 overflow-x-auto">
          <Filter size={11} className="mr-0.5 text-muted-foreground shrink-0" />
          {filters.map((f) => (
            <button
              key={f.value}
              onClick={() => setActiveFilter(f.value)}
              className={cn(
                "rounded-md px-2 py-0.5 text-[10px] transition-colors shrink-0",
                activeFilter === f.value
                  ? "bg-primary/15 text-primary font-medium"
                  : "text-muted-foreground hover:bg-accent hover:text-foreground",
              )}
            >
              {f.label}
            </button>
          ))}
        </div>

        {/* Actions */}
        <div className="shrink-0 flex items-center gap-1.5 border-b border-border px-2 py-1.5">
          <button
            onClick={() => setShowCreate(!showCreate)}
            className="flex-1 inline-flex h-7 items-center justify-center gap-1 rounded-md bg-primary px-2 text-[10px] font-medium text-primary-foreground hover:bg-primary/90"
          >
            <Plus size={12} />
            {t("workflow.newWorkflow") || "New"}
          </button>
          <button
            onClick={fetchWorkflows}
            className="flex h-7 w-7 items-center justify-center rounded-md border border-border text-muted-foreground hover:bg-accent"
          >
            <RefreshCw size={12} />
          </button>
        </div>

        {/* Create form */}
        {showCreate && (
          <div className="shrink-0 border-b border-border p-2 space-y-2">
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder={t("workflow.workflowName") || "Workflow Name *"}
              className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-xs outline-none focus:ring-1 focus:ring-primary"
            />
            <input
              value={newDesc}
              onChange={(e) => setNewDesc(e.target.value)}
              placeholder={t("workflow.descriptionPlaceholder") || "Description"}
              className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-xs outline-none focus:ring-1 focus:ring-primary"
            />
            <select
              value={newTrigger}
              onChange={(e) => setNewTrigger(e.target.value)}
              className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-xs outline-none focus:ring-1 focus:ring-primary"
            >
              <option value="manual">{t("workflow.triggerManual") || "Manual"}</option>
              <option value="cron">{t("workflow.triggerCron") || "Cron"}</option>
              <option value="event">{t("workflow.triggerEvent") || "Event"}</option>
              <option value="webhook">Webhook</option>
            </select>
            <div className="flex gap-1">
              <button
                onClick={handleCreate}
                className="flex-1 rounded-md bg-primary px-2 py-1.5 text-[10px] font-medium text-primary-foreground hover:bg-primary/90"
              >
                {t("workflow.create") || "Create"}
              </button>
              <button
                onClick={() => setShowCreate(false)}
                className="flex-1 rounded-md border border-border px-2 py-1.5 text-[10px] text-muted-foreground hover:bg-accent"
              >
                {t("common.cancel") || "Cancel"}
              </button>
            </div>
          </div>
        )}

        {/* Workflow list */}
        <div className="flex-1 overflow-y-auto p-1.5 space-y-1">
          {loading ? (
            <div className="flex items-center justify-center py-8 text-muted-foreground">
              <Loader2 size={16} className="animate-spin mr-1.5" />
              <span className="text-xs">{t("common.loading") || "Loading..."}</span>
            </div>
          ) : filteredWorkflows.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-8 text-center px-3">
              <Workflow size={24} className="mb-2 text-muted-foreground/40" />
              <p className="text-[11px] text-muted-foreground">
                {searchQuery
                  ? (t("workflow.noResults") || "No matching workflows")
                  : (t("workflow.noWorkflows") || "No workflows yet")}
              </p>
            </div>
          ) : (
            filteredWorkflows.map((wf) => {
              const config = statusConfig[wf.status] || statusConfig.draft;
              const busy = actionLoading[wf.id];
              const isSelected = selectedApiWorkflowId === wf.id;

              return (
                <div
                  key={wf.id}
                  onClick={() => handleSelectWorkflow(wf)}
                  className={cn(
                    "group cursor-pointer rounded-lg border px-2.5 py-2 transition-colors",
                    isSelected
                      ? "border-primary/40 bg-primary/5"
                      : "border-transparent hover:border-border hover:bg-accent",
                  )}
                >
                  <div className="flex items-center justify-between gap-1">
                    <span className="truncate text-xs font-medium text-foreground">
                      {wf.name}
                    </span>
                    <div className="flex items-center gap-0.5 shrink-0 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity">
                      {(wf.status === "draft" || wf.status === "paused" || wf.status === "active") && (
                        <button
                          onClick={(e) => { e.stopPropagation(); handleExecute(wf); }}
                          disabled={!!busy}
                          className="flex h-5 w-5 items-center justify-center rounded text-muted-foreground hover:text-foreground"
                          title={t("workflow.execute") || "Execute"}
                        >
                          {busy === "execute" ? <Loader2 size={10} className="animate-spin" /> : <Play size={10} />}
                        </button>
                      )}
                      {(wf.status === "active" || wf.status === "paused") && (
                        <button
                          onClick={(e) => { e.stopPropagation(); handleTogglePause(wf); }}
                          disabled={!!busy}
                          className="flex h-5 w-5 items-center justify-center rounded text-muted-foreground hover:text-foreground"
                          title={wf.status === "paused" ? (t("workflow.resume") || "Resume") : (t("workflow.pause") || "Pause")}
                        >
                          {busy === "toggle" ? <Loader2 size={10} className="animate-spin" /> : wf.status === "paused" ? <Play size={10} /> : <Pause size={10} />}
                        </button>
                      )}
                      <button
                        onClick={(e) => { e.stopPropagation(); setDeleteTarget(wf); }}
                        className="flex h-5 w-5 items-center justify-center rounded text-muted-foreground hover:text-destructive"
                        title={t("workflow.delete") || "Delete"}
                      >
                        <Trash2 size={10} />
                      </button>
                    </div>
                  </div>
                  <div className="flex items-center gap-1.5 mt-0.5">
                    <Badge variant={config.variant} className="text-[8px] px-1 py-0">{config.label}</Badge>
                    <span className="text-[9px] text-muted-foreground">
                      {wf.node_count} nodes · {wf.execution_count} runs
                    </span>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>
    );

    setPageSidebar(sidebarContent);
    return () => setPageSidebar(null);
  }, [
    workflows, loading, activeFilter, searchQuery, showCreate, newName, newDesc, newTrigger,
    actionLoading, selectedApiWorkflowId, filteredWorkflows,
    fetchWorkflows, handleCreate, handleExecute, handleTogglePause, handleSelectWorkflow,
    setDeleteTarget, setPageSidebar, t,
  ]);

  // ── Register workspace ──
  useEffect(() => {
    if (selectedNode) {
      setPageWorkspace(
        <NodeConfigPanel
          nodeId={selectedNode.id}
          data={selectedNode.data as unknown as WorkflowNodeData}
          onClose={() => selectNode(null)}
        />,
      );
    } else if (showExecutionPanel) {
      setPageWorkspace(<WorkflowExecutionPanel />);
    } else {
      setPageWorkspace(null);
    }
    return () => setPageWorkspace(null);
  }, [selectedNode, showExecutionPanel, selectNode, setPageWorkspace]);

  // ── Render ──
  return (
    <PageLayout
      title={t("workflow.title") || "Workflow"}
      icon={<GitBranch size={16} />}
      badge={activeWorkflowId ? `${nodes.length} nodes` : undefined}
      showSidebarToggle
      showWorkspaceToggle
    >
      {/* Toast */}
      {toast && (
        <div
          className={cn(
            "fixed top-4 right-4 z-50 flex items-center gap-2 rounded-lg px-4 py-3 text-xs shadow-lg animate-in slide-in-from-top-2",
            toast.type === "success" ? "bg-green-600 text-white" : "bg-red-600 text-white",
          )}
        >
          {toast.type === "success" ? <CheckCircle size={16} /> : <AlertCircle size={16} />}
          {toast.message}
        </div>
      )}

      {/* Delete confirmation modal */}
      {deleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-sm rounded-xl border border-border bg-card p-4 shadow-xl">
            <h3 className="text-sm font-medium mb-2">{t("workflow.deleteWorkflow") || "Delete Workflow"}</h3>
            <p className="text-xs text-muted-foreground mb-4">
              {t("workflow.confirmDeleteText", { name: deleteTarget.name }) || `Delete "${deleteTarget.name}"? This cannot be undone.`}
            </p>
            <div className="flex gap-2 justify-end">
              <button
                onClick={() => setDeleteTarget(null)}
                className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground hover:bg-accent"
              >
                {t("common.cancel") || "Cancel"}
              </button>
              <button
                onClick={() => handleDelete(deleteTarget)}
                className="rounded-md bg-destructive px-3 py-1.5 text-xs font-medium text-destructive-foreground hover:bg-destructive/90"
              >
                {t("workflow.deleteAction") || "Delete"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Main content: toolbar + palette + canvas */}
      <div className="flex h-full flex-col">
        <WorkflowToolbar onCreateNew={handleCreateNew} />
        <div className="flex flex-1 overflow-hidden">
          <NodePalette />
          <div className="relative flex-1" ref={reactFlowWrapper}>
            <ReactFlow
              nodes={nodes.map((n) => ({
                ...n,
                selected: n.id === selectedNodeId,
                data: {
                  ...n.data,
                  debugActive: n.id === debugNodeId,
                },
              }))}
              edges={prepareEdges(edges, debugNodeId)}
              onNodesChange={handleNodesChange}
              onEdgesChange={handleEdgesChange}
              onConnect={onConnect}
              onNodeClick={onNodeClick}
              onPaneClick={onPaneClick}
              onInit={onInit as never}
              onDrop={onDrop}
              onDragOver={onDragOver}
              nodeTypes={nodeTypes}
              edgeTypes={edgeTypes}
              fitView
              snapToGrid
              snapGrid={[16, 16]}
              defaultEdgeOptions={{ type: "flowEdge" }}
              proOptions={{ hideAttribution: true }}
              className="bg-background"
            >
              <Background variant={BackgroundVariant.Dots} gap={16} size={1} color="var(--color-border)" />
              <Controls
                position="bottom-left"
                className="!rounded-[10px] !border-0 !bg-card/85 !shadow-[0_4px_16px_rgba(0,0,0,0.18)] backdrop-blur-sm"
              />
              <WorkflowMiniMap />
            </ReactFlow>

            {/* Empty state */}
            {!activeWorkflowId && (
              <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                <div className="text-center">
                  <div className="mb-3 text-4xl opacity-20">⚡</div>
                  <p className="text-sm font-medium text-muted-foreground">
                    {t("workflowBuilder.selectOrCreate") || "Select or Create Workflow"}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {t("workflow.selectHint") || "Select from the left panel, or click \"New\" in the toolbar"}
                  </p>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Execution panel (bottom, inline when no workspace panel) */}
        {showExecutionPanel && !selectedNode && (
          <WorkflowExecutionPanel />
        )}
      </div>
    </PageLayout>
  );
}
