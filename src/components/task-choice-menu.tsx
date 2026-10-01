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

  return (
    <div className="mt-2 rounded-xl border border-border/60 bg-background/80 backdrop-blur-sm overflow-hidden">
      {/* 标题 */}
      <div className="px-4 py-2.5 border-b border-border/40 bg-muted/30">
        <p className="text-sm font-medium text-foreground/90">{title}</p>
        <p className="text-xs text-muted-foreground mt-0.5">
          {t("askUser.multiHint", "多个问题请一次作答后提交")}
        </p>
      </div>

      {/* 问题块 */}
      {questions.map((q, qi) => (
        <div key={q.id} className="px-4 py-3 border-b border-border/30">
          <p className="text-sm font-medium text-foreground/90">
            <span className="text-primary mr-1">{qi + 1}.</span>
            {q.question}
            {q.multi && (
              <span className="ml-2 text-xs text-muted-foreground">
                {t("askUser.multiTag", "可多选")}
              </span>
            )}
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            {q.options.map((opt) => {
              const on = (answers[q.id] || []).includes(opt.id);
              return (
                <button
                  key={opt.id}
                  onClick={() => toggle(q, opt.id)}
                  disabled={disabled || submitted}
                  title={opt.description}
                  className={`px-3 py-1.5 rounded-lg text-sm border transition-all
                    ${on
                      ? "bg-primary/10 border-primary text-primary"
                      : "border-border/60 text-foreground/80 hover:bg-muted/50 active:bg-muted/70 cursor-pointer"
                    } ${disabled || submitted ? "opacity-60 cursor-not-allowed" : ""}`}
                >
                  {on ? "✓ " : ""}
                  {opt.label}
                </button>
              );
            })}
          </div>
          {q.allow_custom && !submitted && (
            <input
              type="text"
              value={customTexts[q.id] || ""}
              onChange={(e) => setCustomTexts((prev) => ({ ...prev, [q.id]: e.target.value }))}
              placeholder={t("askUser.customPlaceholder", "或输入自定义内容...")}
              disabled={disabled}
              className="mt-2 w-full text-sm bg-transparent outline-none border-b border-border/40 pb-1 placeholder:text-muted-foreground/50"
            />
          )}
        </div>
      ))}

      {/* 提交 */}
      {!submitted && (
        <div className="flex items-center justify-end px-3 py-2.5 border-t border-border/40 bg-muted/20">
          <button
            onClick={handleSubmit}
            disabled={!allAnswered || disabled}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-primary/10 text-primary hover:bg-primary/20 disabled:opacity-30 disabled:cursor-not-allowed transition-colors text-sm"
          >
            <Send className="w-3.5 h-3.5" />
            {t("askUser.submit", "提交全部作答")}
          </button>
        </div>
      )}
      {submitted && (
        <div className="px-4 py-2 border-t border-border/40 bg-muted/20 text-xs text-primary">
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
    <div className="mt-2 rounded-xl border border-border/60 bg-background/80 backdrop-blur-sm overflow-hidden">
      {/* 标题 */}
      <div className="px-4 py-2.5 border-b border-border/40 bg-muted/30">
        <p className="text-sm font-medium text-foreground/90">{question}</p>
      </div>

      {/* 选项列表 */}
      <div className="divide-y divide-border/30">
        {pagedOptions.map((opt, idx) => (
          <button
            key={opt.id}
            onClick={() => handleSelect(opt)}
            disabled={disabled || !!selected}
            className={`w-full text-left px-4 py-3 flex items-start gap-3 transition-all
              ${selected === opt.id
                ? "bg-primary/10 border-l-2 border-primary"
                : selected
                  ? "opacity-50 cursor-not-allowed"
                  : "hover:bg-muted/50 active:bg-muted/70 cursor-pointer"
              }`}
          >
            <span className="flex-shrink-0 w-6 h-6 rounded-full bg-primary/10 text-primary text-xs font-bold flex items-center justify-center mt-0.5">
              {page * pageSize + idx + 1}
            </span>
            <div className="min-w-0 flex-1">
              <span className="text-sm font-medium text-foreground/90">{opt.label}</span>
              {opt.description && (
                <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{opt.description}</p>
              )}
            </div>
            {selected === opt.id && (
              <span className="flex-shrink-0 text-xs text-primary font-medium mt-1">✓ 已选择</span>
            )}
          </button>
        ))}
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

      {/* 自定义输入 */}
      {!selected && (
        <div className="flex items-center gap-2 px-3 py-2.5 border-t border-border/40 bg-muted/20">
          <input
            type="text"
            value={customText}
            onChange={e => setCustomText(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') handleCustomSubmit(); }}
            placeholder="或输入自定义内容..."
            disabled={disabled}
            className="flex-1 text-sm bg-transparent outline-none placeholder:text-muted-foreground/50"
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
