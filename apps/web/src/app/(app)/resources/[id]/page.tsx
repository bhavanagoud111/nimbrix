"use client";

import { CostTrendChart } from "@/components/charts/charts";
import { ModeBadge, StatusBadge } from "@/components/ui/badges";
import { Button, Card, Field, PageHeader, Stat, inputClass } from "@/components/ui/primitives";
import { ErrorState, QueryView } from "@/components/ui/states";
import { api, unwrap } from "@/lib/api/client";
import { useCan } from "@/lib/api/hooks";
import { dateTime, money } from "@/lib/format";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";

export default function ResourceDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const canRequest = useCan("approvals:request");
  const [reason, setReason] = useState("");
  const resource = useQuery({
    queryKey: ["resource", id],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/resources/{resource_uuid}", {
          params: { path: { resource_uuid: id } },
        }),
      ),
  });
  const payload = () => {
    const r = resource.data!;
    return {
      resource_id: r.resource_id,
      account_id: r.account_id,
      region: r.region,
      reason: reason || "manual request",
    };
  };
  const preview = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/v1/approvals/preview", {
          body: { action_type: "simulate_stop_nonprod_instance", payload: payload() },
        }),
      ),
  });
  const request = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/v1/approvals", {
          body: { action_type: "simulate_stop_nonprod_instance", payload: payload() },
        }),
      ),
    onSuccess: (a) => router.push(`/approvals/${a.id}`),
  });
  return (
    <QueryView query={resource}>
      {(r) => (
        <>
          <PageHeader
            title={r.name ?? r.resource_id}
            description={<code>{r.resource_id}</code>}
            actions={
              <>
                <StatusBadge status={r.state} />
                <ModeBadge mode={r.mode} />
              </>
            }
          />
          <dl className="grid grid-cols-1 gap-3 sm:grid-cols-4">
            <Stat
              label="Type"
              value={r.resource_type}
              sub={String(r.attributes.instance_type ?? r.attributes.volume_type ?? "")}
            />
            <Stat label="Account / region" value={r.account_id} sub={r.region} />
            <Stat label="Env / team" value={`${r.environment ?? "—"} / ${r.team ?? "—"}`} />
            <Stat
              label="Billed, last 30 days"
              value={money(r.cost_last_30_days, r.cost_currency)}
              sub={`observed ${dateTime(r.observed_at)}`}
            />
          </dl>
          <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-3">
            <Card title="Daily billed cost" className="xl:col-span-2" id="cost">
              <CostTrendChart
                buckets={r.daily_cost.map((d) => d.date)}
                series={[
                  {
                    key: r.resource_id,
                    points: r.daily_cost.map((d) => ({ date: d.date, amount: d.amount })),
                  },
                ]}
                currency={r.cost_currency}
                height={220}
              />
            </Card>
            <Card title="Utilization evidence" id="utilization">
              {r.utilization ? (
                <ul className="space-y-1 text-sm">
                  {Object.entries(r.utilization).map(([k, v]) => (
                    <li key={k} className="flex justify-between gap-2">
                      <span className="text-muted">{k}</span>
                      <span>{String(v)}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-muted">
                  No utilization metrics were collected for this resource.
                </p>
              )}
            </Card>
          </div>
          <Card title="Tags and attributes" className="mt-4" id="tags">
            <pre className="overflow-x-auto rounded bg-background p-3 text-xs">
              {JSON.stringify({ tags: r.tags, attributes: r.attributes }, null, 2)}
            </pre>
          </Card>
          {canRequest && r.resource_type === "ec2_instance" && (
            <Card title="Request a simulated stop" className="mt-4" id="request">
              <div className="flex flex-wrap items-end gap-3">
                <Field label="Reason">
                  <input
                    className={`${inputClass} w-80`}
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                  />
                </Field>
                <Button onClick={() => preview.mutate()} loading={preview.isPending}>
                  Check policy
                </Button>
                <Button
                  variant="primary"
                  onClick={() => request.mutate()}
                  loading={request.isPending}
                >
                  Request approval
                </Button>
              </div>
              {preview.data && (
                <div className="mt-3 text-sm">
                  <p className={preview.data.allowed ? "text-ok" : "text-fail"}>
                    {preview.data.allowed
                      ? "Policy allows this request."
                      : "Policy denies this request:"}
                  </p>
                  {!preview.data.allowed && (
                    <ul className="list-disc pl-5 text-muted">
                      {preview.data.denial_reasons.map((d) => (
                        <li key={d}>{d}</li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
              {request.isError && <ErrorState error={request.error} />}
            </Card>
          )}
        </>
      )}
    </QueryView>
  );
}
