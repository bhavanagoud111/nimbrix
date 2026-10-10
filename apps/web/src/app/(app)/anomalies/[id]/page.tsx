"use client";

import { AnomalyChart } from "@/components/charts/charts";
import { SeverityBadge, StatusBadge } from "@/components/ui/badges";
import { Button, Card, PageHeader, Stat } from "@/components/ui/primitives";
import { DataTable } from "@/components/ui/table";
import { ErrorState, QueryView } from "@/components/ui/states";
import { api, unwrap } from "@/lib/api/client";
import { useCan } from "@/lib/api/hooks";
import { dateTime, money, shortDate } from "@/lib/format";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";

const SCOPE_FILTER: Record<string, string> = {
  service: "service",
  account: "account_id",
  team: "team",
  environment: "environment",
  region: "region",
};
const TRANSITIONS: Record<string, string[]> = {
  open: ["acknowledged", "resolved", "dismissed"],
  acknowledged: ["resolved", "dismissed", "open"],
  resolved: ["open"],
  dismissed: ["open"],
};

function addDays(date: string, days: number): string {
  const d = new Date(`${date}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

export default function AnomalyDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const queryClient = useQueryClient();
  const canUpdate = useCan("anomalies:update");
  const canInvestigate = useCan("investigations:run");
  const anomaly = useQuery({
    queryKey: ["anomaly", id],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/anomalies/{anomaly_id}", { params: { path: { anomaly_id: id } } }),
      ),
  });
  const a = anomaly.data;
  const series = useQuery({
    queryKey: ["anomaly", id, "series"],
    enabled: Boolean(a),
    queryFn: async () => {
      const filterKey = SCOPE_FILTER[a!.scope_type];
      const query: Record<string, string> = {
        start: addDays(a!.window_start, -35),
        end: a!.window_end,
        currency: a!.currency,
      };
      if (filterKey) query[filterKey] = a!.scope_value;
      return unwrap(await api.GET("/api/v1/costs/timeseries", { params: { query } }));
    },
  });
  const investigations = useQuery({
    queryKey: ["investigations", "anomaly", id],
    queryFn: async () =>
      unwrap(await api.GET("/api/v1/investigations", { params: { query: { anomaly_id: id } } })),
  });
  const setStatus = useMutation({
    mutationFn: async (status: "open" | "acknowledged" | "resolved" | "dismissed") =>
      unwrap(
        await api.POST("/api/v1/anomalies/{anomaly_id}/status", {
          params: { path: { anomaly_id: id } },
          body: { status },
        }),
      ),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["anomaly", id] }),
  });
  const investigate = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/v1/anomalies/{anomaly_id}/investigate", {
          params: { path: { anomaly_id: id } },
          body: {},
        }),
      ),
    onSuccess: (inv) => router.push(`/investigations/${inv.id}`),
  });

  return (
    <QueryView query={anomaly}>
      {(a) => {
        const expectedByDate = new Map(
          ((a.evidence.daily as { date: string; expected: string }[] | undefined) ?? []).map(
            (d) => [d.date, Number(d.expected)],
          ),
        );
        const daily = (series.data?.series[0]?.points ?? []).map((p) => ({
          date: p.date,
          observed: Number(p.amount),
          expected: expectedByDate.get(p.date) ?? null,
        }));
        const contributors =
          (a.evidence.contributors as Record<string, string | boolean>[] | undefined) ?? [];
        return (
          <>
            <PageHeader
              title={`${a.scope_type}: ${a.scope_value}`}
              description={`Detected by ${a.detector_version} · first detected ${dateTime(a.first_detected_at)} · baseline: ${String(a.evidence.baseline ?? "")}`}
              actions={
                <>
                  <SeverityBadge severity={a.severity} /> <StatusBadge status={a.status} />
                  {canInvestigate && (
                    <Button
                      variant="primary"
                      onClick={() => investigate.mutate()}
                      loading={investigate.isPending}
                    >
                      Investigate
                    </Button>
                  )}
                </>
              }
            />
            {investigate.isError && <ErrorState error={investigate.error} />}
            <dl className="grid grid-cols-1 gap-3 sm:grid-cols-4">
              <Stat
                label="Window (UTC)"
                value={`${shortDate(a.window_start)} – ${shortDate(a.window_end)}`}
              />
              <Stat label="Observed" value={money(a.observed, a.currency)} tone="fail" />
              <Stat label="Expected" value={money(a.expected, a.currency)} />
              <Stat
                label="Excess"
                value={money(Number(a.observed) - Number(a.expected), a.currency)}
                sub={`max score ${a.score.toFixed(1)}`}
                tone="warn"
              />
            </dl>
            <Card title="Observed vs expected" className="mt-4" id="anomaly-chart">
              {series.isError ? (
                <ErrorState error={series.error} />
              ) : (
                <AnomalyChart
                  daily={daily}
                  windowStart={a.window_start}
                  windowEnd={a.window_end}
                  currency={a.currency}
                />
              )}
            </Card>
            <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
              <Card title="Top contributing resources" className="xl:col-span-2" id="contributors">
                <DataTable
                  caption="Contributing resources"
                  rows={contributors}
                  rowKey={(c) => String(c.resource_id)}
                  columns={[
                    {
                      key: "rid",
                      header: "Resource",
                      render: (c) => <code className="text-xs">{String(c.resource_id)}</code>,
                    },
                    {
                      key: "new",
                      header: "New in window",
                      render: (c) => (c.new_in_window ? "yes" : "no"),
                    },
                    {
                      key: "first",
                      header: "First seen",
                      render: (c) => shortDate(String(c.first_seen)),
                    },
                    {
                      key: "before",
                      header: "Avg/day before",
                      align: "right",
                      render: (c) => money(String(c.avg_daily_baseline), a.currency),
                    },
                    {
                      key: "during",
                      header: "Avg/day during",
                      align: "right",
                      render: (c) => money(String(c.avg_daily_in_window), a.currency),
                    },
                    {
                      key: "delta",
                      header: "Δ/day",
                      align: "right",
                      render: (c) => money(String(c.delta_per_day), a.currency),
                    },
                  ]}
                />
              </Card>
              <Card title="Lifecycle" id="lifecycle">
                {canUpdate ? (
                  <div className="flex flex-wrap gap-2">
                    {TRANSITIONS[a.status].map((s) => (
                      <Button
                        key={s}
                        onClick={() => setStatus.mutate(s as "open")}
                        loading={setStatus.isPending && setStatus.variables === s}
                      >
                        Mark {s}
                      </Button>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-muted">
                    Your role can view but not change anomaly status.
                  </p>
                )}
                {setStatus.isError && <ErrorState error={setStatus.error} />}
                <h3 className="mb-2 mt-4 text-xs font-semibold uppercase text-muted">
                  Investigations
                </h3>
                <QueryView
                  query={investigations}
                  isEmpty={(d) => d.items.length === 0}
                  empty={<p className="text-sm text-muted">None yet.</p>}
                >
                  {(d) => (
                    <ul className="space-y-1 text-sm">
                      {d.items.map((i) => (
                        <li key={i.id} className="flex justify-between">
                          <Link className="text-accent" href={`/investigations/${i.id}`}>
                            {dateTime(i.created_at)}
                          </Link>
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
      }}
    </QueryView>
  );
}
