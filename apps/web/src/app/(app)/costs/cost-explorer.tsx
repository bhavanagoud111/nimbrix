"use client";

import { CostTrendChart, ShareBars } from "@/components/charts/charts";
import { ModeBadge } from "@/components/ui/badges";
import { Button, Card, Field, PageHeader, Stat, inputClass } from "@/components/ui/primitives";
import { DataTable } from "@/components/ui/table";
import { EmptyState, QueryView } from "@/components/ui/states";
import { api, unwrap } from "@/lib/api/client";
import { money, pct, shortDate } from "@/lib/format";
import { useQuery } from "@tanstack/react-query";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

const FILTER_KEYS = [
  "start",
  "end",
  "account_id",
  "service",
  "region",
  "environment",
  "team",
  "tag_key",
  "tag_value",
] as const;
const DIMENSIONS = ["service", "account", "region", "environment", "team", "resource"] as const;
type Dimension = (typeof DIMENSIONS)[number];
const DRILL_FILTER: Record<Dimension, string> = {
  service: "service",
  account: "account_id",
  region: "region",
  environment: "environment",
  team: "team",
  resource: "",
};

export function CostExplorer() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const filters = Object.fromEntries(
    FILTER_KEYS.map((k) => [k, params.get(k) ?? undefined]).filter(([, v]) => v),
  ) as Record<string, string>;
  const groupBy = (params.get("group_by") as Dimension | null) ?? "service";
  const granularity = (params.get("granularity") as "day" | "month" | null) ?? "day";

  function setParam(updates: Record<string, string | null>) {
    const next = new URLSearchParams(params.toString());
    for (const [k, v] of Object.entries(updates)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    router.replace(`${pathname}?${next.toString()}`, { scroll: false });
  }

  const dims = useQuery({
    queryKey: ["costs", "dimensions"],
    queryFn: async () => unwrap(await api.GET("/api/v1/costs/dimensions")),
  });
  const summary = useQuery({
    queryKey: ["costs", "summary", filters],
    queryFn: async () =>
      unwrap(await api.GET("/api/v1/costs/summary", { params: { query: filters } })),
  });
  const series = useQuery({
    queryKey: ["costs", "timeseries", filters, groupBy, granularity],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/costs/timeseries", {
          params: { query: { ...filters, group_by: groupBy, granularity, top: 8 } },
        }),
      ),
  });
  const breakdown = useQuery({
    queryKey: ["costs", "breakdown", filters, groupBy],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/costs/breakdown", {
          params: { query: { ...filters, dimension: groupBy, limit: 12 } },
        }),
      ),
  });
  const units = useQuery({
    queryKey: ["costs", "units", filters],
    queryFn: async () =>
      unwrap(await api.GET("/api/v1/costs/unit-costs", { params: { query: filters } })),
  });

  const select = (key: string, label: string, options: string[] | undefined) => (
    <Field label={label}>
      <select
        className={inputClass}
        value={filters[key] ?? ""}
        onChange={(e) => setParam({ [key]: e.target.value || null })}
      >
        <option value="">All</option>
        {(options ?? []).map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </Field>
  );

  return (
    <>
      <PageHeader
        title="Cost Explorer"
        description="Filter and drill into spend. Dates are UTC; the current day is excluded as partial. Click a bar to drill down."
        actions={summary.data ? <ModeBadge mode={summary.data.data_mode} /> : null}
      />
      <Card title="Filters" id="filters">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
          <Field label="Start (UTC)">
            <input
              type="date"
              className={inputClass}
              value={filters.start ?? ""}
              onChange={(e) => setParam({ start: e.target.value || null })}
            />
          </Field>
          <Field label="End (UTC)">
            <input
              type="date"
              className={inputClass}
              value={filters.end ?? ""}
              onChange={(e) => setParam({ end: e.target.value || null })}
            />
          </Field>
          {select("account_id", "Account", dims.data?.account)}
          {select("service", "Service", dims.data?.service)}
          {select("region", "Region", dims.data?.region)}
          {select("environment", "Environment", dims.data?.environment)}
          {select("team", "Team", dims.data?.team)}
          {select("tag_key", "Tag key", dims.data?.tag_keys)}
          <Field label="Tag value">
            <input
              className={inputClass}
              value={filters.tag_value ?? ""}
              placeholder="any"
              onChange={(e) => setParam({ tag_value: e.target.value || null })}
            />
          </Field>
          <Field label="Group by">
            <select
              className={inputClass}
              value={groupBy}
              onChange={(e) => setParam({ group_by: e.target.value })}
            >
              {DIMENSIONS.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Granularity">
            <select
              className={inputClass}
              value={granularity}
              onChange={(e) => setParam({ granularity: e.target.value })}
            >
              <option value="day">Daily</option>
              <option value="month">Monthly</option>
            </select>
          </Field>
          <div className="flex items-end">
            <Button onClick={() => router.replace(pathname)}>Clear filters</Button>
          </div>
        </div>
      </Card>

      <div className="mt-4">
        <QueryView
          query={summary}
          isEmpty={(d) => !d.has_data}
          empty={<EmptyState title="No data for these filters" />}
        >
          {(d) => (
            <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <Stat
                label={`Total ${shortDate(d.window?.start)} – ${shortDate(d.window?.end)}`}
                value={money(d.total, d.currency)}
                sub={`${d.window?.days} days · avg ${money(d.daily_average, d.currency)}/day`}
              />
              <Stat
                label="Prior period"
                value={money(d.previous_total, d.currency)}
                sub={`change ${pct(d.change_pct)}`}
                tone={Number(d.change_pct) > 10 ? "warn" : "neutral"}
              />
              <Stat
                label="Allocated to teams"
                value={`${d.allocation_pct ?? "—"}%`}
                sub={`${money(d.unallocated, d.currency)} unallocated`}
              />
              <Stat
                label="Other currencies"
                value={
                  d.other_currencies.length
                    ? d.other_currencies.map((c) => money(c.total, c.currency)).join(", ")
                    : "none"
                }
                sub="never converted or added"
              />
            </dl>
          )}
        </QueryView>
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card
          title={`Spend by ${groupBy} (${granularity === "day" ? "daily" : "monthly"})`}
          className="xl:col-span-2"
          id="series"
        >
          <QueryView query={series} isEmpty={(d) => d.series.length === 0}>
            {(d) => (
              <>
                <CostTrendChart buckets={d.buckets} series={d.series} currency={d.currency} />
                {d.partial_last_bucket && (
                  <p className="mt-2 text-xs text-muted">
                    The last month is partial (data through {shortDate(d.window?.end)}).
                  </p>
                )}
              </>
            )}
          </QueryView>
        </Card>
        <Card title={`Breakdown by ${groupBy}`} id="breakdown">
          <QueryView query={breakdown} isEmpty={(d) => d.items.length === 0}>
            {(d) => (
              <ShareBars
                items={d.items}
                currency={d.currency}
                onSelect={
                  DRILL_FILTER[groupBy]
                    ? (key) => {
                        if (key === "(other)") return;
                        const nextGroup = groupBy === "service" ? "resource" : "service";
                        setParam({ [DRILL_FILTER[groupBy]]: key, group_by: nextGroup });
                      }
                    : undefined
                }
              />
            )}
          </QueryView>
        </Card>
      </div>

      <Card title="Unit costs (provider usage units)" className="mt-4" id="units">
        <QueryView query={units} isEmpty={(d) => d.items.length === 0}>
          {(d) => (
            <>
              <DataTable
                caption="Cost per usage unit"
                rows={d.items}
                rowKey={(r) => `${r.service}:${r.usage_unit}`}
                columns={[
                  { key: "service", header: "Service", render: (r) => r.service },
                  { key: "unit", header: "Unit", render: (r) => r.usage_unit },
                  {
                    key: "qty",
                    header: "Quantity",
                    align: "right",
                    render: (r) =>
                      Number(r.usage_quantity).toLocaleString("en-US", {
                        maximumFractionDigits: 2,
                      }),
                  },
                  {
                    key: "amount",
                    header: "Cost",
                    align: "right",
                    render: (r) => money(r.amount, d.currency),
                  },
                  {
                    key: "unit_cost",
                    header: "Cost / unit",
                    align: "right",
                    render: (r) => money(r.unit_cost, d.currency, 6),
                  },
                ]}
              />
              <p className="mt-2 text-xs text-muted">{d.note}</p>
            </>
          )}
        </QueryView>
      </Card>
    </>
  );
}
