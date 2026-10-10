"use client";

import { StatusBadge } from "@/components/ui/badges";
import { Button, Card, Field, PageHeader, inputClass } from "@/components/ui/primitives";
import { DataTable } from "@/components/ui/table";
import { EmptyState, ErrorState, QueryView } from "@/components/ui/states";
import { api, unwrap } from "@/lib/api/client";
import { useCan } from "@/lib/api/hooks";
import { dateTime, money, ratio, shortDate } from "@/lib/format";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

const SCOPES = ["total", "account", "service", "team", "environment"] as const;

export default function BudgetsPage() {
  const queryClient = useQueryClient();
  const canWrite = useCan("budgets:write");
  const budgets = useQuery({
    queryKey: ["budgets"],
    queryFn: async () => unwrap(await api.GET("/api/v1/budgets")),
  });
  const alerts = useQuery({
    queryKey: ["budgets", "alerts"],
    queryFn: async () => unwrap(await api.GET("/api/v1/budgets/alerts")),
  });
  const [form, setForm] = useState({
    name: "",
    scope_type: "team" as (typeof SCOPES)[number],
    scope_value: "",
    amount: "",
  });
  const create = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/v1/budgets", {
          body: {
            name: form.name,
            scope_type: form.scope_type,
            scope_value: form.scope_type === "total" ? null : form.scope_value,
            amount: form.amount,
            currency: "USD",
          },
        }),
      ),
    onSuccess: () => {
      setForm({ name: "", scope_type: "team", scope_value: "", amount: "" });
      void queryClient.invalidateQueries({ queryKey: ["budgets"] });
    },
  });
  const acknowledge = useMutation({
    mutationFn: async (id: string) =>
      unwrap(
        await api.POST("/api/v1/budgets/alerts/{alert_id}/acknowledge", {
          params: { path: { alert_id: id } },
        }),
      ),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["budgets", "alerts"] }),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate();
  }

  return (
    <>
      <PageHeader
        title="Budgets"
        description="Monthly budgets with month-to-date actuals and end-of-month projections (forecast model when available, otherwise run rate)."
      />
      <Card title="Budget utilization" id="budget-table">
        <QueryView
          query={budgets}
          isEmpty={(d) => d.items.length === 0}
          empty={<EmptyState title="No budgets yet" />}
        >
          {(d) => (
            <DataTable
              caption="Budgets"
              rows={d.items}
              rowKey={(b) => b.id}
              columns={[
                {
                  key: "name",
                  header: "Budget",
                  render: (b) => (
                    <>
                      <div className="font-medium">{b.name}</div>
                      <div className="text-xs text-muted">
                        {b.scope_type}
                        {b.scope_value ? `: ${b.scope_value}` : ""}
                      </div>
                    </>
                  ),
                },
                {
                  key: "amount",
                  header: "Amount",
                  align: "right",
                  render: (b) => money(b.amount, b.currency),
                },
                {
                  key: "actual",
                  header: `Actual (MTD)`,
                  align: "right",
                  render: (b) => (
                    <>
                      {money(b.actual, b.currency)}
                      <div className="text-xs text-muted">{ratio(b.utilization)}</div>
                    </>
                  ),
                },
                {
                  key: "projected",
                  header: "Projected",
                  align: "right",
                  render: (b) => (
                    <>
                      {money(b.projected, b.currency)}
                      <div className="text-xs text-muted">
                        {ratio(b.projected_utilization)} · {b.projection_method}
                      </div>
                    </>
                  ),
                },
                {
                  key: "period",
                  header: "Period",
                  render: (b) => (
                    <span className="text-xs">
                      {shortDate(b.period_start)} – {shortDate(b.period_end)}
                      <br />
                      as of {shortDate(b.as_of)}
                    </span>
                  ),
                },
                {
                  key: "status",
                  header: "Status",
                  render: (b) => <StatusBadge status={b.status} />,
                },
              ]}
            />
          )}
        </QueryView>
      </Card>

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card title="Alerts" id="alerts">
          <QueryView
            query={alerts}
            isEmpty={(d) => d.items.length === 0}
            empty={<EmptyState title="No alerts raised" />}
          >
            {(d) => (
              <ul className="space-y-2 text-sm">
                {d.items.map((a) => (
                  <li
                    key={a.id}
                    className="flex flex-wrap items-center justify-between gap-2 rounded border border-border p-2"
                  >
                    <span>
                      <span className="font-medium">{a.budget_name}</span> crossed{" "}
                      {ratio(a.threshold)} ({a.kind}) — {ratio(a.utilization)}
                      <span className="block text-xs text-muted">{dateTime(a.created_at)}</span>
                    </span>
                    <span className="flex items-center gap-2">
                      <StatusBadge status={a.status} />
                      {canWrite && a.status === "open" && (
                        <Button
                          onClick={() => acknowledge.mutate(a.id)}
                          loading={acknowledge.isPending && acknowledge.variables === a.id}
                        >
                          Acknowledge
                        </Button>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </QueryView>
        </Card>
        {canWrite && (
          <Card title="New monthly budget" id="new-budget">
            <form onSubmit={submit} className="grid grid-cols-2 gap-3">
              <Field label="Name">
                <input
                  required
                  className={inputClass}
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </Field>
              <Field label="Scope">
                <select
                  className={inputClass}
                  value={form.scope_type}
                  onChange={(e) =>
                    setForm({ ...form, scope_type: e.target.value as (typeof SCOPES)[number] })
                  }
                >
                  {SCOPES.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Scope value">
                <input
                  className={inputClass}
                  disabled={form.scope_type === "total"}
                  value={form.scope_value}
                  onChange={(e) => setForm({ ...form, scope_value: e.target.value })}
                />
              </Field>
              <Field label="Amount (USD)">
                <input
                  required
                  inputMode="decimal"
                  pattern="\d+(\.\d{1,2})?"
                  className={inputClass}
                  value={form.amount}
                  onChange={(e) => setForm({ ...form, amount: e.target.value })}
                />
              </Field>
              {create.isError && (
                <div className="col-span-2">
                  <ErrorState error={create.error} />
                </div>
              )}
              <div className="col-span-2">
                <Button type="submit" variant="primary" loading={create.isPending}>
                  Create budget
                </Button>
              </div>
            </form>
          </Card>
        )}
      </div>
    </>
  );
}
