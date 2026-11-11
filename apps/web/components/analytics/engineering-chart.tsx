'use client';

import { useEffect, useRef } from 'react';
import * as echarts from 'echarts';
import type { EChartsOption } from 'echarts';
import { useUi } from '@/stores/ui';

function resolveTokens(value: unknown, css: (name: string) => string): unknown {
  if (typeof value === 'string') return value.replace(/var\((--[a-z-]+)\)/g, (_, token: string) => css(token));
  if (Array.isArray(value)) return value.map((item) => resolveTokens(item, css));
  if (value && typeof value === 'object' && Object.getPrototypeOf(value) === Object.prototype) return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, resolveTokens(item, css)]));
  return value;
}

export function EngineeringChart({ option, label, height = 310 }: { option: EChartsOption; label: string; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const theme = useUi((state) => state.theme);
  useEffect(() => {
    if (!ref.current) return;
    const node = ref.current;
    const chart = echarts.init(node, undefined, { renderer: 'svg' });
    const styles = getComputedStyle(document.documentElement);
    const css = (name: string) => styles.getPropertyValue(name).trim();
    chart.setOption({
      backgroundColor: 'transparent',
      textStyle: { fontFamily: 'IBM Plex Sans', color: css('--text-secondary') },
      color: [css('--action'), css('--graphite'), css('--orange'), css('--success')],
      tooltip: { backgroundColor: css('--chart-tooltip'), borderColor: css('--border'), textStyle: { color: css('--text'), fontFamily: 'IBM Plex Mono', fontSize: 11 } },
      xAxis: { axisLabel: { color: css('--text-secondary') }, nameTextStyle: { color: css('--text-secondary') }, axisLine: { lineStyle: { color: css('--border') } }, splitLine: { lineStyle: { color: css('--chart-grid') } } },
      yAxis: { axisLabel: { color: css('--text-secondary') }, nameTextStyle: { color: css('--text-secondary') }, axisLine: { lineStyle: { color: css('--border') } }, splitLine: { lineStyle: { color: css('--chart-grid') } } },
      legend: { textStyle: { color: css('--text-secondary') } },
    });
    chart.setOption(resolveTokens(option, css) as EChartsOption);
    const resize = new ResizeObserver(() => chart.resize());
    resize.observe(node);
    return () => { resize.disconnect(); chart.dispose(); };
  }, [option, theme]);
  return <div ref={ref} role="img" aria-label={label} style={{ height, width: '100%' }} />;
}
