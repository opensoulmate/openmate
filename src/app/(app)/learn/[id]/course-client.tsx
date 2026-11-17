"use client";
import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { useTranslation } from "react-i18next";
import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  CheckCircle2,
  Circle,
  BookOpen,
  BrainCircuit,
  GraduationCap,
  Loader2,
  PanelLeft,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import { getApiBaseUrl } from "@/lib/api-client";
import { useIsMobile } from "@/hooks/use-mobile";

interface QuizQuestion {
  question: string;
  options: string[];
  correctIndex: number;
  explanation?: string;
}

interface Chapter {
  id: string;
  title: string;
  content: string;
  order: number;
  completed: boolean;
  completedAt: number | null;
  quiz: QuizQuestion[];
}

interface CourseDetail {
  id: string;
  title: string;
  description: string;
  tags: string[];
  chapters: Chapter[];
  totalChapters: number;
  completedChapters: number;
  status: string;
}

export function CourseClient({ courseId }: { courseId: string }) {
  const { t } = useTranslation();
  const apiBase = getApiBaseUrl();
  const [course, setCourse] = useState<CourseDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [showQuiz, setShowQuiz] = useState(false);
  const [quizAnswers, setQuizAnswers] = useState<(number | null)[]>([]);
  const [quizSubmitted, setQuizSubmitted] = useState(false);
  const isMobile = useIsMobile();
  const [showSidebar, setShowSidebar] = useState(true);

  const fetchCourse = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/learn/courses/${courseId}`);
      if (res.ok) {
        const data = await res.json();
        setCourse(data);
        if (data.chapters?.length > 0 && quizAnswers.length === 0) {
          setQuizAnswers(new Array(data.chapters[0]?.quiz?.length || 0).fill(null));
        }
      }
    } catch (e) {
      console.error("Failed to fetch course", e);
    } finally {
      setLoading(false);
    }
  }, [apiBase, courseId]);

  useEffect(() => {
    fetchCourse();
  }, [fetchCourse]);

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!course) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 lg:gap-4">
        <p className="text-muted-foreground">{t("learn.courseNotFound") || "Course not found"}</p>
        <Link href="/learn" className="text-xs lg:text-sm text-primary hover:underline">
          ← {t("learn.backToCourses") || "Back to courses"}
        </Link>
      </div>
    );
  }

  const chapters = course.chapters || [];
  const chapter = chapters[currentIndex];
  const completedCount = chapters.filter((c) => c.completed).length;
  const progress = chapters.length > 0 ? Math.round((completedCount / chapters.length) * 100) : 0;
  const currentQuiz = chapter?.quiz || [];

  // Re-init quiz answers when chapter changes
  const handleChapterChange = (index: number) => {
    setCurrentIndex(index);
    setShowQuiz(false);
    setQuizSubmitted(false);
    setQuizAnswers(new Array(chapters[index]?.quiz?.length || 0).fill(null));
  };

  const toggleComplete = async () => {
    if (!chapter) return;
    try {
      const res = await fetch(
        `${apiBase}/api/learn/courses/${courseId}/chapters/${chapter.id}/mark`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ completed: !chapter.completed }),
        },
      );
      if (res.ok) {
        const updated = await res.json();
        setCourse((prev) => {
          if (!prev) return prev;
          const newChapters = prev.chapters.map((ch) =>
            ch.id === chapter.id ? { ...ch, completed: updated.completed, completedAt: updated.completedAt } : ch,
          );
          return {
            ...prev,
            chapters: newChapters,
            completedChapters: newChapters.filter((c) => c.completed).length,
          };
        });
      }
    } catch (e) {
      console.error("Failed to mark chapter", e);
    }
  };

  const submitQuiz = () => setQuizSubmitted(true);

  const quizScore = quizSubmitted
    ? quizAnswers.filter((a, i) => a === currentQuiz[i]?.correctIndex).length
    : null;

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-4 md:px-3 lg:px-6 py-3 md:py-4">
        <div className="flex items-center gap-2 md:gap-3">
          {isMobile && (
            <button onClick={() => setShowSidebar(true)} className="p-1 rounded hover:bg-muted">
              <PanelLeft size={16} />
            </button>
          )}
          <Link
            href="/learn"
            className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <ArrowLeft size={16} />
          </Link>
          <div>
            <h1 className="text-xs lg:text-sm font-semibold">{course.title}</h1>
            <p className="text-xs text-muted-foreground">
              {t("learn.chaptersCompletedCount", { completed: completedCount, total: chapters.length }) || `${completedCount}/${chapters.length} chapters completed`}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {currentQuiz.length > 0 && (
            <button
              onClick={() => setShowQuiz(!showQuiz)}
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border px-3 text-xs font-medium hover:bg-accent"
            >
              <BrainCircuit size={14} />
              <span className="hidden lg:inline">{showQuiz ? t("learn.hideQuiz") || "隐藏测验" : t("learn.takeQuiz") || "开始测验"}</span>
            </button>
          )}
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* Chapter sidebar — sidebar-style sliding on mobile, inline on desktop */}
        {isMobile && showSidebar && (
          <div className="fixed inset-0 z-9 bg-black/40 animate-in fade-in-0" onClick={() => setShowSidebar(false)} aria-hidden="true" />
        )}
        {isMobile ? (
          showSidebar && (
            <div
              className="absolute inset-y-0 left-0 z-10 h-full w-72 min-w-0 border-r border-border transition-[left] duration-200 ease-linear flex flex-col overflow-hidden bg-card"
              style={{ left: 0 }}
            >
              <div className="p-4 border-b border-border flex items-center justify-between">
                <h3 className="text-xs lg:text-sm font-medium">{t("learn.chapters") || "Chapters"}</h3>
                <button onClick={() => setShowSidebar(false)} className="p-1 rounded hover:bg-muted">
                  <ChevronLeft size={16} />
                </button>
              </div>
              <div className="flex-1 overflow-y-auto p-3 lg:p-4">
                {/* Progress */}
                <div className="mb-4">
                  <div className="mb-1 flex items-center justify-between text-xs">
                    <span className="text-muted-foreground">{t("learn.progress")}</span>
                    <span className="font-medium">{progress}%</span>
                  </div>
                  <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full bg-primary transition-all"
                      style={{ width: `${progress}%` }}
                    />
                  </div>
                </div>

                {/* Chapter list */}
                <nav className="space-y-0.5">
                  {chapters.map((ch, i) => (
                    <button
                      key={ch.id}
                      onClick={() => { handleChapterChange(i); if (isMobile) setShowSidebar(false); }}
                      className={`flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-xs lg:text-sm transition-colors ${
                        i === currentIndex
                          ? "bg-accent text-foreground font-medium"
                          : "text-muted-foreground hover:bg-accent hover:text-foreground"
                      }`}
                    >
                      {ch.completed ? (
                        <CheckCircle2 size={14} className="shrink-0 text-green-500" />
                      ) : (
                        <Circle size={14} className="shrink-0" />
                      )}
                      <span className="truncate">{ch.title}</span>
                    </button>
                  ))}
                </nav>
              </div>
            </div>
          )
        ) : (
          <aside className="w-64 shrink-0 overflow-y-auto border-r border-border p-3 lg:p-4">
            {/* Progress */}
            <div className="mb-4">
              <div className="mb-1 flex items-center justify-between text-xs">
                <span className="text-muted-foreground">{t("learn.progress")}</span>
                <span className="font-medium">{progress}%</span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full rounded-full bg-primary transition-all"
                  style={{ width: `${progress}%` }}
                />
              </div>
            </div>

            {/* Chapter list */}
            <nav className="space-y-0.5">
              {chapters.map((ch, i) => (
                <button
                  key={ch.id}
                  onClick={() => handleChapterChange(i)}
                  className={`flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-xs lg:text-sm transition-colors ${
                    i === currentIndex
                      ? "bg-accent text-foreground font-medium"
                      : "text-muted-foreground hover:bg-accent hover:text-foreground"
                  }`}
                >
                  {ch.completed ? (
                    <CheckCircle2 size={14} className="shrink-0 text-green-500" />
                  ) : (
                    <Circle size={14} className="shrink-0" />
                  )}
                  <span className="truncate">{ch.title}</span>
                </button>
              ))}
            </nav>
          </aside>
        )}

        {/* Main content */}
        <div className="flex-1 overflow-y-auto">
          {!chapter ? (
            <div className="flex h-full flex-col items-center justify-center text-muted-foreground">
              <BookOpen size={48} className="mb-4 opacity-30" />
              <p className="text-xs lg:text-sm">{t("learn.noChapters") || "No chapters yet"}</p>
            </div>
          ) : showQuiz && currentQuiz.length > 0 ? (
            <div className="mx-auto max-w-2xl p-8">
              <div className="mb-3 lg:mb-6 flex items-center gap-2">
                <GraduationCap size={20} className="text-primary" />
                <h2 className="text-lg font-semibold">{t("learn.chapterQuiz") || "Chapter Quiz"}</h2>
              </div>

              <div className="space-y-3 lg:space-y-6">
                {currentQuiz.map((q, qi) => (
                  <div
                    key={qi}
                    className="rounded-lg border border-border bg-card p-3 lg:p-4"
                  >
                    <p className="mb-3 text-xs lg:text-sm font-medium">
                      {qi + 1}. {q.question}
                    </p>
                    <div className="space-y-2">
                      {q.options.map((opt, oi) => {
                        const isSelected = quizAnswers[qi] === oi;
                        const isCorrect = quizSubmitted && oi === q.correctIndex;
                        const isWrong =
                          quizSubmitted && isSelected && oi !== q.correctIndex;

                        return (
                          <button
                            key={oi}
                            disabled={quizSubmitted}
                            onClick={() =>
                              setQuizAnswers((prev) => {
                                const next = [...prev];
                                next[qi] = oi;
                                return next;
                              })
                            }
                            className={`flex w-full items-center gap-2 rounded-md border px-3 py-2 text-left text-xs lg:text-sm transition-colors ${
                              isCorrect
                                ? "border-green-500 bg-green-500/10 text-green-700"
                                : isWrong
                                  ? "border-red-500 bg-red-500/10 text-red-700"
                                  : isSelected
                                    ? "border-primary bg-primary/10"
                                    : "border-border hover:bg-accent"
                            }`}
                          >
                            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-border text-[10px]">
                              {String.fromCharCode(65 + oi)}
                            </span>
                            {opt}
                          </button>
                        );
                      })}
                    </div>
                    {quizSubmitted && q.explanation && (
                      <p className="mt-3 text-xs text-muted-foreground">
                        💡 {q.explanation}
                      </p>
                    )}
                  </div>
                ))}
              </div>

              <div className="mt-6 flex items-center justify-between">
                {quizSubmitted ? (
                  <p className="text-xs lg:text-sm font-medium">
                    {t("learn.score") || "Score"}: {quizScore}/{currentQuiz.length} (
                    {Math.round(((quizScore ?? 0) / currentQuiz.length) * 100)}%)
                  </p>
                ) : (
                  <div />
                )}
                <button
                  onClick={submitQuiz}
                  disabled={quizSubmitted || quizAnswers.some((a) => a === null)}
                  className="inline-flex h-8 items-center gap-1.5 rounded-md bg-primary px-4 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                >
                  {quizSubmitted ? t("learn.submitted") || "Submitted" : t("learn.submitAnswers") || "Submit Answers"}
                </button>
              </div>
            </div>
          ) : (
            <div className="mx-auto max-w-3xl p-8">
              <div className="mb-3 lg:mb-6">
                <h2 className="mb-2 text-xl font-semibold">{chapter.title}</h2>
                <div className="flex flex-wrap gap-1.5">
                  {course.tags.map((tag) => (
                    <span
                      key={tag}
                      className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground"
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              </div>

              <article className="prose prose-sm dark:prose-invert max-w-none">
                <ReactMarkdown>{chapter.content}</ReactMarkdown>
              </article>

              {/* Bottom actions */}
              <div className="mt-8 flex items-center justify-between border-t border-border pt-4">
                <button
                  onClick={() => handleChapterChange(Math.max(0, currentIndex - 1))}
                  disabled={currentIndex === 0}
                  className="inline-flex h-8 items-center gap-1 rounded-md border border-border px-3 text-xs font-medium hover:bg-accent disabled:opacity-40"
                >
                  <ChevronLeft size={14} />
                  {t("learn.previous") || "Previous"}
                </button>

                <button
                  onClick={toggleComplete}
                  className={`inline-flex h-8 items-center gap-1.5 rounded-md px-4 text-xs font-medium transition-colors ${
                    chapter.completed
                      ? "bg-green-500/10 text-green-600 hover:bg-green-500/20"
                      : "bg-primary text-primary-foreground hover:bg-primary/90"
                  }`}
                >
                  {chapter.completed ? (
                    <>
                      <CheckCircle2 size={14} />
                      {t("learn.learned") || "Learned"}
                    </>
                  ) : (
                    <>
                      <BookOpen size={14} />
                      {t("learn.markAsLearned") || "Mark as Learned"}
                    </>
                  )}
                </button>

                <button
                  onClick={() =>
                    handleChapterChange(Math.min(chapters.length - 1, currentIndex + 1))
                  }
                  disabled={currentIndex === chapters.length - 1}
                  className="inline-flex h-8 items-center gap-1 rounded-md border border-border px-3 text-xs font-medium hover:bg-accent disabled:opacity-40"
                >
                  {t("learn.next") || "Next"}
                  <ChevronRight size={14} />
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
