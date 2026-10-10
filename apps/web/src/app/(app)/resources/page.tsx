"use client";

import { ModeBadge, StatusBadge } from "@/components/ui/badges";
import { Button, Card, Field, PageHeader, inputClass } from "@/components/ui/primitives";
import { DataTable } from "@/components/ui/table";
import { EmptyState, QueryView } from "@/components/ui/states";
import { api, unwrap } from "@/lib/api/client";
import { money } from "@/lib/format";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";

export default function ResourcesPage() {
  const router = useRouter();
  const [filters, setFilters] = useState({ resource_type: "", environment: "", team: "", q: "" });
  const [cursor, setCursor] = useState<string | undefined>();
  const query = Object.fromEntries(Object.entries(filters).filter(([, v]) => v));
  const list = useQuery({
    queryKey: ["resources", query, cursor],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/resources", { params: { query: { ...query, limit: 50, cursor } } }),
      ),
  });
  const set = (k: keyof typeof filters, v: string) => {
    setCursor(undefined);
    setFilters({ ...filters, [k]: v });
  };
  return (
    <>
      <PageHeader
        title="Resource Inventory"
        description="Observed resources with utilization evidence. Observations are point-in-time; check the observed time before acting."
      />
      <Card
        title="Resources"
        id="resources"
        actions={
          <div className="flex flex-wrap gap-2">
            <Field label="Type">
              <select
                className={inputClass}
                value={filters.resource_type}
                onChange={(e) => set("resource_type", e.target.value)}
              >
                <option value="">All</option>
                {["ec2_instance", "ebs_volume", "elastic_ip"].map((t) => (
                  <option key={t}>{t}</option>
                ))}
              </select>
            </Field>
            <Field label="Environment">
              <select
                className={inputClass}
                value={filters.environment}
                onChange={(e) => set("environment", e.target.value)}
              >
                <option value="">All</option>
                {["prod", "staging", "dev"].map((t) => (
                  <option key={t}>{t}</option>
                ))}
              </select>
            </Field>
            <Field label="Team">
              <input
                className={inputClass}
                value={filters.team}
                onChange={(e) => set("team", e.target.value)}
                placeholder="any"
              />
            </Field>
            <Field label="Search">
              <input
                className={inputClass}
                value={filters.q}
                onChange={(e) => set("q", e.target.value)}
                placeholder="name or ID"
              />
            </Field>
          </div>
        }
      >
        <QueryView
          query={list}
          isEmpty={(d) => d.items.length === 0}
          empty={<EmptyState title="No resources match" />}
        >
          {(d) => (
            <>
              <DataTable
                caption="Resources"
                rows={d.items}
                rowKey={(r) => r.id}
                onRowClick={(r) => router.push(`/resources/${r.id}`)}
                columns={[
                  {
                    key: "name",
                    header: "Name",
                    render: (r) => (
                      <>
                        <div className="font-medium">{r.name ?? r.resource_id}</div>
                        <code className="text-xs text-muted">{r.resource_id}</code>
                      </>
                    ),
                  },
                  {
                    key: "type",
                    header: "Type",
                    render: (r) => (
                      <>
                        {r.resource_type}
                        <div className="text-xs text-muted">
                          {String(r.attributes.instance_type ?? r.attributes.volume_type ?? "")}
                        </div>
                      </>
                    ),
                  },
                  {
                    key: "env",
                    header: "Env / team",
                    render: (r) => `${r.environment ?? "—"} / ${r.team ?? "—"}`,
                  },
                  {
                    key: "cpu",
                    header: "CPU avg / p95",
                    align: "right",
                    render: (r) =>
                      r.utilization
                        ? `${String(r.utilization.statistic_avg_pct)}% / ${String(r.utilization.statistic_p95_pct)}%`
                        : "no metrics",
                  },
                  {
                    key: "price",
                    header: "List price",
                    align: "right",
                    render: (r) =>
                      r.hourly_cost
                        ? `${money(r.hourly_cost, r.currency, 4)}/h`
                        : r.monthly_cost
                          ? `${money(r.monthly_cost, r.currency)}/mo`
                          : "unknown",
                  },
                  {
                    key: "state",
                    header: "State",
                    render: (r) => (
                      <>
                        <StatusBadge status={r.state} /> <ModeBadge mode={r.mode} />
                      </>
                    ),
                  },
                ]}
              />
              {d.next_cursor && (
                <Button className="mt-3" onClick={() => setCursor(d.next_cursor ?? undefined)}>
                  Next page
                </Button>
              )}
            </>
          )}
        </QueryView>
      </Card>
    </>
  );
}
