import { expect, test } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";

/** The owner of a fresh copy of a demo shop, and a customer chatting with it elsewhere. */
async function ownerAndCustomer(browser: Browser, page: Page, firstWords: string) {
  await page.goto("/s/gulmohar-lawn");
  await page.getByRole("button", { name: "Try as the owner" }).click();
  await expect(page).toHaveURL(/\/owner$/);
  const shopPath =
    (await page.getByRole("link", { name: "Open shop page" }).getAttribute("href")) ?? "";
  expect(shopPath).toMatch(/^\/s\/gulmohar-lawn-/);
  const context = await browser.newContext({ locale: "en-US" });
  const customer = await context.newPage();
  await customer.goto(`${shopPath}/chat`);
  await customer.getByLabel("Type a message").fill(firstWords);
  await customer.getByRole("button", { name: "Send", exact: true }).click();
  return { customer, close: () => context.close() };
}

async function say(customer: Page, text: string) {
  await customer.getByLabel("Type a message").fill(text);
  await customer.getByRole("button", { name: "Send", exact: true }).click();
}

test("a customer asks for a person, the owner answers in the inbox and gives the chat back", async ({
  browser,
  page,
}) => {
  test.setTimeout(90_000);
  const { customer, close } = await ownerAndCustomer(
    browser,
    page,
    "Can I talk to a human please?",
  );
  await expect(customer.locator(".bubble-out").last()).toContainText("shop owner", {
    timeout: 15_000,
  });

  // The owner sees who is waiting, reads the chat and replies.
  const inbox = page.getByRole("region", { name: "Inbox" });
  await expect(inbox.locator(".inbox-waiting")).toHaveText("1 waiting", { timeout: 15_000 });
  await inbox.locator(".inbox-row.is-waiting").click();
  const open = inbox.getByRole("article");
  await expect(open).toContainText("Waiting for a person");
  await expect(open.getByRole("list", { name: "Messages in this chat" })).toContainText(
    "Can I talk to a human please?",
  );
  await open.getByLabel("Write to the customer").fill("Hello, this is the owner.");
  await open.getByRole("button", { name: "Send", exact: true }).click();
  await expect(customer.locator(".bubble-out").last()).toContainText("this is the owner", {
    timeout: 15_000,
  });

  // The chat goes back to the assistant, and the customer is told.
  await open.getByRole("button", { name: "Let the assistant continue" }).click();
  await expect(inbox.locator(".inbox-waiting")).toHaveCount(0);
  await expect(customer.locator(".bubble-out").last()).toContainText("assistant is back", {
    timeout: 15_000,
  });
  await close();
});

test("the owner takes a chat from the assistant, and it shows under Mine", async ({
  browser,
  page,
}) => {
  test.setTimeout(90_000);
  const { customer, close } = await ownerAndCustomer(browser, page, "salam");
  await expect(customer.locator(".bubble-out").last()).toBeVisible({ timeout: 15_000 });

  const inbox = page.getByRole("region", { name: "Inbox" });
  const row = inbox.locator(".inbox-row").first();
  await expect(row).toContainText("Web chat", { timeout: 15_000 });
  await expect(row).toContainText("Assistant answering");
  await row.click();
  const open = inbox.getByRole("article");
  await expect(open).toContainText("Take the chat to reply");
  await expect(open.getByLabel("Write to the customer")).toHaveCount(0);
  await open.getByRole("button", { name: "Take this chat" }).click();
  await expect(open).toContainText("With Demo owner");
  await expect(open.getByLabel("Give to")).toHaveValue(/.+/);

  // The assistant is quiet now; the owner answers.
  await say(customer, "koi hai?");
  await open.getByLabel("Write to the customer").fill("Ji, main hoon.");
  await open.getByRole("button", { name: "Send", exact: true }).click();
  await expect(customer.locator(".bubble-out").last()).toContainText("main hoon", {
    timeout: 15_000,
  });
  await inbox.getByRole("button", { name: "Mine" }).click();
  await expect(inbox.locator(".inbox-row")).toHaveCount(1);
  await expect(inbox.locator(".inbox-row")).toContainText("With Demo owner");
  await close();
});
