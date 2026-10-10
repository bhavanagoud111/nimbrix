"use client";

import { ConfidenceBadge, ModeBadge, RiskBadge, StatusBadge } from "@/components/ui/badges";
import { Button, Card, Field, PageHeader, Stat, inputClass } from "@/components/ui/primitives";
import { DataTable } from "@/components/ui/table";
import { ErrorState, QueryView } from "@/components/ui/states";
import { api, unwrap } from "@/lib/api/client";
import { useCan } from "@/lib/api/hooks";
import { dateTime, money, titleCase } from "@/lib/format";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";

export default function RecommendationDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const queryClient = useQueryClient();
  const canSimulate = useCan("recommendations:simulate");
  const canRequest = useCan("approvals:request");
  const canUpdate = useCan("recommendations:update");
  const [reason, setReason] = useState("");
  const rec = useQuery({
    queryKey: ["recommendation", id],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/recommendations/{rec_id}", { params: { path: { rec_id: id } } }),
      ),
  });
  const simulate = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/v1/recommendations/{rec_id}/simulate", {
          params: { path: { rec_id: id } },
        }),
      ),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["recommendation", id] }),
  });
  const requestApproval = useMutation({
    mutationFn: async () => {
      const r = rec.data!;
      return unwrap(
        await api.POST("/api/v1/approvals", {
          body: {
            action_type: r.proposed_action!,
            recommendation_id: r.id,
            payload: {
              resource_id: r.resource_id,
              account_id: r.account_id,
              region: r.region,
              reason: reason || r.title,
            },
          },
        }),
      );
    },
    onSuccess: (approval) => router.push(`/approvals/${approval.id}`),
  });
  const dismiss = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/v1/recommendations/{rec_id}/dismiss", {
          params: { path: { rec_id: id } },
          body: { reason },
        }),
      ),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["recommendation", id] }),
  });

  return (
    <QueryView query={rec}>
      {(r) => {
        const sim = simulate.data ?? (r.last_simulation as typeof simulate.data | null);
        const evidence = r.evidence as Record<string, unknown>;
        const utilization = evidence.utilization as Record<string, unknown> | null | undefined;
        return (
          <>
            <PageHeader
              title={r.title}
              description={r.summary}
              actions={
                <>
                  <StatusBadge status={r.status} />
                  <ConfidenceBadge confidence={r.confidence} />
                  <RiskBadge risk={r.risk} />
                </>
              }
            />
            <dl className="grid grid-cols-1 gap-3 sm:grid-cols-4">
              <Stat
                label="Estimated savings / month"
                value={money(r.estimated_monthly_savings, r.currency)}
                sub={r.savings_formula ?? "no estimate (insufficient evidence)"}
                tone="ok"
              />
              <Stat
                label="Resource"
                value={<code className="text-sm">{r.resource_id}</code>}
                sub={`${r.account_id} · ${r.region}`}
              />
              <Stat label="Owner" value={r.owner ?? "unassigned"} sub={titleCase(r.category)} />
              <Stat label="Evaluated" value={dateTime(r.last_evaluated_at)} sub={r.rule_version} />
            </dl>
            <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
              <Card title="Eligibility checks" id="eligibility">
                <DataTable
                  caption="Eligibility checks"
                  rows={
                    r.eligibility as {
                      condition: string;
                      passed: boolean;
                      observed: unknown;
                      required: string;
                    }[]
                  }
                  rowKey={(c) => c.condition}
                  columns={[
                    { key: "c", header: "Condition", render: (c) => c.condition },
                    {
                      key: "o",
                      header: "Observed",
                      render: (c) => <code className="text-xs">{String(c.observed ?? "—")}</code>,
                    },
                    { key: "r", header: "Required", render: (c) => c.required },
                    {
                      key: "p",
                      header: "Result",
                      render: (c) => <StatusBadge status={c.passed ? "succeeded" : "failed"} />,
                    },
                  ]}
                />
              </Card>
              <Card title="Evidence and assumptions" id="evidence">
                {utilization && (
                  <p className="mb-2 text-sm">
                    CPU avg <strong>{String(utilization.statistic_avg_pct)}%</strong>, p95{" "}
                    <strong>{String(utilization.statistic_p95_pct)}%</strong> over{" "}
                    {String(utilization.window_days)} days ({String(utilization.datapoints)}/
                    {String(utilization.expected_datapoints)} hourly datapoints,{" "}
                    {String(utilization.source)}).
                  </p>
                )}
                <ul className="list-disc space-y-1 pl-5 text-sm text-muted">
                  {r.assumptions.map((a) => (
                    <li key={a}>{a}</li>
                  ))}
                </ul>
                <details className="mt-3 text-xs">
                  <summary className="cursor-pointer text-muted">Raw evidence</summary>
                  <pre className="mt-2 overflow-x-auto rounded bg-background p-2">
                    {JSON.stringify(r.evidence, null, 2)}
                  </pre>
                </details>
              </Card>
            </div>
            <Card
              title="Simulation"
              className="mt-4"
              id="simulation"
              actions={
                canSimulate &&
                r.status !== "insufficient_evidence" && (
                  <Button onClick={() => simulate.mutate()} loading={simulate.isPending}>
                    Simulate change
                  </Button>
                )
              }
            >
              {simulate.isError && <ErrorState error={simulate.error} />}
              {sim ? (
                sim.status === "estimated" ? (
                  <>
                    <div className="mb-3 flex gap-2">
                      <ModeBadge mode="simulation" />
                      <span className="text-xs text-muted">
                        Nothing was changed. Not a realized saving.
                      </span>
                    </div>
                    <dl className="grid grid-cols-1 gap-3 sm:grid-cols-4">
                      <Stat
                        label="Current monthly cost"
                        value={money(sim.current_monthly_cost, sim.currency ?? "USD")}
                      />
                      <Stat
                        label="Projected monthly cost"
                        value={money(sim.projected_monthly_cost, sim.currency ?? "USD")}
                      />
                      <Stat
                        label="Estimated savings"
                        value={money(sim.estimated_monthly_savings, sim.currency ?? "USD")}
                        tone="ok"
                      />
                      <Stat
                        label="Uncertainty range"
                        value={`${money(sim.savings_range?.low, sim.currency ?? "USD")} – ${money(sim.savings_range?.high, sim.currency ?? "USD")}`}
                      />
                    </dl>
                    {sim.excluded_costs.length > 0 && (
                      <ul className="mt-3 list-disc pl-5 text-sm text-muted">
                        {sim.excluded_costs.map((e, i) => (
                          <li key={i}>
                            Excluded: {String(e.item)}{" "}
                            {e.monthly_amount ? `(${money(String(e.monthly_amount))}/mo)` : ""} —{" "}
                            {String(e.reason)}
                          </li>
                        ))}
                      </ul>
                    )}
                  </>
                ) : (
                  <p className="text-sm text-muted">{sim.message}</p>
                )
              ) : (
                <p className="text-sm text-muted">Not simulated yet.</p>
              )}
            </Card>
            {(canRequest || canUpdate) && ["open"].includes(r.status) && (
              <Card title="Act on this recommendation" className="mt-4" id="act">
                <div className="flex flex-wrap items-end gap-3">
                  <Field label="Reason (sent with the request or dismissal)">
                    <input
                      className={`${inputClass} w-80`}
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                    />
                  </Field>
                  {canRequest && r.proposed_action && (
                    <Button
                      variant="primary"
                      onClick={() => requestApproval.mutate()}
                      loading={requestApproval.isPending}
                    >
                      Request approval: {r.proposed_action}
                    </Button>
                  )}
                  {canUpdate && (
                    <Button
                      variant="danger"
                      disabled={reason.length < 3}
                      onClick={() => dismiss.mutate()}
                      loading={dismiss.isPending}
                    >
                      Dismiss
                    </Button>
                  )}
                </div>
                {!r.proposed_action && (
                  <p className="mt-2 text-xs text-muted">
                    This category has no automated action; changes are made manually by the owner.
                  </p>
                )}
                {requestApproval.isError && <ErrorState error={requestApproval.error} />}
                {dismiss.isError && <ErrorState error={dismiss.error} />}
              </Card>
            )}
          </>
        );
      }}
    </QueryView>
  );
}
