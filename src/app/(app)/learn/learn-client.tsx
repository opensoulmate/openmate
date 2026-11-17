'use client';
import { useState, useEffect, useCallback, useMemo } from 'react';
import { LearningCharts } from './learning-charts';
import {
  BookOpen, Plus, Clock, CheckCircle2, RotateCcw, Trash2,
  Loader2, X, Upload, FileText, Edit3, Sparkles,
  Award, BrainCircuit, GraduationCap, Circle, ChevronRight,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import { getApiBaseUrl } from '@/lib/api-client';
import { PageLayout } from '@/components/page-layout';
import { DetailPanel } from '@/components/detail-panel';
import { LeftPanel } from '@/components/left-panel';
import { useAppStore } from '@/stores/app-store';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';

// ── Types ────────────────────────────────────────────────────

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

interface Course {
  id: string;
  title: string;
  description: string;
  tags: string[];
  totalChapters: number;
  completedChapters: number;
  status: 'not_started' | 'in_progress' | 'reviewing' | 'completed';
  updatedAt: number;
  chapters?: Chapter[];
}

function timeAgo(ts: number, t: (key: string, opts?: Record<string, unknown>) => string): string {
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return t('common.justNow') || 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

// ── Status config ────────────────────────────────────────────

const statusConfig = {
  not_started: { label: 'Not Started', color: 'text-muted-foreground', icon: BookOpen, dot: 'bg-zinc-500' },
  in_progress: { label: 'In Progress', color: 'text-blue-500', icon: Clock, dot: 'bg-blue-500' },
  reviewing: { label: 'Reviewing', color: 'text-amber-500', icon: RotateCcw, dot: 'bg-amber-500' },
  completed: { label: 'Completed', color: 'text-green-500', icon: CheckCircle2, dot: 'bg-green-500' },
};

// ── Main Component ───────────────────────────────────────────

export function LearnClient() {
  const { t } = useTranslation();
  const apiBase = getApiBaseUrl();
  const setPageSidebar = useAppStore((s) => s.setPageSidebar);
  const setPageWorkspace = useAppStore((s) => s.setPageWorkspace);

  // Data
  const [courses, setCourses] = useState<Course[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedCourseId, setSelectedCourseId] = useState<string | null>(null);
  const [selectedChapterIndex, setSelectedChapterIndex] = useState<number | null>(null);
  const [courseDetail, setCourseDetail] = useState<Course | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // Modal states
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [showLearningCard, setShowLearningCard] = useState(false);
  const [showPolicyCard, setShowPolicyCard] = useState(false);
  const [generating, setGenerating] = useState(false);

  // Create/Edit form state
  const [formTitle, setFormTitle] = useState('');
  const [formDescription, setFormDescription] = useState('');
  const [formTags, setFormTags] = useState('');
  const [genMode, setGenMode] = useState<'ai' | 'manual'>('ai');
  const [numChapters, setNumChapters] = useState(5);
  const [difficulty, setDifficulty] = useState('intermediate');

  // Quiz state
  const [showQuiz, setShowQuiz] = useState(false);
  const [quizAnswers, setQuizAnswers] = useState<(number | null)[]>([]);
  const [quizSubmitted, setQuizSubmitted] = useState(false);

  // ── Data fetching ──────────────────────────────────────────

  const fetchCourses = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/learn/courses`);
      if (res.ok) {
        const data = await res.json();
        setCourses(data.courses || []);
      }
    } catch (e) {
      console.error('Failed to fetch courses', e);
    } finally {
      setLoading(false);
    }
  }, [apiBase]);

  const fetchCourseDetail = useCallback(async (courseId: string) => {
    setDetailLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/learn/courses/${courseId}`);
      if (res.ok) {
        const data = await res.json();
        setCourseDetail(data);
      }
    } catch (e) {
      console.error('Failed to fetch course detail', e);
    } finally {
      setDetailLoading(false);
    }
  }, [apiBase]);

  useEffect(() => { fetchCourses(); }, [fetchCourses]);

  useEffect(() => {
    if (selectedCourseId) {
      fetchCourseDetail(selectedCourseId);
      setSelectedChapterIndex(null);
      setShowQuiz(false);
    } else {
      setCourseDetail(null);
      setSelectedChapterIndex(null);
    }
  }, [selectedCourseId, fetchCourseDetail]);

  // ── Course actions ─────────────────────────────────────────

  const handleCreate = async () => {
    if (!formTitle.trim()) return;
    setGenerating(true);
    try {
      if (genMode === 'ai') {
        const res = await fetch(`${apiBase}/api/learn/courses/generate`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ topic: formTitle, num_chapters: numChapters, language: 'zh', difficulty }),
        });
        if (res.ok) {
          const course = await res.json();
          setCourses((prev) => [...prev, course]);
          setShowCreateModal(false);
          resetForm();
        } else {
          const err = await res.json();
          alert(err.detail || 'AI generation failed');
        }
      } else {
        const tags = formTags.split(',').map((t) => t.trim()).filter(Boolean);
        const res = await fetch(`${apiBase}/api/learn/courses`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: formTitle, description: formDescription || `Course about ${formTitle}`, tags }),
        });
        if (res.ok) {
          const course = await res.json();
          setCourses((prev) => [...prev, course]);
          setShowCreateModal(false);
          resetForm();
        }
      }
    } catch (e) {
      console.error('Failed to create course', e);
    } finally {
      setGenerating(false);
    }
  };

  const handleEdit = async () => {
    if (!selectedCourseId || !formTitle.trim()) return;
    setGenerating(true);
    try {
      const tags = formTags.split(',').map((t) => t.trim()).filter(Boolean);
      const res = await fetch(`${apiBase}/api/learn/courses/${selectedCourseId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: formTitle, description: formDescription, tags }),
      });
      if (res.ok) {
        await fetchCourses();
        if (selectedCourseId) await fetchCourseDetail(selectedCourseId);
        setShowEditModal(false);
        resetForm();
      }
    } catch (e) {
      console.error('Failed to update course', e);
    } finally {
      setGenerating(false);
    }
  };

  const handleDelete = async (courseId: string) => {
    if (!confirm(t('learn.confirmDelete') || 'Delete this course?')) return;
    try {
      await fetch(`${apiBase}/api/learn/courses/${courseId}`, { method: 'DELETE' });
      setCourses((prev) => prev.filter((c) => c.id !== courseId));
      if (selectedCourseId === courseId) setSelectedCourseId(null);
    } catch (e) {
      console.error('Failed to delete', e);
    }
  };

  const handleImport = () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json';
    input.onchange = async (e) => {
      const file = (e.target as HTMLInputElement).files?.[0];
      if (!file) return;
      try {
        const text = await file.text();
        const data = JSON.parse(text);
        const res = await fetch(`${apiBase}/api/learn/courses`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            title: data.title || file.name.replace('.json', ''),
            description: data.description || 'Imported course',
            tags: data.tags || [],
            chapters: data.chapters || [],
          }),
        });
        if (res.ok) await fetchCourses();
      } catch {
        alert('导入失败：JSON 格式无效');
      }
    };
    input.click();
  };

  // ── Chapter actions ────────────────────────────────────────

  const chapters = courseDetail?.chapters || [];
  const currentChapter = selectedChapterIndex !== null ? chapters[selectedChapterIndex] : null;
  const currentQuiz = currentChapter?.quiz || [];
  const quizScore = quizSubmitted ? quizAnswers.filter((a, i) => a === currentQuiz[i]?.correctIndex).length : null;

  const handleChapterSelect = (index: number) => {
    setSelectedChapterIndex(index);
    setShowQuiz(false);
    setQuizSubmitted(false);
    setQuizAnswers(new Array(chapters[index]?.quiz?.length || 0).fill(null));
  };

  const toggleComplete = async () => {
    if (!currentChapter || !selectedCourseId) return;
    try {
      const res = await fetch(
        `${apiBase}/api/learn/courses/${selectedCourseId}/chapters/${currentChapter.id}/mark`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ completed: !currentChapter.completed }),
        },
      );
      if (res.ok) {
        const updated = await res.json();
        setCourseDetail((prev) => {
          if (!prev) return prev;
          const newChapters = prev.chapters!.map((ch) =>
            ch.id === currentChapter.id ? { ...ch, completed: updated.completed, completedAt: updated.completedAt } : ch,
          );
          return { ...prev, chapters: newChapters, completedChapters: newChapters.filter((c) => c.completed).length };
        });
        // Also update list
        setCourses((prev) =>
          prev.map((c) =>
            c.id === selectedCourseId
              ? {
                  ...c,
                  completedChapters: updated.completed
                    ? c.completedChapters + 1
                    : c.completedChapters - 1,
                }
              : c,
          ),
        );
      }
    } catch (e) {
      console.error('Failed to mark chapter', e);
    }
  };

  // ── Helpers ────────────────────────────────────────────────

  const resetForm = () => {
    setFormTitle('');
    setFormDescription('');
    setFormTags('');
    setGenMode('ai');
    setNumChapters(5);
    setDifficulty('intermediate');
  };

  const selectedCourse = useMemo(() => courses.find((c) => c.id === selectedCourseId) ?? null, [courses, selectedCourseId]);

  const completedCount = chapters.filter((c) => c.completed).length;
  const progress = chapters.length > 0 ? Math.round((completedCount / chapters.length) * 100) : 0;

  // ── LeftPanel sidebar ──────────────────────────────────────

  useEffect(() => {
    setPageSidebar(
      <LeftPanel
        items={courses}
        filter={(course, q) =>
          course.title.toLowerCase().includes(q) ||
          course.tags.some((t) => t.toLowerCase().includes(q))
        }
        renderItem={(course) => {
          const cfg = statusConfig[course.status];
          const prog = course.totalChapters > 0 ? Math.round((course.completedChapters / course.totalChapters) * 100) : 0;
          return (
            <button
              key={course.id}
              onClick={() => setSelectedCourseId(course.id)}
              className={cn(
                'w-full text-left px-2 py-2 rounded-lg transition-colors',
                selectedCourseId === course.id
                  ? 'bg-primary/10 border border-primary/30'
                  : 'hover:bg-muted/50 border border-transparent',
              )}
            >
              <div className="flex items-center gap-1.5 min-w-0">
                <span className={cn('w-2 h-2 rounded-full shrink-0', cfg.dot)} />
                <span className="text-xs font-medium truncate flex-1">{course.title}</span>
              </div>
              <div className="ml-3.5 mt-1">
                <div className="flex items-center justify-between text-[10px] text-muted-foreground mb-0.5">
                  <span>{course.completedChapters}/{course.totalChapters} ch.</span>
                  <span>{prog}%</span>
                </div>
                <div className="h-1 w-full overflow-hidden rounded-full bg-muted">
                  <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${prog}%` }} />
                </div>
              </div>
            </button>
          );
        }}
        header={
          <div className="px-2 pb-2 flex gap-1.5">
            <button
              onClick={() => { resetForm(); setShowCreateModal(true); }}
              className="flex-1 flex items-center justify-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
            >
              <Plus className="w-3.5 h-3.5" />
              {t('learn.createCourse') || '新建课程'}
            </button>
            <button
              onClick={handleImport}
              className="flex items-center justify-center rounded-lg border border-border px-2.5 py-1.5 text-xs text-muted-foreground hover:bg-muted transition-colors"
              title="导入"
            >
              <Upload className="w-3.5 h-3.5" />
            </button>
          </div>
        }
        placeholder={t('learn.filterCourses') || '搜索课程...'}
        emptyState={
          <div className="px-2 py-8 text-center text-muted-foreground/50">
            <BookOpen className="w-8 h-8 mx-auto mb-1.5" />
            <p className="text-xs">{t('learn.noCourses') || '暂无课程'}</p>
          </div>
        }
      />,
    );
    return () => setPageSidebar(null);
  }, [courses, selectedCourseId, t, setPageSidebar]);

  // ── RightPanel workspace (chapter detail) ──────────────────

  useEffect(() => {
    if (!currentChapter || !courseDetail) {
      setPageWorkspace(null);
      return;
    }

    setPageWorkspace(
      <DetailPanel
        title={currentChapter.title}
        subtitle={`${courseDetail.title} · ${t('learn.chapter') || 'Chapter'} ${(selectedChapterIndex ?? 0) + 1}/${chapters.length}`}
        icon={<BookOpen className="w-5 h-5 text-primary" />}
        badge={currentChapter.completed ? '✓' : undefined}
        onClose={() => { setSelectedChapterIndex(null); setShowQuiz(false); }}
        headerActions={
          currentQuiz.length > 0 ? (
            <button
              onClick={() => setShowQuiz(!showQuiz)}
              className="p-1.5 rounded-md hover:bg-muted/50 text-muted-foreground"
              title={showQuiz ? '隐藏测验' : '开始测验'}
            >
              <BrainCircuit className="w-4 h-4" />
            </button>
          ) : undefined
        }
      >
        {/* Chapter content */}
        <div className="space-y-4">
          {showQuiz && currentQuiz.length > 0 ? (
            /* Quiz mode */
            <div className="space-y-4">
              <h3 className="text-sm font-medium flex items-center gap-2">
                <GraduationCap size={16} className="text-primary" />
                {t('learn.chapterQuiz') || 'Chapter Quiz'}
              </h3>
              {currentQuiz.map((q, qi) => (
                <div key={qi} className="rounded-lg border border-border bg-card p-3">
                  <p className="mb-2 text-xs font-medium">{qi + 1}. {q.question}</p>
                  <div className="space-y-1.5">
                    {q.options.map((opt, oi) => {
                      const isSelected = quizAnswers[qi] === oi;
                      const isCorrect = quizSubmitted && oi === q.correctIndex;
                      const isWrong = quizSubmitted && isSelected && oi !== q.correctIndex;
                      return (
                        <button
                          key={oi}
                          disabled={quizSubmitted}
                          onClick={() => setQuizAnswers((prev) => { const next = [...prev]; next[qi] = oi; return next; })}
                          className={cn(
                            'flex w-full items-center gap-2 rounded-md border px-2.5 py-1.5 text-left text-xs transition-colors',
                            isCorrect ? 'border-green-500 bg-green-500/10 text-green-700'
                              : isWrong ? 'border-red-500 bg-red-500/10 text-red-700'
                              : isSelected ? 'border-primary bg-primary/10'
                              : 'border-border hover:bg-accent',
                          )}
                        >
                          <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-border text-[10px]">
                            {String.fromCharCode(65 + oi)}
                          </span>
                          {opt}
                        </button>
                      );
                    })}
                  </div>
                  {quizSubmitted && q.explanation && (
                    <p className="mt-2 text-[10px] text-muted-foreground">💡 {q.explanation}</p>
                  )}
                </div>
              ))}
              <div className="flex items-center justify-between pt-2">
                {quizSubmitted ? (
                  <p className="text-xs font-medium">
                    {t('learn.score') || 'Score'}: {quizScore}/{currentQuiz.length} ({Math.round(((quizScore ?? 0) / currentQuiz.length) * 100)}%)
                  </p>
                ) : <div />}
                <button
                  onClick={() => setQuizSubmitted(true)}
                  disabled={quizSubmitted || quizAnswers.some((a) => a === null)}
                  className="inline-flex h-7 items-center gap-1 rounded-md bg-primary px-3 text-[11px] font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                >
                  {quizSubmitted ? (t('learn.submitted') || 'Submitted') : (t('learn.submitAnswers') || 'Submit')}
                </button>
              </div>
            </div>
          ) : (
            /* Chapter content */
            <div className="space-y-4">
              <article className="prose prose-sm dark:prose-invert max-w-none text-xs lg:text-sm">
                <ReactMarkdown>{currentChapter.content}</ReactMarkdown>
              </article>
              {/* Tags */}
              {courseDetail.tags.length > 0 && (
                <div className="flex flex-wrap gap-1.5 pt-2">
                  {courseDetail.tags.map((tag) => (
                    <span key={tag} className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">
                      {tag}
                    </span>
                  ))}
                </div>
              )}
              {/* Mark complete */}
              <div className="pt-3 border-t border-border">
                <button
                  onClick={toggleComplete}
                  className={cn(
                    'w-full inline-flex items-center justify-center gap-1.5 rounded-md px-4 py-2 text-xs font-medium transition-colors',
                    currentChapter.completed
                      ? 'bg-green-500/10 text-green-600 hover:bg-green-500/20'
                      : 'bg-primary text-primary-foreground hover:bg-primary/90',
                  )}
                >
                  {currentChapter.completed ? (
                    <><CheckCircle2 size={14} /> {t('learn.learned') || 'Learned'}</>
                  ) : (
                    <><BookOpen size={14} /> {t('learn.markAsLearned') || 'Mark as Learned'}</>
                  )}
                </button>
              </div>
            </div>
          )}
        </div>
      </DetailPanel>,
    );
    return () => setPageWorkspace(null);
  }, [currentChapter, courseDetail, chapters, selectedChapterIndex, showQuiz, quizAnswers, quizSubmitted, quizScore, t, setPageWorkspace]);

  // ── Loading state ──────────────────────────────────────────

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  // ── Main content ───────────────────────────────────────────

  return (
    <PageLayout title="学习" icon={<GraduationCap size={16} className="text-primary" />}>
      <div className="h-full overflow-y-auto">
        {!selectedCourseId ? (
          /* No course selected — dashboard overview */
          <div className="p-3 lg:p-6 space-y-4 lg:space-y-6">
            {/* ── Continue Learning ────────────────────────── */}
            {(() => {
              const inProgress = courses.filter(c => c.status === 'in_progress').sort((a, b) => b.updatedAt - a.updatedAt);
              const recent = inProgress[0] ?? courses.filter(c => c.status === 'reviewing').sort((a, b) => b.updatedAt - a.updatedAt)[0];
              if (!recent) return null;
              const prog = recent.totalChapters > 0 ? Math.round((recent.completedChapters / recent.totalChapters) * 100) : 0;
              return (
                <div className="rounded-xl border border-primary/20 bg-gradient-to-br from-primary/5 to-transparent p-4 lg:p-5">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="text-[10px] uppercase tracking-wider text-primary font-medium mb-1">
                        {t('learn.continueLearning') || '继续学习'}
                      </p>
                      <h3 className="text-sm lg:text-base font-semibold truncate mb-1">{recent.title}</h3>
                      <p className="text-xs text-muted-foreground line-clamp-1 mb-3">{recent.description}</p>
                      <div className="flex items-center gap-3">
                        <div className="flex-1">
                          <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                            <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${prog}%` }} />
                          </div>
                        </div>
                        <span className="text-[10px] text-muted-foreground shrink-0">
                          {recent.completedChapters}/{recent.totalChapters} ch. · {prog}%
                        </span>
                      </div>
                    </div>
                    <button
                      onClick={() => setSelectedCourseId(recent.id)}
                      className="shrink-0 inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-xs font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
                    >
                      <ChevronRight size={14} />
                      {t('learn.continue') || '继续'}
                    </button>
                  </div>
                </div>
              );
            })()}

            {/* ── Stats Row ────────────────────────────────── */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 lg:gap-3">
              <div className="rounded-lg border border-border bg-card p-3">
                <p className="text-xl lg:text-2xl font-bold">{courses.length}</p>
                <p className="text-[10px] text-muted-foreground mt-0.5">{t('learn.totalCourses') || '全部课程'}</p>
              </div>
              <div className="rounded-lg border border-border bg-card p-3">
                <p className="text-xl lg:text-2xl font-bold text-blue-500">{courses.reduce((s, c) => s + c.completedChapters, 0)}</p>
                <p className="text-[10px] text-muted-foreground mt-0.5">{t('learn.chaptersLearned') || '已学章节'}</p>
              </div>
              <div className="rounded-lg border border-border bg-card p-3">
                <p className="text-xl lg:text-2xl font-bold text-amber-500">{courses.reduce((s, c) => s + c.totalChapters - c.completedChapters, 0)}</p>
                <p className="text-[10px] text-muted-foreground mt-0.5">{t('learn.chaptersPending') || '待学章节'}</p>
              </div>
              <div className="rounded-lg border border-border bg-card p-3">
                <p className="text-xl lg:text-2xl font-bold text-green-500">
                  {courses.reduce((s, c) => s + c.totalChapters, 0) > 0
                    ? Math.round((courses.reduce((s, c) => s + c.completedChapters, 0) / courses.reduce((s, c) => s + c.totalChapters, 0)) * 100)
                    : 0}%
                </p>
                <p className="text-[10px] text-muted-foreground mt-0.5">{t('learn.completionRate') || '完成率'}</p>
              </div>
            </div>

            {/* ── Learning Charts (Flint) ──────────────── */}
            <LearningCharts courses={courses} />

            {courses.length === 0 && (
              <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                <BookOpen size={48} className="mb-4 opacity-30" />
                <p className="text-sm">{t('learn.noCourses') || '暂无课程'}</p>
                <button
                  onClick={() => { resetForm(); setShowCreateModal(true); }}
                  className="mt-2 text-xs text-primary hover:underline"
                >
                  {t('learn.generateFirst') || '生成你的第一门课程 →'}
                </button>
              </div>
            )}
          </div>
        ) : (
          /* Course selected — show chapters list */
          <div className="p-3 lg:p-6">
            {/* Course header */}
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="text-base lg:text-lg font-semibold">{selectedCourse?.title || courseDetail?.title}</h2>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {completedCount}/{chapters.length} {t('learn.chaptersCompleted') || 'chapters completed'} · {progress}%
                </p>
              </div>
              <div className="flex items-center gap-1.5">
                <button
                  onClick={() => {
                    if (selectedCourse) {
                      setFormTitle(selectedCourse.title);
                      setFormDescription(selectedCourse.description);
                      setFormTags(selectedCourse.tags.join(', '));
                      setShowEditModal(true);
                    }
                  }}
                  className="p-1.5 rounded-md hover:bg-muted text-muted-foreground"
                  title="编辑"
                >
                  <Edit3 size={14} />
                </button>
                <button
                  onClick={() => handleDelete(selectedCourseId!)}
                  className="p-1.5 rounded-md hover:bg-muted text-muted-foreground hover:text-red-500"
                  title="删除"
                >
                  <Trash2 size={14} />
                </button>
                {selectedCourse?.status === 'completed' && (
                  <button
                    onClick={() => setShowLearningCard(true)}
                    className="p-1.5 rounded-md hover:bg-muted text-muted-foreground hover:text-amber-500"
                    title="学习卡片"
                  >
                    <Award size={14} />
                  </button>
                )}
              </div>
            </div>

            {/* Progress bar */}
            <div className="mb-4 lg:mb-6">
              <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${progress}%` }} />
              </div>
            </div>

            {/* Chapter list */}
            {detailLoading ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              </div>
            ) : chapters.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
                <BookOpen size={36} className="mb-3 opacity-30" />
                <p className="text-xs">{t('learn.noChapters') || 'No chapters yet'}</p>
              </div>
            ) : (
              <div className="space-y-1.5">
                {chapters.map((ch, i) => (
                  <button
                    key={ch.id}
                    onClick={() => handleChapterSelect(i)}
                    className={cn(
                      'w-full flex items-center gap-3 rounded-lg px-3 py-2.5 text-left transition-colors',
                      selectedChapterIndex === i
                        ? 'bg-primary/10 border border-primary/30'
                        : 'hover:bg-muted/50 border border-transparent',
                    )}
                  >
                    {ch.completed ? (
                      <CheckCircle2 size={16} className="shrink-0 text-green-500" />
                    ) : (
                      <Circle size={16} className="shrink-0 text-muted-foreground/40" />
                    )}
                    <div className="flex-1 min-w-0">
                      <span className="text-xs lg:text-sm font-medium truncate block">{ch.title}</span>
                      {ch.completedAt && (
                        <span className="text-[10px] text-muted-foreground">
                          ✓ {new Date(ch.completedAt * 1000).toLocaleDateString()}
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-1.5 shrink-0">
                      {ch.quiz && ch.quiz.length > 0 && (
                        <span className="text-[10px] text-muted-foreground flex items-center gap-0.5">
                          <BrainCircuit size={10} /> {ch.quiz.length}
                        </span>
                      )}
                      <ChevronRight size={14} className="text-muted-foreground/40" />
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* ===== CREATE MODAL ===== */}
      {showCreateModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowCreateModal(false)}>
          <div className="w-full max-w-lg rounded-xl border border-border bg-background p-3 lg:p-6 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-base font-semibold">{t('learn.createCourse') || 'Create Course'}</h2>
              <button onClick={() => setShowCreateModal(false)} className="rounded-md p-1 hover:bg-accent"><X size={16} /></button>
            </div>
            <div className="space-y-4">
              <div>
                <label className="mb-1 block text-xs font-medium">{t('learn.courseTitle') || 'Title'}</label>
                <input value={formTitle} onChange={(e) => setFormTitle(e.target.value)} placeholder="输入课程标题..." className="w-full rounded-md border border-border bg-muted px-3 py-2 text-sm outline-none focus:border-primary" autoFocus />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium">{t('learn.description') || 'Description'} <span className="text-muted-foreground">（可选）</span></label>
                <textarea value={formDescription} onChange={(e) => setFormDescription(e.target.value)} placeholder="简要描述..." rows={2} className="w-full resize-none rounded-md border border-border bg-muted px-3 py-2 text-sm outline-none focus:border-primary" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium">{t('learn.tags') || 'Tags'} <span className="text-muted-foreground">（逗号分隔）</span></label>
                <input value={formTags} onChange={(e) => setFormTags(e.target.value)} placeholder="python, machine-learning, ..." className="w-full rounded-md border border-border bg-muted px-3 py-2 text-sm outline-none focus:border-primary" />
              </div>
              <div className="flex gap-2">
                <button onClick={() => setGenMode('ai')} className={cn('flex-1 rounded-md border px-3 py-2 text-sm transition-colors', genMode === 'ai' ? 'border-primary bg-primary/10 text-primary' : 'border-border text-muted-foreground hover:border-primary/40')}>
                  <Sparkles size={14} className="mr-1 inline" /> AI Generate
                </button>
                <button onClick={() => setGenMode('manual')} className={cn('flex-1 rounded-md border px-3 py-2 text-sm transition-colors', genMode === 'manual' ? 'border-primary bg-primary/10 text-primary' : 'border-border text-muted-foreground hover:border-primary/40')}>
                  <BookOpen size={14} className="mr-1 inline" /> Manual
                </button>
              </div>
              {genMode === 'ai' && (
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="mb-1 block text-xs text-muted-foreground">章节</label>
                    <select value={numChapters} onChange={(e) => setNumChapters(parseInt(e.target.value))} className="w-full rounded-md border border-border bg-muted px-3 py-2 text-sm outline-none">
                      {[3, 4, 5, 6, 7, 8, 10].map((n) => <option key={n} value={n}>{n}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="mb-1 block text-xs text-muted-foreground">难度</label>
                    <select value={difficulty} onChange={(e) => setDifficulty(e.target.value)} className="w-full rounded-md border border-border bg-muted px-3 py-2 text-sm outline-none">
                      <option value="beginner">初级</option>
                      <option value="intermediate">中级</option>
                      <option value="advanced">高级</option>
                    </select>
                  </div>
                </div>
              )}
            </div>
            <div className="mt-6 flex justify-end gap-2">
              <button onClick={() => { setShowCreateModal(false); resetForm(); }} className="rounded-md border border-border px-4 py-2 text-sm hover:bg-accent">取消</button>
              <button onClick={handleCreate} disabled={!formTitle.trim() || generating} className="inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
                {generating ? <><Loader2 size={14} className="animate-spin" /> 创建中...</> : <><Plus size={14} /> 创建</>}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ===== EDIT MODAL ===== */}
      {showEditModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowEditModal(false)}>
          <div className="w-full max-w-lg rounded-xl border border-border bg-background p-3 lg:p-6 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-base font-semibold">{t('learn.editCourse') || 'Edit Course'}</h2>
              <button onClick={() => setShowEditModal(false)} className="rounded-md p-1 hover:bg-accent"><X size={16} /></button>
            </div>
            <div className="space-y-4">
              <div>
                <label className="mb-1 block text-xs font-medium">标题</label>
                <input value={formTitle} onChange={(e) => setFormTitle(e.target.value)} className="w-full rounded-md border border-border bg-muted px-3 py-2 text-sm outline-none focus:border-primary" autoFocus />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium">描述</label>
                <textarea value={formDescription} onChange={(e) => setFormDescription(e.target.value)} rows={3} className="w-full resize-none rounded-md border border-border bg-muted px-3 py-2 text-sm outline-none focus:border-primary" />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium">标签</label>
                <input value={formTags} onChange={(e) => setFormTags(e.target.value)} className="w-full rounded-md border border-border bg-muted px-3 py-2 text-sm outline-none focus:border-primary" />
              </div>
            </div>
            <div className="mt-6 flex justify-end gap-2">
              <button onClick={() => { setShowEditModal(false); resetForm(); }} className="rounded-md border border-border px-4 py-2 text-sm hover:bg-accent">取消</button>
              <button onClick={handleEdit} disabled={!formTitle.trim() || generating} className="inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
                {generating ? <><Loader2 size={14} className="animate-spin" /> 保存中...</> : <><CheckCircle2 size={14} /> 保存</>}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ===== LEARNING CARD MODAL ===== */}
      {showLearningCard && selectedCourse && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowLearningCard(false)}>
          <div className="w-full max-w-md rounded-xl border border-border bg-background p-3 lg:p-6 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-base font-semibold flex items-center gap-2">
                <Award size={18} className="text-amber-500" />
                {t('learn.learningCard') || '学习卡片'}
              </h2>
              <button onClick={() => setShowLearningCard(false)} className="rounded-md p-1 hover:bg-accent"><X size={16} /></button>
            </div>
            <div className="relative overflow-hidden rounded-lg border-2 border-amber-400 bg-gradient-to-br from-amber-50 via-white to-blue-50 p-3 lg:p-6">
              <div className="absolute right-4 top-4 text-6xl opacity-10">🎓</div>
              <div className="mb-1 text-xs font-medium uppercase tracking-wider text-amber-600">结业证书</div>
              <div className="mb-4 text-lg font-bold">{selectedCourse.title}</div>
              <div className="mb-3 text-xs text-muted-foreground/80">{selectedCourse.description}</div>
              <div className="mb-4 flex items-center gap-4 text-xs text-muted-foreground">
                <span>📚 {selectedCourse.totalChapters} chapters</span>
                <span>✅ {selectedCourse.completedChapters} completed</span>
              </div>
              <div className="flex flex-wrap gap-1">
                {selectedCourse.tags.map((tag) => (
                  <span key={tag} className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] text-amber-700">{tag}</span>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ===== POLICY CARD MODAL ===== */}
      {showPolicyCard && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setShowPolicyCard(false)}>
          <div className="w-full max-w-lg rounded-xl border border-border bg-background p-3 lg:p-6 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-base font-semibold flex items-center gap-2">
                <FileText size={18} className="text-blue-500" />
                {t('learn.policyCard') || 'Learning Policy'}
              </h2>
              <button onClick={() => setShowPolicyCard(false)} className="rounded-md p-1 hover:bg-accent"><X size={16} /></button>
            </div>
            <div className="space-y-3 rounded-lg border border-border bg-muted/50 p-3 text-sm">
              <div>
                <h3 className="font-medium">📋 课程管理</h3>
                <ul className="mt-1 list-inside list-disc text-xs text-muted-foreground">
                  <li>支持创建、编辑、删除与导入</li>
                  <li>AI 生成需要 Gland 网关</li>
                </ul>
              </div>
              <div>
                <h3 className="font-medium">📊 学习进度</h3>
                <ul className="mt-1 list-inside list-disc text-xs text-muted-foreground">
                  <li>每章独立跟踪</li>
                  <li>全部章节完成后课程自动标记完成</li>
                </ul>
              </div>
              <div>
                <h3 className="font-medium">🔒 数据安全</h3>
                <ul className="mt-1 list-inside list-disc text-xs text-muted-foreground">
                  <li>所有数据本地存储</li>
                  <li>数据不上传云端</li>
                </ul>
              </div>
            </div>
          </div>
        </div>
      )}
    </PageLayout>
  );
}
