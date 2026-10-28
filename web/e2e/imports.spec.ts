import { expect, test } from "@playwright/test";

const SHEET = [
  "title,price,color,item_group_id,description",
  "Silk scarf,2200,Red,S1,Pure silk",
  "Silk scarf,2400,Blue,S1,",
  "Velvet clutch,,,,",
].join("\n");

test("a sheet becomes drafts, and an added one is on sale with its colours", async ({
  browser,
  page,
}) => {
  test.setTimeout(90_000);
  await page.goto("/s/gulmohar-lawn");
  await page.getByRole("button", { name: "Try as the owner" }).click();
  await expect(page).toHaveURL(/\/owner$/);
  const shopPath =
    (await page.getByRole("link", { name: "Open shop page" }).getAttribute("href")) ?? "";

  const imports = page.getByRole("region", { name: "Bring in your products" });
  await imports.getByLabel("Upload a sheet (Excel or CSV)").setInputFiles({
    name: "shop.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(SHEET),
  });
  await expect(imports.getByRole("status")).toContainText("Sheet read", { timeout: 20_000 });
  const drafts = imports.getByRole("list", { name: "Drafts waiting for you" });
  const scarf = drafts
    .getByRole("listitem")
    .filter({ has: page.locator('input[value="Silk scarf"]') });
  await expect(scarf).toContainText("Comes in: Red, Blue");
  const clutch = drafts
    .getByRole("listitem")
    .filter({ has: page.locator('input[value="Velvet clutch"]') });
  await clutch.getByRole("button", { name: "Add to shop" }).click();
  await expect(clutch.getByRole("alert")).toContainText("needs a name and a price");
  await scarf.getByRole("button", { name: "Add to shop" }).click();
  await expect(drafts.getByRole("listitem")).toHaveCount(1, { timeout: 15_000 });

  const buyer = await (await browser.newContext({ locale: "en-US" })).newPage();
  await buyer.goto(shopPath);
  const added = buyer.locator(".product", { hasText: "Silk scarf" });
  await expect(added).toContainText("Red", { timeout: 20_000 });
  await expect(added).toContainText("Blue");
});

test("the size chart shows with the products it covers", async ({ page }) => {
  await page.goto("/s/gulmohar-lawn");
  const suit = page.locator(".product", { hasText: "Khaddar 2-piece, stitched" });
  await suit.getByText("Size chart").click({ timeout: 20_000 });
  const chart = suit.getByRole("table");
  await expect(chart).toContainText("Chest");
  await expect(chart.getByRole("row", { name: /^M / })).toContainText("36–37");
  const lawn = page.locator(".product", { hasText: "Printed lawn 3-piece" });
  await expect(lawn.getByText("Size chart")).toHaveCount(0);
});

test("the owner edits a size chart in the console", async ({ page }) => {
  test.setTimeout(60_000);
  await page.goto("/s/gulmohar-lawn");
  await page.getByRole("button", { name: "Try as the owner" }).click();
  const charts = page.getByRole("region", { name: "Size charts" });
  await expect(charts.getByLabel("Chart name")).toHaveValue("Stitched suits and kurtas", {
    timeout: 20_000,
  });
  await charts.getByRole("button", { name: "Add a size" }).click();
  await charts.getByRole("button", { name: "Save charts" }).click();
  await expect(charts.getByRole("status")).toHaveText("Saved"); // an empty row is left out
  await charts.getByLabel("M Chest").fill("37-36");
  await charts.getByRole("button", { name: "Save charts" }).click();
  await expect(charts.getByRole("alert")).toContainText("smallest to largest");
});
