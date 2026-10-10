import { expect, test } from "@playwright/test";

test("root redirects to a live system status page", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/status$/);
  await expect(page.getByRole("heading", { name: "System status" })).toBeVisible();
  await expect(page.getByText("Simulation mode · no cloud connections")).toBeVisible();

  const readiness = page.getByRole("region", { name: "Service readiness" });
  await expect(readiness.getByText("Ready", { exact: true })).toBeVisible();
  await expect(readiness.getByText("Connected")).toBeVisible();
  await expect(readiness.getByText("Up to date")).toBeVisible();
  await expect(readiness.getByText("simulation_only")).toBeVisible();
});

test("API is reachable through the same-origin proxy with correlation IDs", async ({ request }) => {
  const response = await request.get("/api/v1/health/live", {
    headers: { "x-request-id": "e2e-request-0001" },
  });
  expect(response.status()).toBe(200);
  expect(response.headers()["x-request-id"]).toBe("e2e-request-0001");
  expect(await response.json()).toMatchObject({ status: "ok", service: "nimbrix-api" });
});

test("operational endpoints are not exposed through the web origin", async ({ request }) => {
  for (const path of ["/api/v1/metrics", "/api/v1/docs", "/api/v1/openapi.json"]) {
    const response = await request.get(path);
    expect(response.status(), path).toBe(404);
    expect((await response.json()).error.code).toBe("not_found");
  }
});

test("unknown API routes return the standard error envelope", async ({ request }) => {
  const response = await request.get("/api/v1/does-not-exist");
  expect(response.status()).toBe(404);
  const body = await response.json();
  expect(body.error).toMatchObject({ code: "not_found" });
  expect(body.error.request_id).toEqual(expect.any(String));
});
