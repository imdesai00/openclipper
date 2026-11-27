import AxeBuilder from '@axe-core/playwright';
import { Browser, BrowserContext, Page, expect, test } from '@playwright/test';

const ADMIN = { name: 'Ada Admin', email: 'ada@example.com', password: 'correct horse battery' };
const EDITOR = { name: 'Eddie Editor', email: 'eddie@example.com', password: 'editor passphrase 42' };

/** Fails the test on CSP violations or uncaught page errors; ignores the expected 401s. */
function watch(page: Page, problems: string[]): void {
  page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`));
  page.on('console', (m) => {
    const t = m.text();
    if (m.type() === 'error' && /Content Security Policy|Refused to/i.test(t)) problems.push(t);
  });
}

async function newPage(browser: Browser, problems: string[]): Promise<{ ctx: BrowserContext; page: Page }> {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  watch(page, problems);
  return { ctx, page };
}

async function a11y(page: Page, label: string): Promise<void> {
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
  const msg = r.violations.map((v) => `${v.id} (${v.nodes.length}): ${v.nodes[0]?.target}`).join('\n');
  expect(r.violations, `${label} accessibility violations:\n${msg}`).toEqual([]);
}

test.describe.configure({ mode: 'serial' });

const problems: string[] = [];
let inviteLink = '';

test('fresh install: every entry point leads to the setup wizard', async ({ browser }) => {
  const { ctx, page } = await newPage(browser, problems);
  for (const path of ['/', '/login', '/users']) {
    await page.goto(path);
    await expect(page).toHaveURL(/\/setup$/);
  }
  await expect(page.getByRole('button', { name: 'Create admin account' })).toBeVisible();
  await a11y(page, 'setup (light)');
  await ctx.close();
});

test('page is actually styled (CSP did not break the stylesheet)', async ({ browser }) => {
  const { ctx, page } = await newPage(browser, problems);
  await page.goto('/setup');
  const btn = page.getByRole('button', { name: 'Create admin account' });
  await expect(btn).toBeVisible();
  const bg = await btn.evaluate((el) => getComputedStyle(el).backgroundColor);
  expect(bg).not.toBe('rgba(0, 0, 0, 0)');
  expect(bg).not.toBe('rgb(239, 239, 239)'); // browser default button grey
  await ctx.close();
});

test('setup wizard validates, then signs the new admin in', async ({ browser }) => {
  const { ctx, page } = await newPage(browser, problems);
  await page.goto('/setup');
  await page.getByRole('button', { name: 'Create admin account' }).click();
  await expect(page.getByText('Enter your name.')).toBeVisible();

  await page.getByLabel('Your name').fill(ADMIN.name);
  await page.getByLabel('Email').fill(ADMIN.email);
  await page.getByLabel('Password', { exact: true }).fill('short');
  await page.getByLabel('Confirm password').fill('short');
  await page.getByRole('button', { name: 'Create admin account' }).click();
  await expect(page.getByText('Password must be 12 to 128 characters.')).toBeVisible();

  await page.getByLabel('Password', { exact: true }).fill(ADMIN.password);
  await page.getByLabel('Confirm password').fill('does not match at all');
  await page.getByRole('button', { name: 'Create admin account' }).click();
  await expect(page.getByText('Passwords do not match.')).toBeVisible();

  await page.getByLabel('Confirm password').fill(ADMIN.password);
  await page.getByRole('button', { name: 'Create admin account' }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
  await expect(page.getByText(ADMIN.name)).toBeVisible();
  await ctx.close();
});

test('setup is closed after the first admin exists', async ({ browser }) => {
  const { ctx, page } = await newPage(browser, problems);
  await page.goto('/setup');
  await expect(page).toHaveURL(/\/login$/);
  const r = await page.request.post('/api/v1/setup/admin', {
    data: { email: 'evil@example.com', display_name: 'Evil', password: 'another long password' },
  });
  expect(r.status()).toBe(409);
  await ctx.close();
});

test('sign in: wrong password is rejected, right one works, session survives reload', async ({ browser }) => {
  const { ctx, page } = await newPage(browser, problems);
  await page.goto('/login');
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible(); // wait for lazy route render
  await a11y(page, 'login (light)');
  await page.getByLabel('Email').fill(ADMIN.email);
  await page.getByLabel('Password').fill('wrong password here');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('alert')).toContainText('Invalid email or password');

  await page.getByLabel('Password').fill(ADMIN.password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();

  await page.reload(); // access token is memory-only; the refresh cookie must restore the session
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();

  const status = page.getByRole('region', { name: 'System status' });
  for (const check of ['database', 'migrations', 'redis']) {
    await expect(status.locator('li', { hasText: new RegExp(check, 'i') })).toContainText('OK');
  }
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible();
  await a11y(page, 'dashboard (light)');

  // Dark theme persists across reloads and still passes contrast checks.
  await page.getByRole('button', { name: 'Switch to dark theme' }).click();
  await expect(page.locator('html')).toHaveClass(/dark/);
  await page.reload();
  await expect(page.locator('html')).toHaveClass(/dark/);
  await expect(page.getByRole('button', { name: 'Switch to light theme' })).toBeVisible();
  await a11y(page, 'dashboard (dark)');

  const cookies = await ctx.cookies();
  const refresh = cookies.find((c) => c.name === 'oc_refresh');
  expect(refresh?.httpOnly).toBe(true);
  expect(refresh?.sameSite).toBe('Strict');
  const storage = await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }));
  expect(storage).not.toMatch(/eyJ/); // no JWT in web storage
  await ctx.close();
});

test('admin invites an editor; the link works once', async ({ browser }) => {
  const { ctx, page } = await newPage(browser, problems);
  await page.goto('/login');
  await page.getByLabel('Email').fill(ADMIN.email);
  await page.getByLabel('Password').fill(ADMIN.password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.getByRole('link', { name: 'Users' }).click();
  await expect(page.getByRole('heading', { name: 'Users', exact: true })).toBeVisible();

  await page.getByLabel('Email', { exact: true }).fill(EDITOR.email);
  await page.getByLabel('Role', { exact: true }).selectOption('editor');
  await page.getByRole('button', { name: 'Create invitation' }).click();
  inviteLink = await page.getByLabel('Invitation link').inputValue();
  expect(inviteLink).toContain('/accept-invite?token=');
  await expect(page.getByRole('row', { name: /Ada Admin/ })).toBeVisible();
  await a11y(page, 'users (dark)');
  await ctx.close();

  const ed = await newPage(browser, problems);
  await ed.page.goto(inviteLink);
  await ed.page.getByLabel('Your name').fill(EDITOR.name);
  await ed.page.getByLabel('Password').fill(EDITOR.password);
  await ed.page.getByRole('button', { name: 'Join' }).click();
  await expect(ed.page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
  await expect(ed.page.getByRole('link', { name: 'Users' })).toHaveCount(0); // editors can't manage users
  await ed.page.goto('/users');
  await expect(ed.page.getByRole('heading', { name: 'Dashboard' })).toBeVisible(); // adminGuard redirect
  await ed.ctx.close();

  const reuse = await newPage(browser, problems);
  await reuse.page.goto(inviteLink);
  await reuse.page.getByLabel('Your name').fill('Someone Else');
  await reuse.page.getByLabel('Password').fill('another passphrase 99');
  await reuse.page.getByRole('button', { name: 'Join' }).click();
  await expect(reuse.page.getByRole('alert')).toContainText('invalid or has expired');
  await reuse.ctx.close();
});

test('deactivating a user cuts off their session immediately', async ({ browser }) => {
  const ed = await newPage(browser, problems);
  await ed.page.goto('/login');
  await ed.page.getByLabel('Email', { exact: true }).fill(EDITOR.email);
  await ed.page.getByLabel('Password').fill(EDITOR.password);
  await ed.page.getByRole('button', { name: 'Sign in' }).click();
  await expect(ed.page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();

  const ad = await newPage(browser, problems);
  await ad.page.goto('/login');
  await ad.page.getByLabel('Email').fill(ADMIN.email);
  await ad.page.getByLabel('Password').fill(ADMIN.password);
  await ad.page.getByRole('button', { name: 'Sign in' }).click();
  await ad.page.getByRole('link', { name: 'Users' }).click();
  const row = ad.page.getByRole('row', { name: new RegExp(EDITOR.name) });
  await row.getByRole('button', { name: 'Deactivate' }).click();
  await row.getByRole('button', { name: 'Confirm deactivate' }).click();
  await expect(row.getByText('Deactivated')).toBeVisible();

  // The editor's refresh cookie is revoked too: a reload ends up on the login page.
  await ed.page.reload();
  await expect(ed.page).toHaveURL(/\/login$/);

  await ad.page.getByRole('button', { name: 'Sign out' }).click();
  await expect(ad.page).toHaveURL(/\/login$/);
  await ad.ctx.close();
  await ed.ctx.close();
});

test('no CSP violations or uncaught errors occurred in any browser session', () => {
  expect(problems).toEqual([]);
});
