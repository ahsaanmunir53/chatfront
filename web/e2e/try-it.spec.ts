import { expect, test } from "@playwright/test";

test("a customer finds a demo shop and chats with it", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Chatfront", level: 1 })).toBeVisible();
  await page.getByRole("link", { name: /Gulmohar Lawn House/ }).click();

  await expect(page.getByRole("heading", { name: "Gulmohar Lawn House" })).toBeVisible();
  await expect(page.getByText("Built with Muse Spark Open")).toBeVisible();
  await page.getByRole("link", { name: "Chat with the shop" }).click();

  await page.getByLabel("Type a message").fill("salam");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.locator(".bubble-in")).toHaveText("salam");
  await expect(page.locator(".bubble-out")).toContainText("Wa alaikum assalam", {
    timeout: 10_000,
  });
});

test("Urdu turns the page right to left and is remembered", async ({ page }) => {
  await page.goto("/s/saffron-kitchen");
  await page.getByRole("button", { name: "اردو" }).click();
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByRole("link", { name: "دکان سے بات کریں" })).toBeVisible();
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
