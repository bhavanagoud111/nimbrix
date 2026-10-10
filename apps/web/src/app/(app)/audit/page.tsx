"use client";

import { Badge } from "@/components/ui/primitives";
import { Button, Card, Field, PageHeader, inputClass } from "@/components/ui/primitives";
import { EmptyState, QueryView } from "@/components/ui/states";
import { api, unwrap } from "@/lib/api/client";
import { dateTime } from "@/lib/format";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";

const PREFIXES = [
  "",
  "auth.",
  "authz.",
  "security.",
  "approval.",
  "remediation.",
  "import.",
  "investigation.",
  "anomaly.",
  "recommendation.",
  "budget.",
  "connection.",
];

export default function AuditPage() {
  const [prefix, setPrefix] = useState("");
  const events = useInfiniteQuery({
    queryKey: ["audit", prefix],
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam }) =>
      unwrap(
        await api.GET("/api/v1/audit-events", {
          params: { query: { event_type: prefix || undefined, limit: 50, cursor: pageParam } },
        }),
      ),
    getNextPageParam: (last) => last.page.next_cursor ?? undefined,
  });
  const flat = {
    ...events,
    data: events.data ? events.data.pages.flatMap((p) => p.items) : undefined,
  };
  return (
    <>
      <PageHeader
        title="Audit Timeline"
        description="Append-only record of security-relevant and control-plane events (UPDATE/DELETE are blocked by a database trigger). Payloads are redacted."
      />
      <Card
        title="Events"
        id="events"
        actions={
          <Field label="Event type">
            <select
              className={inputClass}
              value={prefix}
              onChange={(e) => setPrefix(e.target.value)}
            >
              {PREFIXES.map((p) => (
                <option key={p} value={p}>
                  {p || "all"}
                </option>
              ))}
            </select>
          </Field>
        }
      >
        <QueryView
          query={flat}
          isEmpty={(d) => d.length === 0}
          empty={<EmptyState title="No events" />}
        >
          {(items) => (
            <>
              <ol className="relative space-y-3 border-l border-border pl-4">
                {items.map((e) => (
                  <li key={e.id} className="text-sm">
                    <span
                      className="absolute -left-1.5 mt-1.5 h-3 w-3 rounded-full border border-border bg-surface"
                      aria-hidden="true"
                    />
                    <div className="flex flex-wrap items-center gap-2">
                      <code className="text-xs text-accent">{e.event_type}</code>
                      <Badge tone={e.outcome === "success" ? "ok" : "fail"}>{e.outcome}</Badge>
                      <span className="text-xs text-muted">{dateTime(e.occurred_at)}</span>
                    </div>
                    <div className="text-xs text-muted">
                      {e.actor_type}: {e.actor_label ?? "—"}{" "}
                      {e.target_type && (
                        <>
                          · {e.target_type} <code>{e.target_id}</code>
                        </>
                      )}{" "}
                      {e.request_id && <>· request {e.request_id}</>}
                    </div>
                    {Object.keys(e.payload).length > 0 && (
                      <details className="text-xs">
                        <summary className="cursor-pointer text-muted">payload</summary>
                        <pre className="overflow-x-auto rounded bg-background p-2">
                          {JSON.stringify(e.payload, null, 2)}
                        </pre>
                      </details>
                    )}
                  </li>
                ))}
              </ol>
              {events.hasNextPage && (
                <Button
                  className="mt-4"
                  onClick={() => void events.fetchNextPage()}
                  loading={events.isFetchingNextPage}
                >
                  Load older events
                </Button>
              )}
            </>
          )}
        </QueryView>
      </Card>
    </>
  );
}
