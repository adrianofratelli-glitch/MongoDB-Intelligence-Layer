import { defineConfig, devices } from '@playwright/test';

// Pré-requisito: POC no ar (./start.sh) — frontend :5173 + backend :8000.
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
      // tolerância para antialiasing de fontes entre máquinas
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
