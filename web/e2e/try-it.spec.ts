import { expect, test } from "@playwright/test";

test("a customer finds a demo shop and chats with it", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { level: 1, name: /A whole shop inside the chats/ }),
  ).toBeVisible();
  await page.getByRole("link", { name: /Gulmohar Lawn House/ }).click();

  await expect(page.getByRole("heading", { level: 1, name: "Gulmohar Lawn House" })).toBeVisible();
  await expect(page.getByText("Built with Muse Spark Open")).toBeVisible();
  await page.getByRole("link", { name: "Chat here" }).first().click();

  await page.getByLabel("Type a message").fill("salam");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.locator(".bubble-in").first()).toContainText("salam");
  await expect(page.locator(".bubble-out")).toContainText("Wa alaikum assalam", {
    timeout: 10_000,
  });
});

test("the page opens while the server sleeps, and messages wait for it", async ({ page }) => {
  await page.route("**/health", (route) => route.abort());
  await page.goto("/s/indus-mobiles/chat");
  await expect(page.getByRole("status")).toContainText("Waking the server");
  await expect(page.getByText("waking up")).toBeVisible();

  await page.getByRole("button", { name: "Salam!" }).click();
  await expect(page.locator(".bubble-pending")).toContainText("Sends when the server is awake");

  await page.unroute("**/health");
  await expect(page.getByText("online", { exact: true })).toBeVisible({ timeout: 15_000 });
  await expect(page.locator(".bubble-out")).toContainText("Wa alaikum assalam", {
    timeout: 10_000,
  });
});

test("Urdu turns the page right to left and is remembered", async ({ page }) => {
  await page.goto("/s/saffron-kitchen");
  await page.getByRole("button", { name: "اردو" }).click();
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByRole("link", { name: "یہاں چیٹ کریں" }).first()).toBeVisible();
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
});

test("the trust page lists the shop's official channels", async ({ page }) => {
  await page.goto("/s/noor-salon/trust");
  await expect(page.getByRole("heading", { name: "Trust page" })).toBeVisible();
  await expect(page.getByText("Chat on this website")).toBeVisible();
});

test("an unknown shop says so", async ({ page }) => {
  await page.goto("/s/no-such-shop");
  await expect(page.getByRole("alert")).toHaveText("Shop not found");
});
