import { expect, test } from "@playwright/test";

// A stand-in for a payment screenshot. With the mock model nothing is really read from it: the
// recorded reading has no amount and no transaction id, so the owner has to decide.
const SCREENSHOT = {
  name: "payment.png",
  mimeType: "image/png",
  buffer: Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR4nGP8sMWGgYGBiYGBgYGBAQAXXgHkraOxLgAAAABJRU5ErkJggg==",
    "base64",
  ),
};

test("a customer pays by transfer, the owner approves, and the signed receipt checks out", async ({
  browser,
  page,
}) => {
  test.setTimeout(120_000);
  // The owner: a copy of the demo grocery, signed in.
  await page.goto("/s/barkat-kiryana");
  await page.getByRole("button", { name: "Try as the owner" }).click();
  await expect(page).toHaveURL(/\/owner$/);
  const shopPath =
    (await page.getByRole("link", { name: "Open shop page" }).getAttribute("href")) ?? "";
  expect(shopPath).toMatch(/^\/s\/barkat-kiryana-/);

  // A customer orders on that shop's chat and places the order by bank transfer.
  const customerContext = await browser.newContext({ locale: "en-US" });
  const customer = await customerContext.newPage();
  await customer.goto(`${shopPath}/chat`);
  await customer
    .getByLabel("Type a message")
    .fill(
      "Please send 2 sugar to House 12, Street 4, Gulberg III, Lahore. I will pay by bank transfer.",
    );
  await customer.getByRole("button", { name: "Send", exact: true }).click();
  const card = customer
    .locator(".bubble-out")
    .filter({ hasText: "Payment: bank or wallet transfer" });
  await expect(card).toBeVisible({ timeout: 20_000 });
  await card.getByRole("button", { name: "Place order" }).click();
  const placed = customer.locator(".bubble-out").filter({ hasText: "transfer remarks" }).last();
  await expect(placed).toBeVisible({ timeout: 15_000 });
  const code = /Order (\w+) is placed/.exec((await placed.textContent()) ?? "")?.[1] ?? "";
  expect(code).toMatch(/^\w{6}$/);

  // The payment screenshot goes to the owner, since this shop has no SMS forwarding yet.
  await customer.locator('input[type="file"]').setInputFiles(SCREENSHOT);
  await expect(customer.locator(".bubble-out").last()).toContainText(
    "The shop will check it and confirm here",
    { timeout: 20_000 },
  );

  // The owner sees the evidence and approves.
  const review = page.getByRole("region", { name: /Payments to check/ });
  await expect(review).toBeVisible({ timeout: 20_000 });
  await expect(review).toContainText(code);
  await expect(review).toContainText("SMS forwarding is not set up");
  await expect(review.getByRole("img", { name: "Payment screenshot" })).toBeVisible();
  await review.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByRole("region", { name: /Payments to check/ })).toHaveCount(0);
  const received = page.getByRole("region", { name: "Payments", exact: true });
  await expect(received).toContainText("approved by you", { timeout: 15_000 });

  // The customer gets the receipt link; the page checks the shop's signature itself.
  const paid = customer.locator(".bubble-out").filter({ hasText: "Payment received" }).last();
  await expect(paid).toBeVisible({ timeout: 20_000 });
  await paid.getByRole("link", { name: /\/r\// }).click();
  await expect(customer.getByRole("status").filter({ hasText: "Signature checked" })).toBeVisible({
    timeout: 15_000,
  });
  await expect(customer.getByRole("heading", { level: 1 })).toContainText("Barkat");
  await expect(customer.locator(".slip-order")).toContainText(code);
  await expect(customer.getByText("Confirmed by the shop owner")).toBeVisible();
  await customerContext.close();
});
