"use client";

import { useState } from "react";
import { ChevronLeft, ChevronRight, Send } from "lucide-react";
import { useTranslation } from "react-i18next";

export interface ChoiceOption {
  id: string;
  label: string;
  description?: string;
}

// P1交互式提问卡（多问题/多选）：Warp AskUserQuestion / Dify表单形态。
// 一张卡承载1-5个问题，multi=复选框多选/单选钮，allow_custom=允许自定义作答；
// 一次提交全部作答，作答文本格式=每行「问题：选项A、选项B」（含（自定义）段），
// 与后端 clarify 工具描述的解析契约一致（acp-proxy/agent/soulmate_agent.py）。
export interface ChoiceQuestion {
  id: string;
  question: string;
  options: ChoiceOption[];
  multi?: boolean;
  allow_custom?: boolean;
}

// 作答文本组合（纯函数，render测试与组件共用单一真源）
export function composeAskAnswers(
  questions: ChoiceQuestion[],
  answers: Record<string, string[]>,
  customTexts: Record<string, string>,
): string {
  return questions
    .map((q) => {
      const ids = answers[q.id] || [];
      const labels = ids.map((id) => q.options.find((o) => o.id === id)?.label || id);
      const custom = (customTexts[q.id] || "").trim();
      const parts = [...labels];
      if (custom) parts.push(`（自定义）${custom}`);
      return `${q.question}：${parts.join("、")}`;
    })
    .join("\n");
}

// ── Hermes clarify 卡片视觉契约：A/B/C/D 字母徽章 + 首项"(Recommended)"展示标（仅展示，答案文本原样）──
const RECOMMENDED_LABEL = "(Recommended)";
const letterFor = (i: number): string => String.fromCharCode(65 + i);

