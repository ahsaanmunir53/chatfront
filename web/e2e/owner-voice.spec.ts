import { expect, test } from "@playwright/test";

test("owner voice mode offers hold-to-talk and can switch back to typing", async ({ page }) => {
  test.setTimeout(90_000);
  await page.goto("/s/gulmohar-lawn");
  await page.getByRole("button", { name: "Try as the owner" }).click();
  await expect(page).toHaveURL(/\/owner$/);

  const assistant = page.getByRole("region", { name: "Your assistant" });
  const toggle = assistant.getByRole("button", { name: "Voice mode" });
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
  await expect(assistant.getByRole("button", { name: "Hold to talk" })).toBeVisible();

  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
  await expect(assistant.getByLabel("Type a message")).toBeVisible();

  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
  await expect(assistant.getByRole("button", { name: "Hold to talk" })).toBeVisible();
});
