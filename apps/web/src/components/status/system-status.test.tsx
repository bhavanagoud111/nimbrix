import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { jsonResponse, readiness } from "@/test/fixtures";
import { SystemStatus } from "./system-status";

function renderWithClient() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SystemStatus />
    </QueryClientProvider>,
  );
}

describe("SystemStatus", () => {
  it("shows a loading state, then healthy services", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(readiness())));
    renderWithClient();

    expect(screen.getByText("Checking services…")).toBeInTheDocument();
    expect(await screen.findByText("Ready")).toBeInTheDocument();
    expect(screen.getByText("Connected")).toBeInTheDocument();
    expect(screen.getByText("Up to date")).toBeInTheDocument();
    expect(screen.getByText("simulation_only")).toBeInTheDocument();
    expect(screen.getByText("0001_create_tenants / 0001_create_tenants")).toBeInTheDocument();
  });

  it("shows pending migrations when the API is not ready", async () => {
    const body = readiness({
      status: "not_ready",
      checks: {
        database: { status: "ok", latency_ms: 2, detail: null },
        migrations: {
          status: "fail",
          current_revision: null,
          expected_revision: "0001_create_tenants",
        },
      },
    });
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(body, 503)));
    renderWithClient();

    expect(await screen.findByText("Not ready")).toBeInTheDocument();
    expect(screen.getByText("Pending")).toBeInTheDocument();
    expect(screen.getByText("none / 0001_create_tenants")).toBeInTheDocument();
  });

  it("shows an error with retry when the API is unreachable", async () => {
    const envelope = {
      error: {
        code: "upstream_unavailable",
        message: "The Nimbrix API is unreachable",
        request_id: null,
        details: [],
      },
    };
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(envelope, 502, { "x-request-id": "req-12345678" }))
      .mockResolvedValueOnce(jsonResponse(readiness()));
    vi.stubGlobal("fetch", fetchMock);
    renderWithClient();

    expect(await screen.findByRole("alert")).toHaveTextContent("API unreachable");
    expect(screen.getByText("request id: req-12345678")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Retry now" }));
    expect(await screen.findByText("Ready")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