function AskQuestionCard({
  title,
  questions,
  onSubmitAnswers,
  disabled = false,
}: {
  title: string;
  questions: ChoiceQuestion[];
  onSubmitAnswers: (text: string) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const [answers, setAnswers] = useState<Record<string, string[]>>({});
  const [customTexts, setCustomTexts] = useState<Record<string, string>>({});
  const [submitted, setSubmitted] = useState(false);

  const toggle = (q: ChoiceQuestion, optId: string) => {
    if (disabled || submitted) return;
    setAnswers((prev) => {
      const cur = prev[q.id] || [];
      if (q.multi) {
        return { ...prev, [q.id]: cur.includes(optId) ? cur.filter((x) => x !== optId) : [...cur, optId] };
      }
      return { ...prev, [q.id]: cur[0] === optId ? [] : [optId] };
    });
  };

  const answered = (q: ChoiceQuestion) =>
    (answers[q.id] || []).length > 0 || (customTexts[q.id] || "").trim().length > 0;
  const allAnswered = questions.every(answered);

  const handleSubmit = () => {
    if (disabled || submitted || !allAnswered) return;
    onSubmitAnswers(composeAskAnswers(questions, answers, customTexts));
    setSubmitted(true);
  };

  const answeredCount = questions.filter(answered).length;

  return (
    <div className="mt-2 overflow-hidden" data-slot="clarify-card">
      {/* 顶栏：已回答 x/y（Hermes 契约） */}
      <div className="flex items-center justify-between px-4 pt-3 pb-1">
        <span className="text-xs text-muted-foreground">
          已回答 {answeredCount}/{questions.length}
        </span>
        <span className="text-xs text-muted-foreground/60">{title ? "💬?" : ""}</span>
      </div>

      {/* 问题块 */}
      <div className="px-4 pb-2 grid gap-4">
        {questions.map((q, qi) => (
          <div key={q.id} className="grid gap-1.5">
            <div className="flex items-start gap-2">
              <span className="flex-1 whitespace-pre-wrap text-[0.95rem] font-medium text-foreground/95 leading-relaxed">
                {q.question}
              </span>
              {q.options.length > 0 && (
                <span className="mt-0.5 shrink-0 rounded bg-muted/40 px-1.5 py-0.5 text-[0.625rem] text-muted-foreground">
                  {q.multi ? t("askUser.multiTag", "可多选") : "选一个"}
                </span>
              )}
            </div>

            {/* 选项行：A/B/C/D 字母徽章（首项=推荐展示标） */}
            <div className="grid gap-1">
              {q.options.map((opt, oi) => {
                const on = (answers[q.id] || []).includes(opt.id);
                return (
                  <button
                    key={opt.id}
                    onClick={() => toggle(q, opt.id)}
                    disabled={disabled || submitted}
                    title={opt.description}
                    className={`flex w-full items-start gap-2.5 rounded-lg border px-2.5 py-2 text-left text-sm transition-colors
                      ${on
                        ? "border-primary/70 bg-primary/15 text-foreground"
                        : "border-border/70 bg-background/40 text-foreground/90 hover:bg-muted/30"
                      } ${disabled || submitted ? "opacity-60 cursor-not-allowed" : ""}`}
                  >
                    <span className={`mt-px flex h-5 w-5 shrink-0 items-center justify-center rounded border text-[0.6875rem] font-semibold ${on ? "border-primary bg-primary text-primary-foreground" : "border-border text-muted-foreground"}`}>
                      {letterFor(oi)}
                    </span>
                    <span className="flex-1 whitespace-pre-wrap wrap-anywhere leading-relaxed">
                      {opt.label}
                      {oi === 0 && q.options.length > 1 && (
                        <span className="ml-1.5 text-[0.75rem] text-muted-foreground/70">{RECOMMENDED_LABEL}</span>
                      )}
                    </span>
                  </button>
                );
              })}

              {/* 其他（输入你的答案）——Hermes 契约常驻行 */}
              {!submitted && (
                <div className="flex items-center gap-2.5 rounded-lg border border-dashed border-border/60 px-2.5 py-1.5">
                  <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded border border-border text-[0.6875rem] font-semibold text-muted-foreground/60">
                    {letterFor(q.options.length)}
                  </span>
                  <input
                    type="text"
                    value={customTexts[q.id] || ""}
                    onChange={(e) => setCustomTexts((prev) => ({ ...prev, [q.id]: e.target.value }))}
                    placeholder="其他 (输入你的答案)"
                    disabled={disabled}
                    className="flex-1 bg-transparent text-sm text-foreground placeholder:text-muted-foreground/60 focus:outline-none"
                  />
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* 底栏：确认并继续 */}
      {!submitted && (
        <div className="flex items-center justify-end gap-2 border-t border-border px-4 py-2.5">
          <button
            onClick={handleSubmit}
            disabled={!allAnswered || disabled}
            className="rounded-lg bg-primary px-3.5 py-1.5 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-30 disabled:cursor-not-allowed transition-opacity"
          >
            {t("askUser.submit", "确认并继续")}
          </button>
        </div>
      )}
      {submitted && (
        <div className="px-4 py-2 border-t border-border text-xs text-primary">
          {t("askUser.submitted", "已提交作答")}
        </div>
      )}
    </div>
  );
}

interface TaskChoiceMenuProps {
  question: string;
  options: ChoiceOption[];
  onSelect: (optionId: string, label: string) => void;
  onCustomSubmit: (text: string) => void;
  disabled?: boolean;
  pageSize?: number;
  questions?: ChoiceQuestion[];
  onSubmitAnswers?: (text: string) => void;
}

export function TaskChoiceMenu({
  question,
  options,
  onSelect,
  onCustomSubmit,
  disabled = false,
  pageSize = 5,
  questions,
  onSubmitAnswers,
}: TaskChoiceMenuProps) {
  const [page, setPage] = useState(0);
  const [customText, setCustomText] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  // P1多问题/多选形态：questions非空走提问卡（单问题旧形态渲染逐字不变）
  if (questions && questions.length > 0 && onSubmitAnswers) {
    return (
      <AskQuestionCard
        title={question}
        questions={questions}
        onSubmitAnswers={onSubmitAnswers}
        disabled={disabled}
      />
    );
  }

  const totalPages = Math.ceil(options.length / pageSize);
  const pagedOptions = options.slice(page * pageSize, (page + 1) * pageSize);

  const handleSelect = (opt: ChoiceOption) => {
    if (disabled || selected) return;
    setSelected(opt.id);
    onSelect(opt.id, opt.label);
  };

  const handleCustomSubmit = () => {
    if (!customText.trim() || disabled) return;
    onCustomSubmit(customText.trim());
  };

  return (
    <div className="mt-2 overflow-hidden" data-slot="clarify-card">
      {/* 题干 + 选一个（Hermes 契约） */}
      <div className="flex items-start gap-2 px-4 pt-3 pb-1">
        <span className="flex-1 whitespace-pre-wrap text-[0.95rem] font-medium text-foreground/95 leading-relaxed">{question}</span>
        {options.length > 0 && (
          <span className="mt-0.5 shrink-0 rounded bg-muted/40 px-1.5 py-0.5 text-[0.625rem] text-muted-foreground">选一个</span>
        )}
      </div>

      {/* 选项行：A/B/C/D 字母徽章（首项=推荐展示标） */}
      <div className="px-4 pb-2 grid gap-1">
        {pagedOptions.map((opt, idx) => {
          const on = selected === opt.id;
          const gidx = page * pageSize + idx;
          return (
            <button
              key={opt.id}
              onClick={() => handleSelect(opt)}
              disabled={disabled || !!selected}
              title={opt.description}
              className={`flex w-full items-start gap-2.5 rounded-lg border px-2.5 py-2 text-left text-sm transition-colors
                ${on
                  ? "border-primary/70 bg-primary/15 text-foreground"
                  : selected
                    ? "opacity-50 cursor-not-allowed border-border/70 bg-background/40"
                    : "border-border/70 bg-background/40 text-foreground/90 hover:bg-muted/30 cursor-pointer"
                }`}
            >
              <span className={`mt-px flex h-5 w-5 shrink-0 items-center justify-center rounded border text-[0.6875rem] font-semibold ${on ? "border-primary bg-primary text-primary-foreground" : "border-border text-muted-foreground"}`}>
                {letterFor(gidx)}
              </span>
              <span className="flex-1 min-w-0 whitespace-pre-wrap wrap-anywhere leading-relaxed">
                <span className="text-sm">{opt.label}</span>
                {gidx === 0 && options.length > 1 && (
                  <span className="ml-1.5 text-[0.75rem] text-muted-foreground/70">{RECOMMENDED_LABEL}</span>
                )}
                {opt.description && (
                  <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{opt.description}</p>
                )}
              </span>
            </button>
          );
        })}
      </div>

      {/* 分页 */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between px-4 py-2 border-t border-border/30 bg-muted/20">
          <button
            onClick={() => setPage(p => Math.max(0, p - 1))}
            disabled={page === 0}
            className="p-1 rounded hover:bg-muted/50 disabled:opacity-30 disabled:cursor-not-allowed"
          >
            <ChevronLeft className="w-4 h-4" />
          </button>
          <span className="text-xs text-muted-foreground">
            {page + 1} / {totalPages}
          </span>
          <button
            onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
            disabled={page >= totalPages - 1}
            className="p-1 rounded hover:bg-muted/50 disabled:opacity-30 disabled:cursor-not-allowed"
          >
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* 其他（输入你的答案）——Hermes 契约 */}
      {!selected && (
        <div className="flex items-center gap-2.5 mx-4 mb-3 rounded-lg border border-dashed border-border/60 px-2.5 py-1.5">
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded border border-border text-[0.6875rem] font-semibold text-muted-foreground/60">
            {letterFor(options.length)}
          </span>
          <input
            type="text"
            value={customText}
            onChange={e => setCustomText(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') handleCustomSubmit(); }}
            placeholder="其他 (输入你的答案)"
            disabled={disabled}
            className="flex-1 bg-transparent text-sm text-foreground placeholder:text-muted-foreground/60 focus:outline-none"
          />
          <button
            onClick={handleCustomSubmit}
            disabled={!customText.trim() || disabled}
            className="p-1.5 rounded-lg bg-primary/10 text-primary hover:bg-primary/20 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            <Send className="w-3.5 h-3.5" />
          </button>
        </div>
      )}
    </div>
  );
}
