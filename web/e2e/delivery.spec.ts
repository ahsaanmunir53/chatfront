import { expect, test } from "@playwright/test";

test("a vague address gets a pin, and the owner packs, labels and dispatches the order", async ({
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

  // A customer gives only an area; the shop asks for a pin and the browser shares one.
  const customerContext = await browser.newContext({
    locale: "en-US",
    geolocation: { latitude: 31.5204, longitude: 74.3587 },
    permissions: ["geolocation"],
  });
  const customer = await customerContext.newPage();
  await customer.goto(`${shopPath}/chat`);
  const say = async (text: string) => {
    await customer.getByLabel("Type a message").fill(text);
    await customer.getByRole("button", { name: "Send", exact: true }).click();
  };
  await say("2 sugar please, deliver it to Gulberg");
  const share = customer.getByRole("button", { name: "Share my location" });
  await expect(share).toBeVisible({ timeout: 20_000 });
  await share.click();
  await expect(customer.locator(".bubble-in .bubble-pin").last()).toContainText("31.52040", {
    timeout: 15_000,
  });

  // With the pin in place the order can be placed: cash on delivery.
  await expect(customer.locator(".bubble-out").last()).toBeVisible({ timeout: 15_000 });
  await say("ok place it now");
  const card = customer.locator(".bubble-out").filter({ hasText: "Payment: cash on delivery" });
  await expect(card).toBeVisible({ timeout: 20_000 });
  await card.getByRole("button", { name: "Place order" }).click();
  const placed = customer.locator(".bubble-out").filter({ hasText: "is placed" }).last();
  await expect(placed).toBeVisible({ timeout: 15_000 });
  const code = /Order (\w+) is placed/.exec((await placed.textContent()) ?? "")?.[1] ?? "";
  expect(code).toMatch(/^\w{6}$/);

  // The owner sees it with its pin, packs it, takes the label and dispatches it.
  const orders = page.getByRole("region", { name: /^Orders/ });
  const row = orders.locator(".order-row").filter({ hasText: code });
  await expect(row).toContainText("Confirmed", { timeout: 20_000 });
  await expect(row.getByTitle("Location pin")).toBeVisible();
  await row.getByRole("button", { name: "Mark packed" }).click();
  await expect(row).toContainText("Packed", { timeout: 15_000 });
  const download = page.waitForEvent("download");
  await row.getByRole("button", { name: "Shipping label" }).click();
  expect((await download).suggestedFilename()).toBe(`label-${code}.pdf`);
  await row.getByRole("button", { name: "Mark dispatched" }).click();
  await row.getByLabel("Courier").fill("TCS");
  await row.getByLabel("Tracking number").fill("7712345");
  await row.getByRole("button", { name: "Mark dispatched" }).last().click();
  await expect(row).toContainText("On its way", { timeout: 15_000 });

  // The customer is told, and follows the parcel.
  const sent = customer.locator(".bubble-out").filter({ hasText: "is on its way" }).last();
  await expect(sent).toBeVisible({ timeout: 20_000 });
  await expect(sent).toContainText("TCS, tracking number 7712345");
  await sent.getByRole("link", { name: /\/t\// }).click();
  await expect(customer.getByRole("heading", { level: 1, name: "Your parcel" })).toBeVisible();
  await expect(customer.getByText("ready for the rider")).toBeVisible();
  await expect(customer.locator(".track-step")).toHaveText([
    /Order placed/,
    /Packed/,
    /On its way/,
  ]);
  await customerContext.close();
});
