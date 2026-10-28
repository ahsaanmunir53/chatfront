import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

/** A fresh copy of the lawn shop, so its stock is the template's every run. */
async function shopCopy(page: Page): Promise<string> {
  await page.goto("/s/gulmohar-lawn");
  await page.getByRole("button", { name: "Try as the owner" }).click();
  await expect(page).toHaveURL(/\/owner$/);
  return (await page.getByRole("link", { name: "Open shop page" }).getAttribute("href")) ?? "";
}

async function fillDetails(page: Page, address: string, landmark = "") {
  await page.getByLabel("Your name").fill("Ayesha Khan");
  await page.getByLabel("Phone number").fill("0300 1234567");
  await page.getByLabel("City").fill("Lahore");
  await page.getByLabel("House and street").fill(address);
  await page.getByLabel("Area, block or society").fill("Gulberg III");
  await page.getByLabel("A landmark nearby (if any)").fill(landmark);
}

test("a buyer fills a cart on the shop page and places the order there", async ({
  browser,
  page,
}) => {
  test.setTimeout(90_000);
  const shopPath = await shopCopy(page);
  const buyer = await (await browser.newContext({ locale: "en-US" })).newPage();
  await buyer.goto(shopPath);
  const lawn = buyer.locator(".product", { hasText: "Printed lawn 3-piece" });
  const add = lawn.getByRole("button", { name: /Add to cart/ });
  await expect(add).toBeVisible({ timeout: 20_000 });
  await lawn.getByRole("combobox").selectOption({ label: "Rust" });
  await add.click();
  await add.click();
  await expect(lawn.getByRole("status")).toHaveText("In your cart: 2");
  await buyer.getByRole("link", { name: "Check out" }).click();

  const cart = buyer.locator(".checkout-cart");
  await expect(cart).toContainText("Printed lawn 3-piece Rust");
  await expect(cart).toContainText("Rs 9,700");
  await fillDetails(buyer, "House 12, Street 4");
  await expect(cart.locator(".checkout-total")).toContainText("Rs 9,850");
  await buyer.getByRole("radio", { name: "Cash on delivery" }).check();
  await buyer.getByRole("button", { name: "Place order" }).click();
  const placed = buyer.getByRole("heading", { name: /^Order \w+ is placed$/ });
  await expect(placed).toBeVisible({ timeout: 15_000 });
  const code = /Order (\w+) is placed/.exec((await placed.textContent()) ?? "")?.[1] ?? "";
  await expect(buyer.locator(".checkout-message")).toContainText(code);

  // The same instructions wait in the buyer's chat with the shop.
  await buyer.getByRole("link", { name: "Open the chat with the shop" }).click();
  await expect(buyer.locator(".bubble-out").last()).toContainText(code);

  // And the owner sees it, placed from the shop page.
  await page.reload();
  const glance = page.getByRole("region", { name: "At a glance" });
  await expect(glance.locator(".dash-hero-number")).toHaveText("1");
  await expect(glance.locator(".dash-sources")).toContainText("Shop page 1");
});

test("an Instagram shop link lands on the checkout with its cart", async ({ browser, page }) => {
  test.setTimeout(90_000);
  const shopPath = await shopCopy(page);
  const slug = shopPath.split("/").pop() ?? "";
  const products = await (await page.request.get(`/api/shops/${slug}/products`)).json();
  const dupatta = products.find((p: { name: string }) => p.name === "Embroidered chiffon dupatta")
    .variants[0].catalog_id;
  const buyer = await (await browser.newContext({ locale: "en-US" })).newPage();
  await buyer.goto(
    `${shopPath}/checkout?products=${encodeURIComponent(`${dupatta}:3`)}&cart_origin=instagram`,
  );
  await expect(buyer.getByText("Your cart from the shop's Instagram")).toBeVisible();
  await expect(buyer.locator(".checkout-cart")).toContainText("Embroidered chiffon dupatta");
  await expect(buyer.locator(".checkout-qty output")).toHaveText("3");
  await buyer.getByRole("button", { name: "One less Embroidered chiffon dupatta" }).click();
  await expect(buyer.locator(".checkout-qty output")).toHaveText("2");

  // Too vague for a rider: said under the area, and nothing is placed.
  await fillDetails(buyer, "near the market");
  await buyer.getByRole("radio", { name: "Bank or wallet transfer" }).check();
  await buyer.getByRole("button", { name: "Place order" }).click();
  await expect(buyer.locator(".field-error")).toHaveText(
    "Add the area, and a house number or a landmark, so the rider can find it.",
  );
  await fillDetails(buyer, "near the market", "Liberty Market");
  await buyer.getByRole("button", { name: "Place order" }).click();
  await expect(buyer.getByRole("heading", { name: /is placed$/ })).toBeVisible({
    timeout: 15_000,
  });
  await expect(buyer.locator(".checkout-message")).toContainText("transfer remarks");

  await page.reload();
  await expect(
    page.getByRole("region", { name: "At a glance" }).locator(".dash-sources"),
  ).toContainText("Instagram 1");
});
