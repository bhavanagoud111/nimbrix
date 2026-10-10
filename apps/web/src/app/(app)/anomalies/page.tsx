"use client";

import { SeverityBadge, StatusBadge } from "@/components/ui/badges";
import { Button, Card, Field, PageHeader, inputClass } from "@/components/ui/primitives";
import { DataTable } from "@/components/ui/table";
import { EmptyState, QueryView } from "@/components/ui/states";
import { api, unwrap } from "@/lib/api/client";
import { useCan, useJob } from "@/lib/api/hooks";
import { money, shortDate } from "@/lib/format";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

export default function AnomaliesPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const canRun = useCan("analysis:run");
  const [status, setStatus] = useState("");
  const anomalies = useQuery({
    queryKey: ["anomalies", status],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/anomalies", {
          params: { query: { status: status || undefined, limit: 100 } },
        }),
      ),
  });
  const run = useMutation({
    mutationFn: async () => unwrap(await api.POST("/api/v1/analysis/run")),
  });
  const job = useJob(run.data?.id);
  useEffect(() => {
    if (job.data?.state === "succeeded")
      void queryClient.invalidateQueries({ queryKey: ["anomalies"] });
  }, [job.data?.state, queryClient]);

  return (
    <>
      <PageHeader
        title="Anomalies"
        description="Spend increases flagged by the seasonal-residual detector (same-weekday baseline, robust threshold). Open one to see evidence and investigate."
        actions={
          canRun && (
            <Button
              onClick={() => run.mutate()}
              loading={
                run.isPending ||
                (Boolean(job.data) &&
                  job.data?.state !== "succeeded" &&
                  job.data?.state !== "failed")
              }
            >
              Re-run analysis
            </Button>
          )
        }
      />
      {job.data && <p className="mb-3 text-xs text-muted">Analysis job: {job.data.state}</p>}
      <Card
        title="Detected anomalies"
        id="anomaly-list"
        actions={
          <Field label="Status">
            <select
              className={inputClass}
              value={status}
              onChange={(e) => setStatus(e.target.value)}
            >
              <option value="">All</option>
              {["open", "acknowledged", "resolved", "dismissed"].map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </Field>
        }
      >
        <QueryView
          query={anomalies}
          isEmpty={(d) => d.items.length === 0}
          empty={<EmptyState title="No anomalies match" />}
        >
          {(d) => (
            <DataTable
              caption="Anomalies"
              rows={d.items}
              rowKey={(a) => a.id}
              onRowClick={(a) => router.push(`/anomalies/${a.id}`)}
              columns={[
                {
                  key: "scope",
                  header: "Scope",
                  render: (a) => (
                    <>
                      <span className="text-muted">{a.scope_type}</span>{" "}
                      <span className="font-medium">{a.scope_value}</span>
                    </>
                  ),
                },
                {
                  key: "window",
                  header: "Window (UTC)",
                  render: (a) => `${shortDate(a.window_start)} – ${shortDate(a.window_end)}`,
                },
                {
                  key: "observed",
                  header: "Observed",
                  align: "right",
                  render: (a) => money(a.observed, a.currency),
                },
                {
                  key: "expected",
                  header: "Expected",
                  align: "right",
                  render: (a) => money(a.expected, a.currency),
                },
                {
                  key: "score",
                  header: "Score",
                  align: "right",
                  render: (a) => a.score.toFixed(1),
                },
                {
                  key: "severity",
                  header: "Severity",
                  render: (a) => <SeverityBadge severity={a.severity} />,
                },
                {
                  key: "status",
                  header: "Status",
                  render: (a) => <StatusBadge status={a.status} />,
                },
              ]}
            />
          )}
        </QueryView>
      </Card>
    </>
  );
}
