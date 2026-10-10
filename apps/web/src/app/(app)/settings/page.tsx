"use client";

import { StatusBadge } from "@/components/ui/badges";
import { Button, Card, Field, PageHeader, inputClass } from "@/components/ui/primitives";
import { DataTable } from "@/components/ui/table";
import { EmptyState, ErrorState, QueryView } from "@/components/ui/states";
import { api, newIdempotencyKey, readCookie, unwrap, ApiRequestError } from "@/lib/api/client";
import { useCan, useJob, useMe } from "@/lib/api/hooks";
import { dateTime } from "@/lib/format";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";

export default function SettingsPage() {
  const queryClient = useQueryClient();
  const me = useMe();
  const canImport = useCan("imports:write");
  const canConnect = useCan("connections:write");
  const imports = useQuery({
    queryKey: ["imports"],
    queryFn: async () =>
      unwrap(await api.GET("/api/v1/imports", { params: { query: { limit: 20 } } })),
    refetchInterval: 4000,
  });
  const connections = useQuery({
    queryKey: ["connections"],
    queryFn: async () => unwrap(await api.GET("/api/v1/connections")),
  });
  const actions = useQuery({
    queryKey: ["actions"],
    queryFn: async () => unwrap(await api.GET("/api/v1/actions")),
  });
  const demo = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST("/api/v1/imports/demo", { body: { days: 120, seed: 42 } })),
  });
  const demoJob = useJob(demo.data?.job_id);
  useEffect(() => {
    if (demoJob.data?.state === "succeeded") void queryClient.invalidateQueries();
  }, [demoJob.data?.state, queryClient]);

  const [file, setFile] = useState<File | null>(null);
  const upload = useMutation({
    mutationFn: async () => {
      const form = new FormData();
      form.append("file", file!);
      // Multipart is sent with fetch directly; same CSRF + idempotency rules as the typed client.
      const response = await fetch("/api/v1/imports/upload", {
        method: "POST",
        body: form,
        headers: {
          "X-CSRF-Token": readCookie("nimbrix_csrf") ?? "",
          "Idempotency-Key": newIdempotencyKey(),
        },
      });
      const body = await response.json();
      if (!response.ok)
        throw new ApiRequestError(
          body?.error?.message ?? "Upload failed",
          response.status,
          body?.error?.code ?? "http_error",
          body?.error?.request_id ?? null,
        );
      return body;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["imports"] }),
  });

  const [conn, setConn] = useState({ name: "", account_id: "", role_arn: "", external_id: "" });
  const createConn = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/v1/connections", {
          body: {
            provider: "aws",
            name: conn.name,
            account_id: conn.account_id,
            role_arn: conn.role_arn || null,
            external_id: conn.external_id || null,
            regions: ["us-east-1"],
          },
        }),
      ),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["connections"] }),
  });
  const validate = useMutation({
    mutationFn: async (id: string) =>
      unwrap(
        await api.POST("/api/v1/connections/aws/validate", {
          body: { connection_id: id, check_cost_explorer: false },
        }),
      ),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["connections"] }),
  });

  return (
    <>
      <PageHeader
        title="Integrations & Settings"
        description="Data sources, cloud connections, and the remediation action registry."
      />
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card title="Your access" id="access">
          {me.data && (
            <ul className="space-y-1 text-sm">
              <li>
                {me.data.user.display_name} ({me.data.user.email})
              </li>
              <li>
                Tenant: {me.data.tenant.name} <code className="text-xs">{me.data.tenant.slug}</code>
              </li>
              <li>
                Role: <strong>{me.data.role}</strong> · auth: {me.data.auth_method}
              </li>
              <li className="text-xs text-muted">Permissions: {me.data.permissions.join(", ")}</li>
            </ul>
          )}
        </Card>
        <Card title="Load data" id="load">
          {canImport ? (
            <div className="space-y-4">
              <div>
                <p className="mb-2 text-sm">
                  Synthetic demo dataset: 120 days, 3 accounts, labeled spike and idle instance.
                  Labeled <em>demo</em> everywhere.
                </p>
                <Button variant="primary" onClick={() => demo.mutate()} loading={demo.isPending}>
                  Import demo data
                </Button>
                {demo.data && (
                  <p className="mt-2 text-xs text-muted">
                    Import {demo.data.state}
                    {demoJob.data ? ` · job ${demoJob.data.state}` : ""}
                  </p>
                )}
                {demo.isError && <ErrorState error={demo.error} />}
              </div>
              <form
                onSubmit={(e: FormEvent) => {
                  e.preventDefault();
                  if (file) upload.mutate();
                }}
                className="flex flex-wrap items-end gap-2"
              >
                <Field label="Billing CSV (see data/fixtures/README.md)">
                  <input
                    type="file"
                    accept=".csv,text/csv"
                    className={inputClass}
                    onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                  />
                </Field>
                <Button type="submit" disabled={!file} loading={upload.isPending}>
                  Upload
                </Button>
              </form>
              {upload.isError && <ErrorState error={upload.error} />}
            </div>
          ) : (
            <p className="text-sm text-muted">Your role cannot import data.</p>
          )}
        </Card>
      </div>
      <Card title="Recent imports" className="mt-4" id="imports">
        <QueryView
          query={imports}
          isEmpty={(d) => d.items.length === 0}
          empty={<EmptyState title="No imports" />}
        >
          {(d) => (
            <DataTable
              caption="Imports"
              rows={d.items}
              rowKey={(i) => i.id}
              columns={[
                {
                  key: "src",
                  header: "Source",
                  render: (i) => (
                    <>
                      {i.source} <span className="text-xs text-muted">({i.mode})</span>
                      <div className="text-xs text-muted">{i.filename}</div>
                    </>
                  ),
                },
                {
                  key: "period",
                  header: "Period",
                  render: (i) => `${i.period_start ?? "—"} → ${i.period_end ?? "—"}`,
                },
                {
                  key: "rows",
                  header: "Rows (ins/upd/same/rej)",
                  align: "right",
                  render: (i) =>
                    `${i.rows_inserted}/${i.rows_updated}/${i.rows_unchanged}/${i.rows_rejected}`,
                },
                {
                  key: "errors",
                  header: "Errors",
                  render: (i) =>
                    i.errors.length ? (
                      <details className="text-xs">
                        <summary className="cursor-pointer text-fail">
                          {i.errors.length} error(s)
                        </summary>
                        <ul className="mt-1">
                          {i.errors.slice(0, 10).map((e, n) => (
                            <li key={n}>
                              row {String(e.row ?? "-")}: {String(e.code)} — {String(e.message)}
                            </li>
                          ))}
                        </ul>
                      </details>
                    ) : (
                      "—"
                    ),
                },
                { key: "when", header: "Requested", render: (i) => dateTime(i.created_at) },
                { key: "state", header: "State", render: (i) => <StatusBadge status={i.state} /> },
              ]}
            />
          )}
        </QueryView>
      </Card>
      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card title="AWS connections (read-only role, no keys)" id="connections">
          <QueryView
            query={connections}
            isEmpty={(d) => d.items.length === 0}
            empty={
              <EmptyState title="No connections">
                AWS access is optional and off by default.
              </EmptyState>
            }
          >
            {(d) => (
              <ul className="space-y-2 text-sm">
                {d.items.map((c) => (
                  <li
                    key={c.id}
                    className="flex flex-wrap items-center justify-between gap-2 rounded border border-border p-2"
                  >
                    <span>
                      {c.name} <code className="text-xs">{c.account_id}</code>
                      <span className="block text-xs text-muted">{c.role_arn}</span>
                      {c.last_error && (
                        <span className="block text-xs text-fail">{c.last_error}</span>
                      )}
                    </span>
                    <span className="flex gap-2">
                      <StatusBadge status={c.status} />
                      {canConnect && (
                        <Button
                          onClick={() => validate.mutate(c.id)}
                          loading={validate.isPending && validate.variables === c.id}
                        >
                          Validate
                        </Button>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </QueryView>
          {validate.isError && <ErrorState error={validate.error} />}
          {canConnect && (
            <form
              onSubmit={(e: FormEvent) => {
                e.preventDefault();
                createConn.mutate();
              }}
              className="mt-4 grid grid-cols-2 gap-2"
            >
              <Field label="Name">
                <input
                  required
                  className={inputClass}
                  value={conn.name}
                  onChange={(e) => setConn({ ...conn, name: e.target.value })}
                />
              </Field>
              <Field label="Account ID">
                <input
                  required
                  pattern="\d{12}"
                  className={inputClass}
                  value={conn.account_id}
                  onChange={(e) => setConn({ ...conn, account_id: e.target.value })}
                />
              </Field>
              <Field label="Read-only role ARN">
                <input
                  className={inputClass}
                  value={conn.role_arn}
                  onChange={(e) => setConn({ ...conn, role_arn: e.target.value })}
                />
              </Field>
              <Field label="External ID">
                <input
                  className={inputClass}
                  value={conn.external_id}
                  onChange={(e) => setConn({ ...conn, external_id: e.target.value })}
                />
              </Field>
              <div className="col-span-2">
                <Button type="submit" loading={createConn.isPending}>
                  Add connection
                </Button>
              </div>
              {createConn.isError && (
                <div className="col-span-2">
                  <ErrorState error={createConn.error} />
                </div>
              )}
            </form>
          )}
        </Card>
        <Card title="Remediation action registry" id="actions">
          <QueryView query={actions}>
            {(d) => (
              <>
                <p className="mb-2 text-sm">
                  Live cloud actions:{" "}
                  <strong>{d.live_actions_enabled ? "enabled" : "disabled"}</strong>
                </p>
                <ul className="space-y-2 text-sm">
                  {d.items.map((a) => (
                    <li key={a.name}>
                      <code className="text-xs">{a.name}</code> ({a.mode}) — {a.description}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </QueryView>
        </Card>
      </div>
    </>
  );
}
