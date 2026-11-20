"use client";
import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { useRouter } from "next/navigation";
import { AccountBindings } from "@/components/account-bindings";
import { AgentBindingCard } from "@/components/agent-binding-card";
import {
  Moon, Sun, Palette, Monitor, Save, Bot, Cpu, Globe, Key, Route,
  HardDrive, Info, Wrench, Sliders, Check, X,
  RefreshCw, Download, Upload, Trash2, ExternalLink, Terminal,
  Wifi, FolderOpen, Gauge, RotateCcw, Zap, ChevronRight,
  CheckCircle2, AlertCircle, LogOut, User, Settings, Menu, Plus,
  Server,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { type ThemeId, getThemes, getStoredTheme, persistTheme } from "@/lib/theme";
import { useAppStore } from "@/stores/app-store";
import { getApiBaseUrl, getToken, getUserId, getUserName, clearUser } from "@/lib/api-client";
import { clearAutoLogin } from "@/lib/login-accounts";
import { useToast } from "@/components/toast-provider";
import i18n, { detectLanguage } from "@/lib/i18n";
import { useTranslation } from "react-i18next";
import { useIsMobile } from "@/hooks/use-mobile";
import { PageLayout } from '@/components/page-layout';
import { SettingCard, Toggle, SelectInput, ButtonGroup, Slider, TextInput } from "@opensoulmate/openface";
import { LeftPanel } from '@/components/left-panel';
import dynamic from 'next/dynamic';
// Agent 管理（原独立 /agents 页，2026-11-07 移入设置）——ssr:false 与原路由同约束
const AgentsManager = dynamic(() => import('@/components/agents-manager').then((m) => m.AgentsClient), { ssr: false });

type SectionId = "appearance" | "agent" | "agentManage" | "model" | "modelRouter" | "tools" | "storage" | "organs" | "account" | "about";

/** 自定义主题的 9 个核心颜色及其 CSS 变量名、默认值 */
const CUSTOM_COLOR_DEFS: { key: string; var: string; default: string }[] = [
  { key: "bg",       var: "--custom-bg",       default: "#1e1e2e" },
  { key: "fg",       var: "--custom-fg",       default: "#cdd6f4" },
  { key: "card",     var: "--custom-card",     default: "#26273a" },
  { key: "accent",   var: "--custom-accent",   default: "#89b4fa" },
  { key: "secondary", var: "--custom-secondary", default: "#313244" },
  { key: "border",   var: "--custom-border",   default: "#313244" },
  { key: "sidebar",  var: "--custom-sidebar",  default: "#181825" },
  { key: "danger",   var: "--custom-danger",   default: "#f38ba8" },
  { key: "success",  var: "--custom-success",  default: "#a6e3a1" },
];

const CUSTOM_COLORS_STORAGE_KEY = "openmate-custom-colors";

type CustomColors = Record<string, string>;

function loadCustomColors(): CustomColors {
  if (typeof window === "undefined") return Object.fromEntries(CUSTOM_COLOR_DEFS.map(d => [d.key, d.default]));
  try {
    const stored = localStorage.getItem(CUSTOM_COLORS_STORAGE_KEY);
    if (stored) {
      const parsed = JSON.parse(stored);
      return Object.fromEntries(CUSTOM_COLOR_DEFS.map(d => [d.key, parsed[d.key] || d.default]));
    }
  } catch {}
  return Object.fromEntries(CUSTOM_COLOR_DEFS.map(d => [d.key, d.default]));
}

function saveCustomColors(colors: CustomColors) {
  if (typeof window === "undefined") return;
  localStorage.setItem(CUSTOM_COLORS_STORAGE_KEY, JSON.stringify(colors));
}

function applyCustomColors(colors: CustomColors) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  for (const def of CUSTOM_COLOR_DEFS) {
    root.style.setProperty(def.var, colors[def.key] || def.default);
  }
}

interface SettingsState {
  theme: ThemeId; fontSize: string; language: string; sidebarPosition: string; animationEnabled: boolean;
  defaultAgent: string; agentTimeout: number; retryStrategy: string; logLevel: string;
  llmProvider: string; apiKey: string; url: string; model: string; temperature: number; maxTokens: number;
  llmProfiles: Record<string, Record<string, { url: string; apiKey: string; model: string }>>;
  shellWhitelist: string; fileAccess: string; networkAccess: boolean; mcpConfig: string;
  knowledgePath: string; cacheLimit: number;
}

// sections defined inside SettingsClient for i18n

