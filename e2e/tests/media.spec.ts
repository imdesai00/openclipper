import AxeBuilder from '@axe-core/playwright';
import { execFileSync } from 'node:child_process';
import { statSync, writeFileSync } from 'node:fs';
import { BrowserContext, Page, expect, test } from '@playwright/test';

// Runs after first-run.spec.ts (same fresh stack): signs in as the admin created there.
const ADMIN = { email: 'ada@example.com', password: 'correct horse battery' };
const problems: string[] = [];

test.describe.configure({ mode: 'serial' });

let ctx: BrowserContext;
let page: Page;
let sockets: string[] = [];

function ffmpeg(args: string[]): void {
  execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', ...args]);
}

async function a11y(p: Page, label: string): Promise<void> {
  const r = await new AxeBuilder({ page: p }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
  const msg = r.violations.map((v) => `${v.id} (${v.nodes.length}): ${v.nodes[0]?.target}`).join('\n');
  expect(r.violations, `${label} accessibility violations:\n${msg}`).toEqual([]);
}

/** The proxy must actually play in the browser: decodes, has the right length, and time advances. */
async function expectPlayable(p: Page, expectedSeconds: number): Promise<void> {
  const video = p.locator('video');
  await expect(video).toBeVisible();
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.readyState), { timeout: 30_000 }).toBeGreaterThanOrEqual(2);
  const duration = await video.evaluate((v: HTMLVideoElement) => v.duration);
  expect(Math.abs(duration - expectedSeconds)).toBeLessThan(1.5);
  await video.evaluate(async (v: HTMLVideoElement) => {
    v.muted = true;
    await v.play();
  });
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime), { timeout: 15_000 }).toBeGreaterThan(0.4);
  expect(await video.evaluate((v: HTMLVideoElement) => v.videoWidth)).toBeGreaterThan(0);
}

