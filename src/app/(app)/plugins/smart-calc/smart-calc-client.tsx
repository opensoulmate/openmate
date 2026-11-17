"use client";
import { useState, useEffect, useCallback } from "react";
import { getApiBaseUrl } from "@/lib/api-client";
import { useTranslation } from "react-i18next";
import {
  Calculator, ArrowRightLeft, History, Trash2, Loader2,
  Copy, Check, ChevronDown, Zap, Clock,
} from "lucide-react";

interface CalcResult {
  expression: string;
  result: number;
  result_int: number | null;
  elapsed_ms: number;
}

interface ConvertResult {
  value: number;
  from_unit: string;
  to_unit: string;
  result: number;
}

interface HistoryEntry {
  type: string;
  input: string;
  output: string;
  timestamp: number;
}

interface UnitsInfo {
  categories: Record<string, string[]>;
}

type Tab = "calc" | "convert" | "history";

export function SmartCalcClient() {
  const { t } = useTranslation();
  const apiBase = getApiBaseUrl();
  const pluginBase = `${apiBase}/api/plugins/smart-calc`;
  const [tab, setTab] = useState<Tab>("calc");

  // Calculator state
  const [expression, setExpression] = useState("");
  const [calcResult, setCalcResult] = useState<CalcResult | null>(null);
  const [calcLoading, setCalcLoading] = useState(false);
  const [calcError, setCalcError] = useState("");
  const [copied, setCopied] = useState(false);

  // Converter state
  const [convValue, setConvValue] = useState("1");
  const [convFrom, setConvFrom] = useState("km");
  const [convTo, setConvTo] = useState("mi");
  const [convResult, setConvResult] = useState<ConvertResult | null>(null);
  const [convLoading, setConvLoading] = useState(false);
  const [convError, setConvError] = useState("");
  const [units, setUnits] = useState<UnitsInfo | null>(null);
  const [unitCategory, setUnitCategory] = useState("length");

  // History state
  const [history, setHistory] = useState<HistoryEntry[]>([]);

  // Quick expressions
  const quickExprs = [
    "2**10", "sqrt(144)", "pi * 2", "factorial(10)",
    "log10(1000)", "sin(radians(30))", "gcd(48, 18)", "ceil(3.14)",
  ];

  const doCalculate = useCallback(async () => {
    if (!expression.trim()) return;
    setCalcLoading(true);
    setCalcError("");
    try {
      const res = await fetch(`${pluginBase}/calculate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expression: expression.trim() }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Calculation failed");
      }
      setCalcResult(await res.json());
    } catch (e: any) {
      setCalcError(e.message);
    }
    setCalcLoading(false);
  }, [expression, pluginBase]);

  const doConvert = useCallback(async () => {
    if (!convValue.trim()) return;
    setConvLoading(true);
    setConvError("");
    try {
      const res = await fetch(`${pluginBase}/convert`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          value: parseFloat(convValue),
          from_unit: convFrom,
          to_unit: convTo,
        }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Conversion failed");
      }
      setConvResult(await res.json());
    } catch (e: any) {
      setConvError(e.message);
    }
    setConvLoading(false);
  }, [convValue, convFrom, convTo, pluginBase]);

  const fetchHistory = useCallback(async () => {
    try {
      const res = await fetch(`${pluginBase}/history?limit=50`);
      if (res.ok) {
        const data = await res.json();
        setHistory(data.history || []);
      }
    } catch {}
  }, [pluginBase]);

  const fetchUnits = useCallback(async () => {
    try {
      const res = await fetch(`${pluginBase}/units`);
      if (res.ok) setUnits(await res.json());
    } catch {}
  }, [pluginBase]);

  const clearHistory = useCallback(async () => {
    try {
      await fetch(`${pluginBase}/history`, { method: "DELETE" });
      setHistory([]);
    } catch {}
  }, [pluginBase]);

  useEffect(() => {
    fetchUnits();
    fetchHistory();
  }, [fetchUnits, fetchHistory]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (tab === "calc") doCalculate();
      else if (tab === "convert") doConvert();
    }
  };

  const copyResult = () => {
    const text = calcResult?.result_int !== null
      ? String(calcResult?.result_int)
      : String(calcResult?.result);
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const formatTime = (ts: number) => {
    return new Date(ts * 1000).toLocaleString('zh-CN', {
      hour: "2-digit", minute: "2-digit", second: "2-digit",
      fractionalSecondDigits: 3 });
  };

  const currentUnits = units?.categories?.[unitCategory] || [];

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-3 px-3 lg:px-6 py-4 border-b border-border">
        <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-primary/10">
          <Calculator size={20} className="text-primary" />
        </div>
        <div>
          <h1 className="text-lg font-semibold">智能计算器</h1>
          <p className="text-xs text-muted-foreground">{t("smartCalc.subtitle")}</p>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 px-3 lg:px-6 pt-3">
        {(["calc", "convert", "history"] as Tab[]).map((tabItem) => (
          <button
            key={tabItem}
            onClick={() => { setTab(tabItem); if (tabItem === "history") fetchHistory(); }}
            className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
              tab === tabItem ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground hover:bg-muted"
            }`}
          >
            {tabItem === "calc" && <Calculator size={13} />}
            {tabItem === "convert" && <ArrowRightLeft size={13} />}
            {tabItem === "history" && <History size={13} />}
            {tabItem === "calc" ? t("smartCalc.tabCalc") : tabItem === "convert" ? t("smartCalc.tabConvert") : t("smartCalc.tabHistory")}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto px-3 lg:px-6 py-4">
        {/* Calculator Tab */}
        {tab === "calc" && (
          <div className="space-y-4 max-w-2xl">
            {/* Input */}
            <div className="space-y-2">
              <label className="text-xs font-medium text-muted-foreground">{t("smartCalc.mathExpression")}</label>
              <div className="flex gap-2">
                <input
                  value={expression}
                  onChange={(e) => setExpression(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder={t("smartCalc.expressionPlaceholder")}
                  className="flex-1 rounded-lg border border-border bg-muted px-3 py-2.5 text-xs lg:text-sm font-mono outline-none focus:ring-2 focus:ring-primary/30"
                />
                <button
                  onClick={doCalculate}
                  disabled={calcLoading || !expression.trim()}
                  className="flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2.5 text-xs lg:text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                >
                  {calcLoading ? <Loader2 size={14} className="animate-spin" /> : <Zap size={14} />}
                  {t("smartCalc.solve")}
                </button>
              </div>
            </div>

            {/* Quick expressions */}
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">{t("smartCalc.quickExpressions")}</label>
              <div className="flex flex-wrap gap-1.5">
                {quickExprs.map((expr) => (
                  <button
                    key={expr}
                    onClick={() => { setExpression(expr); }}
                    className="rounded-md border border-border bg-background px-2 py-1 text-xs font-mono text-muted-foreground hover:text-foreground hover:border-primary/40 transition-colors"
                  >
                    {expr}
                  </button>
                ))}
              </div>
            </div>

            {/* Result */}
            {calcError && (
              <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-xs lg:text-sm text-red-500">
                {calcError}
              </div>
            )}
            {calcResult && !calcError && (
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-muted-foreground font-mono">{calcResult.expression}</span>
                  <button onClick={copyResult} className="text-muted-foreground hover:text-foreground">
                    {copied ? <Check size={14} className="text-emerald-500" /> : <Copy size={14} />}
                  </button>
                </div>
                <div className="text-2xl lg:text-3xl font-bold font-mono text-foreground">
                  {calcResult.result_int !== null ? calcResult.result_int.toLocaleString() : calcResult.result}
                </div>
                <div className="flex items-center gap-3 text-xs text-muted-foreground">
                  <span className="flex items-center gap-1"><Clock size={11} /> {calcResult.elapsed_ms}ms</span>
                  {calcResult.result_int !== null && (
                    <span className="font-mono">{t("smartCalc.preciseValue", { value: calcResult.result })}</span>
                  )}
                </div>
              </div>
            )}

            {/* Supported functions */}
            <div className="rounded-lg border border-border bg-card p-3 space-y-2">
              <h3 className="text-xs font-medium text-muted-foreground">{t("smartCalc.supportedFunctions")}</h3>
              <div className="flex flex-wrap gap-1">
                {["sin", "cos", "tan", "asin", "acos", "atan", "sqrt", "log", "log2", "log10",
                  "ceil", "floor", "factorial", "abs", "round", "min", "max", "pow",
                  "degrees", "radians", "hypot", "gcd", "pi", "e", "tau"].map((fn) => (
                  <span key={fn} className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-mono text-muted-foreground">
                    {fn}
                  </span>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* Converter Tab */}
        {tab === "convert" && (
          <div className="space-y-4 max-w-2xl">
            {/* Category selector */}
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">{t("smartCalc.category")}</label>
              <div className="flex flex-wrap gap-1.5">
                {units && Object.keys(units.categories).map((cat) => (
                  <button
                    key={cat}
                    onClick={() => {
                      setUnitCategory(cat);
                      const u = units.categories[cat];
                      if (u.length >= 2) { setConvFrom(u[0]); setConvTo(u[1]); }
                    }}
                    className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
                      unitCategory === cat ? "bg-primary text-primary-foreground" : "border border-border text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    {cat === "length" ? t("smartCalc.catLength") : cat === "weight" ? t("smartCalc.catWeight") : cat === "temperature" ? t("smartCalc.catTemperature") :
                     cat === "speed" ? t("smartCalc.catSpeed") : cat === "area" ? t("smartCalc.catArea") : cat === "volume" ? t("smartCalc.catVolume") : cat}
                  </button>
                ))}
              </div>
            </div>

            {/* Value input */}
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">{t("smartCalc.numericValue")}</label>
              <input
                value={convValue}
                onChange={(e) => setConvValue(e.target.value)}
                onKeyDown={handleKeyDown}
                type="number"
                className="w-full rounded-lg border border-border bg-muted px-3 py-2.5 text-xs lg:text-sm font-mono outline-none focus:ring-2 focus:ring-primary/30"
              />
            </div>

            {/* From/To selectors */}
            <div className="grid grid-cols-[1fr,auto,1fr] gap-2 items-end">
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">{t("smartCalc.fromUnit")}</label>
                <select
                  value={convFrom}
                  onChange={(e) => setConvFrom(e.target.value)}
                  className="w-full rounded-lg border border-border bg-muted px-3 py-2.5 text-xs lg:text-sm outline-none focus:ring-2 focus:ring-primary/30"
                >
                  {currentUnits.map((u) => <option key={u} value={u}>{u}</option>)}
                </select>
              </div>
              <button
                onClick={() => { const tmp = convFrom; setConvFrom(convTo); setConvTo(tmp); }}
                className="flex items-center justify-center w-10 h-10 rounded-lg border border-border hover:bg-muted mb-0.5"
              >
                <ArrowRightLeft size={16} className="text-muted-foreground" />
              </button>
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">{t("smartCalc.toUnit")}</label>
                <select
                  value={convTo}
                  onChange={(e) => setConvTo(e.target.value)}
                  className="w-full rounded-lg border border-border bg-muted px-3 py-2.5 text-xs lg:text-sm outline-none focus:ring-2 focus:ring-primary/30"
                >
                  {currentUnits.map((u) => <option key={u} value={u}>{u}</option>)}
                </select>
              </div>
            </div>

            <button
              onClick={doConvert}
              disabled={convLoading || !convValue.trim()}
              className="w-full flex items-center justify-center gap-1.5 rounded-lg bg-primary px-4 py-2.5 text-xs lg:text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              {convLoading ? <Loader2 size={14} className="animate-spin" /> : <ArrowRightLeft size={14} />}
              {t("smartCalc.convert")}
            </button>

            {/* Conversion result */}
            {convError && (
              <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-xs lg:text-sm text-red-500">
                {convError}
              </div>
            )}
            {convResult && !convError && (
              <div className="rounded-xl border border-border bg-card p-3 lg:p-4 space-y-2 text-center">
                <div className="text-xs lg:text-sm text-muted-foreground">
                  {convResult.value} {convResult.from_unit}
                </div>
                <div className="text-xl lg:text-2xl font-bold font-mono">=</div>
                <div className="text-2xl lg:text-3xl font-bold font-mono text-primary">
                  {convResult.result % 1 === 0 ? convResult.result.toLocaleString() : convResult.result.toFixed(6)}
                </div>
                <div className="text-xs lg:text-sm text-muted-foreground">{convResult.to_unit}</div>
              </div>
            )}
          </div>
        )}

        {/* History Tab */}
        {tab === "history" && (
          <div className="space-y-3 max-w-2xl">
            <div className="flex items-center justify-between">
              <span className="text-xs text-muted-foreground">{t("smartCalc.historyCount", { count: history.length })}</span>
              {history.length > 0 && (
                <button onClick={clearHistory} className="flex items-center gap-1 text-xs text-muted-foreground hover:text-red-500">
                  <Trash2 size={12} /> {t("smartCalc.clear")}
                </button>
              )}
            </div>
            {history.length === 0 ? (
              <div className="text-center py-12 text-muted-foreground">
                <History size={40} className="mx-auto mb-3 opacity-30" />
                <p className="text-xs lg:text-sm">{t("smartCalc.noCalcHistory")}</p>
              </div>
            ) : (
              <div className="space-y-1">
                {history.map((entry, i) => (
                  <div
                    key={i}
                    className="flex items-center gap-3 rounded-lg border border-border bg-card px-3 py-2.5 hover:border-primary/30 transition-colors"
                  >
                    <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${
                      entry.type === "calculate" ? "bg-blue-500/10 text-blue-500" : "bg-amber-500/10 text-amber-500"
                    }`}>
                      {entry.type === "calculate" ? t("smartCalc.historyCalculate") : t("smartCalc.historyConvert")}
                    </span>
                    <span className="flex-1 font-mono text-xs lg:text-sm truncate">{entry.input}</span>
                    <span className="font-mono text-xs lg:text-sm font-medium text-primary">= {entry.output}</span>
                    <span className="text-[10px] text-muted-foreground shrink-0">{formatTime(entry.timestamp)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
