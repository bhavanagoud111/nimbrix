import { expect, test } from "@playwright/test";
import { login } from "./helpers";

// One continuous story, as in docs/demo.md: spike -> anomaly -> investigation ->
// recommendation -> simulation -> approval -> simulated execution -> audit.
test.describe.configure({ mode: "serial" });

let approvalUrl = "";

test("analyst sees the spike, investigates it, and requests a simulated stop", async ({ page }) => {
  await login(page, "analyst@acme.demo");
  await expect(page.getByText("Demo data").first()).toBeVisible();
  await expect(page.getByText("Spend, last 30 days")).toBeVisible();

  await page.getByRole("link", { name: "Anomalies" }).first().click();
  await page.getByRole("row", { name: /service AmazonEC2/ }).click();
  await expect(page.getByRole("heading", { name: "service: AmazonEC2" })).toBeVisible();
  await expect(page.getByText("Top contributing resources")).toBeVisible();

  await page.getByRole("button", { name: "Investigate" }).click();
  await expect(page).toHaveURL(/\/investigations\//);
  await expect(page.getByText("completed", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("Root cause")).toBeVisible();
  await expect(page.getByText(/8 new c5\.4xlarge resources/).first()).toBeVisible();
  await expect(page.getByText("Deterministic analysis", { exact: true }).first()).toBeVisible();

  await page.getByRole("link", { name: "Recommendations" }).first().click();
  await page.getByRole("row", { name: /dev-build-runner-legacy/ }).first().click();
  await expect(page.getByRole("heading", { name: /dev-build-runner-legacy/ })).toBeVisible();
  await page.getByRole("button", { name: "Simulate change" }).click();
  await expect(page.getByText("Nothing was changed. Not a realized saving.")).toBeVisible();
  await expect(page.getByText("$280.32").first()).toBeVisible();

  await page.getByRole("link", { name: "Resource Inventory" }).click();
  await page.getByLabel("Search").fill("dev-build-runner-legacy");
  await page.getByLabel("Type").selectOption("ec2_instance");
  await page.getByRole("row", { name: /dev-build-runner-legacy/ }).click();
  await page.getByLabel("Reason").fill("idle for 14 days, owner confirmed");
  await page.getByRole("button", { name: "Check policy" }).click();
  await expect(page.getByText("Policy allows this request.")).toBeVisible();
  await page.getByRole("button", { name: "Request approval" }).click();
  await expect(page).toHaveURL(/\/approvals\//);
  await expect(page.getByText("pending", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Waiting for an approver. Your role cannot decide.")).toBeVisible();
  approvalUrl = page.url();
});

test("approver reviews the exact payload, approves, and runs the simulation", async ({ page }) => {
  expect(approvalUrl).not.toBe("");
  await login(page, "approver@acme.demo");
  await page.goto(approvalUrl);
  await expect(page.getByText("Exact action payload")).toBeVisible();
  await expect(page.getByText(/payload SHA-256/)).toBeVisible();
  await expect(page.getByText("Policy outcome: allowed")).toBeVisible();
  await page.getByRole("button", { name: "Approve simulation" }).click();
  await expect(page.getByText("approved", { exact: true }).first()).toBeVisible();
  await page.getByRole("button", { name: "Execute simulation" }).click();
  const outcome = page.getByRole("region", { name: "Execution outcome" });
  await expect(outcome.getByText("verified", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(outcome.getByText("stopped", { exact: true })).toBeVisible();
  await expect(outcome.getByText("simulated — not realized")).toBeVisible();
  const trail = page.getByRole("region", { name: "Audit trail" });
  await expect(trail.getByText("approval.requested")).toBeVisible();
  await expect(trail.getByText("approval.approved")).toBeVisible();
  await expect(trail.getByText("remediation.succeeded")).toBeVisible();
});

test("auditor sees the immutable timeline", async ({ page }) => {
  await login(page, "auditor@acme.demo");
  await page.getByRole("link", { name: "Audit Timeline" }).click();
  await page.getByLabel("Event type").selectOption("remediation.");
  await expect(page.getByText("remediation.succeeded").first()).toBeVisible();
});
