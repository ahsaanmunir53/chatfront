import { expect, test } from "@playwright/test";

test("a customer asks for a person, the owner answers in the console and gives the chat back", async ({
  browser,
  page,
}) => {
  test.setTimeout(90_000);
  // The owner: a copy of a demo shop, signed in.
  await page.goto("/s/gulmohar-lawn");
  await page.getByRole("button", { name: "Try as the owner" }).click();
  await expect(page).toHaveURL(/\/owner$/);
  const shopLink = page.getByRole("link", { name: "Open shop page" });
  const shopPath = (await shopLink.getAttribute("href")) ?? "";
  expect(shopPath).toMatch(/^\/s\/gulmohar-lawn-/);

  // A customer on that shop's chat, in another browser.
  const customerContext = await browser.newContext({ locale: "en-US" });
  const customer = await customerContext.newPage();
  await customer.goto(`${shopPath}/chat`);
  await customer.getByLabel("Type a message").fill("Can I talk to a human please?");
  await customer.getByRole("button", { name: "Send", exact: true }).click();
  await expect(customer.locator(".bubble-out").last()).toContainText("shop owner", {
    timeout: 15_000,
  });

  // The owner sees who is waiting, reads the chat and replies.
  const waiting = page.getByRole("region", { name: /Customers waiting/ });
  await expect(waiting).toBeVisible({ timeout: 15_000 });
  await expect(waiting).toContainText("Can I talk to a human please?");
  await waiting.getByRole("button", { name: "Open chat" }).click();
  await waiting.getByLabel("Write to the customer").fill("Hello, this is the owner.");
  await waiting.getByRole("button", { name: "Send", exact: true }).click();
  await expect(customer.locator(".bubble-out").last()).toContainText("this is the owner", {
    timeout: 15_000,
  });

  // The chat goes back to the assistant, and the customer is told.
  await waiting.getByRole("button", { name: "Let the assistant continue" }).click();
  await expect(page.getByRole("region", { name: /Customers waiting/ })).toHaveCount(0);
  await expect(customer.locator(".bubble-out").last()).toContainText("assistant is back", {
    timeout: 15_000,
  });
  await customerContext.close();
});
