import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

async function say(page: Page, text: string) {
  await page.getByLabel("Type a message").fill(text);
  await page.getByRole("button", { name: "Send", exact: true }).click();
}

async function switchToTyping(page: Page) {
  const toggle = page.getByRole("region", { name: "Your assistant" }).getByRole("button", {
    name: "Voice mode",
  });
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
  await toggle.click();
}

const SHEET = ["Item,Colour,Qty,Rate", "Linen kurta,L,6,1900", "Silk saree,,2,"].join("\n");

test("stock comes in by chat, form and sheet; the price is settled; a batch is thrown away", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await page.goto("/s/gulmohar-lawn");
  await page.getByRole("button", { name: "Try as the owner" }).click();
  await expect(page).toHaveURL(/\/owner$/);

  await switchToTyping(page);

  // By chat: the card shows before and after, and nothing changes until it is confirmed.
  await say(page, "rust ke 20 aa gaye, 2700 ka ek");
  const card = page.locator(".bubble-out").filter({ hasText: "4 + 20 = 24" }).last();
  await expect(card).toBeVisible({ timeout: 15_000 });
  await card.getByRole("button", { name: /Add stock|Stock daalein/ }).click();
  await expect(page.locator(".bubble-out").last()).toContainText(/1/, { timeout: 15_000 });

  // The new suits cost less than the ones on hand: the owner sets the price in the alerts.
  const alerts = page.getByRole("region", { name: /^Alerts/ });
  const question = alerts.locator(".waiting-item", { hasText: /2,700/ });
  await expect(question).toBeVisible({ timeout: 20_000 });
  await question.getByRole("button", { name: /New price now|Nayi qeemat abhi/ }).click();
  await question.getByLabel("New price").fill("4,600");
  await question.getByRole("button", { name: "Save price" }).click();
  await expect(question.getByRole("status")).toContainText("Rs 4,600");
  await question.getByRole("button", { name: "Done" }).click();
  await expect(question).toBeHidden();

  // By the console's form, with a cost and an expiry date.
  const stock = page.getByRole("region", { name: "Stock", exact: true });
  const form = stock.locator("form", { hasText: "Stock came in" });
  await form.getByLabel("Product").selectOption({ label: "Printed lawn 3-piece (Sea green)" });
  await form.getByLabel("Pieces", { exact: true }).fill("5");
  await form.getByLabel("Cost of these pieces").fill("15,000");
  await form.getByLabel("Expires on").fill("2027-03-31");
  await form.getByLabel("Supplier", { exact: true }).fill("Al-Noor Textiles");
  await form.getByLabel("Supplier's WhatsApp number").fill("0300 1112223");
  await form.getByRole("button", { name: "Add stock" }).click();
  await expect(form.getByRole("status")).toHaveText("1 line added to stock.");
  const batch = stock.locator(".batch", { hasText: "Printed lawn 3-piece (Sea green)" }).first();
  await expect(batch).toContainText("5 of 5 left");
  await expect(batch).toContainText("Rs 3,000 each");
  await expect(batch).toContainText("From Al-Noor Textiles");

  // From a supplier's sheet: matched rows go in, the rest are left out.
  const sheet = stock.locator(".sheet-upload");
  await sheet.locator('input[type="file"]').setInputFiles({
    name: "challan.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(SHEET),
  });
  await expect(sheet.locator(".sheet-rows li")).toHaveCount(2);
  await expect(sheet.locator(".sheet-rows li.is-unready")).toContainText("Silk saree");
  await sheet.getByRole("button", { name: "Add 1 line" }).click();
  await expect(sheet.getByRole("status")).toHaveText("1 line added to stock.");
  await expect(stock.locator(".batch", { hasText: "Linen kurta (L)" }).first()).toContainText(
    "6 of 6 left",
  );

  // Damaged pieces are thrown away; the loss is in the books.
  await batch.getByRole("button", { name: "Discard" }).click();
  await batch.getByLabel("Why").selectOption({ label: "Damaged" });
  await batch.getByRole("button", { name: "Discard 5 pieces" }).click();
  await expect(batch).toContainText("Discarded");
  const daybook = page.getByRole("region", { name: "Books" }).locator(".daybook");
  await expect(daybook.getByRole("row", { name: /^Thrown away/ })).toContainText("Rs 15,000");
  await expect(stock.getByRole("heading", { name: "Profit by product" })).toBeVisible();
});
