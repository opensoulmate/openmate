'use client';
import { useMemo } from 'react';
import { assembleECharts } from 'flint-chart';
import { TrendingUp, BarChart3, Calendar, PieChart } from 'lucide-react';

import { EChart } from '@/components/echart';

interface Course {
  id: string;
  title: string;
  tags: string[];
  totalChapters: number;
  completedChapters: number;
  status: string;
  updatedAt: number;
  chapters?: { id: string; title: string; completed: boolean; completedAt: number | null }[];
}

// ── 学习曲线：按日期累计完成章节 ───────────────────────

function LearningCurve({ courses }: { courses: Course[] }) {
  const option = useMemo(() => {
    const dateMap = new Map<string, number>();
    courses.flatMap(c => c.chapters ?? [])
      .filter(ch => ch.completed && ch.completedAt)
      .forEach(ch => {
        const key = new Date(ch.completedAt! * 1000).toISOString().slice(0, 10);
        dateMap.set(key, (dateMap.get(key) ?? 0) + 1);
      });

    const dates: string[] = [];
    const daily: number[] = [];
    const cumulative: number[] = [];
    let total = 0;
    const today = new Date();
    for (let i = 29; i >= 0; i--) {
      const d = new Date(today);
      d.setDate(d.getDate() - i);
      const key = d.toISOString().slice(0, 10);
      dates.push(key.slice(5)); // MM-DD
      const count = dateMap.get(key) ?? 0;
      daily.push(count);
      total += count;
      cumulative.push(total);
    }

    const result = assembleECharts({
      data: { values: dates.map((d, i) => ({ '日期': d, '累计': cumulative[i], '每日': daily[i] })) },
      semantic_types: { '日期': 'Temporal', '累计': 'Quantity', '每日': 'Quantity' },
      chart_spec: {
        chartType: 'Area Chart',
        encodings: {
          x: { field: '日期' },
          y: { field: '累计' },
        },
        canvasSize: { width: 500, height: 200 },
      },
    });
    return result;
  }, [courses]);

  return (
    <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
      <div className="flex items-center gap-2 mb-3">
        <TrendingUp size={14} className="text-primary" />
        <h4 className="text-xs font-medium">学习曲线（30天）</h4>
      </div>
      <EChart option={option as any} style={{ height: 200 }} opts={{ renderer: 'svg' }} />
    </div>
  );
}

// ── 课程完成率对比 ─────────────────────────────────────

function CourseComparison({ courses }: { courses: Course[] }) {
  const option = useMemo(() => {
    const values = courses.map(c => ({
      '课程': c.title.length > 10 ? c.title.slice(0, 10) + '…' : c.title,
      '完成率': c.totalChapters > 0 ? Math.round((c.completedChapters / c.totalChapters) * 100) : 0,
    }));

    return assembleECharts({
      data: { values },
      semantic_types: { '课程': 'Nominal', '完成率': 'Percentage' },
      chart_spec: {
        chartType: 'Bar Chart',
        encodings: {
          x: { field: '课程' },
          y: { field: '完成率' },
        },
        canvasSize: { width: 500, height: 200 },
      },
    });
  }, [courses]);

  return (
    <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
      <div className="flex items-center gap-2 mb-3">
        <BarChart3 size={14} className="text-blue-500" />
        <h4 className="text-xs font-medium">课程完成率对比</h4>
      </div>
      <EChart option={option as any} style={{ height: 200 }} opts={{ renderer: 'svg' }} />
    </div>
  );
}

// ── 学习热力图 ────────────────────────────────────────

function LearningHeatmap({ courses }: { courses: Course[] }) {
  const option = useMemo(() => {
    const dateMap = new Map<string, number>();
    courses.flatMap(c => c.chapters ?? [])
      .filter(ch => ch.completed && ch.completedAt)
      .forEach(ch => {
        const key = new Date(ch.completedAt! * 1000).toISOString().slice(0, 10);
        dateMap.set(key, (dateMap.get(key) ?? 0) + 1);
      });

    const values: Record<string, string | number>[] = [];
    const today = new Date();
    for (let i = 89; i >= 0; i--) {
      const d = new Date(today);
      d.setDate(d.getDate() - i);
      const key = d.toISOString().slice(0, 10);
      values.push({ '日期': key, '次数': dateMap.get(key) ?? 0 });
    }

    const opt = assembleECharts({
      data: { values },
      semantic_types: { '日期': 'Temporal', '次数': 'Quantity' },
      chart_spec: {
        chartType: 'Calendar Heatmap',
        encodings: {
          x: { field: '日期' },
          y: { field: '次数' },
        },
        canvasSize: { width: 500, height: 120 },
      },
    }) as Record<string, unknown>;
    // 视觉映射图例默认 high/low → 中文
    const vm = opt.visualMap as { text?: string[] } | undefined;
    if (vm) vm.text = ['高', '低'];
    return opt;
  }, [courses]);

  return (
    <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
      <div className="flex items-center gap-2 mb-3">
        <Calendar size={14} className="text-green-500" />
        <h4 className="text-xs font-medium">学习热力图（90天）</h4>
      </div>
      <EChart option={option as any} style={{ height: 120 }} opts={{ renderer: 'svg' }} />
    </div>
  );
}

// ── 状态分布饼图 ──────────────────────────────────────

function StatusPie({ courses }: { courses: Course[] }) {
  const option = useMemo(() => {
    const statusMap = new Map<string, number>();
    courses.forEach(c => {
      const label = c.status === 'not_started' ? '未开始' : c.status === 'in_progress' ? '进行中' : c.status === 'reviewing' ? '复习中' : '已完成';
      statusMap.set(label, (statusMap.get(label) ?? 0) + 1);
    });
    const values = Array.from(statusMap.entries()).map(([status, count]) => ({ status, count }));

    return assembleECharts({
      data: { values },
      semantic_types: { status: 'Nominal', count: 'Quantity' },
      chart_spec: {
        chartType: 'Pie Chart',
        encodings: {
          x: { field: 'status' },
          y: { field: 'count' },
        },
        chartProperties: { innerRadius: 35 },
        canvasSize: { width: 300, height: 200 },
      },
    });
  }, [courses]);

  return (
    <div className="rounded-xl border border-border bg-card p-3 lg:p-4">
      <div className="flex items-center gap-2 mb-3">
        <PieChart size={14} className="text-amber-500" />
        <h4 className="text-xs font-medium">课程状态分布</h4>
      </div>
      <EChart option={option as any} style={{ height: 200 }} opts={{ renderer: 'svg' }} />
    </div>
  );
}

// ── Main Export ────────────────────────────────────────

export function LearningCharts({ courses }: { courses: Course[] }) {
  if (courses.length === 0) return null;

  return (
    <div className="space-y-3 lg:space-y-4">
      <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
        学习动态
      </h3>
      <div className="grid gap-3 lg:gap-4 lg:grid-cols-2">
        <LearningCurve courses={courses} />
        <CourseComparison courses={courses} />
        <LearningHeatmap courses={courses} />
        <StatusPie courses={courses} />
      </div>
    </div>
  );
}
