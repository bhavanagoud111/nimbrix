"use client";

import { ConfidenceBadge, RiskBadge, StatusBadge } from "@/components/ui/badges";
import { Card, Field, PageHeader, Stat, inputClass } from "@/components/ui/primitives";
import { DataTable } from "@/components/ui/table";
import { EmptyState, QueryView } from "@/components/ui/states";
import { api, unwrap } from "@/lib/api/client";
import { money, titleCase } from "@/lib/format";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";

const CATEGORIES = [
  "idle_nonprod_instance",
  "underutilized_compute",
  "unattached_volume",
  "unused_elastic_ip",
  "nonprod_scheduling",
];

export default function RecommendationsPage() {
  const router = useRouter();
  const [status, setStatus] = useState("");
  const [category, setCategory] = useState("");
  const recs = useQuery({
    queryKey: ["recommendations", status, category],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/recommendations", {
          params: {
            query: { status: status || undefined, category: category || undefined, limit: 200 },
          },
        }),
      ),
  });
  return (
    <>
      <PageHeader
        title="Recommendations"
        description="Rule-based, evidence-backed optimization opportunities (rules-v1). Savings are estimated at list prices and are never realized until acted on."
      />
      <QueryView query={recs}>
        {(d) => (
          <dl className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Stat
              label="Estimated savings (open)"
              value={money(d.total_estimated_monthly_savings, d.currency)}
              sub="per month, estimate"
              tone="ok"
            />
            <Stat label="Recommendations shown" value={d.items.length} />
            <Stat
              label="Insufficient evidence"
              value={d.items.filter((r) => r.status === "insufficient_evidence").length}
              sub="not enough data to judge"
            />
          </dl>
        )}
      </QueryView>
      <Card
        title="All recommendations"
        id="recs"
        actions={
          <div className="flex gap-2">
            <Field label="Status">
              <select
                className={inputClass}
                value={status}
                onChange={(e) => setStatus(e.target.value)}
              >
                <option value="">Active</option>
                {[
                  "open",
                  "insufficient_evidence",
                  "approval_requested",
                  "actioned",
                  "dismissed",
                  "superseded",
                ].map((s) => (
                  <option key={s} value={s}>
                    {s.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Category">
              <select
                className={inputClass}
                value={category}
                onChange={(e) => setCategory(e.target.value)}
              >
                <option value="">All</option>
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {titleCase(c)}
                  </option>
                ))}
              </select>
            </Field>
          </div>
        }
      >
        <QueryView
          query={recs}
          isEmpty={(d) => d.items.length === 0}
          empty={<EmptyState title="No recommendations match" />}
        >
          {(d) => (
            <DataTable
              caption="Recommendations"
              rows={d.items}
              rowKey={(r) => r.id}
              onRowClick={(r) => router.push(`/recommendations/${r.id}`)}
              columns={[
                {
                  key: "title",
                  header: "Recommendation",
                  render: (r) => (
                    <>
                      <div className="font-medium">{r.title}</div>
                      <div className="text-xs text-muted">
                        {titleCase(r.category)} · owner {r.owner}
                      </div>
                    </>
                  ),
                },
                {
                  key: "savings",
                  header: "Est. savings / mo",
                  align: "right",
                  render: (r) => money(r.estimated_monthly_savings, r.currency),
                },
                {
                  key: "confidence",
                  header: "Confidence",
                  render: (r) => <ConfidenceBadge confidence={r.confidence} />,
                },
                { key: "risk", header: "Risk", render: (r) => <RiskBadge risk={r.risk} /> },
                {
                  key: "status",
                  header: "Status",
                  render: (r) => <StatusBadge status={r.status} />,
                },
              ]}
            />
          )}
        </QueryView>
      </Card>
    </>
  );
}
