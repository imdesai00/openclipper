import { defineConfig } from '@playwright/test';

// Runs against a real, freshly started stack (docker compose up). No mocks.
// A fresh database is required: the first test is the first-run setup wizard.
export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  reporter: [['list']],
  use: {
    baseURL: process.env['E2E_BASE_URL'] ?? 'http://127.0.0.1:8080',
    trace: 'off',
    launchOptions: process.env['PW_CHROMIUM_PATH']
      ? { executablePath: process.env['PW_CHROMIUM_PATH'], args: ['--no-sandbox'] }
      : { args: ['--no-sandbox'] },
  },
});
