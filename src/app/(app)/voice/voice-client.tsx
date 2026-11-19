"use client";
import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { getApiBaseUrl } from "@/lib/api-client";
import {
  Volume2, RefreshCw, CheckCircle, XCircle, Loader2,
  Mic, Languages, Hash, Zap, Database, Settings,
  Globe, TrendingUp, BarChart3,
  Play, Download, Trash2, BookOpen,
} from "lucide-react";
import { PageLayout } from '@/components/page-layout';

interface VoiceStats {
  status: string;
  component: string;
  engine: {
    backends: Record<string, boolean>;
    preferred_backend: string;
    total_synthesized: number;
    total_characters: number;
    cache_hits: number;
    errors: number;
    cache: {
      entries: number;
      size_bytes: number;
      max_size_bytes: number;
    };
  };
  profiles: {
    total_profiles: number;
    builtin_count: number;
    user_count: number;
    by_language: Record<string, number>;
  };
}

interface VoiceProfile {
  profile_id: string;
  name: string;
  language: string;
  voice_id: string;
  builtin: boolean;
  description?: string;
}

interface VoiceOutput {
  output_id: string;
  text: string;
  profile_id?: string;
  voice_id: string;
  duration_seconds: number;
  size_bytes: number;
  created_at: number;
  audio_url: string;
}

interface KnowledgeItem {
  id: string;
  title: string;
  content: string;
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
}

function StatusBadge({ online }: { online: boolean }) {
  return online ? (
    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-500">
      <CheckCircle size={10} /> Online
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded-full bg-red-500/10 px-2 py-0.5 text-[10px] font-medium text-red-500">
      <XCircle size={10} /> Offline
    </span>
  );
}

