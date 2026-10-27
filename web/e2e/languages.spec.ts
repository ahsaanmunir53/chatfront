import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

async function choose(page: Page, native: string) {
  await page.getByRole("button", { name: /Language|زبان|ژبه|ٻولي|बोली|भाषा|ভাষা|اللغة|بولی/ }).click();
  await page.getByRole("menuitemradio", { name: new RegExp(native) }).click();
}

async function fontOf(page: Page, selector: string): Promise<string> {
  return page.locator(selector).first().evaluate((el) => getComputedStyle(el).fontFamily);
}

const RIGHT_TO_LEFT = [
  { tag: "ur", native: "اردو", chat: "یہاں چیٹ کریں", font: "Noto Nastaliq Urdu" },
  { tag: "pa-Arab", native: "پنجابی", chat: "ایتھے چیٹ کرو", font: "Noto Sans Arabic" },
  { tag: "ps", native: "پښتو", chat: "دلته چټ وکړئ", font: "Noto Sans Arabic" },
  { tag: "sd", native: "سنڌي", chat: "هتي چيٽ ڪريو", font: "Noto Sans Arabic" },
  { tag: "ar", native: "العربية", chat: "تحدّث هنا", font: "Noto Sans Arabic" },
];

for (const language of RIGHT_TO_LEFT) {
  test(`${language.tag} renders right to left in its own font`, async ({ page }) => {
    await page.goto("/s/saffron-kitchen");
    await choose(page, language.native);
    const html = page.locator("html");
    await expect(html).toHaveAttribute("dir", "rtl");
    await expect(html).toHaveAttribute("lang", language.tag);
    await expect(page.getByRole("link", { name: language.chat }).first()).toBeVisible();
    expect(await fontOf(page, ".section-title")).toContain(language.font);
    // The back arrow and send icon point the other way in a right-to-left page.
    const flipped = page.locator(".profile-channels .flip-rtl, .flip-rtl").first();
    if ((await flipped.count()) > 0) {
      expect(await flipped.evaluate((el) => getComputedStyle(el).transform)).not.toBe("none");
    }
  });
}

for (const language of [
  { tag: "hi", native: "हिन्दी", chat: "यहाँ चैट करें", font: "Noto Sans Devanagari" },
  { tag: "bn", native: "বাংলা", chat: "এখানে চ্যাট করুন", font: "Noto Sans Bengali" },
]) {
  test(`${language.tag} stays left to right and loads its script's font`, async ({ page }) => {
    await page.goto("/s/indus-mobiles");
    await choose(page, language.native);
    await expect(page.locator("html")).toHaveAttribute("dir", "ltr");
    await expect(page.getByRole("link", { name: language.chat }).first()).toBeVisible();
    expect(await fontOf(page, ".section-title")).toContain(language.font);
    await expect(page.locator(`link[href*="${language.font.replace(/ /g, "+")}"]`)).toHaveCount(1);
  });
}

test("Roman Urdu keeps the Latin face, not Nastaliq", async ({ page }) => {
  await page.goto("/");
  await choose(page, "Roman Urdu");
  await expect(page.locator("html")).toHaveAttribute("lang", "ur-Latn");
  await expect(page.locator("html")).toHaveAttribute("dir", "ltr");
  expect(await fontOf(page, "body")).toMatch(/^"?Anek Latin/);
});

test("a chosen language is remembered", async ({ page }) => {
  await page.goto("/s/noor-salon");
  await choose(page, "پښتو");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("lang", "ps");
  await expect(page.getByRole("link", { name: "دلته چټ وکړئ" }).first()).toBeVisible();
});

test("every language in the menu is named in its own script, beta ones marked", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: /Language/ }).click();
  const options = page.getByRole("menuitemradio");
  await expect(options).toHaveCount(9);
  await expect(options.first()).toContainText("Roman Urdu");
  await expect(page.locator(".lang-beta").first()).toHaveText("beta");
  await expect(page.locator(".lang-note")).toContainText("native speaker");
  await page.keyboard.press("Escape");
  await expect(options).toHaveCount(0);
});

test("a Pashto customer gets a Pashto reply, written right to left", async ({ page }) => {
  await page.goto("/s/gulmohar-lawn/chat");
  await page.getByLabel("Type a message").fill("دا سوټ په څو دی؟");
  await page.getByRole("button", { name: "Send" }).click();
  const reply = page.locator(".bubble-out span[lang]").first();
  await expect(reply).toContainText("ځواب", { timeout: 10_000 });
  await expect(reply).toHaveAttribute("lang", "ps");
  expect(await reply.evaluate((el) => getComputedStyle(el).direction)).toBe("rtl");
  expect(await reply.evaluate((el) => getComputedStyle(el).fontFamily)).toContain("Noto Sans Arabic");
});

test.describe("an Egyptian browser", () => {
  test.use({ locale: "ar-EG" });

  test("opens in Arabic, right to left", async ({ page }) => {
    await page.goto("/s/saffron-kitchen");
    await expect(page.locator("html")).toHaveAttribute("lang", "ar");
    await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
    await expect(page.getByRole("link", { name: "تحدّث هنا" }).first()).toBeVisible();
  });
});
