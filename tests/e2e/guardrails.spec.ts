import { expect, test } from "@playwright/test";
import { login } from "./helpers";

test("policy denies stopping a production instance", async ({ page }) => {
  await login(page, "analyst@acme.demo");
  await page.goto("/resources");
  await page.getByLabel("Search").fill("payments-batch-01");
  await page.getByLabel("Type").selectOption("ec2_instance");
  await page.getByRole("row", { name: /payments-batch-01/ }).click();
  await page.getByRole("button", { name: "Check policy" }).click();
  await expect(page.getByText("Policy denies this request:")).toBeVisible();
  await expect(page.getByText("environment tag is prod")).toBeVisible();
  await page.getByRole("button", { name: "Request approval" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "violates remediation policy" })).toBeVisible();
});

test("requesters cannot approve their own requests", async ({ page }) => {
  await login(page, "admin@acme.demo");
  await page.goto("/resources");
  await page.getByLabel("Search").fill("data-notebook-dev");
  await page.getByLabel("Type").selectOption("ec2_instance");
  await page.getByRole("row", { name: /data-notebook-dev/ }).click();
  await page.getByRole("button", { name: "Request approval" }).click();
  await expect(page).toHaveURL(/\/approvals\//);
  await expect(page.getByText(/separation of duties requires a different approver/)).toBeVisible();
  await page.getByRole("button", { name: "Approve simulation" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Requesters cannot approve their own requests" })).toBeVisible();
});

test("viewers get read-only screens", async ({ page }) => {
  await login(page, "viewer@acme.demo");
  await expect(page.getByRole("link", { name: "Audit Timeline" })).toHaveCount(0);
  await page.goto("/approvals?status=pending");
  await page.goto("/budgets");
  await expect(page.getByText("New monthly budget")).toHaveCount(0);
  const denied = await page.request.post("/api/v1/imports/demo", { data: {}, headers: { "Idempotency-Key": "e2e-viewer-0001" } });
  expect(denied.status()).toBe(403);
});

test("backend failures show an error state with retry", async ({ page }) => {
  await login(page, "viewer@acme.demo");
  await page.route("**/api/v1/costs/summary**", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      headers: { "x-request-id": "e2e-failure-0001" },
      body: JSON.stringify({ error: { code: "service_unavailable", message: "Database unavailable", request_id: "e2e-failure-0001", details: [] } }),
    }),
  );
  await page.goto("/costs");
  const alert = page.getByRole("alert").filter({ hasText: "Could not load data" }).first();
  await expect(alert).toContainText("Could not load data");
  await expect(alert).toContainText("Database unavailable");
  await expect(alert).toContainText("request id: e2e-failure-0001");
  await page.unroute("**/api/v1/costs/summary**");
  await alert.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByText(/^Total /)).toBeVisible();
});
