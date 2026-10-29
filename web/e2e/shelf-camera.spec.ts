import { expect, test } from "@playwright/test";

test("phone shelf mode changes stock only after owner confirmation", async ({ page }) => {
  test.setTimeout(90_000);
  await page.addInitScript(() => {
    const fakeStream = { getTracks: () => [{ stop: () => undefined }] } as unknown as MediaStream;
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: async () => fakeStream },
    });
    Object.defineProperty(HTMLMediaElement.prototype, "srcObject", {
      configurable: true,
      get: () => null,
      set: () => undefined,
    });
    HTMLMediaElement.prototype.play = async () => undefined;
    Object.defineProperty(HTMLVideoElement.prototype, "videoWidth", {
      configurable: true,
      get: () => 640,
    });
    Object.defineProperty(HTMLVideoElement.prototype, "videoHeight", {
      configurable: true,
      get: () => 480,
    });
    const context = { drawImage: () => undefined } as unknown as CanvasRenderingContext2D;
    HTMLCanvasElement.prototype.getContext = (() =>
      context) as unknown as typeof HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.toBlob = function (callback, type) {
      callback(new Blob(["mock shelf frame"], { type: type ?? "image/jpeg" }));
    };
  });

  let saves = 0;
  await page.route("**/api/owner/stock/count/photo*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        note: "",
        rows: [
          {
            variant_id: "11111111-1111-4111-8111-111111111111",
            name: "Lawn suit",
            before: 3,
            counted: 4,
          },
        ],
        problems: [],
      }),
    }),
  );
  await page.route("**/api/owner/stock/count", (route) => {
    saves += 1;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ changed: 1 }),
    });
  });

  await page.goto("/s/gulmohar-lawn");
  await page.getByRole("button", { name: "Try as the owner" }).click();
  await expect(page).toHaveURL(/\/owner$/);
  const panel = page.getByRole("region", { name: "Shelf camera mode" });
  await expect(panel).toBeVisible();
  await panel.getByRole("button", { name: "Start camera" }).click();
  await expect(panel.getByRole("status").filter({ hasText: "Tally updated." })).toBeVisible({
    timeout: 20_000,
  });
  await expect(panel.getByRole("row").filter({ hasText: "Lawn suit" }).locator("input")).toHaveValue("4");
  expect(saves).toBe(0);

  await panel.getByRole("button", { name: "Stop camera" }).click();
  await panel.getByRole("button", { name: "Confirm stock count" }).click();
  await expect(panel.getByRole("status")).toBeVisible();
  await expect.poll(() => saves).toBe(1);
});
