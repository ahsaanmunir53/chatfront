import { expect, test } from "@playwright/test";

// A busy day in a demo copy (feature 110): simulated customers replay recorded chats with the
// shop, and the owner watches them come in and sees how each chat ended.
test("a demo copy runs a busy day of simulated customers", async ({ page }) => {
  test.setTimeout(150_000);
  await page.goto("/s/gulmohar-lawn");
  await page.getByRole("button", { name: "Try as the owner" }).click();
  await expect(page).toHaveURL(/\/owner$/);

  const busy = page.getByRole("region", { name: "Busy day" });
  await expect(busy).toContainText("No model key yet");
  await busy.getByRole("button", { name: "10 customers" }).click();
  await busy.getByRole("button", { name: "Start a busy day" }).click();
  const status = busy.getByRole("status");
  await expect(status).toContainText("still chatting", { timeout: 15_000 });
  await expect(status).toContainText("The busy day is over: 10 customers sent", {
    timeout: 120_000,
  });
  await expect(status).toContainText(/placed (an order|orders)/);
  await expect(busy.getByRole("button", { name: "Start a busy day" })).toBeVisible();

  // The orders and the model's work show in the console like any other day's.
  await page.reload();
  await expect(page.getByRole("region", { name: /^Orders/ }).locator(".order-row")).not.toHaveCount(
    0,
  );
  await expect(page.getByText("Where the model ran")).toBeVisible();
  await expect(page.getByText("Recorded answers").first()).toBeVisible();
});
