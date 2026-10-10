import { expect, type Page } from "@playwright/test";

export const PASSWORD = process.env.NIMBRIX_DEMO_PASSWORD ?? "nimbrix-demo-password";

export async function login(page: Page, email: string): Promise<void> {
  await page.context().clearCookies();
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}
