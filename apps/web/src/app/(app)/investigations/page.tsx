"use client";

import { ModeBadge, StatusBadge } from "@/components/ui/badges";
import { Card, PageHeader } from "@/components/ui/primitives";
import { DataTable } from "@/components/ui/table";
import { EmptyState, QueryView } from "@/components/ui/states";
import { api, unwrap } from "@/lib/api/client";
import { dateTime } from "@/lib/format";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";

export default function InvestigationsPage() {
  const router = useRouter();
  const list = useQuery({
    queryKey: ["investigations"],
    queryFn: async () =>
      unwrap(await api.GET("/api/v1/investigations", { params: { query: { limit: 100 } } })),
    refetchInterval: 5000,
  });
  return (
    <>
      <PageHeader
        title="Investigations"
        description="Agent investigations of anomalies: read-only, evidence-cited, deterministic unless an LLM provider is configured. Start one from an anomaly."
      />
      <Card title="Recent investigations" id="investigations">
        <QueryView
          query={list}
          isEmpty={(d) => d.items.length === 0}
          empty={
            <EmptyState title="No investigations yet">
              Open an{" "}
              <Link className="text-accent" href="/anomalies">
                anomaly
              </Link>{" "}
              and choose Investigate.
            </EmptyState>
          }
        >
          {(d) => (
            <DataTable
              caption="Investigations"
              rows={d.items}
              rowKey={(i) => i.id}
              onRowClick={(i) => router.push(`/investigations/${i.id}`)}
              columns={[
                {
                  key: "title",
                  header: "Report",
                  render: (i) => (i.report?.title as string | undefined) ?? "In progress",
                },
                { key: "mode", header: "Mode", render: (i) => <ModeBadge mode={i.mode} /> },
                { key: "state", header: "State", render: (i) => <StatusBadge status={i.state} /> },
                { key: "created", header: "Started", render: (i) => dateTime(i.created_at) },
              ]}
            />
          )}
        </QueryView>
      </Card>
    </>
  );
}