const llmProviders = [
  // 国际模型（API层无订阅制套餐，Token Plan灯位灰显"无此体系"）
  { value: "openai", label: "OpenAI", models: ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o1-preview", "o1-mini"], baseUrl: "https://api.openai.com/v1",
    apiVariants: [{ id: "standard", label: "标准API", baseUrl: "https://api.openai.com/v1", keyPrefix: "sk-" }, { id: "subscription", label: "订阅制", baseUrl: "", keyPrefix: "" }] },
  { value: "claude", label: "Claude (Anthropic)", models: ["claude-sonnet-4-20250514", "claude-haiku-4-20250514", "claude-opus-4-20250514"], baseUrl: "https://api.anthropic.com/v1",
    apiVariants: [{ id: "standard", label: "标准API", baseUrl: "https://api.anthropic.com/v1", keyPrefix: "sk-ant-" }, { id: "subscription", label: "订阅制", baseUrl: "", keyPrefix: "" }] },
  { value: "gemini", label: "Google Gemini", models: ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"], baseUrl: "https://generativelanguage.googleapis.com/v1beta",
    apiVariants: [{ id: "standard", label: "标准API", baseUrl: "https://generativelanguage.googleapis.com/v1beta", keyPrefix: "AIza" }, { id: "subscription", label: "订阅制", baseUrl: "", keyPrefix: "" }] },
  
  // 国内模型
  { value: "mimo", label: "MiMo (小米)", models: ["mimo-v2.5-pro", "mimo-v2.5", "mimo-auto", "mimo-v2-pro"], baseUrl: "https://api.xiaomimimo.com/v1", 
    apiVariants: [
      { id: "standard", label: "标准API", baseUrl: "https://api.xiaomimimo.com/v1", keyPrefix: "sk-" },
      { id: "subscription", label: "订阅制", baseUrl: "https://token-plan-cn.xiaomimimo.com/v1", keyPrefix: "tp-" },
    ]
  },
  { value: "deepseek", label: "DeepSeek (深度求索)", models: ["deepseek-chat", "deepseek-coder", "deepseek-r1", "deepseek-v3"], baseUrl: "https://api.deepseek.com/v1",
    apiVariants: [{ id: "standard", label: "标准API", baseUrl: "https://api.deepseek.com/v1", keyPrefix: "sk-" }, { id: "subscription", label: "订阅制", baseUrl: "", keyPrefix: "" }]
  },
  { value: "qwen", label: "通义千问 (阿里)", models: ["qwen-max", "qwen-plus", "qwen-turbo", "qwen-vl-max", "qwen-long"], baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    apiVariants: [{ id: "standard", label: "标准API", baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1", keyPrefix: "sk-" }, { id: "subscription", label: "订阅制", baseUrl: "", keyPrefix: "" }] },
  { value: "zhipu", label: "智谱 (GLM)", models: ["glm-4-plus", "glm-4-flash", "glm-4v-plus", "glm-4-long"], baseUrl: "https://open.bigmodel.cn/api/paas/v4",
    apiVariants: [{ id: "standard", label: "标准API", baseUrl: "https://open.bigmodel.cn/api/paas/v4", keyPrefix: "" }, { id: "subscription", label: "订阅制", baseUrl: "", keyPrefix: "" }] },
  { value: "moonshot", label: "月之暗面 (Kimi)", models: ["moonshot-v1-128k", "moonshot-v1-32k", "moonshot-v1-8k"], baseUrl: "https://api.moonshot.cn/v1",
    apiVariants: [{ id: "standard", label: "标准API", baseUrl: "https://api.moonshot.cn/v1", keyPrefix: "sk-" }, { id: "subscription", label: "订阅制", baseUrl: "", keyPrefix: "" }] },
  { value: "baichuan", label: "百川智能", models: ["Baichuan4", "Baichuan3-Turbo", "Baichuan2-Turbo"], baseUrl: "https://api.baichuan-ai.com/v1",
    apiVariants: [{ id: "standard", label: "标准API", baseUrl: "https://api.baichuan-ai.com/v1", keyPrefix: "sk-" }, { id: "subscription", label: "订阅制", baseUrl: "", keyPrefix: "" }] },
  { value: "yi", label: "零一万物 (Yi)", models: ["yi-large", "yi-medium", "yi-spark", "yi-vl-plus"], baseUrl: "https://api.lingyiwanwu.com/v1",
    apiVariants: [{ id: "standard", label: "标准API", baseUrl: "https://api.lingyiwanwu.com/v1", keyPrefix: "sk-" }, { id: "subscription", label: "订阅制", baseUrl: "", keyPrefix: "" }] },
  { value: "minimax", label: "MiniMax", models: ["abab6.5s-chat", "abab6.5-chat", "abab5.5-chat"], baseUrl: "https://api.minimax.chat/v1",
    apiVariants: [{ id: "standard", label: "标准API", baseUrl: "https://api.minimax.chat/v1", keyPrefix: "" }, { id: "subscription", label: "订阅制", baseUrl: "", keyPrefix: "" }] },
  { value: "stepfun", label: "阶跃星辰", models: ["step-1v-8k", "step-1-32k", "step-2-16k"], baseUrl: "https://api.stepfun.com/v1",
    apiVariants: [{ id: "standard", label: "标准API", baseUrl: "https://api.stepfun.com/v1", keyPrefix: "sk-" }, { id: "subscription", label: "订阅制", baseUrl: "", keyPrefix: "" }] },
  { value: "doubao", label: "豆包 (字节)", models: ["doubao-pro-32k", "doubao-lite-32k", "doubao-pro-128k"], baseUrl: "https://ark.cn-beijing.volces.com/api/v3",
    apiVariants: [{ id: "standard", label: "标准API", baseUrl: "https://ark.cn-beijing.volces.com/api/v3", keyPrefix: "" }, { id: "subscription", label: "订阅制", baseUrl: "", keyPrefix: "" }] },
  
  // 本地部署
  { value: "ollama", label: "Ollama (本地)", models: ["llama3.1", "qwen2.5", "deepseek-r1", "mistral", "phi3", "gemma2"], baseUrl: "http://localhost:11434/v1",
    apiVariants: [{ id: "standard", label: "本地部署", baseUrl: "http://localhost:11434/v1", keyPrefix: "" }] },
  { value: "lmstudio", label: "LM Studio (本地)", models: ["local-model"], baseUrl: "http://localhost:1234/v1",
    apiVariants: [{ id: "standard", label: "本地部署", baseUrl: "http://localhost:1234/v1", keyPrefix: "" }] },
  { value: "vllm", label: "vLLM (本地)", models: ["local-model"], baseUrl: "http://localhost:8000/v1",
    apiVariants: [{ id: "standard", label: "本地部署", baseUrl: "http://localhost:8000/v1", keyPrefix: "" }] },
  
  // 自定义
  { value: "custom", label: "自定义模型", models: [], baseUrl: "",
    apiVariants: [{ id: "standard", label: "标准API", baseUrl: "", keyPrefix: "" }, { id: "subscription", label: "订阅制", baseUrl: "", keyPrefix: "" }] },
];

// ─── 自动路由策略配置 ──────────────────────────────────────────────
interface RoutingRule {
  id: string;
  name: string;
  description: string;
  localModel: string;      // 本地模型
  onlineModel: string;     // 在线API模型
  complexityThreshold: number;  // 复杂度阈值 (0-1)
}

interface RoutingConfig {
  enabled: boolean;
  mode: 'auto' | 'manual' | 'hybrid';
  defaultStrategy: 'local-first' | 'online-first' | 'cost-optimal' | 'quality-optimal';
  rules: RoutingRule[];
  // 自动模式下的复杂度判断参数
  autoParams: {
    shortTextThreshold: number;      // 短文本阈值（字符数）
    codeDetection: boolean;          // 检测代码请求
    questionDetection: boolean;      // 检测问题类型
    imageAnalysis: boolean;          // 图片分析走在线
  };
}

const DEFAULT_ROUTING_CONFIG: RoutingConfig = {
  enabled: true,
  mode: 'auto',
  defaultStrategy: 'local-first',
  rules: [
    {
      id: 'simple-chat',
      name: '简单对话',
      description: '日常聊天、问候、简单问答',
      localModel: 'mimo-auto',
      onlineModel: 'gpt-4o-mini',
      complexityThreshold: 0.3,
    },
    {
      id: 'code-gen',
      name: '代码生成',
      description: '编程、代码分析、调试',
      localModel: 'qwen2.5-coder',
      onlineModel: 'claude-sonnet-4-20250514',
      complexityThreshold: 0.6,
    },
    {
      id: 'complex-reasoning',
      name: '复杂推理',
      description: '数学、逻辑、多步推理',
      localModel: 'deepseek-r1',
      onlineModel: 'gpt-4o',
      complexityThreshold: 0.8,
    },
    {
      id: 'creative-writing',
      name: '创意写作',
      description: '文章、故事、文案创作',
      localModel: 'qwen2.5',
      onlineModel: 'claude-opus-4-20250514',
      complexityThreshold: 0.5,
    },
  ],
  autoParams: {
    shortTextThreshold: 50,
    codeDetection: true,
    questionDetection: true,
    imageAnalysis: true,
  },
};


// ─── Main Component ──────────────────────────────────────────────────

export function SettingsClient() {
  // ─── 路由配置状态 ──────────────────────────────────────────────
  const { addToast } = useToast();
  const [routingConfig, setRoutingConfig] = useState<RoutingConfig>(DEFAULT_ROUTING_CONFIG);
  const [routingLoading, setRoutingLoading] = useState(false);

  const [routingTestResult, setRoutingTestResult] = useState<string>('');

  // 加载路由配置
  useEffect(() => {
    const loadRoutingConfig = async () => {
      try {
        const apiBase = getApiBaseUrl();
        const res = await fetch(`${apiBase}/api/model-router/routing-config`);
        if (res.ok) {
          const data = await res.json();
          setRoutingConfig(data);
        }
      } catch (e) {
        console.error('Failed to load routing config:', e);
      }
    };
    loadRoutingConfig();
  }, []);

  // 保存路由配置
  const handleSaveRoutingConfig = async () => {
    setRoutingLoading(true);
    try {
      const apiBase = getApiBaseUrl();
      const res = await fetch(`${apiBase}/api/model-router/routing-config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(routingConfig),
      });
      if (res.ok) {
        addToast('success', '成功', '路由配置已保存');
      } else {
        addToast('error', '错误', '保存失败');
      }
    } catch (e) {
      addToast('error', '错误', '保存失败: ' + e);
    } finally {
      setRoutingLoading(false);
    }
  };

  // 测试路由规则
  const handleTestRouting = async (testMessage: string) => {
    setRoutingTestResult('测试中...');
    try {
      const apiBase = getApiBaseUrl();
      const res = await fetch(`${apiBase}/api/routing/test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: testMessage, config: routingConfig }),
      });
      const data = await res.json();
      setRoutingTestResult(`
        消息: ${testMessage}
        选择模型: ${data.model}
        路由类型: ${data.type === 'local' ? '本地' : '在线'}
        复杂度: ${(data.complexity * 100).toFixed(0)}%
        原因: ${data.reason}
      `);
    } catch (e) {
      setRoutingTestResult('测试失败: ' + e);
    }
  };


  const router = useRouter();
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const [showSidebar, setShowSidebar] = useState(false);

  const sections = useMemo<{ id: SectionId; label: string; icon: React.ElementType }[]>(() => [
    { id: "appearance", label: t("settings.appearance"), icon: Monitor },
    { id: "model", label: t("settings.modelConfig"), icon: Cpu },
    { id: "modelRouter", label: t("settings.modelRouter"), icon: Route },
    { id: "agent", label: "Agent", icon: Bot },
    { id: "agentManage", label: "Agent 管理", icon: Server },
    { id: "tools", label: t("settings.toolPermissions"), icon: Wrench },
    { id: "storage", label: t("settings.storage"), icon: HardDrive },
    { id: "organs", label: t("settings.organManagement"), icon: Zap },
    { id: "account", label: t("settings.account"), icon: User },
    { id: "about", label: t("settings.about"), icon: Info },
  ], [t]);



  const storeTheme = useAppStore((s) => s.theme);
  const setStoreTheme = useAppStore((s) => s.setTheme);
  const llmConfig = useAppStore((s) => s.llmConfig);
  const setLLMConfig = useAppStore((s) => s.setLLMConfig);
  const setPageSidebar = useAppStore((s) => s.setPageSidebar);

  const [active, setActive] = useState<SectionId>(() => {
    if (typeof window !== "undefined") {
      const hash = window.location.hash.replace("#", "") as SectionId;
      if (["appearance","agent","agentManage","model","modelRouter","tools","storage","account","about","organs"].includes(hash)) return hash;
    }
    return "appearance";
  });

  // hash 变化即时切区（命令面板 push /settings#agentManage 时已在本页也要生效）
  useEffect(() => {
    const onHash = () => {
      const h = window.location.hash.replace("#", "") as SectionId;
      if (["appearance","agent","agentManage","model","modelRouter","tools","storage","account","about","organs"].includes(h)) setActive(h);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const [saved, setSaved] = useState(false);
  const [testStatus, setTestStatus] = useState<"idle" | "testing" | "success" | "error">("idle");
  const [testedModels, setTestedModels] = useState<Set<string>>(new Set());
  const [fetchedModels, setFetchedModels] = useState<string[]>([]);
  const [loadingConfig, setLoadingConfig] = useState(true);
  const [backendVersion, setBackendVersion] = useState<string>("");
  const [customColors, setCustomColors] = useState<CustomColors>(loadCustomColors);
  const [customProviderName, setCustomProviderName] = useState("");
  // ── 大模型预设双体系状态指示灯（标准API / Token Plan） ──
  const [presetStatus, setPresetStatus] = useState<Record<string, Record<string, { status: string; label: string; base_url: string; key_prefix: string }>>>({});
  const [activeApiVariant, setActiveApiVariant] = useState<string>("standard");
  useEffect(() => {
    fetch(`${getApiBaseUrl()}/api/llm/presets/status`).then(r => r.json()).then(d => {
      if (d?.presets) setPresetStatus(d.presets);
    }).catch(() => {});
  }, []);
  const variantStatus = (providerId: string, variantId: string) => presetStatus[providerId]?.[variantId]?.status || "not_configured";
  const providerDisplay = (m: { value?: string; provider?: string; label?: string; name?: string }) => {
    const pid = m.value || m.provider || "";
    const raw = m.label || m.name || pid;
    const brand = raw.split(" (")[0];
    const desc = t(`settings.providerDesc.${pid}`);
    return desc && !desc.startsWith("settings.") ? `${brand} (${desc})` : raw;
  };
  const STATUS_DOT: Record<string, string> = {
    ok: "bg-green-500", low_balance: "bg-yellow-500", invalid_key: "bg-red-500",
    unreachable: "bg-red-400", not_configured: "bg-gray-300",
    unsupported_model: "bg-yellow-500", unknown: "bg-gray-400",
  };
  const STATUS_TEXT: Record<string, string> = {
    ok: "可用", low_balance: "余额不足", invalid_key: "Key无效",
    unreachable: "不可达", not_configured: "未配置",
    unsupported_model: "模型不支持", unknown: "未确认",
  };
  const [customProviders, setCustomProviders] = useState<Array<{id: string; name: string; model: string; url: string; apiKey: string}>>(() => {
    if (typeof window === "undefined") return [];
    try { return JSON.parse(localStorage.getItem("openmate-custom-providers") || "[]"); } catch { return []; }
  });

  const [settings, setSettings] = useState<SettingsState>({
    theme: "dark", fontSize: "medium", language: "system", sidebarPosition: "left", animationEnabled: true,
    defaultAgent: "auto", agentTimeout: 30, retryStrategy: "exponential", logLevel: "info",
    llmProvider: "mimo", apiKey: "", url: "", model: "mimo-v2.5-pro",
    llmProfiles: {},
    temperature: 0.7, maxTokens: 65536,
    shellWhitelist: "ls, cat, grep, find, git", fileAccess: "full", networkAccess: true, mcpConfig: "",
    knowledgePath: "~/.openmate/knowledge", cacheLimit: 512,
  });

  // ─── 模型路由器状态 ─────────────────────────────────────────────
  const [routerMode, setRouterMode] = useState<string>("balance"); // 当前路由模式，默认balance
  const [routerModels, setRouterModels] = useState<string[]>([]);  // 当前模式的模型列表
  const [routerLoading, setRouterLoading] = useState(false);       // 路由配置加载状态

  // ─── 加载后端配置 ───────────────────────────────────────────────
  useEffect(() => {
    const apiBase = getApiBaseUrl();
    const loadBackendConfig = async () => {
      try {
        // Load config
        const res = await fetch(`${apiBase}/api/config`);
        if (res.ok) {
          const data = await res.json();
          if (data.models?.default) {
            setSettings(s => ({ ...s, model: data.models.default }));
          }
        }
        // Load LLM config (api_key, model)
        try {
          const llmRes = await fetch(`${apiBase}/api/llm/config`);
          if (llmRes.ok) {
            const llmData = await llmRes.json();
            // Detect provider from base_url
            let detectedProvider = "";
            if (llmData.base_url) {
              const match = llmProviders.find(p => llmData.base_url.startsWith(p.baseUrl));
              if (match) detectedProvider = match.value;
              else detectedProvider = "custom";
            }
            const variant = llmData.active_variant || "standard";
            const profiles = llmData.profiles || {};
            setSettings(s => ({
              ...s,
              llmProfiles: profiles,
              ...(llmData.model ? { model: llmData.model } : {}),
              ...(llmData.base_url ? { url: llmData.base_url } : {}),
              ...(llmData.api_key ? { ["apiKey"]: llmData.api_key } : {}),
              ...(detectedProvider ? { llmProvider: detectedProvider } : {}),
            }));
            setActiveApiVariant(variant);
            // Persist loaded backend config to localStorage
            if (detectedProvider && llmData.base_url) {
              try {
                const key = `saved-llm-${detectedProvider}-${llmData.base_url}`;
                if (!localStorage.getItem(key)) {
                  localStorage.setItem(key, JSON.stringify({
                    provider: detectedProvider, url: llmData.base_url,
                    model: llmData.model || "", hasKey: !!llmData.api_key,
                  }));
                }
              } catch {}
            }
          }
        } catch {}
        // Load version
        const vRes = await fetch(`${apiBase}/api/version`);
        if (vRes.ok) {
          const vData = await vRes.json();
          setBackendVersion(vData.version || "");
        }
      } catch {} finally {
        setLoadingConfig(false);
      }

      // ─── 加载模型路由器配置 ─────────────────────────────────────
      try {
        const routerRes = await fetch(`${apiBase}/api/model-router/config`);
        if (routerRes.ok) {
          const routerData = await routerRes.json();
          setRouterMode(routerData.mode || "balance");
          // 根据当前模式获取对应的模型列表
          const mm = routerData.models;
          const modeModels = Array.isArray(mm) ? mm : (mm?.[routerData.mode] || mm?.balance || []);
          setRouterModels(modeModels);
        }
      } catch {}
    };
    loadBackendConfig();
  }, []);

  useEffect(() => { setSettings((s) => ({ ...s, theme: storeTheme })); }, [storeTheme]);

  // Apply custom colors whenever the theme is "custom" or customColors change
  useEffect(() => {
    if (settings.theme === "custom") {
      applyCustomColors(customColors);
    }
  }, [settings.theme, customColors]);

  // Register sidebar navigation into the global app shell sidebar
  useEffect(() => {
    setPageSidebar(
      <LeftPanel
        items={sections}
        filter={(s, q) => s.label.toLowerCase().includes(q)}
        renderItem={(s) => (
          <button
            key={s.id}
            onClick={() => setActive(s.id)}
            className={cn(
              "flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-xs transition-colors group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0 group-data-[collapsible=icon]:gap-0",
              active === s.id
                ? "bg-primary/12 text-primary font-medium"
                : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
            )}
            title={s.label}
          >
            <s.icon size={15} />
            <span className="group-data-[collapsible=icon]:hidden">{s.label}</span>
          </button>
        )}
        placeholder={t("settings.searchPlaceholder") || "Search settings..."}
        header={
          <>



          </>
        }
      />
    );
  }, [active, sections, backendVersion, setPageSidebar, t, router]);

  // Cleanup sidebar ONLY on unmount
  useEffect(() => {
    return () => setPageSidebar(null);
  }, [setPageSidebar]);

  const update = useCallback(<K extends keyof SettingsState>(key: K, value: SettingsState[K]) => {
    // Side effects 先执行（在setState外面）
    if (key === "language") {
      if (value === "system") {
        detectLanguage();
      } else {
        i18n.changeLanguage(value as string);
      }
    }
    if (key === "fontSize") {
      const sizes: Record<string, string> = { small: "14px", medium: "16px", large: "18px" };
      document.documentElement.style.fontSize = sizes[value as string] || "16px";
      const scale = parseInt(sizes[value as string]) / 16;
      document.documentElement.style.setProperty("--sidebar-group-font", `${Math.round(11 * scale)}px`);
    }
    if (key === "animationEnabled") {
      document.documentElement.classList.toggle("no-animations", !value);
    }
    if (key === "theme") { persistTheme(String(value) as ThemeId); setStoreTheme(String(value) as ThemeId); }
    setSettings((s) => {
      const next = { ...s, [key]: value };
      return next;
    });
  }, [setStoreTheme]);

  // Handle custom color change for a single color key
  const handleCustomColorChange = useCallback((key: string, value: string) => {
    setCustomColors((prev) => {
      const next = { ...prev, [key]: value };
      saveCustomColors(next);
      return next;
    });
  }, []);

  // Reset all custom colors to defaults
  const handleResetCustomColors = useCallback(() => {
    const defaults = Object.fromEntries(CUSTOM_COLOR_DEFS.map(d => [d.key, d.default]));
    setCustomColors(defaults);
    saveCustomColors(defaults);
  }, []);

  async function handleSave() {
    const apiBase = getApiBaseUrl();

    // Only save settings for the current tab
    switch (active) {
      case "appearance":
        localStorage.setItem("openmate-language", settings.language);
        localStorage.setItem("openmate-fontSize", settings.fontSize);
        localStorage.setItem("openmate-animation", String(settings.animationEnabled));
        break;

      case "model":
        setLLMConfig({ provider: settings.llmProvider, apiKey: "", url: settings.url, model: settings.model });
        // Save to persistent config list (multiple configs coexist)
        try {
          const key = `saved-llm-${settings.llmProvider}-${settings.url}`;
          localStorage.setItem(key, JSON.stringify({
            provider: settings.llmProvider, url: settings.url,
            model: settings.model, hasKey: !!(settings.apiKey),
          }));
        } catch {}
        // Save LLM config to backend
        try {
          await fetch(`${apiBase}/api/llm/config`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              provider: settings.llmProvider,
              variant: activeApiVariant,
              api_key: settings.apiKey || undefined,
              base_url: settings.url || undefined,
              model: settings.model || undefined,
              max_tokens: settings.maxTokens || undefined,
            }),
          });
        } catch {}
        // Save routing config to backend
        try {
          await fetch(`${apiBase}/api/model-router/routing-config`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(routingConfig),
          });
        } catch {}
        break;

      case "agent":
        // Save agent config to backend
        try {
          await fetch(`${apiBase}/api/config`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              data: {
                agent: {
                  default: settings.defaultAgent,
                  timeout: settings.agentTimeout,
                  retry_strategy: settings.retryStrategy,
                  log_level: settings.logLevel,
                },
              },
            }),
          });
        } catch {}
        break;

      case "tools":
        try {
          await fetch(`${apiBase}/api/config`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              data: {
                tools: {
                  shell_whitelist: settings.shellWhitelist,
                  file_access: settings.fileAccess,
                  network_access: settings.networkAccess,
                },
              },
            }),
          });
        } catch {}
        break;

      case "storage":
        try {
          await fetch(`${apiBase}/api/config`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              data: {
                storage: {
                  knowledge_path: settings.knowledgePath,
                  cache_limit_mb: settings.cacheLimit,
                },
              },
            }),
          });
        } catch {}
        break;

      default:
        break;
    }

    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }

  function handleLogout() {
    clearUser();
    clearAutoLogin(); // 显式登出=关自动登录（401 过期接管不走这里，自动登录仍可救过期）
    localStorage.removeItem("openmate-api-url");
    window.location.href = "/login";
  }

  // ── Export / Import / Clear Cache ───────────────────────────────────
  const toast = useToast();
  const fileInputRef = useRef<HTMLInputElement>(null);

  function handleExportData() {
    try {
      const state = useAppStore.getState();
      const data = {
        version: "0.1.0",
        exported_at: new Date().toISOString(),
        settings: settings,
        theme: getStoredTheme(),
        api_url: getApiBaseUrl(),
        user_id: getUserId(),
        // Export store data
        workspaces: state.workspaces,
        conversations: state.conversations,
        knowledgeItems: state.knowledgeItems,
      };
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `openmate-export-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      toast.success(t("settings.exportSuccess"), t("settings.exportSuccessDesc"));
    } catch (e) {
      toast.error(t("settings.exportFailed"), e instanceof Error ? e.message : t("settings.unknownError"));
    }
  }

  function handleImportData() {
    fileInputRef.current?.click();
  }

  async function handleImportFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const text = await file.text();
      const data = JSON.parse(text);
      if (!data.version) {
        toast.error(t("settings.importFailed"), t("settings.importFailedDesc"));
        return;
      }
      // Restore settings
      if (data.settings) {
        setSettings((prev) => ({ ...prev, ...data.settings }));
      }
      // Restore store data
      const store = useAppStore.getState();
      if (data.knowledgeItems && store.setKnowledgeItems) {
        store.setKnowledgeItems(data.knowledgeItems);
      }

      toast.success(t("settings.importSuccess"), `${file.name}`);
    } catch (e) {
      toast.error(t("settings.importFailed"), e instanceof Error ? e.message : t("settings.fileParseError"));
    }
    // Reset file input
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  async function handleClearCache() {
    if (!confirm(t("settings.confirmClearCache"))) return;
    try {
      // Clear localStorage caches (not auth tokens)
      const keysToKeep = ["openmate-token", "openmate-api-url", "openmate-user-id", "openmate-theme"];
      const allKeys = Object.keys(localStorage);
      for (const key of allKeys) {
        if (!keysToKeep.includes(key)) {
          localStorage.removeItem(key);
        }
      }
      // Clear caches API if available
      const apiBase = getApiBaseUrl();
      try {
        await fetch(`${apiBase}/api/admin/clear-cache`, { method: "POST" });
      } catch {}
      toast.success(t("settings.cacheCleared"), t("settings.cacheClearedDesc"));
    } catch (e) {
      toast.error(t("settings.clearFailed"), e instanceof Error ? e.message : t("settings.unknownError"));
    }
  }

  async function fetchModelsFromApi() {
    if (!settings.url) return;
    try {
      const apiBase = getApiBaseUrl();
      const res = await fetch(`${apiBase}/api/llm/models`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          base_url: settings.url,
          api_key: settings.apiKey || undefined,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        if (data.models?.length) {
          setFetchedModels(data.models);
        }
      }
    } catch {}
  }

  async function handleTestConnection() {
    setTestStatus("testing");
    try {
      const apiBase = getApiBaseUrl();
      const testPayload = {
        base_url: settings.url || undefined,
        api_key: settings.apiKey || undefined,
        model: settings.model || undefined,
      };
      console.log("[TestConnection] provider:", settings.llmProvider, "base_url:", testPayload.base_url, "model:", testPayload.model, "api_key_len:", testPayload.api_key?.length || 0);
      const res = await fetch(`${apiBase}/api/llm/test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(testPayload),
      });
      if (res.ok) {
        const data = await res.json();
        if (data.status === "ok") {
          setTestStatus("success");
          const testKey = settings.llmProvider === "custom"
            ? `custom-${settings.model}@${settings.url}`
            : settings.llmProvider;
          setTestedModels(prev => new Set(prev).add(testKey));
          // Auto-fetch available models from API
          fetchModelsFromApi();
          toast.success(
            t("settings.testSuccess") || "连接成功",
            `${data.model} @ ${data.base_url} — ${data.reply}`
          );
        } else {
          setTestStatus("error");
        }
      } else {
        const err = await res.json().catch(() => ({}));
        setTestStatus("error");
        toast.error(
          t("settings.testFailed") || "连接失败",
          err.detail || `HTTP ${res.status}`
        );
      }
    } catch (e) {
      setTestStatus("error");
      toast.error(
        t("settings.testFailed") || "连接失败",
        e instanceof Error ? e.message : t("settings.networkError") || "网络错误"
      );
    }
    setTimeout(() => setTestStatus("idle"), 3000);
  }

  // ─── 切换模型路由模式 ─────────────────────────────────────────
  // 预定义的各模式模型列表（乐观更新，避免加载闪烁）
  const ROUTER_MODE_MODELS: Record<string, string[]> = {
    cost: ["mimo-7b-local", "mimo-v2.5-lite"],
    balance: ["mimo-v2.5-pro", "mimo-7b-local"],
    intelligence: ["mimo-v2.5-pro", "mimo-v2.5-pro-beta"],
    auto: ["mimo-v2.5-pro", "mimo-7b-local", "mimo-v2.5-lite"],
  };

  const handleRouterModeChange = useCallback(async (mode: string) => {
    // 立即更新UI（乐观更新，无闪烁）
    setRouterMode(mode);
    setRouterModels(ROUTER_MODE_MODELS[mode] || []);
    // 后台同步到后端
    try {
      const apiBase = getApiBaseUrl();
      await fetch(`${apiBase}/api/model-router/mode`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode }),
      });
    } catch {}
  }, []);

  const currentProvider = llmProviders.find((p) => p.value === settings.llmProvider);
  const baseModelOptions = currentProvider?.models.length ? currentProvider.models.map((m) => ({ value: m, label: m })) : [{ value: settings.model, label: settings.model || t("settings.inputModelName") }];
  // Merge API-fetched models (includes beta models user has access to)
  const modelOptions = (() => {
    if (!fetchedModels.length) return baseModelOptions;
    const existing = new Set(baseModelOptions.map(o => o.value));
    const extra = fetchedModels.filter(m => !existing.has(m)).map(m => ({ value: m, label: `${m} ★` }));
    return [...baseModelOptions, ...extra];
  })();

  // ─── 已配置模型状态 ─────────────────────────────────────────
  interface ConfiguredModel {
    id: string;
    name: string;
    provider: string;
    endpoint?: string;
    status: 'active' | 'configured' | 'unconfigured';
    isDefault?: boolean;
  }

  const configuredModels: ConfiguredModel[] = [
    // 预设模型
    ...llmProviders.filter(p => p.value !== "custom").map(p => {
      const isCurrent = p.value === settings.llmProvider;
      const hasKey = isCurrent && !!settings.apiKey;
      const tested = testedModels.has(p.value);
      // Check all saved configs for this provider (any URL)
      let isConfigured = hasKey;
      if (!isConfigured && typeof window !== "undefined") {
        try {
          for (let i = 0; i < localStorage.length; i++) {
            const k = localStorage.key(i);
            if (k && k.startsWith(`saved-llm-${p.value}-`)) {
              const cfg = JSON.parse(localStorage.getItem(k) || "null");
              if (cfg?.hasKey) { isConfigured = true; break; }
            }
          }
        } catch {}
      }
      return {
        id: p.value,
        name: p.label,
        provider: p.value,
        status: (tested ? 'active' : isConfigured ? 'configured' : 'unconfigured') as 'active' | 'configured' | 'unconfigured',
        isDefault: isCurrent,
      };
    }),
    // 自定义模型（排在预设后面）
    ...customProviders.map(cp => ({
      id: cp.id,
      name: cp.name,
      provider: 'custom',
      status: (testedModels.has(`custom-${cp.model}@${cp.url}`) ? 'active' : 'configured') as 'active' | 'configured',
      isDefault: settings.llmProvider === 'custom' && settings.model === cp.model && settings.url === cp.url,
    })),
  ];

  // Mobile sidebar navigation (PC sidebar is registered via setPageSidebar into the app shell)
  const MobileSidebarNav = () => (
    <>

      <nav className="flex-1 space-y-0.5">
        {sections.map((s) => {
          const Icon = s.icon;
          return (
            <button key={s.id} onClick={() => { setActive(s.id); setShowSidebar(false); }}
              className={cn("flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-xs transition-colors", active === s.id ? "bg-primary/12 text-primary font-medium" : "text-muted-foreground hover:bg-accent/50 hover:text-foreground")}>
              <Icon size={15} />{s.label}
            </button>
          );
        })}
      </nav>

    </>
  );

  return (
      <PageLayout title="设置">
        
    <div className="flex h-full flex-col lg:flex-row">
      {/* Mobile: top bar with hamburger + current section */}
      {isMobile && (
        <div className="flex items-center gap-3 px-4 py-3 border-b border-border bg-card/50 shrink-0">
          <button onClick={() => setShowSidebar(true)} className="p-1.5 rounded-md hover:bg-muted text-muted-foreground">
            <Menu size={18} />
          </button>
          <div className="flex items-center gap-1.5 text-xs lg:text-sm">
            <span className="text-muted-foreground">{sections.find(s => s.id === active)?.label}</span>
            <ChevronRight size={12} className="text-muted-foreground" />
            <span className="font-medium">{sections.find(s => s.id === active)?.label}</span>
          </div>
        </div>
      )}

      {/* Mobile: sidebar-style sliding panel */}
      {isMobile && showSidebar && (
        <div className="fixed inset-0 z-9 bg-black/40 animate-in fade-in-0" onClick={() => setShowSidebar(false)} aria-hidden="true" />
      )}
      {isMobile && (
        <div
          className="absolute inset-y-0 left-0 z-10 h-full w-64 min-w-0 border-r border-border transition-[left] duration-200 ease-linear flex flex-col overflow-hidden bg-card p-4"
          style={{ left: showSidebar ? 0 : -256 }}
        >
          <MobileSidebarNav />
        </div>
      )}

      {/* Main Content */}
      <div className="flex-1 overflow-y-auto min-h-0" onClick={() => { if (isMobile && showSidebar) setShowSidebar(false); }}>
        <div className="mx-auto px-4 md:px-8 py-3 lg:py-6 md:py-8 space-y-3 lg:space-y-6">
          {/* Breadcrumb - hidden on mobile (shown in top bar) */}
          <div className="hidden lg:flex items-center gap-1.5 text-xs text-muted-foreground">
            <span>{t("settings.title")}</span><ChevronRight size={10} />
            
            <span className="text-foreground">{sections.find(s => s.id === active)?.label}</span>
          </div>

          {/* ─── Appearance ──────────────────────────────────── */}
          {active === "appearance" && (
            <>
              <div className="flex items-center gap-2.5 mb-2">
                <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10"><Monitor size={16} className="text-primary" /></div>
                <div><h1 className="text-lg font-semibold">{t("settings.appearanceSettings")}</h1><p className="text-xs text-muted-foreground">{t("settings.appearanceDesc")}</p></div>
              </div>

              <SettingCard title={t("settings.theme")} description={t("settings.themeDesc")}>
                <ButtonGroup value={settings.theme} onChange={(v) => update("theme", v as ThemeId)}
                  options={getThemes().map((t) => ({ value: t.id, label: t.label, icon: t.id === "dark" ? Moon : t.id === "light" ? Sun : Palette }))} />
              </SettingCard>

              {/* ─── Custom Theme Color Editor ───────────────────────── */}
              {settings.theme === "custom" && (
                <SettingCard title={t("settings.customThemeColors")} description={t("settings.customThemeColorsDesc")}>
                  <div className="grid grid-cols-3 gap-3 sm:grid-cols-3">
                    {CUSTOM_COLOR_DEFS.map((def) => {
                      const labelKey = `settings.color${def.key.charAt(0).toUpperCase() + def.key.slice(1)}` as string;
                      return (
                        <label
                          key={def.key}
                          className="flex flex-col items-center gap-1.5 cursor-pointer group"
                        >
                          <div className="relative">
                            <div
                              className="h-10 w-10 rounded-lg border-2 border-border shadow-sm transition-transform group-hover:scale-105 overflow-hidden"
                              style={{ backgroundColor: customColors[def.key] || def.default }}
                            >
                              <input
                                type="color"
                                value={customColors[def.key] || def.default}
                                onChange={(e) => handleCustomColorChange(def.key, e.target.value)}
                                className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                              />
                            </div>
                          </div>
                          <span className="text-[10px] text-muted-foreground text-center leading-tight">
                            {t(labelKey)}
                          </span>
                        </label>
                      );
                    })}
                  </div>
                  <div className="mt-3 flex justify-end">
                    <button
                      type="button"
                      onClick={handleResetCustomColors}
                      className="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs text-muted-foreground hover:bg-accent/50 hover:text-foreground transition-colors"
                    >
                      <RotateCcw size={12} />
                      {t("settings.resetColors")}
                    </button>
                  </div>
                </SettingCard>
              )}

              <SettingCard title={t("settings.fontSize")} description={t("settings.fontSizeDesc")}>
                <ButtonGroup value={settings.fontSize} onChange={(v) => update("fontSize", v)}
                  options={[{ value: "small", label: t("settings.small") }, { value: "medium", label: t("settings.medium") }, { value: "large", label: t("settings.large") }]} />
              </SettingCard>

              <SettingCard title={t("settings.language")} description={t("settings.languageDesc")}>
                <SelectInput value={settings.language} onChange={(v) => update("language", v)}
                  options={[{ value: "system", label: "跟随系统" }, { value: "zh", label: "中文" }, { value: "en", label: "English" }, { value: "ja", label: "日本語" }]} />
              </SettingCard>

              <SettingCard title={t("settings.animationEffects")} description={t("settings.animationDesc")}>
                <Toggle checked={settings.animationEnabled} onChange={(v) => update("animationEnabled", v)} />
              </SettingCard>
            </>
          )}

          {/* ─── Model ───────────────────────────────────────── */}
          {active === "model" && (
            <>
              <div className="flex items-center gap-2.5 mb-2">
                <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10"><Cpu size={16} className="text-primary" /></div>
                <div><h1 className="text-lg font-semibold">{t("settings.modelConfig")}</h1><p className="text-xs text-muted-foreground">{t("settings.modelConfigDesc")}</p></div>
              </div>

              {/* ─── 已配置模型概览 ────────────────────────────── */}
              <SettingCard
                title={t("settings.configuredModels") || "已配置模型"}
                description={t("settings.configuredModelsDesc") || "查看所有已接入的模型提供商及其配置状态"}
              >
                <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
                  {configuredModels.map(model => (
                    <div
                      key={model.id}
                      className={`
                        p-3 rounded-lg border transition-all cursor-pointer hover:shadow-md
                        ${model.isDefault
                          ? 'border-primary bg-primary/5 shadow-sm'
                          : model.status === 'configured'
                            ? 'border-green-500/30 bg-green-500/5'
                            : 'border-border bg-muted/30'
                        }
                      `}
                      onClick={() => {
                        const p = llmProviders.find(pp => pp.value === model.id);
                        setSettings(s => {
                          const profiles = { ...s.llmProfiles };
                          if (!profiles[s.llmProvider]) profiles[s.llmProvider] = {};
                          profiles[s.llmProvider][activeApiVariant] = { url: s.url, apiKey: s.apiKey, model: s.model };
                          const target = profiles[model.id]?.[activeApiVariant];
                          const vd = p?.apiVariants?.find(x => x.id === activeApiVariant) || p?.apiVariants?.[0];
                          return { ...s, llmProfiles: profiles, llmProvider: model.id,
                            url: target?.url || vd?.baseUrl || "",
                            apiKey: target?.apiKey || "",
                            model: target?.model || (p?.models?.[0] ?? "") };
                        });
                      }}
                    >
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-xs font-medium truncate">{providerDisplay(model)}</span>
                        {model.isDefault && (
                          <CheckCircle2 size={14} className="text-primary flex-shrink-0" />
                        )}
                      </div>
                      <div className="flex items-center gap-2 flex-wrap">
                        {(llmProviders.find(p => p.value === model.id)?.apiVariants || [{ id: "standard", label: "标准API" }]).map((variant) => {
                          const st = variantStatus(model.id, variant.id);
                          const vLabel = variant.label === "标准API" ? (t("settings.standardApi") || "标准API") : variant.label === "订阅制" ? (t("settings.subscription") || "订阅制") : variant.label === "本地" ? (t("settings.localProvider") || "本地") : variant.label;
                          const stMap: Record<string, string> = { ok: "settings.statusOk", low_balance: "settings.statusLowBalance", invalid_key: "settings.statusInvalidKey", unreachable: "settings.statusUnreachable", not_configured: "settings.statusNotConfigured", unsupported_model: "settings.statusUnsupportedModel", unknown: "settings.statusUnknown" };
                          return (
                            <div key={variant.id} className="flex items-center gap-1" title={`${vLabel}: ${t(stMap[st]) || STATUS_TEXT[st] || st}`}>
                              <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${STATUS_DOT[st] || "bg-gray-300"}`} />
                              <span className="text-[9px] text-muted-foreground">
                                {vLabel}
                              </span>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              </SettingCard>

              {/* ─── 当前模型详细配置 ──────────────────────────── */}
              <SettingCard
                title={t("settings.currentModelConfig") || "当前模型配置"}
                description={currentProvider?.label || ""}
              >
                <div className="space-y-3">
                  <div>
                    <p className="text-xs text-muted-foreground mb-1.5">
                      {t("settings.llmProvider") || "提供商"}
                    </p>
                    <SelectInput
                      value={settings.llmProvider}
                      onChange={(v) => {
                        const p = llmProviders.find(pp => pp.value === v);
                        setActiveApiVariant("standard");
                        setSettings(s => {
                          const profiles = { ...s.llmProfiles };
                          if (!profiles[s.llmProvider]) profiles[s.llmProvider] = {};
                          profiles[s.llmProvider][activeApiVariant] = { url: s.url, apiKey: s.apiKey, model: s.model };
                          const target = profiles[v]?.["standard"];
                          const vd = p?.apiVariants?.find(x => x.id === "standard") || p?.apiVariants?.[0];
                          return { ...s, llmProfiles: profiles, llmProvider: v,
                            url: target?.url || vd?.baseUrl || "",
                            apiKey: target?.apiKey || "",
                            model: target?.model || (p?.models?.[0] ?? "") };
                        });
                      }}
                      options={llmProviders.map(p => ({ value: p.value, label: providerDisplay(p) }))}
                    />
                  </div>

                  {/* ── API体系选择：标准API / Token Plan（地址自动跟随） ── */}
                  {(currentProvider?.apiVariants?.length ?? 0) > 1 && (
                    <div>
                      <p className="text-xs text-muted-foreground mb-1.5">
                        {t("settings.apiVariant") || "API体系"}
                      </p>
                      <div className="flex gap-1.5">
                        {(currentProvider?.apiVariants || []).map((v) => (
                          <button
                            key={v.id}
                            onClick={() => {
                                                          setSettings(s => {
                                                            const profiles = { ...s.llmProfiles };
                                                            const pid = s.llmProvider;
                                                            if (!profiles[pid]) profiles[pid] = {};
                                                            profiles[pid][activeApiVariant] = { url: s.url, apiKey: s.apiKey, model: s.model };
                                                            const target = profiles[pid]?.[v.id] || { url: "", apiKey: "", model: "" };
                                                            return { ...s, llmProfiles: profiles, url: target.url || v.baseUrl || "", apiKey: target.apiKey || "", model: target.model || "" };
                                                          });
                                                          setActiveApiVariant(v.id);
                                                        }}
                            className={`flex-1 px-2 py-1.5 rounded-lg border text-[11px] transition-colors ${
                              activeApiVariant === v.id
                                ? "border-primary bg-primary/5 text-primary font-medium"
                                : "border-border text-muted-foreground hover:bg-muted"
                            }`}
                          >
                            {v.label === "标准API" ? (t("settings.standardApi") || "标准API") : v.label === "订阅制" ? (t("settings.subscription") || "订阅制") : v.label === "本地部署" ? (t("settings.localDeploy") || "本地部署") : v.label === "本地" ? (t("settings.localProvider") || "本地") : v.label}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}

                  {settings.llmProvider === "custom" && (
                    <div>
                      <p className="text-xs text-muted-foreground mb-1.5">
                        {t("settings.customProviderName") || "提供商名称"}
                      </p>
                      <TextInput value={customProviderName} onChange={setCustomProviderName} placeholder={t("settings.customProviderNamePlaceholder") || "例如: 我的本地模型"} />
                    </div>
                  )}

                  <div>
                    <p className="text-xs text-muted-foreground mb-1.5">
                      {t("settings.model") || "模型"}
                    </p>
                    {settings.llmProvider === "custom" || !modelOptions.some(o => o.value === settings.model) ? (
                      <div className="flex gap-1.5">
                        <TextInput value={settings.model} onChange={(v) => update("model", v)} placeholder={t("settings.inputModelName") || "输入模型名称"} />
                        {settings.llmProvider !== "custom" && modelOptions.length > 0 && (
                          <button
                            onClick={() => update("model", modelOptions[0].value)}
                            className="px-2 py-1.5 rounded-lg border border-border text-[10px] text-muted-foreground hover:bg-muted shrink-0"
                          >
                            {t("settings.backToList") || "列表"}
                          </button>
                        )}
                      </div>
                    ) : (
                      <div className="flex gap-1.5">
                        <SelectInput value={settings.model} onChange={(v) => { if (v === "__custom__") { update("model", ""); } else { update("model", v); } }} options={[...modelOptions, { value: "__custom__", label: `+ ${t("settings.customModelName") || "自定义模型名"}...` }]} />
                      </div>
                    )}
                  </div>

                  <div>
                    <p className="text-xs text-muted-foreground mb-1.5">
                      {t("settings.baseUrl") || "Base URL"}
                    </p>
                    <TextInput value={settings.url} onChange={(v) => update("url", v)} placeholder={(currentProvider?.apiVariants?.find(x => x.id === activeApiVariant) || currentProvider?.apiVariants?.[0])?.baseUrl || (activeApiVariant === "subscription" ? "订阅服务Base URL（手动填写）" : "https://api.openai.com/v1")} />
                  </div>

                  <div>
                    <p className="text-xs text-muted-foreground mb-1.5">
                      {t("settings.apiKey") || "API Key"}
                    </p>
                    <TextInput value={settings.apiKey} onChange={(v) => update("apiKey", v)} placeholder={(() => { const v = currentProvider?.apiVariants?.find(x => x.id === activeApiVariant) || currentProvider?.apiVariants?.[0]; return (v?.keyPrefix || "sk-") + "..."; })()} type="password" />
                  </div>

                  <button
                    onClick={handleTestConnection}
                    className="w-full px-3 py-2 rounded-lg border border-border text-xs hover:bg-muted flex items-center justify-center gap-1.5 transition-colors"
                  >
                    {testStatus === "testing" ? (
                      <RefreshCw size={12} className="animate-spin" />
                    ) : testStatus === "success" ? (
                      <CheckCircle2 size={12} className="text-green-500" />
                    ) : testStatus === "error" ? (
                      <AlertCircle size={12} className="text-red-500" />
                    ) : (
                      <Wifi size={12} />
                    )}
                    {testStatus === "success"
                      ? (t("settings.testSuccess") || "连接成功")
                      : testStatus === "error"
                        ? (t("settings.testFailed") || "连接失败")
                        : (t("settings.testConnection") || "测试连接")
                    }
                  </button>

                  {settings.llmProvider === "custom" && (
                    <button
                      onClick={() => {
                        if (!settings.model || !settings.url) return;
                        const name = customProviderName || settings.model;
                        const newProvider = {
                          id: `custom-${Date.now()}`,
                          name,
                          model: settings.model,
                          url: settings.url,
                          apiKey: settings.apiKey,
                        };
                        const next = [...customProviders, newProvider];
                        setCustomProviders(next);
                        localStorage.setItem("openmate-custom-providers", JSON.stringify(next));
                        setCustomProviderName("");
                      }}
                      className="w-full px-3 py-2 rounded-lg border border-dashed border-primary/40 text-xs text-primary hover:bg-primary/5 flex items-center justify-center gap-1.5 transition-colors"
                    >
                      <Plus size={12} />
                      {t("settings.addCustomModel") || "添加自定义模型"}
                    </button>
                  )}

                  {customProviders.length > 0 && (
                    <div className="space-y-1.5">
                      <p className="text-[10px] text-muted-foreground">{t("settings.savedCustomModels") || "已保存的自定义模型"}</p>
                      {customProviders.map(cp => (
                        <div key={cp.id} className="flex items-center justify-between p-2 rounded-lg border border-border bg-muted/20">
                          <div className="min-w-0 flex-1">
                            <div className="text-xs font-medium truncate">{cp.name}</div>
                            <div className="text-[10px] text-muted-foreground truncate">{cp.model} @ {cp.url}</div>
                          </div>
                          <div className="flex items-center gap-1 shrink-0">
                            <button
                              onClick={() => {
                                update("llmProvider", "custom");
                                update("model", cp.model);
                                update("url", cp.url);
                                update("apiKey", cp.apiKey);
                                setCustomProviderName(cp.name);
                              }}
                              className="p-1 rounded hover:bg-muted text-muted-foreground"
                              title={t("settings.useThisModel") || "使用此模型"}
                            >
                              <CheckCircle2 size={12} />
                            </button>
                            <button
                              onClick={() => {
                                const next = customProviders.filter(p => p.id !== cp.id);
                                setCustomProviders(next);
                                localStorage.setItem("openmate-custom-providers", JSON.stringify(next));
                              }}
                              className="p-1 rounded hover:bg-red-500/10 text-red-500/60"
                              title={t("settings.deleteCustomModel") || "删除"}
                            >
                              <Trash2 size={12} />
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </SettingCard>

              <SettingCard title="温度" description={`${t("settings.temperatureDesc")}: ${settings.temperature}`}>
                <Slider value={settings.temperature} onChange={(v) => update("temperature", v)} min={0} max={2} step={0.1} />
              </SettingCard>

              <SettingCard title="最大 Token 数" description={t("settings.maxTokensDesc")}>
                <Slider value={settings.maxTokens} onChange={(v) => update("maxTokens", v)} min={256} max={131072} step={256} />
              </SettingCard>
            </>
          )}

          {/* ─── 模型路由 ──────────────────────────────────────── */}
          {active === "modelRouter" && (
            <>
              {/* ─── 模型路由器配置 ─────────────────────────────────── */}
              <SettingCard
                title={t("settings.modelRouter")}
                description={t("settings.modelRouterDesc")}
              >
                {/* 路由模式选择器 — 4个选项按钮 */}
                <ButtonGroup
                  value={routerMode}
                  onChange={handleRouterModeChange}
                  options={[
                    { value: "cost", label: t("settings.routerCost") },
                    { value: "balance", label: t("settings.routerBalance") },
                    { value: "intelligence", label: t("settings.routerIntelligence") },
                    { value: "auto", label: t("settings.routerAuto") },
                  ]}
                />
                {/* 当前选中模式的描述文字 */}
                <p className="mt-2 text-xs text-muted-foreground transition-all duration-200" style={{ minHeight: '20px' }}>
                  {t(`settings.router${routerMode.charAt(0).toUpperCase() + routerMode.slice(1)}Desc`)}
                </p>
                {/* 当前模式的模型列表 — 小标签展示 */}
                <div className="mt-3" style={{ minHeight: '52px' }}>
                  <p className="text-[10px] text-muted-foreground mb-1.5">{t("settings.routerModels")}</p>
                  <div className="flex flex-wrap gap-1.5 transition-all duration-200">
                    {routerModels.map((model) => (
                      <span
                        key={model}
                        className="inline-flex items-center rounded-md bg-primary/10 px-2 py-0.5 text-[10px] font-mono text-primary"
                      >
                        {model}
                      </span>
                    ))}
                  </div>
                </div>
                {/* Loading indicator removed - optimistic update */}
              </SettingCard>
              {/* ─── 自动路由策略设置 ──────────────────────────────── */}
              <SettingCard
                title={t("settings.autoRouting") || "自动路由策略"}
                description={t("settings.autoRoutingDesc") || "配置模型自动选择策略，基础操作走本地，复杂问题走在线API"}
              >
                {/* 路由开关 */}
                <div className="flex items-center justify-between mb-4">
                  <span className="text-sm">{t("settings.enableRouting") || "启用自动路由"}</span>
                  <Toggle
                    checked={routingConfig.enabled}
                    onChange={(v) => setRoutingConfig(prev => ({ ...prev, enabled: v }))}
                  />
                </div>

                {routingConfig.enabled && (
                  <>
                    {/* 路由模式选择 */}
                    <div className="mb-4">
                      <p className="text-xs text-muted-foreground mb-2">
                        {t("settings.routingMode") || "路由模式"}
                      </p>
                      <ButtonGroup
                        value={routingConfig.mode}
                        onChange={(v) => setRoutingConfig(prev => ({ ...prev, mode: v as any }))}
                        options={[
                          { value: "auto", label: t("settings.routingAuto") || "自动模式" },
                          { value: "manual", label: t("settings.routingManual") || "手动模式" },
                          { value: "hybrid", label: t("settings.routingHybrid") || "混合模式" },
                        ]}
                      />
                      <p className="mt-2 text-xs text-muted-foreground">
                        {routingConfig.mode === 'auto' && (t("settings.routingAutoDesc") || "根据消息复杂度自动选择本地或在线模型")}
                        {routingConfig.mode === 'manual' && (t("settings.routingManualDesc") || "固定使用默认策略选择的模型")}
                        {routingConfig.mode === 'hybrid' && (t("settings.routingHybridDesc") || "自动模式基础上允许用户覆盖选择")}
                      </p>
                    </div>

                    {/* 默认策略 */}
                    <div className="mb-4">
                      <p className="text-xs text-muted-foreground mb-2">
                        {t("settings.defaultStrategy") || "默认策略"}
                      </p>
                      <SelectInput
                        value={routingConfig.defaultStrategy}
                        onChange={(v) => setRoutingConfig(prev => ({ ...prev, defaultStrategy: v as any }))}
                        options={[
                          { value: "local-first", label: t("settings.localFirst") || "本地优先 (省成本)" },
                          { value: "online-first", label: t("settings.onlineFirst") || "在线优先 (高质量)" },
                          { value: "cost-optimal", label: t("settings.costOptimal") || "成本最优" },
                          { value: "quality-optimal", label: t("settings.qualityOptimal") || "质量最优" },
                        ]}
                      />
                    </div>

                    {/* 自动模式参数 */}
                    {routingConfig.mode === 'auto' && (
                      <div className="space-y-3 p-3 bg-muted/30 rounded-lg">
                        <p className="text-xs font-medium">
                          {t("settings.autoParams") || "自动模式参数"}
                        </p>
                        
                        {/* 短文本阈值 */}
                        <div>
                          <p className="text-xs text-muted-foreground mb-1">
                            {t("settings.shortTextThreshold") || "短文本阈值"}: {routingConfig.autoParams.shortTextThreshold} 字符
                          </p>
                          <Slider
                            value={routingConfig.autoParams.shortTextThreshold}
                            onChange={(v) => setRoutingConfig(prev => ({
                              ...prev,
                              autoParams: { ...prev.autoParams, shortTextThreshold: v }
                            }))}
                            min={10}
                            max={200}
                            step={10}
                          />
                          <p className="text-[10px] text-muted-foreground mt-1">
                            {t("settings.shortTextDesc") || "低于此长度的消息优先使用本地模型"}
                          </p>
                        </div>

                        {/* 检测选项 */}
                        <div className="space-y-2">
                          <div className="flex items-center justify-between">
                            <span className="text-xs">{t("settings.codeDetection") || "代码请求走在线"}</span>
                            <Toggle
                              checked={routingConfig.autoParams.codeDetection}
                              onChange={(v) => setRoutingConfig(prev => ({
                                ...prev,
                                autoParams: { ...prev.autoParams, codeDetection: v }
                              }))}
                            />
                          </div>
                          <div className="flex items-center justify-between">
                            <span className="text-xs">{t("settings.questionDetection") || "复杂问题走在线"}</span>
                            <Toggle
                              checked={routingConfig.autoParams.questionDetection}
                              onChange={(v) => setRoutingConfig(prev => ({
                                ...prev,
                                autoParams: { ...prev.autoParams, questionDetection: v }
                              }))}
                            />
                          </div>
                          <div className="flex items-center justify-between">
                            <span className="text-xs">{t("settings.imageAnalysis") || "图片分析走在线"}</span>
                            <Toggle
                              checked={routingConfig.autoParams.imageAnalysis}
                              onChange={(v) => setRoutingConfig(prev => ({
                                ...prev,
                                autoParams: { ...prev.autoParams, imageAnalysis: v }
                              }))}
                            />
                          </div>
                        </div>
                      </div>
                    )}

                    {/* 路由规则预览 */}
                    <div className="mt-4">
                      <p className="text-xs text-muted-foreground mb-2">
                        {t("settings.routingRules") || "路由规则预览"}
                      </p>
                      <div className="space-y-2">
                        {routingConfig.rules.map(rule => (
                          <div key={rule.id} className="p-2 bg-muted/20 rounded text-xs">
                            <p className="font-medium">{rule.name}</p>
                            <p className="text-muted-foreground mb-1">{rule.description}</p>
                            <div className="flex gap-2">
                              <span className="text-[10px] px-1.5 py-0.5 bg-blue-500/10 text-blue-500 rounded">
                                本地: {rule.localModel}
                              </span>
                              <span className="text-[10px] px-1.5 py-0.5 bg-purple-500/10 text-purple-500 rounded">
                                在线: {rule.onlineModel}
                              </span>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>


                  </>
                )}
              </SettingCard>

              {/* 保存按钮 */}
              <div className="flex justify-end">
                <button
                  onClick={handleSaveRoutingConfig}
                  className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm hover:bg-primary/90 transition-colors"
                >
                  {t("common.save") || "保存"}
                </button>
              </div>
            </>
          )}

          {/* ─── Agent ───────────────────────────────────────── */}
          {active === "agent" && (
            <>
              <div className="flex items-center gap-2.5 mb-2">
                <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10"><Bot size={16} className="text-primary" /></div>
                <div><h1 className="text-lg font-semibold">{t("settings.defaultAgent")}</h1><p className="text-xs text-muted-foreground">{t("settings.agentTimeoutDesc")}</p></div>
              </div>

              <SettingCard title={t("settings.defaultAgent")} description={t("settings.defaultAgentDesc")}>
                <SelectInput value={settings.defaultAgent} onChange={(v) => update("defaultAgent", v)}
                  options={[{ value: "auto", label: t("settings.autoSelect") }, { value: "hermes", label: "Hermes" }, { value: "mimo", label: "MiMo" }]} />
              </SettingCard>

              <SettingCard title={t("settings.agentTimeout")} description={`${t("settings.agentTimeoutDesc")}: ${settings.agentTimeout}s`}>
                <Slider value={settings.agentTimeout} onChange={(v) => update("agentTimeout", v)} min={5} max={300} step={5} unit="s" />
              </SettingCard>

              <SettingCard title={t("settings.retryStrategy")} description={t("settings.retryStrategyDesc")}>
                <ButtonGroup value={settings.retryStrategy} onChange={(v) => update("retryStrategy", v)}
                  options={[{ value: "none", label: t("settings.noRetry") }, { value: "linear", label: t("settings.linear") }, { value: "exponential", label: t("settings.exponential") }]} />
              </SettingCard>

              <SettingCard title={t("settings.logLevel")} description={t("settings.logLevelDesc")}>
                <ButtonGroup value={settings.logLevel} onChange={(v) => update("logLevel", v)}
                  options={[{ value: "debug", label: "调试" }, { value: "info", label: "信息" }, { value: "warn", label: "警告" }, { value: "error", label: "错误" }]} />
              </SettingCard>
            </>
          )}

          {/* ─── Tools ───────────────────────────────────────── */}
          {active === "agentManage" && <AgentsManager />}

          {active === "tools" && (
            <>
              <div className="flex items-center gap-2.5 mb-2">
                <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10"><Wrench size={16} className="text-primary" /></div>
                <div><h1 className="text-lg font-semibold">{t("settings.toolPermissions")}</h1><p className="text-xs text-muted-foreground">{t("settings.toolPermissionsDesc")}</p></div>
              </div>

              <SettingCard title={t("settings.shellWhitelist")} description={t("settings.shellWhitelistDesc")}>
                <TextInput value={settings.shellWhitelist} onChange={(v) => update("shellWhitelist", v)} placeholder={t("settings.shellWhitelistPlaceholder")} />
              </SettingCard>

              <SettingCard title={t("settings.fileAccess")} description={t("settings.fileAccessDesc")}>
                <ButtonGroup value={settings.fileAccess} onChange={(v) => update("fileAccess", v)}
                  options={[{ value: "full", label: t("settings.fullAccess") }, { value: "restricted", label: t("settings.restricted") }, { value: "readonly", label: t("settings.readonly") }]} />
              </SettingCard>

              <SettingCard title={t("settings.networkAccess")} description={t("settings.networkAccessDesc")}>
                <Toggle checked={settings.networkAccess} onChange={(v) => update("networkAccess", v)} />
              </SettingCard>
            </>
          )}

          {/* ─── Storage ─────────────────────────────────────── */}
          {active === "storage" && (
            <>
              <div className="flex items-center gap-2.5 mb-2">
                <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10"><HardDrive size={16} className="text-primary" /></div>
                <div><h1 className="text-lg font-semibold">{t("settings.storageManagement")}</h1><p className="text-xs text-muted-foreground">{t("settings.storageDesc")}</p></div>
              </div>

              <SettingCard title={t("settings.knowledgePath")} description={t("settings.knowledgePathDesc")}>
                <TextInput value={settings.knowledgePath} onChange={(v) => update("knowledgePath", v)} placeholder={t("settings.knowledgePathPlaceholder")} />
              </SettingCard>

              <SettingCard title={t("settings.cacheLimit")} description={`${t("settings.cacheLimitDesc")}: ${settings.cacheLimit}MB`}>
                <Slider value={settings.cacheLimit} onChange={(v) => update("cacheLimit", v)} min={64} max={4096} step={64} unit="MB" />
              </SettingCard>

              <SettingCard title={t("settings.dataManagement")} description={t("settings.dataManagementDesc")}>
                <div className="flex gap-2">
                  <button onClick={handleExportData} className="px-3 py-2 rounded-lg border border-border text-xs hover:bg-muted flex items-center gap-1.5"><Download size={12} />{t("settings.exportData")}</button>
                  <button onClick={handleImportData} className="px-3 py-2 rounded-lg border border-border text-xs hover:bg-muted flex items-center gap-1.5"><Upload size={12} />{t("settings.importData")}</button>
                  <input ref={fileInputRef} type="file" accept=".json" onChange={handleImportFile} className="hidden" />
                  <button onClick={handleClearCache} className="px-3 py-2 rounded-lg border border-red-500/30 text-xs text-red-500 hover:bg-red-500/5 flex items-center gap-1.5"><Trash2 size={12} />{t("settings.clearCache")}</button>
                </div>
              </SettingCard>
            </>
          )}

          {/* ─── Organs ──────────────────────────────────────── */}
          {active === "organs" && (
            <OrgansSection apiBase={getApiBaseUrl()} />
          )}

          {/* ─── Account ─────────────────────────────────────── */}
          {active === "account" && (
            <>
              <div className="flex items-center gap-2.5 mb-2">
                <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10"><User size={16} className="text-primary" /></div>
                <div><h1 className="text-lg font-semibold">{t("settings.account")}</h1><p className="text-xs text-muted-foreground">{t("settings.accountDesc")}</p></div>
              </div>

              <SettingCard title={t("settings.userInfo")}>
                <div className="flex items-center gap-3 p-3 rounded-lg bg-muted/50">
                  <div className="flex h-10 w-10 items-center justify-center rounded-full bg-primary text-xs lg:text-sm font-medium text-primary-foreground">
                    {(getUserName() || getUserId() || "U")[0].toUpperCase()}
                  </div>
                  <div>
                    <div className="text-xs lg:text-sm font-medium">{getUserName() || getUserId() || "用户"}</div>
                    <div className="text-xs text-muted-foreground">{t("settings.signedIn")}</div>
                  </div>
                </div>
              </SettingCard>

              <SettingCard title={t("settings.thirdPartyBinding", "第三方登录绑定")} description={t("settings.thirdPartyBindingDesc", "绑定微信/QQ 后可一键登录")}>
                <AccountBindings />
              </SettingCard>

              <SettingCard title={t("settings.agentBinding", "Agent 绑定")} description={t("settings.agentBindingDesc", "绑定后消息只走指定 agent/profile（Ekko 式分配路由）")}>
                <AgentBindingCard />
              </SettingCard>

              <SettingCard title={t("settings.apiAddress")} description={t("settings.apiAddressDesc")}>
                <div className="text-xs lg:text-sm font-mono text-muted-foreground p-2 rounded bg-muted/50">{getApiBaseUrl()}</div>
              </SettingCard>

              <SettingCard title={t("settings.logout")} description={t("settings.logoutDesc")}>
                <button onClick={handleLogout} className="px-4 py-2 rounded-lg border border-red-500/30 text-xs lg:text-sm text-red-500 hover:bg-red-500/5 flex items-center gap-2">
                  <LogOut size={14} />{t("settings.logout")}
                </button>
              </SettingCard>
            </>
          )}

          {/* ─── About ───────────────────────────────────────── */}
          {active === "about" && (
            <>
              <div className="flex items-center gap-2.5 mb-2">
                <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10"><Info size={16} className="text-primary" /></div>
                <div><h1 className="text-lg font-semibold">{t("settings.about")}</h1><p className="text-xs text-muted-foreground">{t("settings.aboutDesc")}</p></div>
              </div>

              <SettingCard title="OpenMate">
                <div className="space-y-2 text-xs lg:text-sm">
                  <div className="flex justify-between"><span className="text-muted-foreground">{t("settings.frontendVersion")}</span><span className="font-mono">v0.1.0</span></div>
                  <div className="flex justify-between"><span className="text-muted-foreground">{t("settings.backendVersion")}</span><span className="font-mono">{backendVersion || t("settings.detecting")}</span></div>
                  <div className="flex justify-between"><span className="text-muted-foreground">{t("settings.framework")}</span><span>Next.js + Tauri</span></div>
                  <div className="flex justify-between"><span className="text-muted-foreground">{t("settings.backend")}</span><span>OpenSoul (FastAPI)</span></div>
                  <div className="flex justify-between"><span className="text-muted-foreground">{t("settings.license")}</span><span>MIT</span></div>
                </div>
              </SettingCard>

              <SettingCard title={t("settings.ecosystem")}>
                <div className="grid grid-cols-2 gap-1.5 text-xs">
                  {["OpenSoul", "OpenMate", "OpenSoma", "OpenCortex", "OpenNerve", "OpenVein", "OpenSense", "OpenWill", "OpenVital", "OpenGland", "OpenImmune", "OpenMarrow", "OpenGene", "OpenEcho", "OpenMirror", "OpenLink", "OpenHippo", "OpenReflex", "OpenHeredity", "OpenNest", "OpenPulse", "OpenLimb", "OpenVoice", "OpenVision", "OpenMind"].map(name => (
                    <div key={name} className="flex items-center gap-1.5 p-1.5 rounded bg-muted/50">
                      <span className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-500" />
                      <span>{name}</span>
                    </div>
                  ))}
                </div>
              </SettingCard>

              <SettingCard title={t("settings.links")}>
                <div className="space-y-2">
                  <a href="https://github.com/open-soulmate" target="_blank" className="flex items-center gap-2 text-xs lg:text-sm text-muted-foreground hover:text-foreground">
                    <ExternalLink size={14} />GitHub
                  </a>
                  <a href="#" className="flex items-center gap-2 text-xs lg:text-sm text-muted-foreground hover:text-foreground">
                    <ExternalLink size={14} />Documentation
                  </a>
                </div>
              </SettingCard>
            </>
          )}

          {/* Save button */}
          <div className="sticky bottom-0 pt-4 pb-6 bg-background/80 backdrop-blur-sm">
            <button onClick={handleSave} className={cn("w-full px-4 py-2.5 rounded-xl text-xs lg:text-sm font-medium flex items-center justify-center gap-2 transition-all",
              saved ? "bg-green-500 text-white" : "bg-primary text-primary-foreground hover:bg-primary/90")}>
              {saved ? <><Check size={16} />{t("settings.saved")}</> : <><Save size={16} />{t("settings.saveSettings")}</>}
            </button>
          </div>
        </div>
      </div>
    </div>
  
      </PageLayout>
    );
}

// ─── Organs Management Section ─────────────────────────────────────
function OrgansSection({ apiBase }: { apiBase: string }) {
  const { t } = useTranslation();
  const [organs, setOrgans] = useState<Array<{ key: string; enabled: boolean; config: Record<string, unknown> }>>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${apiBase}/api/organs`)
      .then(r => r.json())
      .then(data => setOrgans(data.organs || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [apiBase]);

  const toggleOrgan = async (key: string, enabled: boolean) => {
    try {
      await fetch(`${apiBase}/api/organs/${key}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      setOrgans(prev => prev.map(o => o.key === key ? { ...o, enabled } : o));
    } catch {}
  };

  const organLabels: Record<string, { label: string; emoji: string }> = {
    cortex: { label: t("nav.cortex"), emoji: "🧠" }, nerve: { label: t("nav.nerve"), emoji: "⚡" },
    vein: { label: t("nav.vein"), emoji: "🩸" }, sense: { label: t("nav.sense"), emoji: "👁" },
    will: { label: t("nav.will"), emoji: "✨" }, immune: { label: t("nav.immune"), emoji: "🛡" },
    vital: { label: t("nav.vital"), emoji: "📊" }, marrow: { label: t("nav.marrow"), emoji: "🦴" },
    gland: { label: t("nav.gland"), emoji: "🧪" }, gene: { label: t("nav.gene"), emoji: "🧬" },
    echo: { label: t("nav.echo"), emoji: "🔊" }, mirror: { label: t("nav.mirror"), emoji: "🪞" },
    link: { label: t("nav.link"), emoji: "🔗" }, hippo: { label: t("nav.hippo"), emoji: "🧠" },
    reflex: { label: t("nav.reflex"), emoji: "⚡" }, heredity: { label: t("nav.heredity"), emoji: "🔗" },
    pulse: { label: t("nav.pulse"), emoji: "💓" }, nest: { label: t("nav.nest"), emoji: "🏠" },
    limb: { label: t("nav.limb"), emoji: "💪" }, voice: { label: t("nav.voice"), emoji: "🎤" },
    vision: { label: t("nav.vision"), emoji: "🎨" }, mind: { label: t("nav.mind"), emoji: "💭" },
    trajectory: { label: t("nav.trajectory"), emoji: "📊" }, mcp: { label: "MCP", emoji: "🔌" },
    learn: { label: t("nav.learn"), emoji: "📚" },
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-32">
        <RefreshCw size={16} className="animate-spin text-muted-foreground" />
      </div>
    );
  }

  const enabledCount = organs.filter(o => o.enabled).length;

  return (
    <>
      <div className="flex items-center gap-2.5 mb-2">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10"><Zap size={16} className="text-primary" /></div>
        <div><h1 className="text-lg font-semibold">{t("settings.organManagement")}</h1><p className="text-xs text-muted-foreground">{t("settings.organManagementDesc")} ({enabledCount}/{organs.length} {t("settings.enabledCount")})</p></div>
      </div>

      <div className="rounded-xl border border-border bg-card p-5">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
          {organs.map(organ => {
            const info = organLabels[organ.key] || { label: organ.key, emoji: "⚙️" };
            return (
              <div key={organ.key} className="flex items-center justify-between p-3 rounded-lg border border-border hover:bg-muted/30 transition-colors">
                <div className="flex items-center gap-2.5">
                  <span className="text-lg">{info.emoji}</span>
                  <div>
                    <div className="text-xs lg:text-sm font-medium">{info.label}</div>
                    <div className="text-[10px] text-muted-foreground font-mono">{organ.key}</div>
                  </div>
                </div>
                <button
                  onClick={() => toggleOrgan(organ.key, !organ.enabled)}
                  className={cn(
                    "relative inline-flex h-5 w-9 items-center rounded-full transition-colors",
                    organ.enabled ? "bg-green-500" : "bg-muted-foreground/30"
                  )}
                >
                  <span className={cn(
                    "inline-block h-3.5 w-3.5 rounded-full bg-white transition-transform",
                    organ.enabled ? "translate-x-4" : "translate-x-0.5"
                  )} />
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
}