export function VoiceClient() {
  const { t } = useTranslation();
  const apiBase = getApiBaseUrl();
  const [stats, setStats] = useState<VoiceStats | null>(null);
  const [profiles, setProfiles] = useState<VoiceProfile[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  // ── TTS 合成 ──
  const [synthText, setSynthText] = useState("你好，OpenMate 语音引擎测试");
  const [synthProfileId, setSynthProfileId] = useState("");
  const [synthRate, setSynthRate] = useState(0);
  const [synthesizing, setSynthesizing] = useState(false);
  const [synthError, setSynthError] = useState("");
  const [outputs, setOutputs] = useState<VoiceOutput[]>([]);
  const [knowledge, setKnowledge] = useState<KnowledgeItem[]>([]);
  const [readingId, setReadingId] = useState("");

  const fetchStats = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch(`${apiBase}/api/voice/stats`);
      if (res.ok) {
        setStats(await res.json());
      } else {
        setError(`HTTP ${res.status}`);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to fetch stats");
    } finally {
      setLoading(false);
    }
  }, [apiBase]);

  const fetchProfiles = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/voice/profiles`);
      if (res.ok) {
        const data = await res.json();
        setProfiles(data.profiles || data || []);
      }
    } catch {}
  }, [apiBase]);

  const fetchOutputs = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/voice/outputs`);
      if (res.ok) {
        const data = await res.json();
        setOutputs(data.outputs || []);
      }
    } catch {}
  }, [apiBase]);

  const fetchKnowledge = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/knowledge/?user_id=default&limit=10`);
      if (res.ok) {
        const data = await res.json();
        setKnowledge(Array.isArray(data) ? data : []);
      }
    } catch {}
  }, [apiBase]);

  const runSynthesize = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || synthesizing) return;
      setSynthesizing(true);
      setSynthError("");
      try {
        const res = await fetch(`${apiBase}/api/voice/synthesize`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            text: trimmed,
            profile_id: synthProfileId,
            rate: `${synthRate >= 0 ? "+" : ""}${synthRate}%`,
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          const detail = data?.detail;
          throw new Error(typeof detail === "string" ? detail : `HTTP ${res.status}`);
        }
        await fetchOutputs();
      } catch (e) {
        setSynthError(e instanceof Error ? e.message : String(e));
      } finally {
        setSynthesizing(false);
      }
    },
    [apiBase, synthProfileId, synthRate, synthesizing, fetchOutputs]
  );

  const deleteOutput = useCallback(
    async (outputId: string) => {
      try {
        const res = await fetch(`${apiBase}/api/voice/outputs/${outputId}`, { method: "DELETE" });
        if (res.ok) {
          await fetchOutputs();
        } else {
          setSynthError(`HTTP ${res.status}`);
        }
      } catch (e) {
        setSynthError(e instanceof Error ? e.message : String(e));
      }
    },
    [apiBase, fetchOutputs]
  );

  const readKnowledge = useCallback(
    async (item: KnowledgeItem) => {
      setReadingId(item.id);
      try {
        const res = await fetch(`${apiBase}/api/knowledge/${item.id}?user_id=default`);
        const full = res.ok ? await res.json() : item;
        const content = full.content || item.content || item.title || "";
        setSynthText(content);
        await runSynthesize(content);
      } catch (e) {
        setSynthError(e instanceof Error ? e.message : String(e));
      } finally {
        setReadingId("");
      }
    },
    [apiBase, runSynthesize]
  );

  useEffect(() => {
    fetchStats();
    fetchProfiles();
    fetchOutputs();
    fetchKnowledge();
    const interval = setInterval(fetchStats, 30000);
    return () => clearInterval(interval);
  }, [fetchStats, fetchProfiles, fetchOutputs, fetchKnowledge]);

  const backends = stats?.engine?.backends || {};
  const languages = stats?.profiles?.by_language || {};

  return (
      <PageLayout title="声带">
        
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-3 lg:px-6 py-4">
        <div className="flex items-center gap-2 lg:gap-3 min-w-0 flex-1">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-rose-500/10">
            <Volume2 size={18} className="text-rose-500 shrink-0" />
          </div>
          <div className="min-w-0">
            <h1 className="text-lg lg:text-xl font-semibold truncate">{t("voice.title") || "Voice Engine"}</h1>
            <p className="text-xs lg:text-sm text-muted-foreground truncate">{t('organTag.voice')}</p>
          </div>
          {stats && <StatusBadge online={stats.status === "ok"} />}
        </div>
        <button
          onClick={() => { fetchStats(); fetchProfiles(); }}
          disabled={loading}
          className="flex items-center gap-1.5 rounded-lg border border-border px-2 lg:px-3 py-1.5 text-xs lg:text-sm hover:bg-muted transition-colors disabled:opacity-50 shrink-0 touch-manipulation"
        >
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
          <span className="hidden sm:inline">{t("common.refresh") || "Refresh"}</span>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3 lg:px-6 py-4 lg:py-6 space-y-3 lg:space-y-6">
        {error && (
          <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 lg:p-4 text-xs lg:text-sm text-red-500">
            {error}
          </div>
        )}

        {/* TTS 合成 */}
        <div className="rounded-xl border border-border bg-card p-3 lg:p-4 space-y-3">
          <h3 className="text-xs lg:text-sm font-semibold flex items-center gap-2">
            <Mic size={14} className="text-rose-500" />
            {t("voice.synth.title", "语音合成")}
          </h3>
          <textarea
            value={synthText}
            onChange={(e) => setSynthText(e.target.value)}
            rows={3}
            placeholder={t("voice.synth.placeholder", "输入要合成的文字…")}
            className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs lg:text-sm focus:outline-none focus:ring-1 focus:ring-primary"
          />
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-xs text-muted-foreground mr-1">
              {t("voice.synth.voice", "音色")}
            </span>
            <button
              onClick={() => setSynthProfileId("")}
              className={cn(
                "rounded px-2 py-1 text-xs no-underline transition-colors",
                synthProfileId === ""
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-muted"
              )}
            >
              {t("voice.synth.defaultVoice", "默认")}
            </button>
            {profiles.map((p) => (
              <button
                key={p.profile_id}
                onClick={() => setSynthProfileId(p.profile_id)}
                className={cn(
                  "rounded px-2 py-1 text-xs no-underline transition-colors",
                  synthProfileId === p.profile_id
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground hover:bg-muted"
                )}
              >
                {p.name}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-3">
            <span className="text-xs text-muted-foreground shrink-0">
              {t("voice.synth.rate", "语速")}
            </span>
            <input
              type="range"
              min={-50}
              max={50}
              step={5}
              value={synthRate}
              onChange={(e) => setSynthRate(Number(e.target.value))}
              className="flex-1 accent-primary"
            />
            <span className="w-12 text-right text-xs font-mono text-muted-foreground shrink-0">
              {synthRate >= 0 ? `+${synthRate}%` : `${synthRate}%`}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => runSynthesize(synthText)}
              disabled={synthesizing || !synthText.trim()}
              className="flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs lg:text-sm text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50 no-underline"
            >
              {synthesizing ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Play size={14} />
              )}
              {synthesizing
                ? t("voice.synth.running", "合成中…")
                : t("voice.synth.button", "合成")}
            </button>
            {synthError && (
              <span className="text-xs text-red-500 truncate">{synthError}</span>
            )}
          </div>
        </div>

        {/* 合成结果 */}
        <div className="rounded-xl border border-border bg-card overflow-hidden">
          <div className="px-5 py-3 border-b border-border bg-muted/30 flex items-center gap-2">
            <Volume2 size={14} className="text-rose-500" />
            <h3 className="text-xs lg:text-sm font-semibold">
              {t("voice.outputs.title", "合成结果")}
            </h3>
            <span className="rounded-full bg-rose-500/10 px-2 py-0.5 text-[10px] font-medium text-rose-500">
              {outputs.length}
            </span>
          </div>
          {outputs.length === 0 ? (
            <p className="px-5 py-6 text-xs text-muted-foreground text-center">
              {t("voice.outputs.empty", "暂无合成结果，先合成一段文字吧")}
            </p>
          ) : (
            <div className="divide-y divide-border">
              {outputs.map((o) => (
                <div key={o.output_id} className="px-5 py-3 flex flex-col gap-2">
                  <div className="flex items-start gap-2">
                    <p className="flex-1 text-xs lg:text-sm line-clamp-2">{o.text}</p>
                    <button
                      onClick={() => deleteOutput(o.output_id)}
                      title={t("voice.outputs.delete", "删除")}
                      className="rounded p-1 text-muted-foreground hover:bg-red-500/10 hover:text-red-500 transition-colors shrink-0"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[10px] font-mono text-muted-foreground">
                      {o.voice_id}
                    </span>
                    <span className="text-[10px] text-muted-foreground">
                      {(o.duration_seconds ?? 0).toFixed(1)}s · {formatBytes(o.size_bytes)}
                    </span>
                    <audio
                      controls
                      preload="none"
                      src={`${apiBase}${o.audio_url}`}
                      className="h-8 flex-1 min-w-[180px]"
                    />
                    <a
                      href={`${apiBase}${o.audio_url}`}
                      download={`${o.output_id}.mp3`}
                      className="flex items-center gap-1 rounded px-2 py-1 text-[10px] text-muted-foreground hover:bg-muted no-underline"
                    >
                      <Download size={12} />
                      {t("voice.outputs.download", "下载")}
                    </a>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* 知识库有声朗读 */}
        <div className="rounded-xl border border-border bg-card overflow-hidden">
          <div className="px-5 py-3 border-b border-border bg-muted/30 flex items-center gap-2">
            <BookOpen size={14} className="text-rose-500" />
            <h3 className="text-xs lg:text-sm font-semibold">
              {t("voice.kb.title", "知识库有声朗读")}
            </h3>
            <span className="rounded-full bg-rose-500/10 px-2 py-0.5 text-[10px] font-medium text-rose-500">
              {knowledge.length}
            </span>
          </div>
          {knowledge.length === 0 ? (
            <p className="px-5 py-6 text-xs text-muted-foreground text-center">
              {t("voice.kb.empty", "知识库暂无内容")}
            </p>
          ) : (
            <div className="divide-y divide-border">
              {knowledge.map((k) => (
                <div key={k.id} className="flex items-center gap-2 px-5 py-2.5">
                  <p className="flex-1 text-xs lg:text-sm truncate">
                    {k.title || k.content}
                  </p>
                  <button
                    onClick={() => readKnowledge(k)}
                    disabled={readingId === k.id || synthesizing}
                    className="flex items-center gap-1 rounded bg-primary/10 px-2 py-1 text-[10px] text-primary no-underline hover:bg-primary/20 transition-colors disabled:opacity-50"
                  >
                    {readingId === k.id ? (
                      <Loader2 size={12} className="animate-spin" />
                    ) : (
                      <Play size={12} />
                    )}
                    {t("voice.kb.read", "朗读")}
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        {stats && (
          <>
            {/* Backend Status */}
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
              <h3 className="text-xs lg:text-sm font-semibold flex items-center gap-2 mb-4">
                <Settings size={14} className="text-rose-500" />
                {t("voice.backendStatus") || "Backend Status"}
              </h3>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-2 lg:gap-4">
                {Object.entries(backends).map(([name, available]) => (
                  <div
                    key={name}
                    className={cn(
                      "rounded-lg border p-3 flex items-center gap-3",
                      available
                        ? "border-emerald-500/30 bg-emerald-500/5"
                        : "border-red-500/30 bg-red-500/5"
                    )}
                  >
                    {available ? (
                      <CheckCircle size={16} className="text-emerald-500 shrink-0" />
                    ) : (
                      <XCircle size={16} className="text-red-500 shrink-0" />
                    )}
                    <div>
                      <p className="text-xs lg:text-sm font-medium">{name}</p>
                      <p className="text-[10px] text-muted-foreground">
                        {available ? (t("voice.available") || "Available") : (t("voice.unavailable") || "Unavailable")}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
              <div className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
                <Zap size={12} className="text-rose-500" />
                <span>{t("voice.preferredBackend") || "Preferred"}: <strong className="text-foreground">{stats.engine.preferred_backend}</strong></span>
              </div>
            </div>

            {/* Synthesis Stats */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 lg:gap-4">
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-medium text-muted-foreground">{t("voice.totalSynthesized") || "Synthesized"}</span>
                  <div className="rounded-lg p-1.5 bg-rose-500/10"><Mic size={14} className="text-rose-500" /></div>
                </div>
                <p className="text-xl lg:text-2xl font-bold">{stats.engine.total_synthesized}</p>
              </div>
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-medium text-muted-foreground">{t("voice.totalCharacters") || "Characters"}</span>
                  <div className="rounded-lg p-1.5 bg-blue-500/10"><Hash size={14} className="text-blue-500" /></div>
                </div>
                <p className="text-xl lg:text-2xl font-bold">{stats.engine.total_characters.toLocaleString()}</p>
              </div>
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-medium text-muted-foreground">{t("voice.cacheHits") || "Cache Hits"}</span>
                  <div className="rounded-lg p-1.5 bg-emerald-500/10"><Database size={14} className="text-emerald-500" /></div>
                </div>
                <p className="text-xl lg:text-2xl font-bold">{stats.engine.cache_hits}</p>
                <p className="text-[10px] text-muted-foreground mt-0.5">
                  {stats.engine.cache.entries} entries · {formatBytes(stats.engine.cache.size_bytes)}
                </p>
              </div>
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-medium text-muted-foreground">{t("voice.errors") || "Errors"}</span>
                  <div className="rounded-lg p-1.5 bg-red-500/10"><XCircle size={14} className="text-red-500" /></div>
                </div>
                <p className="text-xl lg:text-2xl font-bold">{stats.engine.errors}</p>
              </div>
            </div>

            {/* Profile Stats */}
            <div className="grid grid-cols-2 md:grid-cols-3 gap-2 lg:gap-4">
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-medium text-muted-foreground">{t("voice.totalProfiles") || "Total Profiles"}</span>
                  <div className="rounded-lg p-1.5 bg-violet-500/10"><Settings size={14} className="text-violet-500" /></div>
                </div>
                <p className="text-xl lg:text-2xl font-bold">{stats.profiles.total_profiles}</p>
                <p className="text-[10px] text-muted-foreground mt-0.5">
                  {stats.profiles.builtin_count} {t("voice.builtin") || "builtin"} · {stats.profiles.user_count} {t("voice.user") || "user"}
                </p>
              </div>
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4 lg:p-4 col-span-2">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-medium text-muted-foreground">{t("voice.languageBreakdown") || "Language Breakdown"}</span>
                  <div className="rounded-lg p-1.5 bg-amber-500/10"><Globe size={14} className="text-amber-500" /></div>
                </div>
                <div className="flex flex-wrap gap-2 mt-2">
                  {Object.entries(languages).map(([lang, count]) => (
                    <span
                      key={lang}
                      className="inline-flex items-center gap-1.5 rounded-full bg-amber-500/10 px-3 py-1 text-xs font-medium text-amber-500"
                    >
                      <Languages size={10} />
                      {lang}: {count}
                    </span>
                  ))}
                  {Object.keys(languages).length === 0 && (
                    <span className="text-xs text-muted-foreground">{t("voice.noLanguages") || "No language data"}</span>
                  )}
                </div>
              </div>
            </div>
          </>
        )}

        {/* Profile List */}
        {profiles.length > 0 && (
          <div className="rounded-xl border border-border bg-card overflow-hidden">
            <div className="px-5 py-3 border-b border-border bg-muted/30">
              <h3 className="text-xs lg:text-sm font-semibold flex items-center gap-2">
                <Mic size={14} className="text-rose-500" />
                {t("voice.voiceProfiles") || "Voice Profiles"}
                <span className="rounded-full bg-rose-500/10 px-2 py-0.5 text-[10px] font-medium text-rose-500">
                  {profiles.length}
                </span>
              </h3>
            </div>
            <div className="divide-y divide-border">
              {profiles.map((p) => (
                <div key={p.profile_id} className="flex items-center gap-2 lg:gap-4 px-5 py-3 hover:bg-muted/30 transition-colors">
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-xs lg:text-sm font-medium">{p.name}</span>
                      {p.builtin && (
                        <span className="rounded-full bg-blue-500/10 px-1.5 py-0.5 text-[9px] text-blue-500">
                          {t("voice.builtin") || "builtin"}
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground mt-0.5">{p.description || p.voice_id}</p>
                  </div>
                  <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-mono text-muted-foreground">
                    {p.language}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Loading state */}
        {!stats && loading && (
          <div className="flex items-center justify-center py-16">
            <Loader2 size={24} className="animate-spin text-rose-500" />
          </div>
        )}
      </div>
    </div>
  
      </PageLayout>
    );
}
