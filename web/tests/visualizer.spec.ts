import { expect, test } from "@playwright/test";

test("immersive visualizer uses audio frames, remembers styles and closes cleanly", async ({ page, request }, info) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  let connected = 0, disconnected = 0;
  await page.routeWebSocket("**/ws/visualizer", (socket) => {
    connected++;
    const timer = setInterval(() => socket.send(JSON.stringify({
      available: true,
      spectrum: Array.from({ length: 64 }, (_, i) => Math.max(0.04, 0.52 + Math.sin(i * 0.22) * 0.22 - i / 180)),
      peak: [-12, -14],
      output_peak: [-13, -15],
    })), 80);
    socket.onClose(() => { clearInterval(timer); disconnected++; });
  });
  await page.goto("/");
  expect((await request.post("/api/v1/sources/spotify/demo")).ok()).toBeTruthy();
  await page.getByRole("button", { name: "Open visualizer", exact: true }).click();
  const view = page.getByRole("dialog", { name: "Audio visualizer" });
  await expect(view).toBeVisible();
  await expect(page).toHaveURL(/#visualizer$/);
  await expect(view.getByRole("status")).toHaveText("Live audio");
  await expect(view.getByRole("heading", { name: "A moment of quiet" })).toBeVisible();
  await expect(view.getByRole("button", { name: "Bloom", exact: false })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("main")).toHaveAttribute("inert", "");
  await expect(view).toHaveJSProperty("scrollWidth", await page.evaluate(() => innerWidth));
  for (const scene of ["Bloom", "Warp", "Terrain"]) {
    await view.getByRole("button", { name: scene, exact: false }).click();
    await expect(view.getByRole("img", { name: `${scene} audio visualization` })).toBeVisible();
    await page.waitForTimeout(500);
    await expect(view.locator("canvas")).toHaveCount(1);
    const rendered = await view.locator("canvas").evaluate((el: HTMLCanvasElement) => new Promise<{ error: number; brightest: number }>((resolve) => requestAnimationFrame(() => {
      const gl = el.getContext("webgl")!;
      const pixels = new Uint8Array(el.width * el.height * 4);
      gl.readPixels(0, 0, el.width, el.height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
      let brightest = 0;
      for (let i = 0; i < pixels.length; i += 64) brightest = Math.max(brightest, pixels[i] + pixels[i + 1] + pixels[i + 2]);
      resolve({ error: gl.getError(), brightest });
    })));
    expect(rendered.error).toBe(0);
    expect(rendered.brightest).toBeGreaterThan(60);
    await page.screenshot({ path: `../.state/screenshots/${info.project.name}-visualizer-${scene.toLowerCase()}.png` });
  }
  await view.getByRole("button", { name: "Ember colours" }).click();
  await view.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(view.getByRole("status")).toHaveText("Waiting for music");
  await view.getByRole("button", { name: "Play", exact: true }).click();
  await expect(view.getByRole("status")).toHaveText("Live audio");
  await page.keyboard.press("Escape");
  await expect(view).toHaveCount(0);
  await expect.poll(() => disconnected).toBeGreaterThan(0);
  await expect(page.locator("main")).not.toHaveAttribute("inert");
  await expect(page.getByRole("button", { name: "Open visualizer", exact: true })).toBeFocused();
  await page.goto("/#visualizer");
  await expect(view).toBeVisible();
  await expect(view.getByRole("button", { name: "Terrain", exact: false })).toHaveAttribute("aria-pressed", "true");
  await expect(view.getByRole("button", { name: "Ember colours" })).toHaveAttribute("aria-pressed", "true");
  await page.keyboard.press("Tab");
  await expect(view.getByRole("button", { name: "Enter browser fullscreen" })).toBeFocused();
  if (info.project.name === "desktop") {
    await view.getByRole("button", { name: "Enter browser fullscreen" }).click();
    await expect.poll(() => page.evaluate(() => document.fullscreenElement?.getAttribute("role"))).toBe("dialog");
    await view.getByRole("button", { name: "Leave browser fullscreen" }).click();
    await expect.poll(() => page.evaluate(() => document.fullscreenElement)).toBeNull();
    await view.getByRole("button", { name: "Enter browser fullscreen" }).click();
  }
  await view.getByRole("button", { name: "Exit full screen" }).click();
  await expect(view).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => document.fullscreenElement)).toBeNull();
  expect(connected).toBeGreaterThan(0);
  expect(errors).toEqual([]);
});

test("visualizer works without WebGL and respects reduced motion", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, type: string, ...args: any[]) {
      if (type === "webgl") return null;
      return (original as any).call(this, type, ...args);
    } as typeof original;
  });
  await page.goto("/#visualizer");
  const view = page.getByRole("dialog", { name: "Audio visualizer" });
  await expect(view).toBeVisible();
  await expect.poll(() => view.locator("canvas").evaluate((el: HTMLCanvasElement) => el.getContext("2d")?.getImageData(0, 0, 1, 1).data[3])).toBe(255);
  await view.getByRole("button", { name: "Warp", exact: false }).click();
  await page.keyboard.press("Escape");
  await expect(view).toHaveCount(0);
});

