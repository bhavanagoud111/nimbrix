import { expect, test } from "@playwright/test";

test("status page is public and shows live readiness", async ({ page }) => {
  await page.context().clearCookies();
  await page.goto("/status");
  await expect(page.getByRole("heading", { name: "System status" })).toBeVisible();
  await expect(page.getByText("Simulation mode · no cloud changes")).toBeVisible();
  const readiness = page.getByRole("region", { name: "Service readiness" });
  await expect(readiness.getByText("Ready", { exact: true })).toBeVisible();
  await expect(readiness.getByText("Connected")).toBeVisible();
  await expect(readiness.getByText("Up to date")).toBeVisible();
});

test("unauthenticated users are sent to login with a safe return path", async ({ page }) => {
  await page.context().clearCookies();
  await page.goto("/budgets");
  await expect(page).toHaveURL(/\/login\?next=%2Fbudgets$/);
  await expect(page.getByRole("heading", { name: "Sign in to Nimbrix" })).toBeVisible();
});

test("API is reachable through the same-origin proxy with correlation IDs", async ({ request }) => {
  const response = await request.get("/api/v1/health/live", { headers: { "x-request-id": "e2e-request-0001" } });
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

test("protected API routes fail closed without a session", async ({ request }) => {
  const response = await request.get("/api/v1/costs/summary");
  expect(response.status()).toBe(401);
  expect((await response.json()).error.code).toBe("unauthenticated");
});
