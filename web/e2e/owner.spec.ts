import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

// A 2x2 PNG: enough for the server to store, shrink and show.
const PHOTO = {
  name: "lawn.png",
  mimeType: "image/png",
  buffer: Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR4nGP8sMWGgYGBiYGBgYGBAQAXXgHkraOxLgAAAABJRU5ErkJggg==",
    "base64",
  ),
};

/** Wait for the newest reply, then tap its first button (Confirm, Apply, Make my shop). */
async function tapFirstButton(page: Page, replyText: RegExp | string) {
  const reply = page.locator(".bubble-out").filter({ hasText: replyText }).last();
  await expect(reply).toBeVisible({ timeout: 15_000 });
  await reply.locator("button:not([disabled])").first().click();
}

async function say(page: Page, text: string) {
  await page.getByLabel("Type a message").fill(text);
  await page.getByRole("button", { name: "Send", exact: true }).click();
}

test("a visitor runs a demo shop as its owner: a product from a photo, a new price", async ({
  page,
}) => {
  test.setTimeout(90_000);
  await page.goto("/s/gulmohar-lawn");
  await expect(page.getByRole("heading", { level: 3, name: "Printed lawn 3-piece" })).toBeVisible();
  await expect(page.locator(".product").filter({ hasText: "Printed lawn 3-piece" })).toContainText(
    "Rs 4,850",
  );

  await page.getByRole("button", { name: "Try as the owner" }).click();
  await expect(page).toHaveURL(/\/owner$/);
  await expect(page.getByRole("heading", { level: 1, name: "Gulmohar Lawn House" })).toBeVisible();
  await expect(page.getByText("This is your own copy of a demo shop")).toBeVisible();
  const desktop = (page.viewportSize()?.width ?? 0) >= 960;
  const showProducts = async () => {
    if (!desktop) await page.getByRole("tab", { name: "Products" }).click();
  };
  const showChat = async () => {
    if (!desktop) await page.getByRole("tab", { name: "Your assistant" }).click();
  };
  await showProducts();
  await expect(page.locator(".khata-entry")).toHaveCount(4);

  // A photo, with the typed words as its caption: the card waits for Confirm.
  await showChat();
  await page.getByLabel("Type a message").fill("Lawn suit 4850");
  const assistant = page.getByRole("region", { name: "Your assistant" });
  await assistant.locator('input[type="file"]').setInputFiles(PHOTO);
  await expect(page.locator(".bubble-in .bubble-photo")).toBeVisible();
  await tapFirstButton(page, "Lawn suit: Rs 4,850");
  await expect(page.locator(".bubble-out").last()).toContainText("Lawn suit", { timeout: 15_000 });
  await showProducts();
  await expect(page.locator(".khata-entry")).toHaveCount(5);
  await expect(page.locator(".khata-entry").filter({ hasText: "Lawn suit" })).toContainText(
    "Rs 4,850",
  );

  // A typed price change is shown as before and after, and saved on Apply.
  await showChat();
  await say(page, "Linen kurta 2900");
  await tapFirstButton(page, "Linen kurta: Rs 2,750 → Rs 2,900");
  await showProducts();
  await expect(page.locator(".khata-entry").filter({ hasText: "Linen kurta" })).toContainText(
    "Rs 2,900",
    { timeout: 15_000 },
  );

  // Customers see the new product on the shop page.
  await page.getByRole("link", { name: "Open shop page" }).click();
  await expect(page.getByRole("heading", { level: 3, name: "Lawn suit" })).toBeVisible();
});

test("a new shop is made by chatting on the start page", async ({ page }) => {
  test.setTimeout(120_000);
  const name = `Noor Fabrics ${Date.now() % 100_000}`;
  await page.goto("/start");
  await say(page, "Hello");
  await expect(page.locator(".bubble-out").last()).toContainText("make your shop", {
    timeout: 15_000,
  });
  await say(page, name);
  await page.getByRole("button", { name: "Clothing and fashion" }).click({ timeout: 15_000 });
  await expect(page.locator(".bubble-out").last()).toContainText("Which city", {
    timeout: 15_000,
  });
  await say(page, "Multan");
  await expect(page.locator(".bubble-out").last()).toContainText("Where do you deliver", {
    timeout: 15_000,
  });
  await say(page, "All over Pakistan, 3 days");
  await expect(page.locator(".bubble-out").last()).toContainText("How do customers pay", {
    timeout: 15_000,
  });
  await say(page, "JazzCash and cash on delivery");
  await expect(page.locator(".bubble-out").last()).toContainText("send a photo", {
    timeout: 15_000,
  });
  await say(page, "done");
  await tapFirstButton(page, name);
  await expect(page.locator(".bubble-out").last()).toContainText("Your shop is ready", {
    timeout: 15_000,
  });

  // The private console link is a button, not a token on screen.
  await page.getByRole("link", { name: "Open your shop console" }).click();
  await expect(page).toHaveURL(/\/owner$/);
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
  // The new shop's page is public, and the owner can get back to the console from any page.
  await page.getByRole("link", { name: "Open shop page" }).click();
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
  await page.getByRole("link", { name: "Owner console" }).click();
  await expect(page).toHaveURL(/\/owner$/);
});

test("the console without a session points to the ways in", async ({ page }) => {
  await page.goto("/owner");
  await expect(page.getByRole("heading", { level: 1, name: "Your shop's console" })).toBeVisible();
  await page.getByRole("link", { name: "Make your shop" }).click();
  await expect(page).toHaveURL(/\/start$/);
});
