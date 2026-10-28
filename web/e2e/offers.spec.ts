import { expect, test } from "@playwright/test";

test("the sale counts down on the shop page and its code comes off at checkout", async ({
  browser,
  page,
}) => {
  test.setTimeout(90_000);
  // A fresh copy of the lawn shop: it comes with the WELCOME10 sale and a bundle.
  await page.goto("/s/gulmohar-lawn");
  await page.getByRole("button", { name: "Try as the owner" }).click();
  await expect(page).toHaveURL(/\/owner$/);
  const shopPath =
    (await page.getByRole("link", { name: "Open shop page" }).getAttribute("href")) ?? "";

  const buyer = await (await browser.newContext({ locale: "en-US" })).newPage();
  await buyer.goto(shopPath);
  const deadlines = buyer.getByRole("list", { name: "Deadlines" });
  await expect(deadlines).toContainText("Sale: 10% off with code WELCOME10", { timeout: 20_000 });
  await expect(deadlines).toContainText(/\d+ days? left/);

  const lawn = buyer.locator(".product", { hasText: "Printed lawn 3-piece" });
  await lawn.getByRole("combobox").selectOption({ label: "Rust" });
  await lawn.getByRole("button", { name: /Add to cart/ }).click();
  await buyer.getByRole("link", { name: "Check out" }).click();
  const cart = buyer.locator(".checkout-cart");
  await expect(cart.locator(".checkout-total")).toContainText("Rs 4,850");
  await buyer.getByLabel("Discount code").fill("nope99");
  await buyer.getByRole("button", { name: "Apply" }).click();
  await expect(buyer.getByText("There is no such code at this shop.")).toBeVisible();
  await buyer.getByLabel("Discount code").fill("welcome10");
  await buyer.getByLabel("Discount code").press("Enter"); // applies the code, places nothing
  await expect(cart).toContainText("Code WELCOME10");
  await expect(cart.locator(".checkout-offer")).toContainText("Rs 485");
  await expect(cart.locator(".checkout-total")).toContainText("Rs 4,365");
  await expect(buyer.getByRole("heading", { name: /is placed/ })).toHaveCount(0);

  // The owner sees the code and the bundle, and adds a code of their own.
  const offers = page.getByRole("region", { name: "Offers" });
  await expect(offers).toContainText("WELCOME10");
  await expect(offers).toContainText("Winter suit with dupatta");
  await offers.getByLabel("Code", { exact: true }).fill("EID20");
  await offers.getByLabel("Percent off").fill("20");
  await offers.getByRole("button", { name: "Add code" }).click();
  const codes = offers.getByRole("list", { name: "Discount codes" });
  await expect(codes).toContainText("EID20", { timeout: 15_000 });
  await expect(codes).toContainText("20% off");
});
