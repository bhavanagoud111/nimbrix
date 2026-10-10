"use client";

import type { ReadinessResponse } from "@nimbrix/contracts";
import { useQuery } from "@tanstack/react-query";
import { ApiUnavailableError, fetchReadiness } from "@/lib/api/health";

const REFRESH_MS = 10_000;

type Tone = "ok" | "fail" | "unknown";

const toneClasses: Record<Tone, string> = {
  ok: "border-ok/50 text-ok",
  fail: "border-fail/50 text-fail",
  unknown: "border-border text-muted",
};

function StatusPill({ tone, label }: { tone: Tone; label: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium ${toneClasses[tone]}`}
    >
      <span aria-hidden="true">{tone === "ok" ? "●" : tone === "fail" ? "▲" : "○"}</span>
      <span>{label}</span>
    </span>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border py-3 first:border-t-0">
      <dt className="text-sm text-muted">{label}</dt>
      <dd className="text-sm">{children}</dd>
    </div>
  );
}

function ReadinessDetails({ data }: { data: ReadinessResponse }) {
  const { database, migrations } = data.checks;
  return (
    <dl>
      <Row label="API">
        <StatusPill
          tone={data.status === "ready" ? "ok" : "fail"}
          label={data.status === "ready" ? "Ready" : "Not ready"}
        />
      </Row>
      <Row label="Database">
        <span className="flex items-center gap-2">
          <StatusPill
            tone={database.status === "ok" ? "ok" : "fail"}
            label={database.status === "ok" ? "Connected" : "Unavailable"}
          />
          {database.latency_ms != null && (
            <span className="font-mono text-xs text-muted">{database.latency_ms} ms</span>
          )}
        </span>
      </Row>
      <Row label="Schema migrations">
        <span className="flex items-center gap-2">
          <StatusPill
            tone={migrations.status === "ok" ? "ok" : "fail"}
            label={migrations.status === "ok" ? "Up to date" : "Pending"}
          />
          <span className="font-mono text-xs text-muted">
            {migrations.current_revision ?? "none"} / {migrations.expected_revision ?? "unknown"}
          </span>
        </span>
      </Row>
      <Row label="Remediation mode">
        <span className="font-mono text-xs">{data.remediation_mode}</span>
      </Row>
      <Row label="Environment · version">
        <span className="font-mono text-xs">
          {data.environment} · {data.version}
        </span>
      </Row>
    </dl>
  );
}

export function SystemStatus() {
  const query = useQuery({
    queryKey: ["health", "ready"],
    queryFn: () => fetchReadiness(),
    refetchInterval: REFRESH_MS,
  });

  return (
    <section
      aria-labelledby="readiness-heading"
      aria-busy={query.isPending}
      className="rounded-lg border border-border bg-surface p-5"
    >
      <div className="mb-3 flex items-center justify-between">
        <h2 id="readiness-heading" className="font-medium">
          Service readiness
        </h2>
        {query.dataUpdatedAt > 0 && (
          <span className="text-xs text-muted">
            Checked {new Date(query.dataUpdatedAt).toLocaleTimeString()}
          </span>
        )}
      </div>

      {query.isPending && <p className="text-sm text-muted">Checking services…</p>}

      {query.isError && (
        <div role="alert" className="rounded border border-fail/50 p-3 text-sm">
          <p className="font-medium text-fail">API unreachable</p>
          <p className="mt-1 text-muted">{query.error.message}</p>
          {query.error instanceof ApiUnavailableError && query.error.requestId && (
            <p className="mt-1 font-mono text-xs text-muted">request id: {query.error.requestId}</p>
          )}
          <button
            type="button"
            onClick={() => void query.refetch()}
            className="mt-3 rounded border border-border px-3 py-1 text-xs hover:border-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
          >
            Retry now
          </button>
        </div>
      )}

      {query.isSuccess && <ReadinessDetails data={query.data} />}
    </section>
  );
}
