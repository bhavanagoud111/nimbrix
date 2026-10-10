import { Badge, type Tone } from "./primitives";

const statusTones: Record<string, Tone> = {
  open: "warn",
  acknowledged: "accent",
  resolved: "ok",
  dismissed: "neutral",
  pending: "warn",
  approved: "accent",
  executed: "ok",
  rejected: "fail",
  expired: "neutral",
  cancelled: "neutral",
  queued: "neutral",
  running: "accent",
  succeeded: "ok",
  completed: "ok",
  failed: "fail",
  partially_failed: "warn",
  duplicate: "neutral",
  insufficient_evidence: "neutral",
  approval_requested: "accent",
  actioned: "ok",
  superseded: "neutral",
  on_track: "ok",
  warning: "warn",
  at_risk: "warn",
  over_budget: "fail",
  validated: "ok",
  insufficient_data: "neutral",
};

export function StatusBadge({ status }: { status: string }) {
  return <Badge tone={statusTones[status] ?? "neutral"}>{status.replace(/_/g, " ")}</Badge>;
}

const severityTones: Record<string, Tone> = { low: "neutral", medium: "warn", high: "fail" };

export function SeverityBadge({ severity }: { severity: string }) {
  return <Badge tone={severityTones[severity] ?? "neutral"}>{severity}</Badge>;
}

export function RiskBadge({ risk }: { risk: string }) {
  return <Badge tone={severityTones[risk] ?? "neutral"}>{risk} risk</Badge>;
}

export function ConfidenceBadge({ confidence }: { confidence: string }) {
  const tone: Tone = confidence === "high" ? "ok" : confidence === "medium" ? "accent" : "neutral";
  return <Badge tone={tone}>{confidence.replace(/_/g, " ")}</Badge>;
}

/** Visible label for where data or a result came from (demo, live, simulation, model). */
export function ModeBadge({ mode }: { mode: string }) {
  const tone: Tone =
    mode === "live" ? "accent" : mode === "simulation" || mode === "demo" ? "warn" : "neutral";
  const labels: Record<string, string> = {
    demo: "Demo data",
    live: "Live data",
    mixed: "Demo + live data",
    simulation: "Simulation",
    deterministic: "Deterministic analysis",
    none: "No data",
  };
  return (
    <Badge tone={tone} title="Where this data or result comes from">
      {labels[mode] ?? `Model: ${mode}`}
    </Badge>
  );
}
