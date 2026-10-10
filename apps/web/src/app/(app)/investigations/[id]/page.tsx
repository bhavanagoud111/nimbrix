"use client";

import { ConfidenceBadge, ModeBadge, StatusBadge } from "@/components/ui/badges";
import { Button, Card, PageHeader, Stat } from "@/components/ui/primitives";
import { ErrorState, QueryView } from "@/components/ui/states";
import { api, unwrap } from "@/lib/api/client";
import { useCan } from "@/lib/api/hooks";
import { money } from "@/lib/format";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";

const GRAPH = [
  ["triage", "Cost Analyst Agent"],
  ["collect_evidence", "Infrastructure Investigator"],
  ["forecast_context", "Forecast Analyst"],
  ["hypothesize", "Cost Analyst Agent"],
  ["synthesize_recommendations", "Optimization Planner"],
  ["policy_review", "Optimization Planner"],
  ["report", "Report Agent"],
] as const;

interface Hypothesis {
  statement: string;
  evidence_ids: string[];
  confidence: string;
}
interface EvidenceItem {
  id: string;
  kind: string;
  summary: string;
}
interface Proposed {
  action_type: string;
  resource_id: string;
  estimated_monthly_savings: string | null;
  policy_allowed: boolean;
  policy_denial_reasons: string[];
}

