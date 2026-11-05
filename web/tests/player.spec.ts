import { test, expect } from "@playwright/test";
test("receiver, playback, configuration and responsive views", async ({
  page,
  request,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Now playing" }),
  ).toBeVisible();
  await expect(
    page.getByText("Audio services are simulated.", { exact: false }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Sources", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Simulate playback" }),
  ).toHaveCount(0);
  await page
    .getByRole("article")
    .filter({ has: page.getByRole("heading", { name: "Spotify Connect" }) })
    .getByRole("button", { name: "Connection settings" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Settings", exact: true }),
  ).toBeVisible();
  // Seed the receiver state through the test backend, without a simulation control in the UI.
  const started = await request.post("/api/v1/sources/spotify/demo");
  expect(started.ok()).toBeTruthy();
  await page.getByRole("button", { name: "Now playing", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "A moment of quiet" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Play", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("textbox", { name: "Admin PIN" }).fill("123456");
  await page.getByRole("button", { name: "Unlock", exact: true }).click();
  await expect(
    page.getByText("Settings unlocked", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Use this output" }).click();
  await expect(
    page.getByText("Output configured", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Audio", exact: true }).click();
  await page.getByRole("button", { name: "Add band" }).click();
  await page.getByRole("button", { name: "Apply audio settings" }).click();
  await expect(
    page.getByText("DSP profile applied", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Library", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "A moment of quiet LosSpeaker Sessions" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Radio", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Find a station" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Now playing", exact: true }).click();
  await expect(page.locator("body")).toHaveJSProperty(
    "scrollWidth",
    await page.evaluate(() => window.innerWidth),
  );
  const visualizer = page.locator("details.visual-panel");
  await expect(visualizer).toHaveJSProperty("open", false);
  await visualizer.locator("summary").press("Enter");
  await expect(visualizer.locator(".spectrum")).toBeVisible();
  await expect(visualizer.locator("summary")).toContainText("Simulated mode");
  await visualizer.locator("summary").click();
  await expect(visualizer.locator(".spectrum")).not.toBeVisible();
  const audioDetails = page.locator("details.audio-info");
  await expect(audioDetails).toHaveJSProperty("open", false);
  await audioDetails.locator("summary").press("Enter");
  await expect(audioDetails.getByText("Source format")).toBeVisible();
  await audioDetails.locator("summary").click();
  await page.screenshot({
    path: `../.state/screenshots/${info.project.name}.png`,
    fullPage: true,
  });
  await page.getByRole("button", { name: "Full screen artwork" }).click();
  await expect(
    page.getByRole("button", { name: "Exit full screen" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Exit full screen" }).click();
  for (const tab of ["Sources", "Audio", "Settings"]) {
    await page.getByRole("button", { name: tab, exact: true }).click();
    await expect(page.locator("body")).toHaveJSProperty(
      "scrollWidth",
      await page.evaluate(() => window.innerWidth),
    );
    await page.screenshot({
      path: `../.state/screenshots/${info.project.name}-${tab.toLowerCase()}.png`,
      fullPage: true,
    });
  }
  expect(errors).toEqual([]);
});
