import { defineConfig, devices } from '@playwright/test';

// Prerequisite: the app is running (./start.sh) — frontend :5173 + backend :8000.
export default defineConfig({
  testDir: 'tests/visual',
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list']],
  use: {
    baseURL: 'http://localhost:5173',
    colorScheme: 'dark',
    trace: 'off',
  },
  expect: {
    toHaveScreenshot: {
      // tolerance for font antialiasing differences across machines
      maxDiffPixelRatio: 0.02,
    },
  },
  projects: [
    {
      name: 'desktop-chromium',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
  ],
});
