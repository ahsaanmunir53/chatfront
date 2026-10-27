import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

async function say(page: Page, text: string) {
  await page.getByLabel("Type a message").fill(text);
  await page.getByRole("button", { name: "Send", exact: true }).click();
}

test("the owner keeps the books and the khata, runs a team, and sees why", async ({
  browser,
  page,
}) => {
  test.setTimeout(150_000);
  await page.goto("/s/barkat-kiryana");
  await page.getByRole("button", { name: "Try as the owner" }).click();
  await expect(page).toHaveURL(/\/owner$/);
  const shopPath =
    (await page.getByRole("link", { name: "Open shop page" }).getAttribute("href")) ?? "";

  // A customer places an order to pay by transfer.
  const customerContext = await browser.newContext({ locale: "en-US" });
  const customer = await customerContext.newPage();
  await customer.goto(`${shopPath}/chat`);
  await say(
    customer,
    "Please send 2 sugar to House 12, Street 4, Gulberg III, Lahore. I will pay by bank transfer.",
  );
  const card = customer
    .locator(".bubble-out")
    .filter({ hasText: "Payment: bank or wallet transfer" });
  await expect(card).toBeVisible({ timeout: 20_000 });
  await card.getByRole("button", { name: "Place order" }).click();
  const placed = customer.locator(".bubble-out").filter({ hasText: "transfer remarks" }).last();
  await expect(placed).toBeVisible({ timeout: 15_000 });
  const code = /Order (\w+) is placed/.exec((await placed.textContent()) ?? "")?.[1] ?? "";

  // The owner asks how the day is going, in their own words.
  await say(page, "aaj ki sale?");
  await expect(page.locator(".bubble-out").last()).toContainText(/Today so far|Aaj ab tak/, {
    timeout: 15_000,
  });

  // Why does this order wait for a transfer? The order says so itself.
  const row = page
    .getByRole("region", { name: /^Orders/ })
    .locator(".order-row", { hasText: code });
  await row.getByText("Why?").click();
  await expect(row.locator(".order-why")).toContainText(
    "Paid in full by transfer before dispatch.",
  );
  await expect(row.locator(".order-why")).toContainText("First order from this buyer");

  // An expense from the console, then undone.
  const books = page.getByRole("region", { name: "Books" });
  const expense = books.locator("form", { hasText: "Add an expense" });
  await expense.getByLabel("Amount").fill("2,500");
  await expense.getByLabel("Kind of expense").selectOption({ label: "Rent" });
  await expense.getByLabel("What for").fill("Shop rent");
  await expense.getByRole("button", { name: "Save expense" }).click();
  const daybook = books.locator(".daybook");
  await expect(daybook.getByRole("row", { name: /^Expenses/ })).toContainText("Rs 2,500");
  const rent = books.locator(".entry-line", { hasText: "Shop rent" }).first();
  await rent.getByRole("button", { name: "Undo" }).click();
  await expect(rent).toHaveClass(/is-undone/);
  await expect(daybook.getByRole("row", { name: /^Expenses/ })).toContainText("Rs 0");

  // Udhaar for Bilal in the khata.
  const khata = books.locator(".khata-block");
  await khata.getByLabel("Name").fill("Bilal");
  await khata.getByLabel("Amount").fill("3000");
  await khata.getByRole("button", { name: "Save" }).click();
  await expect(khata.locator(".pay-rows")).toContainText("Bilal");
  await expect(khata.locator(".pay-rows")).toContainText("Rs 3,000");

  // Discounts above 10% now wait for the owner.
  const settings = page.getByRole("region", { name: "What the assistant may do" });
  const discounts = settings.locator("fieldset", { hasText: "Discounts while bargaining" });
  await discounts.getByRole("radio", { name: "Ask me" }).click();
  await settings.getByRole("button", { name: "Save" }).click();
  await expect(settings.getByRole("status")).toHaveText("Saved");

  // A person on the accounts side gets their own link and sees only their part.
  const team = page.getByRole("region", { name: /^Team/ });
  await team.getByLabel("Name").fill("Sana");
  await team.getByLabel("Role").selectOption({ label: "Accounts" });
  await team.getByRole("button", { name: "Add", exact: true }).click();
  const link = (await team.locator(".team-link code").textContent()) ?? "";
  expect(link).toContain("/owner#t=");
  const sana = await (await browser.newContext({ locale: "en-US" })).newPage();
  await sana.goto(link);
  await expect(sana.getByRole("region", { name: "Books" })).toBeVisible({ timeout: 15_000 });
  await expect(sana.getByRole("region", { name: /^Payments$/ })).toBeVisible();
  await expect(sana.getByRole("region", { name: /^Team/ })).toHaveCount(0);
  await expect(sana.getByRole("region", { name: "Activity log" })).toHaveCount(0);

  // Every step is in the activity log, and the chain checks out.
  await page.reload();
  const log = page.getByRole("region", { name: "Activity log" });
  await expect(log).toContainText("Unbroken", { timeout: 15_000 });
  await expect(log).toContainText("Person added");
  await expect(log).toContainText("Settings changed");
});