function anchor(id: string): string {
  return `ev-${id.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
}

export default function InvestigationWorkspace() {
  const { id } = useParams<{ id: string }>();
  const queryClient = useQueryClient();
  const canRun = useCan("investigations:run");
  const inv = useQuery({
    queryKey: ["investigation", id],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/investigations/{investigation_id}", {
          params: { path: { investigation_id: id } },
        }),
      ),
    refetchInterval: (q) =>
      q.state.data && ["queued", "running"].includes(q.state.data.state) ? 1000 : false,
  });
  const cancel = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/v1/investigations/{investigation_id}/cancel", {
          params: { path: { investigation_id: id } },
        }),
      ),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["investigation", id] }),
  });

  return (
    <QueryView query={inv} loadingLabel="Loading investigation…">
      {(i) => {
        const report = i.report as Record<string, unknown> | null;
        const hypotheses = (report?.hypotheses as Hypothesis[] | undefined) ?? [];
        const evidence = (report?.evidence as EvidenceItem[] | undefined) ?? [];
        const figures = (report?.key_figures as Record<string, string> | undefined) ?? {};
        const proposed = (report?.proposed_actions as Proposed[] | undefined) ?? [];
        const rejected = (report?.model_output_rejected as string[] | undefined) ?? [];
        const done = new Map(i.steps.map((s) => [s.node, s]));
        return (
          <>
            <PageHeader
              title={(report?.title as string | undefined) ?? "Investigation in progress"}
              description={
                <>
                  Trace <code>{i.trace_id}</code>
                  {i.anomaly_id && (
                    <>
                      {" "}
                      ·{" "}
                      <Link className="text-accent" href={`/anomalies/${i.anomaly_id}`}>
                        anomaly
                      </Link>
                    </>
                  )}
                </>
              }
              actions={
                <>
                  <ModeBadge mode={i.mode} />
                  <StatusBadge status={i.state} />
                  {i.state === "completed" && (
                    <a
                      className="rounded-md border border-border px-3 py-1.5 text-sm hover:border-accent"
                      href={`/api/v1/investigations/${i.id}/report.md`}
                    >
                      Export Markdown
                    </a>
                  )}
                  {canRun && ["queued", "running"].includes(i.state) && (
                    <Button
                      variant="danger"
                      onClick={() => cancel.mutate()}
                      loading={cancel.isPending}
                    >
                      Cancel
                    </Button>
                  )}
                </>
              }
            />
            {i.error && <ErrorState error={new Error(i.error)} />}
            <Card title="Agent graph progress" id="graph">
              <ol className="grid grid-cols-1 gap-2 md:grid-cols-7">
                {GRAPH.map(([node, role]) => {
                  const step = done.get(node);
                  return (
                    <li
                      key={node}
                      className={`rounded border p-2 text-xs ${step ? "border-ok/50" : "border-border text-muted"}`}
                    >
                      <div className="font-medium">{node.replace(/_/g, " ")}</div>
                      <div className="text-muted">{role}</div>
                      {step && (
                        <div className="mt-1">
                          {step.latency_ms.toFixed(0)} ms · {step.mode}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ol>
              <details className="mt-3 text-sm">
                <summary className="cursor-pointer text-muted">
                  Step log ({i.steps.length} steps, {String(i.usage.tool_calls ?? 0)} tool calls,{" "}
                  {String(i.usage.llm_calls ?? 0)} LLM calls)
                </summary>
                <ol className="mt-2 space-y-1">
                  {i.steps.map((s) => (
                    <li key={s.step_index}>
                      <span className="text-muted">
                        #{s.step_index} {s.node} ({s.agent_role}):
                      </span>{" "}
                      {s.summary}
                      {s.tokens_in ? ` · ${s.tokens_in}/${s.tokens_out} tokens` : ""}
                    </li>
                  ))}
                </ol>
              </details>
            </Card>
            {report && (
              <>
                <Card title="Summary" className="mt-4" id="summary">
                  <p className="text-sm">{String(report.summary)}</p>
                  {Boolean(report.insufficient_evidence) && (
                    <p className="mt-2 text-sm text-warn">
                      Insufficient evidence:{" "}
                      {((report.missing_evidence as string[]) ?? []).join("; ")}
                    </p>
                  )}
                </Card>
                <dl className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-4">
                  <Stat
                    label="Observed in window"
                    value={money(figures.observed, figures.currency)}
                  />
                  <Stat label="Expected" value={money(figures.expected, figures.currency)} />
                  <Stat
                    label="New resources add"
                    value={`${money(figures.new_resources_delta_per_day, figures.currency)}/day`}
                    tone="fail"
                  />
                  <Stat
                    label="Excess explained"
                    value={`${figures.explained_pct ?? "—"}%`}
                    sub="computed deterministically"
                  />
                </dl>
                <Card title="Hypotheses" className="mt-4" id="hypotheses">
                  <ol className="space-y-3">
                    {hypotheses.map((h, n) => (
                      <li key={n} className="rounded border border-border p-3 text-sm">
                        <div className="mb-1 flex items-center gap-2">
                          {n === 0 && (
                            <span className="text-xs font-semibold uppercase text-accent">
                              Root cause
                            </span>
                          )}
                          <ConfidenceBadge confidence={h.confidence} />
                        </div>
                        <p>{h.statement}</p>
                        <p className="mt-2 flex flex-wrap gap-1 text-xs">
                          {h.evidence_ids.map((e) => (
                            <a
                              key={e}
                              href={`#${anchor(e)}`}
                              className="rounded border border-border px-1.5 py-0.5 font-mono hover:border-accent"
                            >
                              {e}
                            </a>
                          ))}
                        </p>
                      </li>
                    ))}
                  </ol>
                </Card>
                <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
                  <Card title="Proposed actions (policy-reviewed, nothing executed)" id="proposed">
                    {proposed.length === 0 ? (
                      <p className="text-sm text-muted">No allowlisted action applies.</p>
                    ) : (
                      <ul className="space-y-2 text-sm">
                        {proposed.map((p) => (
                          <li key={p.resource_id} className="rounded border border-border p-2">
                            <code className="text-xs">{p.action_type}</code> on{" "}
                            <code className="text-xs">{p.resource_id}</code> · est.{" "}
                            {money(p.estimated_monthly_savings)}/mo{" "}
                            <StatusBadge status={p.policy_allowed ? "approved" : "rejected"} />
                            {!p.policy_allowed && (
                              <ul className="mt-1 list-disc pl-5 text-xs text-muted">
                                {p.policy_denial_reasons.map((r) => (
                                  <li key={r}>{r}</li>
                                ))}
                              </ul>
                            )}
                          </li>
                        ))}
                      </ul>
                    )}
                  </Card>
                  <Card title="Limitations" id="limitations">
                    <ul className="list-disc space-y-1 pl-5 text-sm text-muted">
                      {((report.limitations as string[]) ?? []).map((l) => (
                        <li key={l}>{l}</li>
                      ))}
                    </ul>
                    {rejected.length > 0 && (
                      <>
                        <h3 className="mb-1 mt-3 text-xs font-semibold uppercase text-warn">
                          Rejected model output (deterministic fallback used)
                        </h3>
                        <ul className="list-disc pl-5 text-xs text-muted">
                          {rejected.map((r) => (
                            <li key={r}>{r}</li>
                          ))}
                        </ul>
                      </>
                    )}
                  </Card>
                </div>
                <Card title={`Evidence (${evidence.length})`} className="mt-4" id="evidence">
                  <ul className="space-y-1 text-sm">
                    {evidence.map((e) => (
                      <li
                        key={e.id}
                        id={anchor(e.id)}
                        className="scroll-mt-4 rounded px-1 target:bg-accent/10"
                      >
                        <code className="text-xs text-accent">{e.id}</code>{" "}
                        <span className="text-xs text-muted">({e.kind})</span> {e.summary}
                      </li>
                    ))}
                  </ul>
                </Card>
              </>
            )}
          </>
        );
      }}
    </QueryView>
  );
}
