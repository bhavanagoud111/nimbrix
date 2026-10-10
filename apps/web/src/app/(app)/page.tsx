"use client";

import { CostTrendChart } from "@/components/charts/charts";
import { ModeBadge, SeverityBadge, StatusBadge } from "@/components/ui/badges";
import { Card, PageHeader, Stat } from "@/components/ui/primitives";
import { EmptyState, QueryView } from "@/components/ui/states";
import { api, unwrap } from "@/lib/api/client";
import { dateTime, money, pct, ratio, shortDate } from "@/lib/format";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

export default function OverviewPage() {
  const summary = useQuery({
    queryKey: ["costs", "summary", "overview"],
    queryFn: async () => unwrap(await api.GET("/api/v1/costs/summary")),
  });
  const trend = useQuery({
    queryKey: ["costs", "timeseries", "overview"],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/costs/timeseries", {
          params: { query: { group_by: "service", top: 6 } },
        }),
      ),
  });
  const recs = useQuery({
    queryKey: ["recommendations", "open"],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/recommendations", {
          params: { query: { status: "open", limit: 200 } },
        }),
      ),
  });
  const anomalies = useQuery({
    queryKey: ["anomalies", "open"],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/anomalies", { params: { query: { status: "open", limit: 50 } } }),
      ),
  });
  const budgets = useQuery({
    queryKey: ["budgets"],
    queryFn: async () => unwrap(await api.GET("/api/v1/budgets")),
  });
  const imports = useQuery({
    queryKey: ["imports", "recent"],
    queryFn: async () =>
      unwrap(await api.GET("/api/v1/imports", { params: { query: { limit: 5 } } })),
  });

  const s = summary.data;
  return (
    <>
      <PageHeader
        title="Overview"
        description="Where money is going, what changed, and what can be saved. Every number below comes from the API; savings are estimates, never realized."
        actions={s ? <ModeBadge mode={s.data_mode} /> : null}
      />
      <QueryView
        query={summary}
        isEmpty={(d) => !d.has_data}
        empty={
          <EmptyState title="No cost data yet">
            Import the synthetic demo dataset or upload a billing CSV in{" "}
            <Link className="text-accent" href="/settings">
              Integrations &amp; Settings
            </Link>
            .
          </EmptyState>
        }
      >
        {(d) => (
          <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
            <Stat
              label="Spend, last 30 days"
              value={money(d.total, d.currency)}
              sub={
                <>
                  {pct(d.change_pct)} vs prior 30 days · as of {shortDate(d.as_of)}
                </>
              }
              tone={Number(d.change_pct) > 10 ? "warn" : "neutral"}
            />
            <Stat
              label="Month to date"
              value={money(d.month_to_date?.amount, d.currency)}
              sub={<>{pct(d.month_to_date?.change_pct)} vs same days last month</>}
            />
            <Stat
              label="Unallocated spend"
              value={money(d.unallocated, d.currency)}
              sub={`${d.allocation_pct ?? "—"}% allocated to teams`}
              tone="warn"
            />
            <Stat
              label="Open anomalies"
              value={anomalies.data?.items.length ?? "…"}
              sub="spend increases detected"
              tone={anomalies.data?.items.length ? "fail" : "ok"}
            />
            <Stat
              label="Potential savings / month"
              value={money(recs.data?.total_estimated_monthly_savings, "USD")}
              sub="estimated from open recommendations"
              tone="ok"
            />
          </dl>
        )}
      </QueryView>

      <div className="mt-6 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card title="Daily spend by service (90 days)" className="xl:col-span-2" id="trend">
          <QueryView query={trend} isEmpty={(d) => d.series.length === 0}>
            {(d) => <CostTrendChart buckets={d.buckets} series={d.series} currency={d.currency} />}
          </QueryView>
        </Card>
        <Card title="Top cost drivers (change vs prior period)" id="drivers">
          <QueryView query={summary} isEmpty={(d) => !d.top_drivers?.length}>
            {(d) => (
              <ul className="space-y-2 text-sm">
                {d.top_drivers!.map((driver) => (
                  <li key={driver.key} className="flex justify-between gap-2">
                    <Link
                      href={`/costs?service=${encodeURIComponent(driver.key)}`}
                      className="truncate hover:text-accent"
                    >
                      {driver.key}
                    </Link>
                    <span
                      className={`tabular-nums ${Number(driver.delta) > 0 ? "text-fail" : "text-ok"}`}
                    >
                      {Number(driver.delta) > 0 ? "+" : ""}
                      {money(driver.delta, d.currency)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </QueryView>
        </Card>
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card
          title="Open anomalies"
          id="anomalies"
          actions={
            <Link href="/anomalies" className="text-xs text-accent">
              All anomalies
            </Link>
          }
        >
          <QueryView
            query={anomalies}
            isEmpty={(d) => d.items.length === 0}
            empty={<EmptyState title="No open anomalies" />}
          >
            {(d) => (
              <ul className="space-y-2 text-sm">
                {d.items.slice(0, 6).map((a) => (
                  <li key={a.id} className="flex items-center justify-between gap-2">
                    <Link href={`/anomalies/${a.id}`} className="truncate hover:text-accent">
                      {a.scope_type}: {a.scope_value}
                    </Link>
                    <span className="flex items-center gap-2">
                      <span className="text-xs text-muted">{shortDate(a.window_start)}</span>
                      <SeverityBadge severity={a.severity} />
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </QueryView>
        </Card>
        <Card
          title="Budgets this month"
          id="budgets"
          actions={
            <Link href="/budgets" className="text-xs text-accent">
              Budgets
            </Link>
          }
        >
          <QueryView
            query={budgets}
            isEmpty={(d) => d.items.length === 0}
            empty={<EmptyState title="No budgets" />}
          >
            {(d) => (
              <ul className="space-y-3 text-sm">
                {d.items.map((b) => (
                  <li key={b.id}>
                    <div className="flex justify-between">
                      <span>{b.name}</span>
                      <StatusBadge status={b.status} />
                    </div>
                    <div
                      className="mt-1 h-2 rounded bg-background"
                      role="img"
                      aria-label={`${ratio(b.utilization)} of budget used`}
                    >
                      <div
                        className={`h-2 rounded ${Number(b.utilization) >= 1 ? "bg-fail" : Number(b.utilization) >= 0.8 ? "bg-warn" : "bg-ok"}`}
                        style={{ width: `${Math.min(Number(b.utilization), 1) * 100}%` }}
                      />
                    </div>
                    <p className="mt-1 text-xs text-muted">
                      {money(b.actual, b.currency)} of {money(b.amount, b.currency)} · projected{" "}
                      {money(b.projected, b.currency)} ({b.projection_method})
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </QueryView>
        </Card>
        <Card
          title="Latest data jobs"
          id="jobs"
          actions={
            <Link href="/settings" className="text-xs text-accent">
              Imports
            </Link>
          }
        >
          <QueryView
            query={imports}
            isEmpty={(d) => d.items.length === 0}
            empty={<EmptyState title="No imports yet" />}
          >
            {(d) => (
              <ul className="space-y-2 text-sm">
                {d.items.map((i) => (
                  <li key={i.id} className="flex items-center justify-between gap-2">
                    <span className="truncate">
                      {i.source} {i.filename ? `· ${i.filename}` : ""}{" "}
                      <span className="text-xs text-muted">{dateTime(i.created_at)}</span>
                    </span>
                    <StatusBadge status={i.state} />
                  </li>
                ))}
              </ul>
            )}
          </QueryView>
        </Card>
      </div>
    </>
  );
}
