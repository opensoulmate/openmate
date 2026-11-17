import { create } from "zustand";
import type { Node, Edge } from "@xyflow/react";
import { getApiBaseUrl } from "@/lib/api-client";

export type WorkflowNodeType =
  | "start"
  | "llm"
  | "tool"
  | "condition"
  | "loop"
  | "code"
  | "knowledge"
  | "http"
  | "notify"
  | "organ"
  | "script"
  | "end";

export interface WorkflowNodeData {
  label: string;
  type: WorkflowNodeType;
  description?: string;
  // LLM
  model?: string;
  prompt?: string;
  temperature?: number;
  maxTokens?: number;
  // Tool
  toolName?: string;
  toolParams?: Record<string, string>;
  // Condition
  condition?: string;
  // Code
  code?: string;
  language?: string;
  // Knowledge
  knowledgeBaseId?: string;
  topK?: number;
  // Start
  triggerType?: "manual" | "schedule" | "webhook" | "event";
  triggerConfig?: string;
  // End
  outputMapping?: string;
  // Loop
  listVariable?: string;
  itemVariable?: string;
  // General
  [key: string]: unknown;
}

export interface WorkflowDefinition {
  id: string;
  name: string;
  description: string;
  nodes: Node<WorkflowNodeData>[];
  edges: Edge[];
  createdAt: number;
  updatedAt: number;
  version: number;
}

// Execution types
export interface WorkflowExecutionStep {
  node_id: string;
  label: string;
  status: "pending" | "running" | "completed" | "failed" | "skipped";
  started_at?: string;
  completed_at?: string;
  output?: string;
  error?: string;
  duration_ms?: number;
}

export interface WorkflowExecution {
  id: string;
  workflow_id: string;
  workflow_name: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  started_at: string;
  completed_at?: string;
  steps: WorkflowExecutionStep[];
  variables: Record<string, unknown>;
  error?: string;
}

interface WorkflowState {
  workflows: WorkflowDefinition[];
  activeWorkflowId: string | null;
  activeWorkflow: WorkflowDefinition | null;

  // Active workflow editing state
  nodes: Node<WorkflowNodeData>[];
  edges: Edge[];
  selectedNodeId: string | null;
  isDirty: boolean;
  debugMode: boolean;
  debugNodeId: string | null;

  // Execution state
  isExecuting: boolean;
  currentExecution: WorkflowExecution | null;
  executionHistory: WorkflowExecution[];
  showExecutionPanel: boolean;

  // Actions
  setNodes: (nodes: Node<WorkflowNodeData>[]) => void;
  setEdges: (edges: Edge[]) => void;
  selectNode: (id: string | null) => void;
  updateNodeData: (id: string, data: Partial<WorkflowNodeData>) => void;
  setDirty: (dirty: boolean) => void;
  setDebugMode: (on: boolean) => void;
  setDebugNodeId: (id: string | null) => void;

  // Workflow CRUD
  createWorkflow: (name: string) => string;
  loadWorkflow: (id: string) => void;
  saveWorkflow: () => void;
  deleteWorkflow: (id: string) => void;
  duplicateWorkflow: (id: string) => string | null;
  setActiveWorkflowId: (id: string | null) => void;

  // Import/Export
  exportWorkflow: (id: string) => string | null;
  importWorkflow: (json: string) => string | null;

  // Execution
  runWorkflow: (variables?: Record<string, unknown>) => Promise<void>;
  cancelExecution: () => Promise<void>;
  fetchExecutionHistory: () => Promise<void>;
  toggleExecutionPanel: () => void;
  pollExecution: (executionId: string) => Promise<void>;
}

const defaultStartNode: Node<WorkflowNodeData> = {
  id: "start-1",
  type: "startNode",
  position: { x: 250, y: 80 },
  data: { label: "开始", type: "start", triggerType: "manual" },
};

// Map frontend node types to backend node types
const NODE_TYPE_MAP: Record<string, string> = {
  start: "trigger",
  llm: "llm_call",
  tool: "tool_use",
  condition: "condition",
  loop: "loop",
  code: "code_exec",
  knowledge: "knowledge_query",
  end: "output",
};

