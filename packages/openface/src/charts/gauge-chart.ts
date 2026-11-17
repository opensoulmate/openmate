import type { EChartsOption } from 'echarts';
import { colors } from '../design-tokens';

export interface GaugeChartOpts {
  min?: number;
  max?: number;
  unit?: string;
  color?: string;
  fontSize?: number;
}

export function buildGaugeOption(
  value: number,
  name: string,
  opts?: GaugeChartOpts,
): EChartsOption {
  const min = opts?.min ?? 0;
  const max = opts?.max ?? 100;
  const gaugeColor = opts?.color ?? (value > 80 ? colors.error : value > 60 ? colors.warning : colors.success);

  return {
    series: [{
      type: 'gauge',
      min,
      max,
      startAngle: 220,
      endAngle: -40,
      progress: {
        show: true,
        width: 12,
        itemStyle: { color: gaugeColor },
      },
      axisLine: {
        lineStyle: {
          width: 12,
          color: [[1, colors.border]],
        },
      },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: { show: false },
      pointer: { show: false },
      title: {
        show: true,
        offsetCenter: [0, '60%'],
        fontSize: 14,
        color: colors.mutedForeground,
      },
      detail: {
        valueAnimation: true,
        fontSize: opts?.fontSize ?? 28,
        fontWeight: 'bold',
        color: colors.foreground,
        offsetCenter: [0, '20%'],
        formatter: `{value}${opts?.unit ? opts.unit : ''}`,
      },
      data: [{ value, name }],
    }],
  } as EChartsOption;
}

/**
 * Dual gauge for system health (CPU + Memory).
 * Returns an ECharts option with two gauge series side by side.
 */
export function buildDualGaugeOption(
  cpu: number,
  mem: number,
): EChartsOption {
  const gaugeColorFor = (v: number) =>
    v > 80 ? colors.error : v > 60 ? colors.warning : colors.success;

  return {
    series: [
      {
        type: 'gauge',
        center: ['25%', '55%'],
        min: 0,
        max: 100,
        startAngle: 220,
        endAngle: -40,
        progress: {
          show: true,
          width: 10,
          itemStyle: { color: gaugeColorFor(cpu) },
        },
        axisLine: {
          lineStyle: { width: 10, color: [[1, colors.border]] },
        },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: { show: false },
        pointer: { show: false },
        title: {
          show: true,
          offsetCenter: [0, '60%'],
          fontSize: 13,
          color: colors.mutedForeground,
        },
        detail: {
          valueAnimation: true,
          fontSize: 22,
          fontWeight: 'bold',
          color: colors.foreground,
          offsetCenter: [0, '20%'],
          formatter: '{value}%',
        },
        data: [{ value: cpu, name: 'CPU' }],
      },
      {
        type: 'gauge',
        center: ['75%', '55%'],
        min: 0,
        max: 100,
        startAngle: 220,
        endAngle: -40,
        progress: {
          show: true,
          width: 10,
          itemStyle: { color: gaugeColorFor(mem) },
        },
        axisLine: {
          lineStyle: { width: 10, color: [[1, colors.border]] },
        },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: { show: false },
        pointer: { show: false },
        title: {
          show: true,
          offsetCenter: [0, '60%'],
          fontSize: 13,
          color: colors.mutedForeground,
        },
        detail: {
          valueAnimation: true,
          fontSize: 22,
          fontWeight: 'bold',
          color: colors.foreground,
          offsetCenter: [0, '20%'],
          formatter: '{value}%',
        },
        data: [{ value: mem, name: '内存' }],
      },
    ],
  } as EChartsOption;
}
