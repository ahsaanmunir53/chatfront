import { expect, test } from "@playwright/test";

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

test("a customer books a time in the chat and the owner cancels it", async ({ browser, page }) => {
  test.setTimeout(90_000);
  await page.goto("/s/noor-salon");
  await page.getByRole("button", { name: "Try as the owner" }).click();
  await expect(page).toHaveURL(/\/owner$/);
  const shopPath =
    (await page.getByRole("link", { name: "Open shop page" }).getAttribute("href")) ?? "";

  // Open every day, all day, so tomorrow always has times.
  const panel = page.getByRole("region", { name: /Appointments/ });
  for (const day of DAYS) await panel.getByLabel(day, { exact: true }).fill("00:00-24:00");
  await panel.getByRole("button", { name: "Save hours" }).click();
  await expect(panel.getByRole("status")).toHaveText("Saved");

  const buyer = await (await browser.newContext({ locale: "en-US" })).newPage();
  await buyer.goto(shopPath);
  await buyer.getByRole("link", { name: "Chat here" }).first().click();
  await buyer.getByRole("textbox").fill("haircut kal");
  await buyer.getByRole("textbox").press("Enter");
  const times = buyer.locator(".bubble-list").last();
  await expect(times.getByRole("button").first()).toBeVisible({ timeout: 15_000 });
  await times.getByRole("button").first().click();
  const booked = buyer.locator(".bubble-out", { hasText: /B[A-Z0-9]{5}/ }).last();
  await expect(booked).toContainText("Haircut and blow-dry", { timeout: 15_000 });

  // The owner sees it and cancels it; the customer is told.
  await page.reload();
  const row = page
    .getByRole("region", { name: /Appointments/ })
    .locator(".booking-row")
    .first();
  await expect(row).toContainText("Haircut and blow-dry");
  await row.getByRole("button", { name: "Cancel and tell the customer" }).click();
  await expect(row).toContainText("Cancelled");
  await expect(buyer.locator(".bubble-out").last()).toContainText(/cancel/i, { timeout: 15_000 });
});