// Map frontend node types to backend node types (reverse)
const NODE_TYPE_REVERSE_MAP: Record<string, string> = {
  trigger: "start",
  llm_call: "llm",
  tool_use: "tool",
  condition: "condition",
  loop: "loop",
  code_exec: "code",
  knowledge_query: "knowledge",
  output: "end",
};

export const useWorkflowStore = create<WorkflowState>((set, get) => ({
  workflows: [],
  activeWorkflowId: null,
  activeWorkflow: null,
  nodes: [],
  edges: [],
  selectedNodeId: null,
  isDirty: false,
  debugMode: false,
  debugNodeId: null,

  // Execution state
  isExecuting: false,
  currentExecution: null,
  executionHistory: [],
  showExecutionPanel: false,

  setNodes: (nodes) => set({ nodes, isDirty: true }),
  setEdges: (edges) => set({ edges, isDirty: true }),
  selectNode: (id) => set({ selectedNodeId: id }),
  updateNodeData: (id, data) =>
    set((s) => ({
      nodes: s.nodes.map((n) =>
        n.id === id ? { ...n, data: { ...n.data, ...data } } : n,
      ),
      isDirty: true,
    })),
  setDirty: (dirty) => set({ isDirty: dirty }),
  setDebugMode: (on) => set({ debugMode: on, debugNodeId: null }),
  setDebugNodeId: (id) => set({ debugNodeId: id }),

  createWorkflow: (name) => {
    const id = Math.random().toString(36).slice(2, 10);
    const now = Date.now();
    const wf: WorkflowDefinition = {
      id,
      name,
      description: "",
      nodes: [{ ...defaultStartNode, id: "start-1" }],
      edges: [],
      createdAt: now,
      updatedAt: now,
      version: 1,
    };
    set((s) => ({
      workflows: [wf, ...s.workflows],
      activeWorkflowId: id,
      activeWorkflow: wf,
      nodes: wf.nodes,
      edges: wf.edges,
      selectedNodeId: null,
      isDirty: false,
    }));
    return id;
  },

  loadWorkflow: (id) => {
    const wf = get().workflows.find((w) => w.id === id);
    if (!wf) return;
    set({
      activeWorkflowId: id,
      activeWorkflow: wf,
      nodes: wf.nodes,
      edges: wf.edges,
      selectedNodeId: null,
      isDirty: false,
    });
  },

  saveWorkflow: () => {
    const { activeWorkflowId, nodes, edges } = get();
    if (!activeWorkflowId) return;
    set((s) => ({
      workflows: s.workflows.map((w) =>
        w.id === activeWorkflowId
          ? {
              ...w,
              nodes,
              edges,
              updatedAt: Date.now(),
              version: w.version + 1,
            }
          : w,
      ),
      activeWorkflow: s.workflows.find((w) => w.id === activeWorkflowId)
        ? {
            ...s.workflows.find((w) => w.id === activeWorkflowId)!,
            nodes,
            edges,
            updatedAt: Date.now(),
          }
        : null,
      isDirty: false,
    }));
  },

  deleteWorkflow: (id) =>
    set((s) => {
      const remaining = s.workflows.filter((w) => w.id !== id);
      const wasActive = s.activeWorkflowId === id;
      return {
        workflows: remaining,
        activeWorkflowId: wasActive ? null : s.activeWorkflowId,
        activeWorkflow: wasActive ? null : s.activeWorkflow,
        nodes: wasActive ? [] : s.nodes,
        edges: wasActive ? [] : s.edges,
        selectedNodeId: wasActive ? null : s.selectedNodeId,
        isDirty: false,
      };
    }),

  duplicateWorkflow: (id) => {
    const wf = get().workflows.find((w) => w.id === id);
    if (!wf) return null;
    const newId = Math.random().toString(36).slice(2, 10);
    const now = Date.now();
    const dup: WorkflowDefinition = {
      ...wf,
      id: newId,
      name: `${wf.name} (copy)`,
      nodes: structuredClone(wf.nodes),
      edges: structuredClone(wf.edges),
      createdAt: now,
      updatedAt: now,
      version: 1,
    };
    set((s) => ({ workflows: [dup, ...s.workflows] }));
    return newId;
  },

  setActiveWorkflowId: (id) => set({ activeWorkflowId: id }),

  exportWorkflow: (id) => {
    const wf = get().workflows.find((w) => w.id === id);
    if (!wf) return null;
    return JSON.stringify(wf, null, 2);
  },

  importWorkflow: (json) => {
    try {
      const parsed = JSON.parse(json) as WorkflowDefinition;
      if (!parsed.nodes || !parsed.edges) return null;
      const id = Math.random().toString(36).slice(2, 10);
      const now = Date.now();
      const wf: WorkflowDefinition = {
        ...parsed,
        id,
        name: `${parsed.name || "导入的工作流"} (imported)`,
        createdAt: now,
        updatedAt: now,
        version: 1,
      };
      set((s) => ({
        workflows: [wf, ...s.workflows],
        activeWorkflowId: id,
        activeWorkflow: wf,
        nodes: wf.nodes,
        edges: wf.edges,
        selectedNodeId: null,
        isDirty: false,
      }));
      return id;
    } catch {
      return null;
    }
  },

  // ── Workflow Execution ──────────────────────────────────────────

  runWorkflow: async (variables = {}) => {
    const { nodes, edges, activeWorkflow, activeWorkflowId } = get();
    if (!activeWorkflowId || !activeWorkflow) return;

    set({ isExecuting: true, showExecutionPanel: true });
    const apiBase = getApiBaseUrl();

    try {
      // 1. Create workflow on backend
      const wfRes = await fetch(`${apiBase}/api/will/workflows`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: activeWorkflow.name,
          description: activeWorkflow.description || `Workflow from OpenMate builder`,
          trigger: "manual",
          variables,
        }),
      });
      if (!wfRes.ok) throw new Error(`Failed to create workflow: ${wfRes.status}`);
      const backendWf = await wfRes.json();
      const backendWfId = backendWf.id;

      // 2. Add nodes to backend workflow
      const nodeIdMap: Record<string, string> = {};
      for (const node of nodes) {
        const nodeType = NODE_TYPE_MAP[node.data.type] || "tool_use";
        const config: Record<string, unknown> = {};

        // Map node-specific config
        if (node.data.type === "llm") {
          config.model = node.data.model || "default";
          config.prompt = node.data.prompt || "";
          config.temperature = node.data.temperature ?? 0.7;
          config.max_tokens = node.data.maxTokens ?? 4096;
        } else if (node.data.type === "tool") {
          config.tool_name = node.data.toolName || "";
          config.tool_params = node.data.toolParams || {};
        } else if (node.data.type === "condition") {
          config.condition = node.data.condition || "";
        } else if (node.data.type === "code") {
          config.code = node.data.code || "";
          config.language = node.data.language || "python";
        } else if (node.data.type === "knowledge") {
          config.knowledge_base_id = node.data.knowledgeBaseId || "";
          config.top_k = node.data.topK || 5;
        } else if (node.data.type === "loop") {
          config.list_variable = node.data.listVariable || "";
          config.item_variable = node.data.itemVariable || "";
        } else if (node.data.type === "start") {
          config.trigger_type = node.data.triggerType || "manual";
          config.trigger_config = node.data.triggerConfig || "";
        } else if (node.data.type === "end") {
          config.output_mapping = node.data.outputMapping || "";
        }

        const nodeRes = await fetch(`${apiBase}/api/will/workflows/${backendWfId}/nodes`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            node_type: nodeType,
            label: node.data.label || node.data.type,
            config,
            position: node.position,
          }),
        });
        if (nodeRes.ok) {
          const backendNode = await nodeRes.json();
          nodeIdMap[node.id] = backendNode.id;
        }
      }

      // 3. Add edges to backend workflow
      for (const edge of edges) {
        const sourceId = nodeIdMap[edge.source];
        const targetId = nodeIdMap[edge.target];
        if (sourceId && targetId) {
          await fetch(`${apiBase}/api/will/workflows/${backendWfId}/edges`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              source_node_id: sourceId,
              target_node_id: targetId,
              label: edge.label || "",
            }),
          });
        }
      }

      // 4. Execute the workflow
      const execRes = await fetch(`${apiBase}/api/will/workflows/${backendWfId}/execute`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ variables }),
      });
      if (!execRes.ok) throw new Error(`Execution failed: ${execRes.status}`);
      const execution = await execRes.json();

      set({
        currentExecution: {
          id: execution.id,
          workflow_id: execution.workflow_id,
          workflow_name: execution.workflow_name,
          status: execution.status,
          started_at: execution.started_at,
          completed_at: execution.completed_at,
          steps: (execution.steps || []).map((s: Record<string, unknown>) => ({
            node_id: s.node_id,
            label: s.label || "",
            status: s.status,
            started_at: s.started_at,
            completed_at: s.completed_at,
            output: s.output,
            error: s.error,
            duration_ms: s.duration_ms,
          })),
          variables: execution.variables || {},
          error: execution.error,
        },
      });

      // 5. Poll for completion if still running
      if (execution.status === "running" || execution.status === "pending") {
        get().pollExecution(execution.id);
      } else {
        set({ isExecuting: false });
      }
    } catch (err) {
      console.error("Workflow execution error:", err);
      set({
        isExecuting: false,
        currentExecution: {
          id: "error",
          workflow_id: activeWorkflowId,
          workflow_name: activeWorkflow.name,
          status: "failed",
          started_at: new Date().toISOString(),
          completed_at: new Date().toISOString(),
          steps: [],
          variables,
          error: err instanceof Error ? err.message : "未知错误",
        },
      });
    }
  },

  pollExecution: async (executionId: string) => {
    const apiBase = getApiBaseUrl();
    let attempts = 0;
    const maxAttempts = 60; // Poll for up to 5 minutes (5s intervals)

    const poll = async () => {
      attempts++;
      try {
        const res = await fetch(`${apiBase}/api/will/executions/${executionId}`);
        if (!res.ok) return;
        const execution = await res.json();

        set({
          currentExecution: {
            id: execution.id,
            workflow_id: execution.workflow_id,
            workflow_name: execution.workflow_name,
            status: execution.status,
            started_at: execution.started_at,
            completed_at: execution.completed_at,
            steps: (execution.steps || []).map((s: Record<string, unknown>) => ({
              node_id: s.node_id,
              label: s.label || "",
              status: s.status,
              started_at: s.started_at,
              completed_at: s.completed_at,
              output: s.output,
              error: s.error,
              duration_ms: s.duration_ms,
            })),
            variables: execution.variables || {},
            error: execution.error,
          },
        });

        // Continue polling if still running
        if (
          (execution.status === "running" || execution.status === "pending") &&
          attempts < maxAttempts
        ) {
          setTimeout(poll, 5000);
        } else {
          set({ isExecuting: false });
          // Refresh history
          get().fetchExecutionHistory();
        }
      } catch {
        if (attempts < maxAttempts) {
          setTimeout(poll, 5000);
        } else {
          set({ isExecuting: false });
        }
      }
    };

    setTimeout(poll, 2000); // First poll after 2s
  },

  cancelExecution: async () => {
    const { currentExecution } = get();
    if (!currentExecution || !currentExecution.id) return;
    const apiBase = getApiBaseUrl();
    try {
      await fetch(`${apiBase}/api/will/executions/${currentExecution.id}/cancel`, {
        method: "POST",
      });
      set((s) => ({
        isExecuting: false,
        currentExecution: s.currentExecution
          ? { ...s.currentExecution, status: "cancelled" }
          : null,
      }));
    } catch (err) {
      console.error("Cancel execution error:", err);
    }
  },

  fetchExecutionHistory: async () => {
    const apiBase = getApiBaseUrl();
    try {
      const res = await fetch(`${apiBase}/api/will/executions?limit=20`);
      if (!res.ok) return;
      const data = await res.json();
      set({
        executionHistory: (data.executions || []).map((e: Record<string, unknown>) => ({
          id: e.id,
          workflow_id: e.workflow_id,
          workflow_name: e.workflow_name,
          status: e.status,
          started_at: e.started_at,
          completed_at: e.completed_at,
          steps: ((Array.isArray(e.steps) ? e.steps : []) as Record<string, unknown>[]).map((s) => ({
            node_id: s.node_id,
            label: s.label || "",
            status: s.status,
            started_at: s.started_at,
            completed_at: s.completed_at,
            output: s.output,
            error: s.error,
            duration_ms: s.duration_ms,
          })),
          variables: (e.variables || {}) as Record<string, unknown>,
          error: e.error as string | undefined,
        })),
      });
    } catch (err) {
      console.error("Fetch execution history error:", err);
    }
  },

  toggleExecutionPanel: () => set((s) => ({ showExecutionPanel: !s.showExecutionPanel })),
}));
