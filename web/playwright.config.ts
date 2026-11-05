import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:8011",
    launchOptions: {
      executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
    },
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "desktop", use: { viewport: { width: 1440, height: 1100 } } },
    {
      name: "mobile",
      use: {
        viewport: { width: 390, height: 844 },
        isMobile: true,
        hasTouch: true,
      },
    },
  ],
  webServer: {
    command:
      "cd .. && LOSSPEAKER_DEMO=1 LOSSPEAKER_DATA=.state/e2e uv run uvicorn losspeaker.main:app --host 127.0.0.1 --port 8011",
    url: "http://127.0.0.1:8011/api/v1/state",
    reuseExistingServer: false,
  },
});