test.beforeAll(async ({ browser }) => {
  ctx = await browser.newContext();
  page = await ctx.newPage();
  page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`));
  page.on('console', (m) => {
    if (m.type() === 'error' && /Content Security Policy|Refused to/i.test(m.text())) problems.push(m.text());
  });
  page.on('websocket', (ws) => sockets.push(ws.url()));
  await page.goto('/login');
  await page.getByLabel('Email').fill(ADMIN.email);
  await page.getByLabel('Password').fill(ADMIN.password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
});

test.afterAll(async () => {
  await ctx.close();
});

test('dashboard shows an empty state, then the real project count', async () => {
  await expect(page.getByText('Nothing here yet')).toBeVisible();
  await page.getByRole('link', { name: 'Projects' }).click();
  await expect(page.getByText('No projects yet')).toBeVisible();
  await a11y(page, 'projects (empty)');

  await page.getByLabel('New project name').fill('Weekly podcast');
  await page.getByRole('button', { name: 'Create project' }).click();
  await expect(page.getByRole('heading', { name: 'Weekly podcast' })).toBeVisible();
  await expect(page.getByText('No videos yet.')).toBeVisible();

  await page.getByRole('link', { name: 'Dashboard' }).click();
  await expect(page.getByRole('term').filter({ hasText: 'Projects' })).toBeVisible();
  await expect(page.getByRole('definition').first()).toHaveText('1');
});

test('uploads are blocked until the permission box is ticked', async () => {
  await page.getByRole('link', { name: 'Projects' }).click();
  await page.getByRole('link', { name: /Weekly podcast/ }).click();
  await expect(page.locator('#file')).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Delete project' })).toBeVisible();
  await page.getByRole('checkbox', { name: /I own this content/ }).check();
  await expect(page.locator('#file')).toBeEnabled();
  await a11y(page, 'project page');
});

test('upload a video: it is processed and plays in the browser', async ({}, testInfo) => {
  const file = testInfo.outputPath('interview.mp4');
  ffmpeg(['-f', 'lavfi', '-i', 'testsrc2=size=1280x720:rate=30:duration=6', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=6',
    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', file]); // fmt: skip
  await page.locator('#file').setInputFiles(file);
  await expect(page.getByText('Uploaded, now processing')).toBeVisible({ timeout: 30_000 });

  const row = page.getByRole('listitem').filter({ hasText: 'interview' });
  await expect(row.getByText('Ready')).toBeVisible({ timeout: 90_000 });
  await expect(row.locator('img')).toBeVisible(); // thumbnail from a signed URL
  expect(await row.locator('img').evaluate((i: HTMLImageElement) => i.naturalWidth)).toBeGreaterThan(0);

  await row.getByRole('link', { name: 'interview' }).first().click();
  await expect(page.getByRole('heading', { name: 'interview' })).toBeVisible();
  await expectPlayable(page, 6);
  await expect(page.getByText('1280 × 720')).toBeVisible();
  for (const stage of ['Check the file', 'Prepare the editing copy', 'Draw the waveform', 'Make a thumbnail']) {
    await expect(page.getByRole('listitem').filter({ hasText: stage }).getByText('Done')).toBeVisible();
  }
  await a11y(page, 'source page');
});

test('a file that is not a video fails visibly, with a reason and a Retry', async ({}, testInfo) => {
  const fake = testInfo.outputPath('notes.mp4');
  writeFileSync(fake, 'This is a text file pretending to be a video.\n'.repeat(100));
  await page.getByRole('link', { name: 'Projects' }).click();
  await page.getByRole('link', { name: /Weekly podcast/ }).click();
  await page.getByRole('checkbox', { name: /I own this content/ }).check();
  await page.locator('#file').setInputFiles(fake);
  const row = page.getByRole('listitem').filter({ hasText: 'notes' }).filter({ hasText: 'Failed' });
  await expect(row.first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText('This file is not a readable video').first()).toBeVisible();
  await row.first().getByRole('link', { name: 'notes' }).first().click();
  await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible();
  await expect(page.locator('video')).toHaveCount(0);
});

test('live updates arrive over a WebSocket allowed by the CSP', async () => {
  expect(sockets.some((u) => u.includes('/ws/jobs?ticket='))).toBe(true);
});

test('deleting a video asks first; deleting a project needs its name typed', async () => {
  await page.getByRole('link', { name: 'Projects' }).click();
  await page.getByRole('link', { name: /Weekly podcast/ }).click();
  const row = page.getByRole('listitem').filter({ hasText: 'notes' });
  await row.getByRole('link', { name: 'notes' }).first().click();
  await page.getByRole('button', { name: 'Delete', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('button', { name: 'Cancel' }).click();
  await expect(page.getByRole('heading', { name: 'notes' })).toBeVisible(); // nothing was deleted
  await page.getByRole('button', { name: 'Delete', exact: true }).click();
  await page.getByRole('button', { name: 'Delete video' }).click();
  await expect(page.getByRole('heading', { name: 'Weekly podcast' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'notes' })).toHaveCount(0);

  await page.getByRole('button', { name: 'Delete project' }).click();
  const confirm = page.getByRole('button', { name: 'Delete project' }).last();
  await expect(confirm).toBeDisabled();
  await page.getByLabel(/Type .* to confirm/).fill('weekly podcast'); // wrong case
  await expect(confirm).toBeDisabled();
  await page.getByLabel(/Type .* to confirm/).fill('Weekly podcast');
  await expect(confirm).toBeEnabled();
  await confirm.click();
  await expect(page).toHaveURL(/\/projects$/);
  await expect(page.getByText('No projects yet')).toBeVisible();
});

test('admin settings: link downloads are off by default and explain the risk', async () => {
  await page.getByRole('link', { name: 'Settings' }).click();
  await expect(page.getByRole('heading', { name: 'Link downloads (YouTube)' })).toBeVisible();
  await expect(page.getByRole('checkbox', { name: 'Allow YouTube links' })).not.toBeChecked();
  await expect(page.getByRole('note')).toContainText("Terms of Service");
  await a11y(page, 'settings');

  await page.getByRole('checkbox', { name: 'Allow YouTube links' }).check();
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('Saved.')).toBeVisible();
  // Switched on, but no ingest worker is running in this stack: the UI says so instead of pretending.
  await expect(page.getByText('not running.')).toBeVisible();
  await page.getByRole('checkbox', { name: 'Allow YouTube links' }).uncheck();
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('Saved.')).toBeVisible();
});

test('1 GB upload, interrupted by a network drop, resumes and produces a proxy that plays', async ({}, testInfo) => {
  test.skip(!process.env['E2E_LARGE'], 'SKIPPED: set E2E_LARGE=1 to run the 1 GB upload test');
  test.setTimeout(480_000);
  const big = testInfo.outputPath('big.avi');
  // Uncompressed 1080p: ~1.1 GB, a valid file every video tool can read, quick to generate.
  ffmpeg(['-f', 'lavfi', '-i', 'testsrc2=size=1920x1080:rate=30:duration=12', '-c:v', 'rawvideo', '-pix_fmt', 'yuv420p', big]);
  expect(statSync(big).size).toBeGreaterThan(1_073_741_824);

  await page.getByRole('link', { name: 'Projects' }).click();
  await page.getByLabel('New project name').fill('Big files');
  await page.getByRole('button', { name: 'Create project' }).click();
  await page.getByRole('checkbox', { name: /I own this content/ }).check();
  await page.locator('#file').setInputFiles(big);

  const bar = page.getByRole('progressbar', { name: /Uploading big/ });
  await expect.poll(async () => Number(await bar.getAttribute('aria-valuenow')), { timeout: 120_000 }).toBeGreaterThan(20);
  await ctx.setOffline(true); // the connection drops mid-upload
  await expect(page.getByText('Connection lost. Waiting to resume…')).toBeVisible({ timeout: 30_000 });
  const during = Number(await bar.getAttribute('aria-valuenow'));
  expect(during).toBeGreaterThan(0);
  expect(during).toBeLessThan(100); // it did not fail and it did not pretend to finish
  await ctx.setOffline(false); // the browser reports "online" and the upload continues by itself
  await expect(page.getByText('Uploaded, now processing')).toBeVisible({ timeout: 300_000 });

  const row = page.getByRole('listitem').filter({ hasText: 'big' });
  await expect(row.getByText('Ready')).toBeVisible({ timeout: 240_000 });
  await row.getByRole('link', { name: 'big' }).first().click();
  await expect(page.getByText('1920 × 1080')).toBeVisible();
  await expectPlayable(page, 12);
  const proxyHeight = await page.locator('video').evaluate((v: HTMLVideoElement) => v.videoHeight);
  expect(proxyHeight).toBe(720); // the browser plays the lightweight proxy, not the 1 GB original
});

test('no CSP violations or uncaught errors in the whole session', () => {
  expect(problems).toEqual([]);
});
