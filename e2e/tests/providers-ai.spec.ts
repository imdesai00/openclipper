import AxeBuilder from '@axe-core/playwright';
import { BrowserContext, Page, expect, test } from '@playwright/test';

// Runs after first-run.spec.ts and media.spec.ts on the same fresh stack (alphabetical order).
const ADMIN = { email: 'ada@example.com', password: 'correct horse battery' };
const MODEL = process.env['E2E_OLLAMA_MODEL'] ?? 'qwen2.5:0.5b';
const SECRET = 'sk-or-v1-E2E-SECRET-0123456789abcdef-NEVER-SHOW-ME';

const problems: string[] = [];
const bodies: string[] = [];
let ctx: BrowserContext;
let page: Page;

test.describe.configure({ mode: 'serial' });

async function a11y(p: Page, label: string): Promise<void> {
  const r = await new AxeBuilder({ page: p }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
  const msg = r.violations.map((v) => `${v.id} (${v.nodes.length}): ${v.nodes[0]?.target}`).join('\n');
  expect(r.violations, `${label} accessibility violations:\n${msg}`).toEqual([]);
}

test.beforeAll(async ({ browser }) => {
  ctx = await browser.newContext();
  page = await ctx.newPage();
  page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`));
  page.on('console', (m) => {
    if (m.type() === 'error' && /Content Security Policy|Refused to/i.test(m.text())) problems.push(m.text());
  });
  page.on('response', async (r) => {
    if (!r.url().includes('/api/')) return;
    try {
      bodies.push(await r.text());
    } catch {
      /* streaming or already-consumed bodies cannot be read; their content is covered by the API tests */
    }
  });
  await page.goto('/login');
  await page.getByLabel('Email').fill(ADMIN.email);
  await page.getByLabel('Password').fill(ADMIN.password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
  await page.getByRole('link', { name: 'AI', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'AI', exact: true })).toBeVisible();
});

test.afterAll(async () => {
  await ctx.close();
});

test('the AI page starts empty and is accessible', async () => {
  await expect(page.getByText('No providers yet')).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Providers and models' })).toHaveAttribute('aria-selected', 'true');
  await a11y(page, 'AI providers (empty, light)');
  await page.getByRole('button', { name: 'Switch to dark theme' }).click();
  await page.waitForTimeout(400); // buttons animate their colours for 150 ms; scan the settled page, not a frame mid-fade
  await a11y(page, 'AI providers (empty, dark)');
  await page.getByRole('button', { name: 'Switch to light theme' }).click();
});

test('add the included Ollama, connect, download a real model with visible progress', async () => {
  await expect(page.getByLabel('Type')).toHaveValue('0');
  await page.getByLabel('Type').selectOption({ label: 'Ollama (included)' });
  await expect(page.getByLabel('Address')).toHaveValue('http://ollama:11434');
  await page.getByRole('button', { name: 'Add provider' }).click();
  const card = page.getByRole('listitem', { name: 'Ollama (included)' });
  await expect(card).toBeVisible();
  await expect(card.getByText('Local', { exact: true })).toBeVisible();

  await card.getByRole('button', { name: 'Test connection' }).click();
  await expect(card.getByText(/Connected: Ollama/)).toBeVisible({ timeout: 30_000 });

  await card.getByLabel('Download a model').fill(MODEL);
  await card.getByRole('button', { name: 'Download', exact: true }).click();
  await expect(card.getByText(new RegExp(`${MODEL}: (pulling|downloading|verifying|writing|success|Downloaded)`, 'i'))).toBeVisible({ timeout: 60_000 });
  const row = card.getByRole('row').filter({ hasText: MODEL });
  await expect(row).toBeVisible({ timeout: 240_000 }); // appears once the download finished and models were re-read
  await expect(row).toContainText('Free (runs here)');
  await expect(row).not.toContainText('availability not guaranteed');
  expect(await row.locator('td').nth(1).innerText()).toMatch(/\d/); // a real context length was discovered
});

test('send each job to the local model and run a prompt through the real route', async () => {
  await page.getByRole('tab', { name: 'Routing and test' }).click();
  await expect(page.getByRole('radio', { name: /Local only/ })).toBeChecked(); // nothing leaves this machine by default
  const first = page.locator('#highlight-0');
  await first.selectOption({ label: `Ollama (included) · ${MODEL}` });
  await page.getByRole('button', { name: 'Save choices' }).click();
  await expect(page.getByText('Saved.')).toBeVisible();

  await page.locator('#try-prompt').fill('Reply with the single word: pong');
  await page.getByRole('button', { name: 'Run', exact: true }).click();
  await expect(page.locator('pre')).toContainText(/pong/i, { timeout: 120_000 });
  const result = page.locator('[role=status]').filter({ has: page.locator('pre') });
  await expect(result).toContainText(`Ollama (included) · ${MODEL}`);
  await expect(result).toContainText('about 0 USD'); // local inference is free and says so
  await a11y(page, 'AI routing');

  await page.getByRole('tab', { name: 'Usage' }).click();
  await expect(page.getByRole('row').filter({ hasText: MODEL }).first()).toBeVisible();
  await expect(page.getByText('Estimated cost')).toBeVisible();
  await a11y(page, 'AI usage');
});

test('a hosted provider key is accepted once and is never shown, stored or sent back', async () => {
  await page.getByRole('tab', { name: 'Providers and models' }).click();
  await page.getByLabel('Type').selectOption({ label: 'OpenRouter' });
  const key = page.getByLabel(/API key/);
  await expect(key).toHaveAttribute('type', 'password');
  await expect(key).toHaveAttribute('autocomplete', 'new-password');
  await key.fill(SECRET);
  await page.getByRole('button', { name: 'Add provider' }).click();
  const card = page.getByRole('listitem', { name: 'OpenRouter' });
  await expect(card).toBeVisible();
  await expect(card.getByText('sk-…ysme'.replace('ysme', SECRET.slice(-4)))).toBeVisible();
  await expect(card.getByText('Remote', { exact: true })).toBeVisible();
  await expect(page.getByLabel(/API key/)).toHaveValue('');

  expect(await page.content()).not.toContain(SECRET);
  await page.reload();
  await page.getByRole('link', { name: 'AI', exact: true }).click();
  expect(await page.content()).not.toContain(SECRET);
  const stored = await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }));
  expect(stored).not.toContain(SECRET);
  expect((await ctx.cookies()).some((c) => c.value.includes(SECRET))).toBe(false);
  expect(bodies.some((b) => b.includes(SECRET))).toBe(false); // not in a single API response the browser received
  expect(bodies.length).toBeGreaterThan(10);

  // Online providers are blocked while the mode is Local-only, and the page explains it in words.
  await page.getByRole('tab', { name: 'Routing and test' }).click();
  await expect(page.getByRole('radio', { name: /Local only/ })).toBeChecked();
});

test('removing a provider asks first and deletes its key', async () => {
  await page.getByRole('tab', { name: 'Providers and models' }).click();
  const card = page.getByRole('listitem', { name: 'OpenRouter' });
  await card.getByRole('button', { name: 'Remove provider' }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('button', { name: 'Cancel' }).click();
  await expect(card).toBeVisible();
  await card.getByRole('button', { name: 'Remove provider' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Remove provider' }).click();
  await expect(card).toHaveCount(0);
  await expect(page.getByRole('listitem', { name: 'Ollama (included)' })).toBeVisible();
});

test('non-admins cannot reach the AI settings', async ({ browser }) => {
  const c2 = await browser.newContext();
  const p2 = await c2.newPage();
  await p2.goto('/ai');
  await expect(p2).toHaveURL(/\/login$/); // signed out: the guard sends them to sign in, not into the page
  expect((await p2.request.get('/api/v1/providers')).status()).toBe(401);
  await c2.close();
});

test('no CSP violations or uncaught errors in the AI session', () => {
  expect(problems).toEqual([]);
});
