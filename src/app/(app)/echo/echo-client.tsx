"use client";
import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { getApiBaseUrl } from "@/lib/api-client";
import { useIsMobile } from "@/hooks/use-mobile";
import {
  Volume2, RefreshCw, Send, Radio, Settings,
  CheckCircle, XCircle, Clock, MessageSquare,
  FileText, Plus, Trash2, Eye, Zap, Edit3,
  ChevronDown, ChevronRight, Copy,
} from "lucide-react";
import { PageLayout } from '@/components/page-layout';

interface Template {
  template_id: string;
  name: string;
  description: string;
  channel: string;
  title_template: string;
  content_template: string;
  variables: string[];
  category: string;
  icon: string;
  usage_count: number;
  last_used: number;
  created_at: number;
}

export function EchoClient() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<"send" | "channels" | "history" | "templates">("send");
  const [health, setHealth] = useState<any>(null);
  const [channels, setChannels] = useState<any[]>([]);
  const [channelHealth, setChannelHealth] = useState<any>(null);
  const [testingHealth, setTestingHealth] = useState(false);
  const [history, setHistory] = useState<any[]>([]);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [sendTitle, setSendTitle] = useState("");
  const [sendContent, setSendContent] = useState("");
  const [sendChannel, setSendChannel] = useState("webhook");
  const [sendTarget, setSendTarget] = useState("");
  const [sendResult, setSendResult] = useState<any>(null);
  const [chEndpoint, setChEndpoint] = useState("");
  const [chToken, setChToken] = useState("");
  const [chExtra, setChExtra] = useState("");
  const [selectedTemplate, setSelectedTemplate] = useState<Template | null>(null);
  const [templateVars, setTemplateVars] = useState<Record<string, string>>({});
  const [previewResult, setPreviewResult] = useState<any>(null);
  const [showCreateTemplate, setShowCreateTemplate] = useState(false);
  const [newTplName, setNewTplName] = useState("");
  const [newTplTitle, setNewTplTitle] = useState("");
  const [newTplContent, setNewTplContent] = useState("");
  const [newTplDesc, setNewTplDesc] = useState("");
  const [newTplCategory, setNewTplCategory] = useState("custom");
  const [newTplIcon, setNewTplIcon] = useState("📨");
  const [sendingTemplate, setSendingTemplate] = useState(false);
  const isMobile = useIsMobile();
  const apiBase = getApiBaseUrl();

  const fetchHealth = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/echo/health`);
      setHealth(await res.json());
    } catch {}
  }, [apiBase]);

  const fetchChannels = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/echo/channels`);
      const data = await res.json();
      setChannels(data.channels || []);
    } catch {}
  }, [apiBase]);

  const fetchChannelHealth = useCallback(async () => {
    setTestingHealth(true);
    try {
      const res = await fetch(`${apiBase}/api/echo/channels/health`);
      const data = await res.json();
      setChannelHealth(data);
    } catch {}
    setTestingHealth(false);
  }, [apiBase]);

  const fetchHistory = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/echo/history?limit=50`);
      const data = await res.json();
      setHistory(data.messages || []);
    } catch {}
  }, [apiBase]);

  const fetchTemplates = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/echo/templates`);
      const data = await res.json();
      setTemplates(data.templates || []);
    } catch {}
  }, [apiBase]);

  useEffect(() => {
    fetchHealth();
    if (tab === "channels") fetchChannels();
    if (tab === "history") fetchHistory();
    if (tab === "templates") fetchTemplates();
  }, [tab, fetchHealth, fetchChannels, fetchHistory, fetchTemplates]);

  const handleSend = async () => {
    if (!sendTitle.trim() || !sendContent.trim()) return;
    try {
      const res = await fetch(`${apiBase}/api/echo/send`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          channel: sendChannel,
          title: sendTitle,
          content: sendContent,
          target: sendTarget,
        }),
      });
      setSendResult(await res.json());
      fetchHealth();
    } catch {}
  };

  const handleBroadcast = async () => {
    if (!sendTitle.trim() || !sendContent.trim()) return;
    try {
      const res = await fetch(`${apiBase}/api/echo/broadcast`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: sendTitle, content: sendContent }),
      });
      setSendResult(await res.json());
      fetchHealth();
    } catch {}
  };

  const handleConfigure = async (channel: string) => {
    try {
      let extra = {};
      if (chExtra.trim()) {
        try { extra = JSON.parse(chExtra); } catch { /* ignore */ }
      }
      await fetch(`${apiBase}/api/echo/channels/configure`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ channel, endpoint: chEndpoint, token: chToken, enabled: true, extra }),
      });
      setChEndpoint(""); setChToken(""); setChExtra("");
      fetchChannels();
    } catch {}
  };

  const handleTemplateSend = async (tpl: Template) => {
    setSendingTemplate(true);
    try {
      const res = await fetch(`${apiBase}/api/echo/templates/${tpl.template_id}/send`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          variables: templateVars,
          channel: sendChannel,
          target: sendTarget,
        }),
      });
      setSendResult(await res.json());
      fetchHealth();
      fetchTemplates();
    } catch {} finally { setSendingTemplate(false); }
  };

  const handleTemplatePreview = async (tpl: Template) => {
    try {
      const res = await fetch(`${apiBase}/api/echo/templates/${tpl.template_id}/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ variables: templateVars }),
      });
      setPreviewResult(await res.json());
    } catch {}
  };

  const handleCreateTemplate = async () => {
    if (!newTplName.trim() || !newTplTitle.trim() || !newTplContent.trim()) return;
    try {
      await fetch(`${apiBase}/api/echo/templates`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: newTplName,
          title_template: newTplTitle,
          content_template: newTplContent,
          description: newTplDesc,
          category: newTplCategory,
          icon: newTplIcon,
        }),
      });
      setShowCreateTemplate(false);
      setNewTplName(""); setNewTplTitle(""); setNewTplContent(""); setNewTplDesc("");
      fetchTemplates();
    } catch {}
  };

  const handleDeleteTemplate = async (id: string) => {
    if (!confirm(t("echo.confirmDeleteTemplate") || "Delete this template?")) return;
    try {
      await fetch(`${apiBase}/api/echo/templates/${id}`, { method: "DELETE" });
      if (selectedTemplate?.template_id === id) setSelectedTemplate(null);
      fetchTemplates();
    } catch {}
  };

  const categoryColor = (cat: string) => {
    if (cat === "task") return "bg-blue-500/10 text-blue-500";
    if (cat === "alert") return "bg-red-500/10 text-red-500";
    if (cat === "system") return "bg-emerald-500/10 text-emerald-500";
    return "bg-muted text-muted-foreground";
  };

  const tabs = [
    { id: "send" as const, label: t("echo.send") || "Send Message", icon: Send },
    { id: "templates" as const, label: t("echo.templates") || "Templates", icon: FileText },
    { id: "channels" as const, label: t("echo.t17213") || "Channel Management", icon: Radio },
    { id: "history" as const, label: t("echo.history") || "History", icon: Clock },
  ];

  return (
      <PageLayout title="回声">
        
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-3 lg:px-6 py-4">
        <div className="flex items-center gap-2 lg:gap-3 min-w-0 flex-1">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-pink-500/10">
            <Volume2 size={20} className="text-pink-500" />
          </div>
          <div className="min-w-0">
            <h1 className="text-lg lg:text-xl font-semibold truncate">{t("echo.title") || "Echo · Message Push"}</h1>
            <p className="text-xs lg:text-sm text-muted-foreground truncate">{t('organTag.echo')}</p>
          </div>
        </div>
        <button onClick={() => { fetchHealth(); tab === "channels" && fetchChannels(); tab === "history" && fetchHistory(); tab === "templates" && fetchTemplates(); }}
          className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs lg:text-sm hover:bg-muted">
          <RefreshCw size={14} /> {t("common.refresh") || "Refresh"}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3 lg:px-6 py-4 lg:py-6 space-y-3 lg:space-y-6">
        {/* Stats */}
        {health && (
          <div className="grid grid-cols-2 md:grid-cols-5 gap-2 lg:gap-4">
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
              <span className="text-xs text-muted-foreground">{t("echo.t38907") || "Total Messages"}</span>
              <p className="text-xl lg:text-2xl font-bold">{health.total_messages || 0}</p>
            </div>
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
              <span className="text-xs text-muted-foreground">{t("echo.success") || "Success"}</span>
              <p className="text-xl lg:text-2xl font-bold text-emerald-500">{health.sent || 0}</p>
            </div>
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
              <span className="text-xs text-muted-foreground">{t("echo.fail") || "Failed"}</span>
              <p className="text-xl lg:text-2xl font-bold text-red-500">{health.failed || 0}</p>
            </div>
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
              <span className="text-xs text-muted-foreground">{t("echo.channel") || "Channel"}</span>
              <p className="text-xl lg:text-2xl font-bold">{health.channels_enabled || 0}/{health.channels_configured || 0}</p>
            </div>
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
              <span className="text-xs text-muted-foreground">{t("echo.templates") || "Templates"}</span>
              <p className="text-xl lg:text-2xl font-bold">{health.templates?.total_templates || 0}</p>
              <p className="text-xs text-muted-foreground mt-0.5">{(t("echo.usageTimes") || "Used {{count}} times").replace("{count}", String(health.templates?.total_usage || 0))}</p>
            </div>
          </div>
        )}

        {/* Tabs */}
        <div className="flex gap-2">
          {tabs.map((tabItem) => (
            <button key={tabItem.id} onClick={() => setTab(tabItem.id)}
              className={cn("flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs lg:text-sm",
                tab === tabItem.id ? "bg-pink-500/10 text-pink-600 font-medium" : "hover:bg-muted text-muted-foreground")}>
              <tabItem.icon size={14} /> {tabItem.label}
            </button>
          ))}
        </div>

        {/* Send Tab */}
        {tab === "send" && (
          <div className="space-y-4 max-w-xl">
            <div className="flex flex-col sm:flex-row gap-2 lg:gap-3">
              <select value={sendChannel} onChange={(e) => setSendChannel(e.target.value)}
                className="rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm">
                <option value="webhook">Webhook</option>
                <option value="dingtalk">{t("echo.t83975") || "DingTalk"}</option>
                <option value="wechat_work">{t("echo.t19991") || "WeCom"}</option>
                <option value="feishu">{t("echo.t80862") || "Feishu"}</option>
                <option value="telegram">Telegram</option>
                <option value="email">{t("echo.t32383") || "Email"}</option>
                <option value="console">{t("echo.t24631") || "Console"}</option>
              </select>
              <input value={sendTarget} onChange={(e) => setSendTarget(e.target.value)}
                placeholder={t("echo.t63160") || "Target address (optional)"} className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
            </div>
            <input value={sendTitle} onChange={(e) => setSendTitle(e.target.value)}
              placeholder={t("echo.t20317") || "Message title"} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
            <textarea value={sendContent} onChange={(e) => setSendContent(e.target.value)}
              placeholder={t("echo.t31187") || "Message content"} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm min-h-[100px] resize-none" />
            <div className="flex gap-2">
              <button onClick={handleSend}
                className="flex items-center gap-2 rounded-lg bg-pink-500 px-2 lg:px-4 py-2 text-xs lg:text-sm text-white hover:bg-pink-600">
                <Send size={14} /> {t("echo.t72588") || "Send"}
              </button>
              <button onClick={handleBroadcast}
                className="flex items-center gap-2 rounded-lg border border-pink-500/30 px-2 lg:px-4 py-2 text-xs lg:text-sm text-pink-600 hover:bg-pink-500/10">
                <Radio size={14} /> {t("echo.t63980") || "Broadcast all channels"}
              </button>
            </div>
            {sendResult && (
              <div className={cn("rounded-lg border p-3 text-xs lg:text-sm",
                sendResult.success !== false ? "border-emerald-500/30 bg-emerald-500/5" : "border-red-500/30 bg-red-500/5")}>
                <pre className="text-xs">{JSON.stringify(sendResult, null, 2)}</pre>
              </div>
            )}
          </div>
        )}

        {/* Templates Tab */}
        {tab === "templates" && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-xs lg:text-sm font-medium">{t("echo.messageTemplates") || "Message Templates"}</h3>
              <button onClick={() => setShowCreateTemplate(true)}
                className="flex items-center gap-1.5 rounded-lg bg-pink-500 px-3 py-1.5 text-xs lg:text-sm text-white hover:bg-pink-600">
                <Plus size={14} /> {t("echo.createTemplate") || "New Template"}
              </button>
            </div>

            <div className="flex gap-2 lg:gap-6 relative">
              {/* Template List — full width on mobile, w-80 on desktop */}
              <div className={`${isMobile ? (selectedTemplate ? "hidden" : "w-full") : "w-80"} space-y-2`}>
                {isMobile && (
                  <div className="flex items-center justify-between pb-2 border-b border-border">
                    <h3 className="text-xs lg:text-sm font-medium">{t("echo.templates") || "Templates"}</h3>
                    <span className="text-xs text-muted-foreground">{templates.length}</span>
                  </div>
                )}
                {templates.map((tpl) => (
                  <div key={tpl.template_id}
                    onClick={() => { setSelectedTemplate(tpl); setTemplateVars({}); setPreviewResult(null); }}
                    className={cn(
                      "rounded-xl border border-border bg-card p-3 cursor-pointer transition-all hover:shadow-md",
                      selectedTemplate?.template_id === tpl.template_id && "ring-2 ring-pink-500"
                    )}>
                    <div className="flex items-center justify-between mb-1">
                      <div className="flex items-center gap-2">
                        <span>{tpl.icon}</span>
                        <span className="font-medium text-xs lg:text-sm">{tpl.name}</span>
                      </div>
                      <span className={cn("text-[10px] px-1.5 py-0.5 rounded-full", categoryColor(tpl.category))}>
                        {tpl.category}
                      </span>
                    </div>
                    <p className="text-xs text-muted-foreground line-clamp-1">{tpl.description}</p>
                    <div className="flex items-center gap-2 lg:gap-3 mt-1.5 text-[10px] text-muted-foreground">
                      <span>{(t("echo.variableCount") || "Variables: {{count}}").replace("{count}", String(tpl.variables.length))}</span>
                      <span>{(t("echo.usageCount") || "Used: {{count}}").replace("{count}", String(tpl.usage_count))}</span>
                    </div>
                  </div>
                ))}
                {templates.length === 0 && (
                  <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
                    <FileText size={32} className="mb-2 opacity-30" />
                    <p className="text-xs lg:text-sm">{t("echo.noTemplates") || "No templates yet"}</p>
                  </div>
                )}
              </div>

              {/* Template Detail / Send Panel — sidebar-style sliding on mobile, inline on desktop */}
              {isMobile && !!selectedTemplate && (
                <div className="fixed inset-0 z-9 bg-black/40 animate-in fade-in-0" onClick={() => setSelectedTemplate(null)} aria-hidden="true" />
              )}
              {isMobile ? (
                !!selectedTemplate && (
                  <div
                    className="absolute inset-y-0 right-0 z-10 h-full w-full min-w-0 border-l border-border transition-[right] duration-200 ease-linear flex flex-col overflow-hidden bg-card"
                    style={{ right: 0 }}
                  >
                    <div className="flex-1 overflow-y-auto p-3 lg:p-4 space-y-4">
                      {selectedTemplate && (
                        <>
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2">
                              <span className="text-lg">{selectedTemplate.icon}</span>
                              <h3 className="font-semibold">{selectedTemplate.name}</h3>
                              <span className={cn("text-[10px] px-1.5 py-0.5 rounded-full", categoryColor(selectedTemplate.category))}>
                                {selectedTemplate.category}
                              </span>
                            </div>
                            <div className="flex gap-2">
                              <button onClick={() => handleDeleteTemplate(selectedTemplate.template_id)}
                                className="rounded-lg border border-red-500/30 px-2 py-1 text-xs text-red-500 hover:bg-red-500/10">
                                <Trash2 size={12} />
                              </button>
                            </div>
                          </div>

                          <p className="text-xs text-muted-foreground">{selectedTemplate.description}</p>

                          {/* Template Preview */}
                          <div className="rounded-lg border border-border bg-muted/20 p-3">
                            <div className="text-xs text-muted-foreground mb-1">{t("echo.titleTemplate") || "Title Template"}</div>
                            <div className="text-xs lg:text-sm font-mono">{selectedTemplate.title_template}</div>
                            <div className="text-xs text-muted-foreground mt-2 mb-1">{t("echo.contentTemplate") || "Content Template"}</div>
                            <pre className="text-xs font-mono whitespace-pre-wrap">{selectedTemplate.content_template}</pre>
                          </div>

                          {/* Variables Input */}
                          {selectedTemplate.variables.length > 0 && (
                            <div className="rounded-xl border border-border p-3 lg:p-4 space-y-3">
                              <h4 className="text-xs lg:text-sm font-medium">{t("echo.fillVariables") || "Fill Variables"}</h4>
                              <div className="grid grid-cols-2 gap-2 lg:gap-3">
                                {selectedTemplate.variables.map((v) => (
                                  <div key={v}>
                                    <label className="text-xs text-muted-foreground font-mono">{`{{${v}}}`}</label>
                                    <input
                                      value={templateVars[v] || ""}
                                      onChange={(e) => setTemplateVars({ ...templateVars, [v]: e.target.value })}
                                      placeholder={v}
                                      className="mt-1 w-full rounded border border-border bg-background px-2 py-1.5 text-xs"
                                    />
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}

                          {/* Channel Override */}
                          <div className="flex flex-col sm:flex-row gap-2 lg:gap-3">
                            <select value={sendChannel} onChange={(e) => setSendChannel(e.target.value)}
                              className="rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm">
                              <option value="console">控制台</option>
                              <option value="webhook">Webhook</option>
                              <option value="dingtalk">{t("echo.t83975") || "DingTalk"}</option>
                              <option value="wechat_work">{t("echo.t19991") || "WeCom"}</option>
                              <option value="feishu">{t("echo.t80862") || "Feishu"}</option>
                              <option value="telegram">Telegram</option>
                              <option value="email">{t("echo.t32383") || "Email"}</option>
                            </select>
                            <input value={sendTarget} onChange={(e) => setSendTarget(e.target.value)}
                              placeholder={t("echo.t63160") || "Target address (optional)"} className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
                          </div>

                          {/* Action Buttons */}
                          <div className="flex gap-2">
                            <button onClick={() => handleTemplateSend(selectedTemplate)} disabled={sendingTemplate}
                              className="flex items-center gap-2 rounded-lg bg-pink-500 px-2 lg:px-4 py-2 text-xs lg:text-sm text-white hover:bg-pink-600 disabled:opacity-50">
                              {sendingTemplate ? <RefreshCw size={14} className="animate-spin" /> : <Zap size={14} />}
                              {t("echo.t72588") || "Send"}
                            </button>
                            <button onClick={() => handleTemplatePreview(selectedTemplate)}
                              className="flex items-center gap-2 rounded-lg border border-border px-2 lg:px-4 py-2 text-xs lg:text-sm hover:bg-muted">
                              <Eye size={14} /> {t("echo.preview") || "Preview"}
                            </button>
                          </div>

                          {/* Preview Result */}
                          {previewResult && (
                            <div className="rounded-lg border border-pink-500/30 bg-pink-500/5 p-3 lg:p-4 space-y-2">
                              <div className="text-xs text-pink-500 font-medium">{t("echo.previewResult") || "Preview Result"}</div>
                              <div className="font-medium">{previewResult.title}</div>
                              <pre className="text-xs whitespace-pre-wrap text-muted-foreground">{previewResult.content}</pre>
                            </div>
                          )}

                          {/* Send Result */}
                          {sendResult && (
                            <div className={cn("rounded-lg border p-3 text-xs lg:text-sm",
                              sendResult.success !== false ? "border-emerald-500/30 bg-emerald-500/5" : "border-red-500/30 bg-red-500/5")}>
                              {sendResult.rendered_title && (
                                <div className="font-medium mb-1">{sendResult.rendered_title}</div>
                              )}
                              <pre className="text-xs">{JSON.stringify(sendResult, null, 2)}</pre>
                            </div>
                          )}
                        </>
                      )}
                    </div>
                  </div>
                )
              ) : (
                selectedTemplate && (
                  <div className="flex-1 space-y-4">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="text-lg">{selectedTemplate.icon}</span>
                        <h3 className="font-semibold">{selectedTemplate.name}</h3>
                        <span className={cn("text-[10px] px-1.5 py-0.5 rounded-full", categoryColor(selectedTemplate.category))}>
                          {selectedTemplate.category}
                        </span>
                      </div>
                      <div className="flex gap-2">
                        <button onClick={() => handleDeleteTemplate(selectedTemplate.template_id)}
                          className="rounded-lg border border-red-500/30 px-2 py-1 text-xs text-red-500 hover:bg-red-500/10">
                          <Trash2 size={12} />
                        </button>
                      </div>
                    </div>

                    <p className="text-xs text-muted-foreground">{selectedTemplate.description}</p>

                    {/* Template Preview */}
                    <div className="rounded-lg border border-border bg-muted/20 p-3">
                      <div className="text-xs text-muted-foreground mb-1">{t("echo.titleTemplate") || "Title Template"}</div>
                      <div className="text-xs lg:text-sm font-mono">{selectedTemplate.title_template}</div>
                      <div className="text-xs text-muted-foreground mt-2 mb-1">{t("echo.contentTemplate") || "Content Template"}</div>
                      <pre className="text-xs font-mono whitespace-pre-wrap">{selectedTemplate.content_template}</pre>
                    </div>

                    {/* Variables Input */}
                    {selectedTemplate.variables.length > 0 && (
                      <div className="rounded-xl border border-border p-3 lg:p-4 space-y-3">
                        <h4 className="text-xs lg:text-sm font-medium">{t("echo.fillVariables") || "Fill Variables"}</h4>
                        <div className="grid grid-cols-2 gap-2 lg:gap-3">
                          {selectedTemplate.variables.map((v) => (
                            <div key={v}>
                              <label className="text-xs text-muted-foreground font-mono">{`{{${v}}}`}</label>
                              <input
                                value={templateVars[v] || ""}
                                onChange={(e) => setTemplateVars({ ...templateVars, [v]: e.target.value })}
                                placeholder={v}
                                className="mt-1 w-full rounded border border-border bg-background px-2 py-1.5 text-xs"
                              />
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Channel Override */}
                    <div className="flex flex-col sm:flex-row gap-2 lg:gap-3">
                      <select value={sendChannel} onChange={(e) => setSendChannel(e.target.value)}
                        className="rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm">
                        <option value="console">控制台</option>
                        <option value="webhook">Webhook</option>
                        <option value="dingtalk">{t("echo.t83975") || "DingTalk"}</option>
                        <option value="wechat_work">{t("echo.t19991") || "WeCom"}</option>
                        <option value="feishu">{t("echo.t80862") || "Feishu"}</option>
                        <option value="telegram">Telegram</option>
                        <option value="email">{t("echo.t32383") || "Email"}</option>
                      </select>
                      <input value={sendTarget} onChange={(e) => setSendTarget(e.target.value)}
                        placeholder={t("echo.t63160") || "Target address (optional)"} className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
                    </div>

                    {/* Action Buttons */}
                    <div className="flex gap-2">
                      <button onClick={() => handleTemplateSend(selectedTemplate)} disabled={sendingTemplate}
                        className="flex items-center gap-2 rounded-lg bg-pink-500 px-2 lg:px-4 py-2 text-xs lg:text-sm text-white hover:bg-pink-600 disabled:opacity-50">
                        {sendingTemplate ? <RefreshCw size={14} className="animate-spin" /> : <Zap size={14} />}
                        {t("echo.t72588") || "Send"}
                      </button>
                      <button onClick={() => handleTemplatePreview(selectedTemplate)}
                        className="flex items-center gap-2 rounded-lg border border-border px-2 lg:px-4 py-2 text-xs lg:text-sm hover:bg-muted">
                        <Eye size={14} /> {t("echo.preview") || "Preview"}
                      </button>
                    </div>

                    {/* Preview Result */}
                    {previewResult && (
                      <div className="rounded-lg border border-pink-500/30 bg-pink-500/5 p-3 lg:p-4 space-y-2">
                        <div className="text-xs text-pink-500 font-medium">{t("echo.previewResult") || "Preview Result"}</div>
                        <div className="font-medium">{previewResult.title}</div>
                        <pre className="text-xs whitespace-pre-wrap text-muted-foreground">{previewResult.content}</pre>
                      </div>
                    )}

                    {/* Send Result */}
                    {sendResult && (
                      <div className={cn("rounded-lg border p-3 text-xs lg:text-sm",
                        sendResult.success !== false ? "border-emerald-500/30 bg-emerald-500/5" : "border-red-500/30 bg-red-500/5")}>
                        {sendResult.rendered_title && (
                          <div className="font-medium mb-1">{sendResult.rendered_title}</div>
                        )}
                        <pre className="text-xs">{JSON.stringify(sendResult, null, 2)}</pre>
                      </div>
                    )}
                  </div>
                )
              )}
            </div>

            {/* Create Template Modal */}
            {showCreateTemplate && (
              <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
                <div className="w-full max-w-lg rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4 space-y-4">
                  <h3 className="font-semibold">{t("echo.createNewTemplate") || "New Message Template"}</h3>
                  <div className="grid grid-cols-2 gap-2 lg:gap-3">
                    <input value={newTplName} onChange={(e) => setNewTplName(e.target.value)}
                      placeholder={t("echo.templateName") || "Template Name"} className="rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
                    <div className="flex gap-2">
                      <input value={newTplIcon} onChange={(e) => setNewTplIcon(e.target.value)}
                        placeholder={t("echo.icon") || "Icon"} className="w-16 rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
                      <select value={newTplCategory} onChange={(e) => setNewTplCategory(e.target.value)}
                        className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm">
                        <option value="custom">{t("echo.custom") || "Custom"}</option>
                        <option value="task">{t("echo.task") || "Task"}</option>
                        <option value="alert">{t("echo.alert") || "Alert"}</option>
                        <option value="system">{t("echo.system") || "System"}</option>
                      </select>
                    </div>
                  </div>
                  <input value={newTplDesc} onChange={(e) => setNewTplDesc(e.target.value)}
                    placeholder={t("echo.descriptionOptional") || "Description (optional)"} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
                  <input value={newTplTitle} onChange={(e) => setNewTplTitle(e.target.value)}
                    placeholder={t("echo.titleTemplatePlaceholder") || "Title template, e.g.: ✅ Task complete: {{task_name}}"} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm" />
                  <textarea value={newTplContent} onChange={(e) => setNewTplContent(e.target.value)}
                    placeholder={t("echo.contentTemplatePlaceholder") || "Content template, supports {{variable}} variables"} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm min-h-[120px] resize-none" />
                  <p className="text-xs text-muted-foreground">{t("echo.variableHelp") || "Use {{variable_name}} syntax to define variables, e.g. {{task_name}}, {{duration}}"}</p>
                  <div className="flex justify-end gap-2">
                    <button onClick={() => setShowCreateTemplate(false)}
                      className="rounded-lg border border-border px-2 lg:px-4 py-2 text-xs lg:text-sm hover:bg-muted">{t("echo.cancel") || "Cancel"}</button>
                    <button onClick={handleCreateTemplate}
                      className="rounded-lg bg-pink-500 px-2 lg:px-4 py-2 text-xs lg:text-sm text-white hover:bg-pink-600">{t("echo.create") || "Create"}</button>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Channels Tab */}
        {tab === "channels" && (
          <div className="space-y-4">
            <div className="flex gap-2 lg:gap-3 items-end">
              <div>
                <label className="text-xs text-muted-foreground">端点</label>
                <input value={chEndpoint} onChange={(e) => setChEndpoint(e.target.value)}
                  placeholder="Webhook URL / SMTP 主机"
                  className="block rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm w-full sm:w-80" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">Token / 密码</label>
                <input value={chToken} onChange={(e) => setChToken(e.target.value)}
                  placeholder="API Token / SMTP 密码" type="password"
                  className="block rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm w-48" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">附加参数（JSON）</label>
                <input value={chExtra} onChange={(e) => setChExtra(e.target.value)}
                  placeholder='{"smtp_port":587,"username":"..."}'
                  className="block rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm w-full sm:w-64" />
              </div>
              <button onClick={fetchChannelHealth} disabled={testingHealth}
                className="flex items-center gap-1.5 rounded-lg bg-emerald-500 px-3 py-2 text-xs lg:text-sm text-white hover:bg-emerald-600 disabled:opacity-50">
                {testingHealth ? <RefreshCw size={14} className="animate-spin" /> : <CheckCircle size={14} />}
                {t("echo.testHealth") || "Test Health"}
              </button>
            </div>
            {/* Channel Health Summary */}
            {channelHealth && (
              <div className={cn("rounded-xl border p-3 text-xs lg:text-sm",
                channelHealth.status === "ok" ? "border-emerald-500/30 bg-emerald-500/5" : "border-yellow-500/30 bg-yellow-500/5")}>
                <div className="flex items-center gap-2">
                  {channelHealth.status === "ok" ? <CheckCircle size={14} className="text-emerald-500" /> : <XCircle size={14} className="text-yellow-500" />}
                  <span className="font-medium">
                    {channelHealth.healthy}/{channelHealth.total} {t("echo.channelsHealthy") || "Channels Healthy"}
                  </span>
                </div>
              </div>
            )}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2 lg:gap-4">
              {channels.map((ch) => {
                const healthInfo = channelHealth?.channels?.find((h: any) => h.channel === ch.channel);
                return (
                <div key={ch.channel} className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <Radio size={16} className={ch.enabled ? "text-emerald-500" : "text-muted-foreground"} />
                      <span className="font-medium text-xs lg:text-sm">{ch.channel}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      {healthInfo && (
                        <span className={cn("text-[10px] px-1.5 py-0.5 rounded-full",
                          healthInfo.status === "ok" ? "bg-emerald-500/20 text-emerald-500" :
                          healthInfo.status === "unconfigured" ? "bg-yellow-500/20 text-yellow-500" :
                          "bg-red-500/20 text-red-500")}>
                          {healthInfo.status === "ok" ? `✓ ${healthInfo.latency_ms}ms` : healthInfo.status}
                        </span>
                      )}
                      <span className={cn("text-xs", ch.enabled ? "text-emerald-500" : "text-muted-foreground")}>
                        {ch.enabled ? t("echo.enabled") || "Enabled" : t("echo.t00979") || "Disabled"}
                      </span>
                    </div>
                  </div>
                  {ch.has_endpoint && <p className="text-xs text-muted-foreground font-mono">{t("echo.t53402") || "Endpoint configured"}</p>}
                  {ch.has_token && <p className="text-xs text-muted-foreground">{t("echo.t36791") || "Token configured"}</p>}
                  {healthInfo?.error && <p className="text-xs text-red-400 mt-1">{healthInfo.error}</p>}
                  <button onClick={() => handleConfigure(ch.channel)}
                    className="mt-2 rounded-lg border border-border px-3 py-1 text-xs hover:bg-muted">
                    {t("echo.t45063") || "Update config"}
                  </button>
                </div>
                );
              })}
            </div>
          </div>
        )}

        {/* History Tab */}
        {tab === "history" && (
          <div className="rounded-xl border border-border overflow-x-auto">
            <table className="w-full text-xs lg:text-sm">
              <thead>
                <tr className="border-b border-border bg-muted/30">
                  <th className="px-2 lg:px-4 py-2.5 text-left font-medium text-muted-foreground">{t("echo.time") || "Time"}</th>
                  <th className="px-2 lg:px-4 py-2.5 text-left font-medium text-muted-foreground">{t("echo.channel") || "Channel"}</th>
                  <th className="px-2 lg:px-4 py-2.5 text-left font-medium text-muted-foreground">{t("echo.title_label") || "Title"}</th>
                  <th className="px-2 lg:px-4 py-2.5 text-left font-medium text-muted-foreground">{t("echo.status") || "Status"}</th>
                </tr>
              </thead>
              <tbody>
                {history.length === 0 ? (
                  <tr><td colSpan={4} className="px-2 lg:px-4 py-8 text-center text-muted-foreground text-xs">{t("echo.t98064") || "No message history"}</td></tr>
                ) : history.map((m, i) => (
                  <tr key={m.msg_id || i} className="border-b border-border last:border-0 hover:bg-muted/30">
                    <td className="px-2 lg:px-4 py-2.5 text-xs text-muted-foreground">{new Date(m.timestamp * 1000).toLocaleString('zh-CN', { fractionalSecondDigits: 3 })}</td>
                    <td className="px-2 lg:px-4 py-2.5 text-xs font-mono">{m.channel}</td>
                    <td className="px-2 lg:px-4 py-2.5 text-xs">{m.title}</td>
                    <td className="px-2 lg:px-4 py-2.5">
                      {m.status === "sent" ? <CheckCircle size={14} className="text-emerald-500" /> : <XCircle size={14} className="text-red-500" />}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  
      </PageLayout>
    );
}
