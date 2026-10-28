import { expect, test } from "@playwright/test";

test("the owner sees which wallets are read and agrees to share unread shapes", async ({
  page,
}) => {
  await page.goto("/s/gulmohar-lawn");
  await page.getByRole("button", { name: "Try as the owner" }).click();
  await expect(page).toHaveURL(/\/owner$/);

  const payments = page.getByRole("region", { name: "Payments" });
  const wallets = payments.getByRole("list", { name: "Wallets and banks Chatfront reads" });
  await expect(wallets).toContainText("JazzCash");
  await expect(wallets).toContainText("not yet checked on a real message");

  const share = payments.getByRole("checkbox", { name: /Share the shape of wallet messages/ });
  await share.check();
  await expect(payments).toContainText("0 messages kept to add");
  await share.uncheck();
  await expect(payments).not.toContainText("messages kept to add");
});
