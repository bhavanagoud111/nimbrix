import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ShareBars } from "@/components/charts/charts";
import { ApiRequestError } from "@/lib/api/client";
import { ModeBadge, StatusBadge } from "./badges";
import { DataTable } from "./table";
import { EmptyState, ErrorState, QueryView } from "./states";

describe("badges", () => {
  it("labels data provenance explicitly", () => {
    render(
      <>
        <ModeBadge mode="demo" />
        <ModeBadge mode="simulation" />
        <ModeBadge mode="anthropic" />
      </>,
    );
    expect(screen.getByText("Demo data")).toBeInTheDocument();
    expect(screen.getByText("Simulation")).toBeInTheDocument();
    expect(screen.getByText("Model: anthropic")).toBeInTheDocument();
  });
  it("humanizes statuses", () => {
    render(<StatusBadge status="approval_requested" />);
    expect(screen.getByText("approval requested")).toBeInTheDocument();
  });
});

describe("ErrorState", () => {
  it("shows permission errors, details, request id, and retry", async () => {
    const retry = vi.fn();
    const error = new ApiRequestError("Role 'viewer' lacks permission", 403, "forbidden", "req-1", [
      { field: "x", message: "detail" },
    ]);
    render(<ErrorState error={error} onRetry={retry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("You do not have permission to view this");
    expect(screen.getByText("request id: req-1")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retry).toHaveBeenCalledOnce();
  });
});

describe("QueryView", () => {
  const base = { isPending: false, isError: false, error: null, refetch: vi.fn() };
  it("renders loading, empty, and data states", () => {
    const { rerender } = render(
      <QueryView query={{ ...base, isPending: true, data: undefined }}>{() => "data"}</QueryView>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    rerender(
      <QueryView
        query={{ ...base, data: [] as number[] }}
        isEmpty={(d) => d.length === 0}
        empty={<EmptyState title="Nothing" />}
      >
        {() => "data"}
      </QueryView>,
    );
    expect(screen.getByText("Nothing")).toBeInTheDocument();
    rerender(
      <QueryView query={{ ...base, data: [1] }} isEmpty={(d) => d.length === 0}>
        {(d) => `rows: ${d.length}`}
      </QueryView>,
    );
    expect(screen.getByText("rows: 1")).toBeInTheDocument();
  });
});

describe("DataTable", () => {
  it("renders an accessible table and handles row clicks", async () => {
    const onRowClick = vi.fn();
    render(
      <DataTable
        caption="Things"
        rows={[{ id: "a", n: 1 }]}
        rowKey={(r) => r.id}
        onRowClick={onRowClick}
        columns={[{ key: "n", header: "N", render: (r) => r.n, align: "right" }]}
      />,
    );
    expect(screen.getByRole("table", { name: "Things" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("cell", { name: "1" }));
    expect(onRowClick).toHaveBeenCalledWith({ id: "a", n: 1 });
  });
});

describe("ShareBars", () => {
  it("drills down on selection with an accessible label", async () => {
    const onSelect = vi.fn();
    render(
      <ShareBars
        currency="USD"
        onSelect={onSelect}
        items={[{ key: "AmazonEC2", amount: "100", share_pct: "60.00" }]}
      />,
    );
    const bar = screen.getByRole("button", { name: "AmazonEC2: $100.00, 60.00% of total" });
    await userEvent.click(bar);
    expect(onSelect).toHaveBeenCalledWith("AmazonEC2");
  });
});
