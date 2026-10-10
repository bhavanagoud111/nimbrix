"use client";

import { ModeBadge, StatusBadge } from "@/components/ui/badges";
import { Button, Card, Field, PageHeader, Stat, inputClass } from "@/components/ui/primitives";
import { DataTable } from "@/components/ui/table";
import { ErrorState, QueryView } from "@/components/ui/states";
import { api, unwrap } from "@/lib/api/client";
import { useCan, useMe } from "@/lib/api/hooks";
import { dateTime, money } from "@/lib/format";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

interface Check {
  check: string;
  passed: boolean;
  detail: string;
}

export default function ApprovalDetailPage() {
  const { id } = useParams<{ id: string }>();
  const queryClient = useQueryClient();
  const me = useMe();
  const canDecide = useCan("approvals:decide");
  const canExecute = useCan("remediations:execute");
  const canAudit = useCan("audit:read");
  const [reason, setReason] = useState("");
  const approval = useQuery({
    queryKey: ["approval", id],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/approvals/{approval_id}", { params: { path: { approval_id: id } } }),
      ),
  });
  const executionId = approval.data?.execution_id;
  const execution = useQuery({
    queryKey: ["remediation", executionId],
    enabled: Boolean(executionId),
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/remediations/{execution_id}", {
          params: { path: { execution_id: executionId! } },
        }),
      ),
    refetchInterval: (q) =>
      q.state.data && ["pending", "running"].includes(q.state.data.state) ? 1000 : false,
  });
  const audit = useQuery({
    queryKey: ["audit", "approval", id, executionId],
    enabled: canAudit,
    queryFn: async () => {
      const a = unwrap(
        await api.GET("/api/v1/audit-events", { params: { query: { target_id: id } } }),
      );
      const e = executionId
        ? unwrap(
            await api.GET("/api/v1/audit-events", {
              params: { query: { target_id: executionId } },
            }),
          )
        : { items: [] };
      return [...a.items, ...e.items].sort((x, y) => x.occurred_at.localeCompare(y.occurred_at));
    },
  });
  const executionState = execution.data?.state;
  useEffect(() => {
    if (executionState === "succeeded" || executionState === "failed") {
      void queryClient.invalidateQueries({ queryKey: ["audit"] });
      void queryClient.invalidateQueries({ queryKey: ["approval", id] });
    }
  }, [executionState, queryClient, id]);
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["approval", id] });
    void queryClient.invalidateQueries({ queryKey: ["audit"] });
  };
  const approve = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/v1/approvals/{approval_id}/approve", {
          params: { path: { approval_id: id } },
          body: { payload_hash: approval.data!.payload_hash, reason: reason || null },
        }),
      ),
    onSuccess: refresh,
  });
  const reject = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/v1/approvals/{approval_id}/reject", {
          params: { path: { approval_id: id } },
          body: { reason },
        }),
      ),
    onSuccess: refresh,
  });
  const execute = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST("/api/v1/remediations", { body: { approval_request_id: id } })),
    onSuccess: refresh,
  });

  return (
    <QueryView query={approval}>
      {(a) => {
        const checks = (a.policy_result.checks as Check[] | undefined) ?? [];
        const isRequester = me.data?.user.id === a.requested_by;
        const ex = execution.data;
        return (
          <>
            <PageHeader
              title={`${a.action_type} → ${a.target_resource_id}`}
              description={
                <>
                  Requested by {a.requested_by_email} on {dateTime(a.created_at)} · expires{" "}
                  {dateTime(a.expires_at)}
                  {a.decided_by_email && <> · decided by {a.decided_by_email}</>}
                </>
              }
              actions={
                <>
                  <ModeBadge mode="simulation" />
                  <StatusBadge status={a.status} />
                </>
              }
            />
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
              <Card title="Exact action payload" id="payload">
                <pre className="overflow-x-auto rounded bg-background p-3 text-xs">
                  {JSON.stringify(a.payload, null, 2)}
                </pre>
                <p className="mt-2 break-all text-xs text-muted">
                  payload SHA-256: <code>{a.payload_hash}</code>
                </p>
                <p className="mt-1 text-xs text-muted">
                  Approving sends this hash; the server rejects the decision if the payload changed.
                </p>
              </Card>
              <Card
                title={`Policy outcome: ${a.policy_result.allowed ? "allowed" : "denied"}`}
                id="policy"
              >
                <DataTable
                  caption="Policy checks"
                  rows={checks}
                  rowKey={(c) => c.check}
                  columns={[
                    { key: "check", header: "Check", render: (c) => c.check },
                    {
                      key: "detail",
                      header: "Detail",
                      render: (c) => <span className="text-xs">{c.detail}</span>,
                    },
                    {
                      key: "ok",
                      header: "Result",
                      render: (c) => <StatusBadge status={c.passed ? "succeeded" : "failed"} />,
                    },
                  ]}
                />
              </Card>
              <Card title="Impact preview" id="impact">
                <dl className="grid grid-cols-2 gap-3">
                  <Stat
                    label="Current monthly compute"
                    value={money(a.impact_preview.current_monthly_compute_cost as string)}
                  />
                  <Stat
                    label="Estimated savings / mo"
                    value={money(a.impact_preview.estimated_monthly_savings as string)}
                    tone="ok"
                  />
                </dl>
                <p className="mt-2 text-sm">
                  Blast radius: {String(a.impact_preview.blast_radius)}
                </p>
                <p className="mt-1 text-xs text-muted">
                  Basis: {String(a.impact_preview.savings_basis)}. Excluded:{" "}
                  {((a.impact_preview.excluded_costs as string[]) ?? []).join(", ")}.
                </p>
              </Card>
              <Card title="Rollback plan" id="rollback">
                <ol className="list-decimal pl-5 text-sm">
                  {((a.rollback_plan.steps as string[]) ?? []).map((s) => (
                    <li key={s}>{s}</li>
                  ))}
                </ol>
                <p className="mt-2 text-xs text-muted">
                  Data loss: {String(a.rollback_plan.data_loss)} · recovery ≈{" "}
                  {String(a.rollback_plan.estimated_recovery_minutes)} min
                </p>
              </Card>
            </div>

            {a.status === "pending" && canDecide && (
              <Card title="Decision" className="mt-4" id="decision">
                {isRequester && (
                  <p className="mb-2 text-sm text-warn">
                    You requested this action; separation of duties requires a different approver.
                  </p>
                )}
                <div className="flex flex-wrap items-end gap-3">
                  <Field label="Reason (required to reject)">
                    <input
                      className={`${inputClass} w-80`}
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                    />
                  </Field>
                  <Button
                    variant="primary"
                    onClick={() => approve.mutate()}
                    loading={approve.isPending}
                  >
                    Approve simulation
                  </Button>
                  <Button
                    variant="danger"
                    disabled={reason.length < 3}
                    onClick={() => reject.mutate()}
                    loading={reject.isPending}
                  >
                    Reject
                  </Button>
                </div>
                {approve.isError && <ErrorState error={approve.error} />}
                {reject.isError && <ErrorState error={reject.error} />}
              </Card>
            )}
            {a.status === "pending" && !canDecide && (
              <p className="mt-4 text-sm text-muted">
                Waiting for an approver. Your role cannot decide.
              </p>
            )}

            {a.status === "approved" && canExecute && !a.execution_id && (
              <Card title="Execute" className="mt-4" id="execute">
                <p className="mb-2 text-sm">
                  Runs the <strong>simulation</strong> adapter after re-validating policy and
                  inventory freshness. No cloud API is called.
                </p>
                <Button
                  variant="primary"
                  onClick={() => execute.mutate()}
                  loading={execute.isPending}
                >
                  Execute simulation
                </Button>
                {execute.isError && <ErrorState error={execute.error} />}
              </Card>
            )}

            {ex && (
              <Card
                title="Execution outcome"
                className="mt-4"
                id="outcome"
                actions={<StatusBadge status={ex.state} />}
              >
                <dl className="grid grid-cols-1 gap-3 sm:grid-cols-4">
                  <Stat
                    label="Before"
                    value={String(
                      (ex.result.before as Record<string, string> | undefined)?.state ?? "—",
                    )}
                    sub={`${money((ex.result.before as Record<string, string> | undefined)?.monthly_compute_cost)}/mo compute`}
                  />
                  <Stat
                    label="After (simulated)"
                    value={String(
                      (ex.result.after as Record<string, string> | undefined)?.state ?? "pending",
                    )}
                    sub={`${money((ex.result.after as Record<string, string> | undefined)?.monthly_compute_cost)}/mo compute`}
                    tone="ok"
                  />
                  <Stat
                    label="Estimated savings"
                    value={money(ex.result.estimated_monthly_savings as string)}
                    sub="simulated — not realized"
                  />
                  <Stat
                    label="Verification"
                    value={
                      ex.verification.verified
                        ? "verified"
                        : ex.state === "failed"
                          ? "failed"
                          : "pending"
                    }
                    sub={`attempts: ${ex.attempts} · cloud API called: ${String(ex.result.cloud_api_called ?? false)}`}
                    tone={ex.verification.verified ? "ok" : "neutral"}
                  />
                </dl>
                {ex.error && <p className="mt-2 text-sm text-fail">{ex.error}</p>}
              </Card>
            )}

            {canAudit && (
              <Card title="Audit trail" className="mt-4" id="audit-trail">
                <QueryView query={audit} isEmpty={(d) => d.length === 0}>
                  {(events) => (
                    <ol className="space-y-1 text-sm">
                      {events.map((e) => (
                        <li key={e.id}>
                          <span className="text-xs text-muted">{dateTime(e.occurred_at)}</span>{" "}
                          <code className="text-xs">{e.event_type}</code> by{" "}
                          {e.actor_label ?? e.actor_type}{" "}
                          <StatusBadge status={e.outcome === "success" ? "succeeded" : "failed"} />
                        </li>
                      ))}
                    </ol>
                  )}
                </QueryView>
              </Card>
            )}
          </>
        );
      }}
    </QueryView>
  );
}
