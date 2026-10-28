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

  // Delivered: the buyer gets a link to review this order, once (feature 46).
  await row.getByRole("button", { name: "Mark delivered" }).click();
  await expect(row).toContainText("Delivered", { timeout: 15_000 });
  await customer.goto(`${shopPath}/chat`);
  const delivered = customer
    .locator(".bubble-out")
    .filter({ hasText: "Rate your order here" })
    .last();
  await expect(delivered).toBeVisible({ timeout: 20_000 });
  await delivered.getByRole("link", { name: /\/review\// }).click();
  await expect(customer.getByRole("heading", { level: 1 })).toContainText("Review your order");
  await customer.getByLabel("4 stars out of 5").check();
  await customer.getByLabel("A few words (optional)").fill("Fresh sugar, on time");
  await customer.getByRole("button", { name: "Send review" }).click();
  await expect(customer.getByText("Your review is on the shop's page")).toBeVisible();

  // The owner reads it and replies once; the shop page shows both.
  await page.reload();
  const reviews = page.getByRole("region", { name: /^What buyers say/ });
  await expect(reviews).toContainText("Fresh sugar, on time", { timeout: 15_000 });
  await reviews.getByLabel("Your reply").fill("Thank you, see you again");
  await reviews.getByRole("button", { name: "Reply" }).click();
  await expect(reviews.locator(".review-reply")).toContainText("Thank you, see you again");
  await customer.goto(shopPath);
  const shown = customer.getByRole("region", { name: "What buyers say" });
  await expect(shown).toContainText("4.0 out of 5 from 1 review");
  await expect(shown).toContainText("Thank you, see you again");
  await customer.goto(`${shopPath}/trust`);
  const numbers = customer.getByRole("region", { name: "From the shop's records" });
  await expect(numbers).toContainText("1 order delivered");
  await expect(numbers).toContainText("Too few orders yet");

  // A buyer can be reported for fraud from the customers list (feature 37).
  const people = page.getByRole("region", { name: /^Customers/ });
  const buyer = people.locator("li").first();
  await buyer.getByText("Report fraud").click();
  await buyer.getByLabel("What happened").selectOption("refused_cod");
  await buyer.getByRole("button", { name: "Send report" }).click();
  await expect(buyer).toContainText("Reported, waiting for review", { timeout: 15_000 });
  await page.reload();
  await expect(page.getByRole("region", { name: "Your fraud reports" })).toContainText(
    "Refused a cash-on-delivery parcel",
  );
  await customerContext.close();
});
