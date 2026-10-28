import { expect, test } from "@playwright/test";

test("a customer finds a demo shop and chats with it", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { level: 1, name: /A whole shop inside the chats/ }),
  ).toBeVisible();
  await page.getByRole("link", { name: /Gulmohar Lawn House/ }).click();

  await expect(page.getByRole("heading", { level: 1, name: "Gulmohar Lawn House" })).toBeVisible();
  await expect(page.getByText("Built with Muse Spark Open")).toBeVisible();
  await page.getByRole("link", { name: "Chat here" }).first().click();

  await page.getByLabel("Type a message").fill("salam");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.locator(".bubble-in").first()).toContainText("salam");
  await expect(page.locator(".bubble-out")).toContainText("Wa alaikum assalam", {
    timeout: 10_000,
  });
});

test("the page opens while the server sleeps, and messages wait for it", async ({ page }) => {
  await page.route("**/health", (route) => route.abort());
  await page.goto("/s/indus-mobiles/chat");
  await expect(page.getByRole("status")).toContainText("Waking the server");
  await expect(page.getByText("waking up")).toBeVisible();

  await page.getByRole("button", { name: "Salam!" }).click();
  await expect(page.locator(".bubble-pending")).toContainText("Sends when the server is awake");

  await page.unroute("**/health");
  await expect(page.getByText("online", { exact: true })).toBeVisible({ timeout: 15_000 });
  await expect(page.locator(".bubble-out")).toContainText("Wa alaikum assalam", {
    timeout: 10_000,
  });
});

test("Urdu turns the page right to left and is remembered", async ({ page }) => {
  await page.goto("/s/saffron-kitchen");
  await page.getByRole("button", { name: /Language/ }).click();
  await page.getByRole("menuitemradio", { name: /اردو/ }).click();
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByRole("link", { name: "یہاں چیٹ کریں" }).first()).toBeVisible();
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
});

test("the catalog opens before the server, and a product can be asked about", async ({ page }) => {
  await page.route("**/health", (route) => route.abort());
  await page.goto("/s/saffron-kitchen");
  await expect(page.locator(".product")).not.toHaveCount(0); // bundled demo products
  const first = page.locator(".product").first();
  const name = (await first.locator(".product-name").textContent()) ?? "";
  await first.getByRole("link", { name: "Ask about this" }).click();
  await expect(page.getByLabel("Type a message")).toHaveValue(`Is ${name} available?`);
});

test("the trust page lists the shop's official channels", async ({ page }) => {
  await page.goto("/s/noor-salon/trust");
  await expect(page.getByRole("heading", { name: "Trust page" })).toBeVisible();
  await expect(page.getByText("Chat on this website")).toBeVisible();
});

test("the trust page gives AI assistants an address to order through", async ({
  page,
  context,
}) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto("/s/noor-salon/trust");
  const access = page.getByRole("region", { name: "Order through your AI assistant" });
  await expect(access).toContainText("/mcp/noor-salon");
  await access.getByRole("button", { name: "Copy address" }).click();
  await expect(access.getByRole("button", { name: "Copied" })).toBeVisible();
  const address = await page.evaluate(() => navigator.clipboard.readText());
  const listed = await page.request.post(address, {
    data: { jsonrpc: "2.0", id: 1, method: "tools/list" },
  });
  const { result } = (await listed.json()) as { result: { tools: { name: string }[] } };
  expect(result.tools.map((tool) => tool.name)).toContain("start_checkout");
});

test("a shared product link shows a preview and leads back to the product", async ({
  page,
  context,
}) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto("/s/saffron-kitchen");
  const share = page.getByRole("button", { name: /^Share: / }).first();
  await share.click();
  await expect(page.getByText("Link copied")).toBeVisible();
  const link = await page.evaluate(() => navigator.clipboard.readText());
  const code = /\/s\/saffron-kitchen\/p\/(\w+)$/.exec(link)?.[1] ?? "";
  expect(code).not.toBe("");
  const preview = await page.request.get(link);
  const card = /property="og:image" content="([^"]+)"/.exec(await preview.text())?.[1] ?? "";
  const picture = await page.request.get(card.replaceAll("&amp;", "&"));
  expect(picture.headers()["content-type"]).toBe("image/png");
  await page.goto(link);
  await expect(page).toHaveURL(new RegExp(`/s/saffron-kitchen#p-${code}$`));
  await expect(page.locator(`#p-${code}`)).toBeInViewport();
});

test("an unknown shop says so", async ({ page }) => {
  await page.goto("/s/no-such-shop");
  await expect(page.getByRole("alert")).toHaveText("Shop not found");
});
