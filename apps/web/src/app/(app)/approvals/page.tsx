"use client";

import { StatusBadge } from "@/components/ui/badges";
import { Card, PageHeader } from "@/components/ui/primitives";
import { DataTable } from "@/components/ui/table";
import { EmptyState, QueryView } from "@/components/ui/states";
import { api, unwrap } from "@/lib/api/client";
import { dateTime, money } from "@/lib/format";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";

const TABS = ["pending", "approved", "executed", "rejected", "expired", ""] as const;

export default function ApprovalsPage() {
  const router = useRouter();
  const [tab, setTab] = useState<(typeof TABS)[number]>("pending");
  const list = useQuery({
    queryKey: ["approvals", tab],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/approvals", {
          params: { query: { status: tab || undefined, limit: 100 } },
        }),
      ),
  });
  return (
    <>
      <PageHeader
        title="Approval Center"
        description="Every action needs explicit human approval. Approvers see the exact payload, its hash, the policy outcome, impact, and rollback plan. Execution is simulation-only."
      />
      <div role="tablist" aria-label="Approval status" className="mb-3 flex flex-wrap gap-1">
        {TABS.map((t) => (
          <button
            key={t || "all"}
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`rounded-md border px-3 py-1 text-sm ${tab === t ? "border-accent text-accent" : "border-border text-muted"}`}
          >
            {t || "all"}
          </button>
        ))}
      </div>
      <Card title="Requests" id="approvals">
        <QueryView
          query={list}
          isEmpty={(d) => d.items.length === 0}
          empty={<EmptyState title={`No ${tab || ""} requests`} />}
        >
          {(d) => (
            <DataTable
              caption="Approval requests"
              rows={d.items}
              rowKey={(a) => a.id}
              onRowClick={(a) => router.push(`/approvals/${a.id}`)}
              columns={[
                {
                  key: "action",
                  header: "Action",
                  render: (a) => (
                    <>
                      <code className="text-xs">{a.action_type}</code>
                      <div className="text-xs text-muted">{a.target_resource_id}</div>
                    </>
                  ),
                },
                {
                  key: "savings",
                  header: "Est. savings / mo",
                  align: "right",
                  render: (a) => money(a.impact_preview.estimated_monthly_savings as string | null),
                },
                { key: "requester", header: "Requested by", render: (a) => a.requested_by_email },
                { key: "created", header: "Requested", render: (a) => dateTime(a.created_at) },
                { key: "expires", header: "Expires", render: (a) => dateTime(a.expires_at) },
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
