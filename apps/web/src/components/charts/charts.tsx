"use client";

import { compactMoney, money, shortDate } from "@/lib/format";
import type { ReactNode } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export const SERIES_COLORS = [
  "#4cc2ff",
  "#a371f7",
  "#3fb950",
  "#d29922",
  "#f778ba",
  "#56d4dd",
  "#ff7b72",
  "#8b9bb0",
  "#e3b341",
];
const AXIS = { stroke: "currentColor", fontSize: 11, tickLine: false };

function Figure({
  caption,
  height,
  children,
}: {
  caption: string;
  height: number;
  children: ReactNode;
}) {
  return (
    <figure className="text-muted">
      <div style={{ width: "100%", height }}>{children}</div>
      <figcaption className="sr-only">{caption}</figcaption>
    </figure>
  );
}

interface SeriesInput {
  key: string;
  points: { date: string; amount: string }[];
}

export function CostTrendChart({
  buckets,
  series,
  currency,
  height = 280,
  stacked = true,
}: {
  buckets: string[];
  series: SeriesInput[];
  currency: string;
  height?: number;
  stacked?: boolean;
}) {
  const data = buckets.map((bucket, i) => {
    const row: Record<string, number | string> = { date: bucket };
    for (const s of series) row[s.key] = Number(s.points[i]?.amount ?? 0);
    return row;
  });
  return (
    <Figure caption={`Cost over time by ${series.map((s) => s.key).join(", ")}`} height={height}>
      <ResponsiveContainer>
        <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeOpacity={0.15} vertical={false} />
          <XAxis dataKey="date" tickFormatter={shortDate} {...AXIS} minTickGap={24} />
          <YAxis tickFormatter={(v: number) => compactMoney(v, currency)} {...AXIS} width={64} />
          <Tooltip
            contentStyle={{
              background: "var(--surface)",
              border: "1px solid var(--border)",
              color: "var(--foreground)",
            }}
            labelFormatter={(l) => shortDate(String(l))}
            formatter={(v) => money(Number(v), currency)}
          />
          {series.length > 1 && <Legend wrapperStyle={{ fontSize: 12 }} />}
          {series.map((s, i) => (
            <Area
              key={s.key}
              type="monotone"
              dataKey={s.key}
              stackId={stacked ? "1" : undefined}
              stroke={SERIES_COLORS[i % SERIES_COLORS.length]}
              fill={SERIES_COLORS[i % SERIES_COLORS.length]}
              fillOpacity={0.25}
              isAnimationActive={false}
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </Figure>
  );
}

export function ForecastChart({
  actuals,
  forecast,
  currency,
  height = 300,
}: {
  actuals: { date: string; amount: string }[];
  forecast: { date: string; yhat: string; lower: string; upper: string }[];
  currency: string;
  height?: number;
}) {
  const data: Record<string, unknown>[] = [
    ...actuals.map((a) => ({ date: a.date, actual: Number(a.amount) })),
    ...forecast.map((f) => ({
      date: f.date,
      forecast: Number(f.yhat),
      band: [Number(f.lower), Number(f.upper)],
    })),
  ];
  return (
    <Figure
      caption="Actual daily spend followed by forecast with prediction interval"
      height={height}
    >
      <ResponsiveContainer>
        <ComposedChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeOpacity={0.15} vertical={false} />
          <XAxis dataKey="date" tickFormatter={shortDate} {...AXIS} minTickGap={24} />
          <YAxis tickFormatter={(v: number) => compactMoney(v, currency)} {...AXIS} width={64} />
          <Tooltip
            contentStyle={{
              background: "var(--surface)",
              border: "1px solid var(--border)",
              color: "var(--foreground)",
            }}
            labelFormatter={(l) => shortDate(String(l))}
            formatter={(v) =>
              Array.isArray(v)
                ? `${money(v[0], currency)} – ${money(v[1], currency)}`
                : money(Number(v), currency)
            }
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Area
            dataKey="band"
            name="Prediction interval"
            stroke="none"
            fill="#a371f7"
            fillOpacity={0.2}
            isAnimationActive={false}
          />
          <Line
            dataKey="actual"
            name="Actual"
            stroke="#4cc2ff"
            dot={false}
            strokeWidth={2}
            isAnimationActive={false}
          />
          <Line
            dataKey="forecast"
            name="Forecast"
            stroke="#a371f7"
            strokeDasharray="5 4"
            dot={false}
            strokeWidth={2}
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </Figure>
  );
}

export function AnomalyChart({
  daily,
  windowStart,
  windowEnd,
  currency,
  height = 260,
}: {
  daily: { date: string; observed: number; expected: number | null }[];
  windowStart: string;
  windowEnd: string;
  currency: string;
  height?: number;
}) {
  return (
    <Figure
      caption={`Observed versus expected daily cost; anomaly window ${windowStart} to ${windowEnd}`}
      height={height}
    >
      <ResponsiveContainer>
        <ComposedChart data={daily} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeOpacity={0.15} vertical={false} />
          <XAxis dataKey="date" tickFormatter={shortDate} {...AXIS} minTickGap={24} />
          <YAxis tickFormatter={(v: number) => compactMoney(v, currency)} {...AXIS} width={64} />
          <Tooltip
            contentStyle={{
              background: "var(--surface)",
              border: "1px solid var(--border)",
              color: "var(--foreground)",
            }}
            labelFormatter={(l) => shortDate(String(l))}
            formatter={(v) => money(Number(v), currency)}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <ReferenceArea x1={windowStart} x2={windowEnd} fill="#ff7b72" fillOpacity={0.12} />
          <Line
            dataKey="observed"
            name="Observed"
            stroke="#4cc2ff"
            dot={false}
            strokeWidth={2}
            isAnimationActive={false}
          />
          <Line
            dataKey="expected"
            name="Expected (seasonal baseline)"
            stroke="#8b9bb0"
            strokeDasharray="4 4"
            dot={false}
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </Figure>
  );
}

/** Horizontal share bars (pure CSS: readable, accessible, no chart runtime). */
export function ShareBars({
  items,
  currency,
  onSelect,
}: {
  items: { key: string; amount: string; share_pct: string | null; change_pct?: string | null }[];
  currency: string;
  onSelect?: (key: string) => void;
}) {
  const max = Math.max(...items.map((i) => Number(i.amount)), 1);
  return (
    <ul className="space-y-2">
      {items.map((item, i) => (
        <li key={item.key}>
          <button
            type="button"
            disabled={!onSelect}
            onClick={() => onSelect?.(item.key)}
            className="w-full text-left disabled:cursor-default"
            aria-label={`${item.key}: ${money(item.amount, currency)}${item.share_pct ? `, ${item.share_pct}% of total` : ""}`}
          >
            <div className="flex justify-between text-xs">
              <span className="truncate pr-2">{item.key}</span>
              <span className="tabular-nums text-muted">
                {money(item.amount, currency)} {item.share_pct ? `· ${item.share_pct}%` : ""}
              </span>
            </div>
            <div className="mt-1 h-2 rounded bg-background">
              <div
                className="h-2 rounded"
                style={{
                  width: `${(Number(item.amount) / max) * 100}%`,
                  background: SERIES_COLORS[i % SERIES_COLORS.length],
                }}
              />
            </div>
          </button>
        </li>
      ))}
    </ul>
  );
}
