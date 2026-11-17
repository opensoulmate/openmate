"use client";
import { useState, useEffect, useCallback, useRef } from "react";
import { getApiBaseUrl } from "@/lib/api-client";
import { useTranslation } from "react-i18next";
import {
  Timer, Play, Square, CheckCircle, XCircle, Clock,
  Settings, Trash2, Flame, Coffee, BarChart3, Loader2,
} from "lucide-react";

interface TimerStatus {
  active: boolean;
  task?: string;
  type?: string;
  duration_minutes?: number;
  remaining_seconds?: number;
  elapsed_seconds?: number;
  progress?: number;
  completed_sessions?: number;
}

interface Session {
  task: string;
  type: string;
  duration_minutes: number;
  elapsed_seconds: number;
  completed: boolean;
  started_at: number;
  ended_at: number;
}

interface Stats {
  today: { sessions: number; focus_minutes: number };
  all_time: { sessions: number; focus_hours: number; cancelled: number };
  config: Record<string, number>;
}

type Tab = "timer" | "history" | "settings";

export function PomodoroClient() {
  const { t: tr } = useTranslation();
  const apiBase = getApiBaseUrl();
  const pluginBase = `${apiBase}/api/plugins/pomodoro`;
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const [tab, setTab] = useState<Tab>("timer");
  const [status, setStatus] = useState<TimerStatus>({ active: false });
  const [stats, setStats] = useState<Stats | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);

  // Timer input
  const [taskName, setTaskName] = useState("");
  const [duration, setDuration] = useState(25);
  const [loading, setLoading] = useState(false);

  // Settings
  const [workMin, setWorkMin] = useState(25);
  const [shortBreak, setShortBreak] = useState(5);
  const [longBreak, setLongBreak] = useState(15);
  const [longInterval, setLongInterval] = useState(4);

  // Display time
  const [displayTime, setDisplayTime] = useState("25:00");

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch(`${pluginBase}/status`);
      if (res.ok) {
        const data = await res.json();
        setStatus(data);
      }
    } catch {}
  }, [pluginBase]);

  const fetchStats = useCallback(async () => {
    try {
      const res = await fetch(`${pluginBase}/stats`);
      if (res.ok) setStats(await res.json());
    } catch {}
  }, [pluginBase]);

  const fetchSessions = useCallback(async () => {
    try {
      const res = await fetch(`${pluginBase}/sessions?limit=50`);
      if (res.ok) {
        const data = await res.json();
        setSessions(data.sessions || []);
      }
    } catch {}
  }, [pluginBase]);

  // Poll timer status
  useEffect(() => {
    fetchStatus();
    fetchStats();
    intervalRef.current = setInterval(() => {
      fetchStatus();
    }, 1000);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [fetchStatus, fetchStats]);

  // Update display time
  useEffect(() => {
    if (status.active && status.remaining_seconds !== undefined) {
      const mins = Math.floor(status.remaining_seconds / 60);
      const secs = Math.floor(status.remaining_seconds % 60);
      setDisplayTime(`${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`);
    } else {
      setDisplayTime(`${String(duration).padStart(2, "0")}:00`);
    }
  }, [status, duration]);

  const startTimer = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${pluginBase}/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task: taskName, duration_minutes: duration }),
      });
      if (res.ok) {
        await fetchStatus();
        fetchStats();
      }
    } catch {}
    setLoading(false);
  };

  const stopTimer = async (completed: boolean) => {
    setLoading(true);
    try {
      const res = await fetch(`${pluginBase}/stop`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ completed }),
      });
      if (res.ok) {
        await fetchStatus();
        fetchStats();
        fetchSessions();
      }
    } catch {}
    setLoading(false);
  };

  const clearSessions = async () => {
    try {
      await fetch(`${pluginBase}/sessions`, { method: "DELETE" });
      setSessions([]);
      fetchStats();
    } catch {}
  };

  const saveConfig = async () => {
    try {
      await fetch(`${pluginBase}/config?work_minutes=${workMin}&short_break_minutes=${shortBreak}&long_break_minutes=${longBreak}&long_break_interval=${longInterval}`, {
        method: "PUT",
      });
      fetchStats();
    } catch {}
  };

  const formatTime = (ts: number) => {
    return new Date(ts * 1000).toLocaleString('zh-CN', {
      month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    });
  };

  const progressPercent = status.progress || 0;
  const circumference = 2 * Math.PI * 120;
  const strokeDashoffset = circumference - (progressPercent / 100) * circumference;

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-2 lg:gap-3 px-3 lg:px-6 py-4 border-b border-border">
        <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-red-500/10">
          <Timer size={20} className="text-red-500" />
        </div>
        <div>
          <h1 className="text-lg font-semibold">番茄钟</h1>
          <p className="text-xs text-muted-foreground">{tr("plugins.pomodoroSubtitle") || "Pomodoro Technique — Focus 25min, Break 5min"}</p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 px-3 lg:px-6 pt-3">
        {(["timer", "history", "settings"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => { setTab(t); if (t === "history") fetchSessions(); if (t === "settings") fetchStats(); }}
            className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
              tab === t ? "bg-red-500 text-white" : "text-muted-foreground hover:text-foreground hover:bg-muted"
            }`}
          >
            {t === "timer" && <Timer size={13} />}
            {t === "history" && <Clock size={13} />}
            {t === "settings" && <Settings size={13} />}
            {t === "timer" ? (tr("plugins.timer") || "Timer") : t === "history" ? (tr("plugins.historyTab") || "History") : (tr("plugins.settingsTab") || "Settings")}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto px-3 lg:px-6 py-4">
        {/* Timer Tab */}
        {tab === "timer" && (
          <div className="flex flex-col items-center space-y-3 lg:space-y-6 max-w-md mx-auto">
            {/* Circular timer */}
            <div className="relative w-64 h-64">
              <svg className="w-64 h-64 -rotate-90" viewBox="0 0 256 256">
                <circle cx="128" cy="128" r="120" fill="none" stroke="currentColor"
                  strokeWidth="6" className="text-muted/30" />
                <circle cx="128" cy="128" r="120" fill="none"
                  stroke={status.active ? "#ef4444" : "#6b7280"}
                  strokeWidth="6" strokeLinecap="round"
                  strokeDasharray={circumference}
                  strokeDashoffset={strokeDashoffset}
                  className="transition-all duration-1000" />
              </svg>
              <div className="absolute inset-0 flex flex-col items-center justify-center">
                <span className="text-5xl font-mono font-bold">{displayTime}</span>
                {status.active && (
                  <span className="text-xs lg:text-sm text-muted-foreground mt-1">
                    {status.task || (tr("plugins.focusing") || "Focusing...")}
                  </span>
                )}
                {!status.active && (
                  <span className="text-xs lg:text-sm text-muted-foreground mt-1">
                    {tr("plugins.pomodoroSummary", { sessions: stats?.today.sessions || 0, minutes: stats?.today.focus_minutes || 0 }) || `${stats?.today.sessions || 0} sessions · ${stats?.today.focus_minutes || 0} min`}
                  </span>
                )}
              </div>
            </div>

            {/* Controls */}
            {!status.active ? (
              <div className="w-full space-y-3">
                <input
                  value={taskName}
                  onChange={(e) => setTaskName(e.target.value)}
                  placeholder={tr("plugins.taskNameOptional") || "Task name (optional)"}
                  className="w-full rounded-lg border border-border bg-muted px-3 py-2 text-xs lg:text-sm outline-none focus:ring-2 focus:ring-red-500/30"
                />
                <div className="flex items-center gap-2">
                  <label className="text-xs text-muted-foreground">{tr("plugins.duration") || "Duration"}</label>
                  <select
                    value={duration}
                    onChange={(e) => setDuration(Number(e.target.value))}
                    className="rounded-lg border border-border bg-muted px-2 py-1.5 text-xs lg:text-sm outline-none"
                  >
                    {[15, 20, 25, 30, 45, 60].map((m) => (
                      <option key={m} value={m}>{tr("plugins.minutes", { m }) || `${m} min`}</option>
                    ))}
                  </select>
                </div>
                <button
                  onClick={startTimer}
                  disabled={loading}
                  className="w-full flex items-center justify-center gap-2 rounded-lg bg-red-500 px-4 py-3 text-xs lg:text-sm font-medium text-white hover:bg-red-600 disabled:opacity-50"
                >
                  {loading ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />}
                  {tr("plugins.startFocus") || "Start Focus"}
                </button>
              </div>
            ) : (
              <div className="flex gap-2 lg:gap-3 w-full">
                <button
                  onClick={() => stopTimer(true)}
                  disabled={loading}
                  className="flex-1 flex items-center justify-center gap-2 rounded-lg bg-emerald-500 px-4 py-3 text-xs lg:text-sm font-medium text-white hover:bg-emerald-600 disabled:opacity-50"
                >
                  <CheckCircle size={16} /> {tr("plugins.done") || "Complete"}
                </button>
                <button
                  onClick={() => stopTimer(false)}
                  disabled={loading}
                  className="flex-1 flex items-center justify-center gap-2 rounded-lg border border-border px-4 py-3 text-xs lg:text-sm font-medium text-muted-foreground hover:text-foreground disabled:opacity-50"
                >
                  <XCircle size={16} /> {tr("plugins.cancel") || "Cancel"}
                </button>
              </div>
            )}

            {/* Today stats */}
            {stats && (
              <div className="grid grid-cols-2 lg:grid-cols-3 gap-2 lg:gap-3 w-full">
                <div className="rounded-lg border border-border bg-card p-3 text-center">
                  <div className="text-xl lg:text-2xl font-bold text-red-500">{stats.today.sessions}</div>
                  <div className="text-[10px] text-muted-foreground">{tr("plugins.todayPomodoros") || "Today's Pomodoros"}</div>
                </div>
                <div className="rounded-lg border border-border bg-card p-3 text-center">
                  <div className="text-xl lg:text-2xl font-bold text-amber-500">{stats.today.focus_minutes}</div>
                  <div className="text-[10px] text-muted-foreground">{tr("plugins.focusMinutes") || "Focus Minutes"}</div>
                </div>
                <div className="rounded-lg border border-border bg-card p-3 text-center">
                  <div className="text-xl lg:text-2xl font-bold text-blue-500">{stats.all_time.sessions}</div>
                  <div className="text-[10px] text-muted-foreground">{tr("plugins.totalPomodoros") || "Total Pomodoros"}</div>
                </div>
              </div>
            )}
          </div>
        )}

        {/* History Tab */}
        {tab === "history" && (
          <div className="space-y-3 max-w-xl">
            <div className="flex items-center justify-between">
              <span className="text-xs text-muted-foreground">{tr("plugins.recordsCount", { count: sessions.length }) || `${sessions.length} records`}</span>
              {sessions.length > 0 && (
                <button onClick={clearSessions} className="flex items-center gap-1 text-xs text-muted-foreground hover:text-red-500">
                  <Trash2 size={12} /> {tr("plugins.clear") || "Clear"}
                </button>
              )}
            </div>
            {sessions.length === 0 ? (
              <div className="text-center py-12 text-muted-foreground">
                <Timer size={40} className="mx-auto mb-3 opacity-30" />
                <p className="text-xs lg:text-sm">{tr("plugins.noPomodoroHistory") || "No pomodoro records yet"}</p>
              </div>
            ) : (
              <div className="space-y-1">
                {sessions.map((s, i) => (
                  <div key={i} className="flex items-center gap-2 lg:gap-3 rounded-lg border border-border bg-card px-3 py-2.5">
                    {s.completed ? (
                      <CheckCircle size={16} className="text-emerald-500 shrink-0" />
                    ) : (
                      <XCircle size={16} className="text-muted-foreground shrink-0" />
                    )}
                    <div className="flex-1 min-w-0">
                      <div className="text-xs lg:text-sm font-medium truncate">{s.task || (tr("plugins.noTask") || "No task")}</div>
                      <div className="text-[10px] text-muted-foreground">{formatTime(s.started_at)}</div>
                    </div>
                    <div className="text-right shrink-0">
                      <div className="text-xs lg:text-sm font-mono">{Math.round(s.elapsed_seconds / 60)}m</div>
                      <div className="text-[10px] text-muted-foreground">/{s.duration_minutes}m</div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Settings Tab */}
        {tab === "settings" && (
          <div className="space-y-4 max-w-md">
            <div className="rounded-xl border border-border bg-card p-3 lg:p-4 space-y-4">
              <h3 className="text-xs lg:text-sm font-medium">{tr("plugins.pomodoroConfig") || "Pomodoro Settings"}</h3>
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs lg:text-sm">{tr("plugins.workDuration") || "Work Duration"}</span>
                  <div className="flex items-center gap-2">
                    <input type="range" min={5} max={60} value={workMin}
                      onChange={(e) => setWorkMin(Number(e.target.value))}
                      className="w-32 accent-red-500" />
                    <span className="text-xs lg:text-sm font-mono w-12 text-right">{workMin}m</span>
                  </div>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-xs lg:text-sm">{tr("plugins.shortBreak") || "Short Break"}</span>
                  <div className="flex items-center gap-2">
                    <input type="range" min={1} max={15} value={shortBreak}
                      onChange={(e) => setShortBreak(Number(e.target.value))}
                      className="w-32 accent-emerald-500" />
                    <span className="text-xs lg:text-sm font-mono w-12 text-right">{shortBreak}m</span>
                  </div>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-xs lg:text-sm">{tr("plugins.longBreak") || "Long Break"}</span>
                  <div className="flex items-center gap-2">
                    <input type="range" min={5} max={30} value={longBreak}
                      onChange={(e) => setLongBreak(Number(e.target.value))}
                      className="w-32 accent-blue-500" />
                    <span className="text-xs lg:text-sm font-mono w-12 text-right">{longBreak}m</span>
                  </div>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-xs lg:text-sm">{tr("plugins.longBreakInterval") || "Long Break Interval"}</span>
                  <div className="flex items-center gap-2">
                    <input type="range" min={2} max={8} value={longInterval}
                      onChange={(e) => setLongInterval(Number(e.target.value))}
                      className="w-32 accent-amber-500" />
                    <span className="text-xs lg:text-sm font-mono w-12 text-right">{tr("plugins.longIntervalCount", { count: longInterval }) || `${longInterval}`}</span>
                  </div>
                </div>
              </div>
              <button onClick={saveConfig}
                className="w-full rounded-lg bg-primary px-4 py-2 text-xs lg:text-sm font-medium text-primary-foreground hover:bg-primary/90">
                {tr("plugins.saveConfig") || "Save Settings"}
              </button>
            </div>

            {/* All-time stats */}
            {stats && (
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4 space-y-3">
                <h3 className="text-xs lg:text-sm font-medium">{tr("plugins.cumulativeStats") || "Cumulative Stats"}</h3>
                <div className="grid grid-cols-2 gap-2 lg:gap-3">
                  <div className="text-center">
                    <div className="text-2xl lg:text-3xl font-bold text-red-500">{stats.all_time.sessions}</div>
                    <div className="text-xs text-muted-foreground">{tr("plugins.completedPomodoros") || "Completed Pomodoros"}</div>
                  </div>
                  <div className="text-center">
                    <div className="text-2xl lg:text-3xl font-bold text-amber-500">{stats.all_time.focus_hours}h</div>
                    <div className="text-xs text-muted-foreground">{tr("plugins.focusTime") || "Focus Time"}</div>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