test("live levels drive the image and unavailable audio fades out", async ({ page, request }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  let level = 0, available = true;
  await page.routeWebSocket("**/ws/visualizer", socket => {
    const timer = setInterval(() => socket.send(JSON.stringify({ available, reason: "No processed audio", spectrum: Array.from({ length: 64 }, () => level), peak: [-12, -12] })), 80);
    socket.onClose(() => clearInterval(timer));
  });
  await page.goto("/");
  expect((await request.post("/api/v1/sources/spotify/demo")).ok()).toBeTruthy();
  await page.getByRole("button", { name: "Open visualizer", exact: true }).click();
  const view = page.getByRole("dialog", { name: "Audio visualizer" });
  await expect(view.getByRole("status")).toHaveText("Live audio");
  const brightness = () => view.locator("canvas").evaluate((el: HTMLCanvasElement) => new Promise<number>(resolve => requestAnimationFrame(() => {
    const gl = el.getContext("webgl")!;
    const pixels = new Uint8Array(el.width * el.height * 4);
    gl.readPixels(0, 0, el.width, el.height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
    let light = 0;
    for (let i = 0; i < pixels.length; i += 64) light += pixels[i] + pixels[i + 1] + pixels[i + 2];
    resolve(light);
  })));
  const quiet = await brightness();
  level = .85;
  await page.waitForTimeout(800);
  const loud = await brightness();
  expect(loud).toBeGreaterThan(quiet * 1.1);
  available = false;
  await expect(view.getByRole("status")).toHaveText("No processed audio");
  await page.waitForTimeout(1200);
  expect(await brightness()).toBeLessThan(loud * .9);
  await view.getByRole("button", { name: "Exit full screen" }).click();
});

test("song overlay persists after controls hide and follows artwork changes", async ({ page, request }, info) => {
  expect((await request.post("/api/v1/sources/spotify/demo")).ok()).toBeTruthy();
  const state = await (await request.get("/api/v1/state")).json();
  state.player.active_source = "bluetooth";
  state.player.artwork = "/api/v1/artwork/" + "a".repeat(64);
  let stateSocket: { send: (value: string) => void };
  await page.routeWebSocket("**/ws/state", socket => { stateSocket = socket; socket.send(JSON.stringify(state)); });
  await page.route("**/api/v1/artwork/*", route => route.fulfill({
    contentType: "image/svg+xml",
    body: '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="#7654ad"/><circle cx="50" cy="50" r="34" fill="#eac6a2"/></svg>',
  }));
  await page.goto("/#visualizer");
  const view = page.getByRole("dialog", { name: "Audio visualizer" });
  const overlay = view.getByRole("region", { name: "Now playing overlay" });
  const image = overlay.getByRole("img");
  await expect(overlay.getByRole("heading", { name: "A moment of quiet" })).toBeVisible();
  await expect.poll(() => image.evaluate((el: HTMLImageElement) => el.naturalWidth)).toBe(100);
  await view.locator("canvas").click({ position: { x: 20, y: 230 } });
  await expect(view).toHaveClass(/hud-hidden/, { timeout: 7000 });
  await expect(view.locator(".immersive-bottom")).toHaveCSS("opacity", "0");
  await expect(overlay).toHaveCSS("opacity", "1");
  await expect(image).toBeVisible();
  state.player.title = "Second song";
  state.player.artist = "Second artist";
  state.player.artwork = "/api/v1/artwork/" + "b".repeat(64);
  stateSocket!.send(JSON.stringify(state));
  await expect(overlay.getByRole("heading", { name: "Second song" })).toBeVisible();
  await expect(image).toHaveAttribute("src", "/api/v1/artwork/" + "b".repeat(64));
  await expect.poll(() => image.evaluate((el: HTMLImageElement) => el.naturalWidth)).toBe(100);
  await expect(view).toHaveClass(/hud-hidden/);
  await page.screenshot({ path: `../.state/screenshots/${info.project.name}-persistent-song-overlay.png` });
  state.player.title = "A very long song title ".repeat(12);
  state.player.artwork = null;
  stateSocket!.send(JSON.stringify(state));
  await expect(overlay.getByRole("img")).toHaveCount(0);
  if (info.project.name === "mobile") await page.setViewportSize({ width: 844, height: 390 });
  const bounds = await overlay.boundingBox();
  expect(bounds).not.toBeNull();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(await page.evaluate(() => innerWidth));
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(await page.evaluate(() => innerHeight));
  await page.keyboard.press("Escape");
});

test("late Bluetooth artwork appears without refreshing and retries a failed download", async ({ page, request }) => {
  const state = await (await request.get("/api/v1/state")).json();
  Object.assign(state.player, {active_source: "bluetooth", status: "playing", title: "Late cover", artist: "Bluetooth artist", artwork: null});
  let socket: { send: (value: string) => void };
  await page.routeWebSocket("**/ws/state", connection => { socket = connection; connection.send(JSON.stringify(state)); });
  let downloads = 0, available = false;
  await page.route("**/api/v1/artwork/**", route => {
    downloads++;
    return available ? route.fulfill({
      contentType: "image/svg+xml",
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="#7654ad"/></svg>',
    }) : route.fulfill({status: 503, body: "Temporarily unavailable"});
  });
  let navigations = 0;
  page.on("request", request => { if (request.isNavigationRequest() && request.frame() === page.mainFrame()) navigations++; });
  await page.goto("/#visualizer");
  const overlay = page.getByRole("region", {name: "Now playing overlay"});
  await expect(overlay.getByRole("heading")).toHaveText("Late cover");
  await expect(overlay.getByRole("img")).toHaveCount(0);
  state.player.artwork = "/api/v1/artwork/" + "d".repeat(64);
  socket!.send(JSON.stringify(state));
  await expect.poll(() => downloads).toBeGreaterThan(0);
  // The asset becomes reachable after the browser's first request failed.
  available = true;
  const image = overlay.getByRole("img");
  await expect.poll(() => image.evaluate((el: HTMLImageElement) => el.naturalWidth)).toBe(100);
  await expect(image).toBeVisible();
  await page.keyboard.press("Escape");
  const playerCover = page.locator(".cover img");
  await expect.poll(() => playerCover.evaluate((el: HTMLImageElement) => el.naturalWidth)).toBe(100);
  await expect(playerCover).toBeVisible();
  expect(navigations).toBe(1);
});
