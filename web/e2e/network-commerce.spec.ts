import { expect, test } from "@playwright/test";

test("owner network workspace can create a branch", async ({ page }) => {
  test.setTimeout(90_000);
  await page.goto("/s/gulmohar-lawn");
  await page.getByRole("button", { name: "Try as the owner" }).click();
  await expect(page).toHaveURL(/\/owner$/);

  const panel = page.getByTestId("network-commerce-panel");
  await expect(panel).toBeVisible();
  await panel.getByTestId("network-branch-name").fill("CI Network Branch");
  await panel.getByTestId("network-branch-code").fill("ci-network-branch");
  await panel.getByTestId("network-create-branch").click();
  await expect(panel).toContainText("CI Network Branch");
});
