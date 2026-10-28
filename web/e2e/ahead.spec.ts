import { expect, test } from "@playwright/test";

test("the owner counts the cash, makes a credit passport and a lender checks it", async ({
  browser,
  page,
}) => {
  test.setTimeout(90_000);
  await page.goto("/s/gulmohar-lawn");
  await page.getByRole("button", { name: "Try as the owner" }).click();
  await expect(page).toHaveURL(/\/owner$/);
  const shopPath =
    (await page.getByRole("link", { name: "Open shop page" }).getAttribute("href")) ?? "";

  const ahead = page.getByRole("region", { name: "Ahead and behind" });
  await expect(ahead).toContainText("Write the cash you have now");
  await ahead.getByLabel("Cash you have now, in hand and in the bank").fill("50000");
  await ahead.getByRole("button", { name: "Save", exact: true }).click();
  await expect(ahead).toContainText("Cash now, by the books: Rs 50,000");
  await expect(ahead.locator("table.forecast tbody tr")).toHaveCount(4);

  await ahead.getByRole("checkbox", { name: /I agree to share/ }).check();
  await ahead.getByRole("button", { name: "Make a passport" }).click();
  const code = ahead.getByRole("link", { name: /^CP/ }).first();
  await expect(code).toBeVisible();
  const href = (await code.getAttribute("href")) ?? "";

  const lender = await (await browser.newContext({ locale: "en-US" })).newPage();
  await lender.goto(href);
  await expect(lender.getByRole("status")).toContainText("The signature checks out", {
    timeout: 15_000,
  });
  await expect(lender.locator("table.passport-table tbody tr")).toHaveCount(12);

  // A buyer checks a number that wrote to them against the shop's own channels.
  await lender.goto(`${shopPath}/trust`);
  await lender.getByLabel("WhatsApp number, Instagram or Facebook account").fill("0300 1234567");
  await lender.getByRole("button", { name: "Check", exact: true }).click();
  await expect(lender.getByRole("status")).toContainText("No, this is not one of");
});
