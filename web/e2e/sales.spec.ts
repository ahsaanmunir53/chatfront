import { expect, test } from "@playwright/test";

test("the owner starts a live sale and a customer takes a piece by writing its number", async ({
  browser,
  page,
}) => {
  test.setTimeout(90_000);
  await page.goto("/s/gulmohar-lawn");
  await page.getByRole("button", { name: "Try as the owner" }).click();
  await expect(page).toHaveURL(/\/owner$/);
  const shopPath =
    (await page.getByRole("link", { name: "Open shop page" }).getAttribute("href")) ?? "";

  const sales = page.getByRole("region", { name: "Flash and live sales" });
  await sales.getByLabel("Name", { exact: true }).fill("Friday live");
  await sales.getByRole("checkbox", { name: /Live sale/ }).check();
  await sales.getByRole("checkbox", { name: /Printed lawn 3-piece/ }).check();
  await sales.getByLabel("Sale price").fill("3999");
  await sales.getByLabel("Pieces").fill("1");
  await sales.getByRole("button", { name: "Start the sale" }).click();
  await expect(sales.locator(".sale-card").first()).toContainText("Friday live");
  await expect(sales.locator(".sale-card").first()).toContainText("1 / 1");

  // The shop page shows the sale; the customer writes the number in the chat.
  const buyer = await (await browser.newContext({ locale: "en-US" })).newPage();
  await buyer.goto(shopPath);
  const lawn = buyer.locator(".product", { hasText: "Printed lawn 3-piece" });
  await expect(lawn.locator(".product-sale")).toContainText("Live #1", { timeout: 20_000 });
  await expect(lawn.locator(".product-sale")).toContainText("Rs 3,999");
  await buyer.getByRole("link", { name: "Chat here" }).click();
  await buyer.getByRole("textbox").fill("1");
  await buyer.getByRole("textbox").press("Enter");
  const choice = buyer.locator(".bubble-out").last();
  await expect(choice).toContainText("#1, Printed lawn 3-piece", { timeout: 15_000 });
  await buyer.getByRole("button", { name: "Rust" }).click();
  const taken = buyer.locator(".bubble-out").last();
  await expect(taken).toContainText("Printed lawn 3-piece (Rust)", { timeout: 15_000 });
  await expect(taken).toContainText("Rs 3,999");

  // The owner sees the piece held in a cart.
  await page.reload();
  const card = page
    .getByRole("region", { name: "Flash and live sales" })
    .locator(".sale-card")
    .first();
  await expect(card).toContainText("0 / 1");
  await card.getByRole("button", { name: "Stop the sale" }).click();
  await expect(card).toContainText("Ended");
});
