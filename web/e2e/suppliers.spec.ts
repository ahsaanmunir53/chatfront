import { expect, test } from "@playwright/test";

test("the owner gets one reorder message for the low items", async ({ page }) => {
  await page.goto("/s/gulmohar-lawn");
  await page.getByRole("button", { name: "Try as the owner" }).click();
  await expect(page).toHaveURL(/\/owner$/);

  const panel = page.getByRole("region", { name: "Suppliers" });
  await expect(panel).toContainText("Reorder messages");
  await expect(panel.locator(".supplier-reorder").first()).toContainText("No supplier known yet");
  await expect(panel).toContainText("Once you buy an item from two suppliers");
});
