"use client";

import { ForecastChart } from "@/components/charts/charts";
import { StatusBadge } from "@/components/ui/badges";
import { Button, Card, Field, PageHeader, Stat, inputClass } from "@/components/ui/primitives";
import { DataTable } from "@/components/ui/table";
import { EmptyState, ErrorState, QueryView } from "@/components/ui/states";
import { api, unwrap } from "@/lib/api/client";
import { useCan, useJob } from "@/lib/api/hooks";
import { money, shortDate, titleCase } from "@/lib/format";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

type ScopeType = "total" | "service" | "account" | "team" | "environment" | "region";
const FILTER_KEY: Record<ScopeType, string> = {
  total: "",
  service: "service",
  account: "account_id",
  team: "team",
  environment: "environment",
  region: "region",
};

export default function ForecastsPage() {
  const queryClient = useQueryClient();
  const canRun = useCan("analysis:run");
  const [scopeType, setScopeType] = useState<ScopeType>("total");
  const [scopeValue, setScopeValue] = useState("total");
  const [horizon, setHorizon] = useState<7 | 30>(30);
  const dims = useQuery({
    queryKey: ["costs", "dimensions"],
    queryFn: async () => unwrap(await api.GET("/api/v1/costs/dimensions")),
  });
  const forecasts = useQuery({
    queryKey: ["forecasts", scopeType, scopeValue, horizon],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/forecasts", {
          params: {
            query: { scope_type: scopeType, scope_value: scopeValue, horizon_days: horizon },
          },
        }),
      ),
  });
  const forecast = forecasts.data?.items[0];
  const actuals = useQuery({
    queryKey: ["forecasts", "actuals", scopeType, scopeValue, forecast?.training_end],
    enabled: Boolean(forecast?.training_end),
    queryFn: async () => {
      const end = forecast!.training_end!;
      const start = new Date(`${end}T00:00:00Z`);
      start.setUTCDate(start.getUTCDate() - 59);
      const query: Record<string, string> = {
        start: start.toISOString().slice(0, 10),
        end,
        currency: forecast!.currency,
      };
      if (FILTER_KEY[scopeType]) query[FILTER_KEY[scopeType]] = scopeValue;
      return unwrap(await api.GET("/api/v1/costs/timeseries", { params: { query } }));
    },
  });
  const run = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/api/v1/forecasts/run", {
          body: {
            scope_type: scopeType,
            scope_value: scopeValue,
            horizon_days: horizon,
            currency: "USD",
            interval_level: 0.8,
          },
        }),
      ),
  });
  const job = useJob(run.data?.id);
  useEffect(() => {
    if (job.data?.state === "succeeded")
      void queryClient.invalidateQueries({ queryKey: ["forecasts"] });
  }, [job.data?.state, queryClient]);

  const scopeOptions: Record<ScopeType, string[]> = {
    total: ["total"],
    service: dims.data?.service ?? [],
    account: dims.data?.account ?? [],
    team: (dims.data?.team ?? []).filter((t) => t !== "(unallocated)"),
    environment: (dims.data?.environment ?? []).filter((t) => t !== "(unallocated)"),
    region: dims.data?.region ?? [],
  };
  const models = Object.entries(
    (forecast?.metrics.models as Record<string, Record<string, number | null>> | undefined) ?? {},
  );

  return (
    <>
      <PageHeader
        title="Forecasting"
        description="7/30-day spend forecasts with prediction intervals. Models are validated by rolling-origin backtests against a seasonal-naive baseline."
      />
      <Card title="Scope" id="scope">
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Scope type">
            <select
              className={inputClass}
              value={scopeType}
              onChange={(e) => {
                const t = e.target.value as ScopeType;
                setScopeType(t);
                setScopeValue(t === "total" ? "total" : (scopeOptions[t][0] ?? ""));
              }}
            >
              {(Object.keys(FILTER_KEY) as ScopeType[]).map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Scope value">
            <select
              className={inputClass}
              value={scopeValue}
              onChange={(e) => setScopeValue(e.target.value)}
              disabled={scopeType === "total"}
            >
              {scopeOptions[scopeType].map((v) => (
                <option key={v}>{v}</option>
              ))}
            </select>
          </Field>
          <Field label="Horizon">
            <select
              className={inputClass}
              value={horizon}
              onChange={(e) => setHorizon(Number(e.target.value) as 7 | 30)}
            >
              <option value={7}>7 days</option>
              <option value={30}>30 days</option>
            </select>
          </Field>
          {canRun && (
            <Button
              variant="primary"
              onClick={() => run.mutate()}
              loading={
                run.isPending ||
                (Boolean(job.data) && !["succeeded", "failed"].includes(job.data!.state))
              }
            >
              Run forecast
            </Button>
          )}
          {job.data && <span className="text-xs text-muted">job {job.data.state}</span>}
        </div>
        {run.isError && <ErrorState error={run.error} />}
      </Card>

      <QueryView
        query={forecasts}
        isEmpty={(d) => d.items.length === 0}
        empty={
          <div className="mt-4">
            <EmptyState title="No forecast for this scope yet">
              Run one above (analysts and admins).
            </EmptyState>
          </div>
        }
      >
        {() =>
          forecast && (
            <>
              {forecast.status !== "succeeded" ? (
                <div className="mt-4">
                  <EmptyState title={`Forecast status: ${forecast.status.replace(/_/g, " ")}`}>
                    {forecast.message}
                  </EmptyState>
                </div>
              ) : (
                <>
                  <dl className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-4">
                    <Stat
                      label={`Forecast, next ${forecast.horizon_days} days`}
                      value={money(
                        forecast.points.reduce((s, p) => s + Number(p.yhat), 0),
                        forecast.currency,
                      )}
                    />
                    <Stat
                      label="Model"
                      value={titleCase(forecast.model_name)}
                      sub={forecast.model_version}
                    />
                    <Stat
                      label="Interval"
                      value={`${Math.round(forecast.interval_level * 100)}%`}
                      sub={String(forecast.metrics.interval_method ?? "")}
                    />
                    <Stat
                      label="Trained through"
                      value={shortDate(forecast.training_end)}
                      sub={`${forecast.metrics.history_days ?? "?"} days of history`}
                    />
                  </dl>
                  <Card title="Actuals and forecast" className="mt-4" id="forecast-chart">
                    {forecast.message && (
                      <p className="mb-2 text-xs text-warn">{forecast.message}</p>
                    )}
                    <ForecastChart
                      actuals={actuals.data?.series[0]?.points ?? []}
                      forecast={forecast.points}
                      currency={forecast.currency}
                    />
                  </Card>
                </>
              )}
              <Card title="Backtest metrics" className="mt-4" id="metrics">
                {models.length === 0 ? (
                  <p className="text-sm text-muted">No backtest metrics (history too short).</p>
                ) : (
                  <DataTable
                    caption="Model backtest comparison"
                    rows={models}
                    rowKey={([name]) => name}
                    columns={[
                      {
                        key: "model",
                        header: "Model",
                        render: ([name]) => (
                          <>
                            {titleCase(name)}{" "}
                            {name === forecast.metrics.selected_model && (
                              <StatusBadge status="succeeded" />
                            )}
                          </>
                        ),
                      },
                      { key: "folds", header: "Folds", align: "right", render: ([, m]) => m.folds },
                      {
                        key: "mae",
                        header: "MAE",
                        align: "right",
                        render: ([, m]) => money(m.mae, forecast.currency),
                      },
                      {
                        key: "wape",
                        header: "WAPE",
                        align: "right",
                        render: ([, m]) =>
                          m.wape === null ? "—" : `${(Number(m.wape) * 100).toFixed(2)}%`,
                      },
                      {
                        key: "cov",
                        header: "Interval coverage",
                        align: "right",
                        render: ([, m]) =>
                          m.interval_coverage === null
                            ? "—"
                            : `${(Number(m.interval_coverage) * 100).toFixed(1)}%`,
                      },
                    ]}
                  />
                )}
                <p className="mt-2 text-xs text-muted">
                  {String(forecast.metrics.selection_rule ?? "")}
                </p>
              </Card>
            </>
          )
        }
      </QueryView>
    </>
  );
}
