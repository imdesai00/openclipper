import AxeBuilder from '@axe-core/playwright';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { BrowserContext, Page, expect, test } from '@playwright/test';

// Real speech recognition in a real browser against the real stack: espeak-ng speaks, the tiny model listens.
// Runs after first-run.spec.ts and media.spec.ts (same fresh stack). SKIPPED (not passed) without espeak-ng.
const ADMIN = { email: 'ada@example.com', password: 'correct horse battery' };
const SENTENCE = 'The quick brown fox jumps over the lazy dog near the river bank.';

test.describe.configure({ mode: 'serial' });

let ctx: BrowserContext;
let page: Page;
const problems: string[] = [];

function hasEspeak(): boolean {
  try {
    execFileSync('espeak-ng', ['--version'], { stdio: 'ignore' });
    return true;
  } catch {
    return false;
  }
}

async function a11y(p: Page, label: string): Promise<void> {
  const r = await new AxeBuilder({ page: p }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
  const msg = r.violations.map((v) => `${v.id} (${v.nodes.length}): ${v.nodes[0]?.target}`).join('\n');
  expect(r.violations, `${label} accessibility violations:\n${msg}`).toEqual([]);
}

test.beforeAll(async ({ browser }) => {
  ctx = await browser.newContext({ acceptDownloads: true });
  page = await ctx.newPage();
  page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`));
  page.on('console', (m) => {
    if (m.type() === 'error' && /Content Security Policy|Refused to/i.test(m.text())) problems.push(m.text());
  });
  await page.goto('/login');
  await page.getByLabel('Email').fill(ADMIN.email);
  await page.getByLabel('Password').fill(ADMIN.password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
});

test.afterAll(async () => {
  await ctx.close();
});

test('settings show the speech service and download a model with visible progress', async () => {
  test.setTimeout(240_000);
  await page.getByRole('link', { name: 'Settings' }).click();
  const card = page.getByRole('region', { name: 'Speech recognition' });
  await expect(card.getByText('running', { exact: false }).first()).toBeVisible({ timeout: 30_000 });
  const tiny = card.getByRole('listitem').filter({ hasText: 'tiny' }).first();
  if (await tiny.getByRole('button', { name: 'Download model tiny' }).count()) {
    await tiny.getByRole('button', { name: 'Download model tiny' }).click();
  }
  await expect(tiny.getByText('Downloaded')).toBeVisible({ timeout: 200_000 });
  await a11y(page, 'settings with speech card');
});

test('upload spoken audio, transcribe it, and see the words in the browser', async ({}, testInfo) => {
  test.skip(!hasEspeak(), 'SKIPPED: espeak-ng is not installed (it generates the spoken test audio)');
  test.setTimeout(240_000);
  const wav = testInfo.outputPath('briefing.wav');
  execFileSync('espeak-ng', ['-s', '150', '-w', wav, `${SENTENCE} Artificial intelligence is changing how people edit video.`]);

  await page.getByRole('link', { name: 'Projects' }).click();
  await page.getByLabel('New project name').fill('Speech check');
  await page.getByRole('button', { name: 'Create project' }).click();
  await expect(page.getByRole('heading', { name: 'Speech check' })).toBeVisible();
  await page.getByRole('checkbox', { name: /I own this content/ }).check();
  await page.locator('#file').setInputFiles(wav);

  const row = page.getByRole('listitem').filter({ hasText: 'briefing' });
  await expect(row.getByText('Ready')).toBeVisible({ timeout: 90_000 });
  await row.getByRole('link', { name: 'briefing' }).first().click();

  // Audio-only: an audio player, an honest note, no video element.
  await expect(page.locator('audio')).toBeVisible();
  await expect(page.locator('video')).toHaveCount(0);
  await expect(page.getByText('Audio only: this file has no picture.')).toBeVisible();

  const section = page.getByRole('region', { name: 'Transcript' });
  await section.getByLabel('Speech model').selectOption('tiny');
  await section.getByRole('button', { name: 'Transcribe' }).click();
  await expect(section.getByText('Transcribing.')).toBeVisible({ timeout: 30_000 });

  const segments = section.getByRole('list', { name: 'Transcript segments' });
  await expect(segments).toContainText('quick brown', { timeout: 180_000 });
  await expect(section).toContainText('English');
  await a11y(page, 'source page with transcript');

  // Clicking a timestamp moves the player there.
  const audio = page.locator('audio');
  await expect.poll(() => audio.evaluate((a: HTMLAudioElement) => a.readyState), { timeout: 30_000 }).toBeGreaterThanOrEqual(1);
  await segments.getByRole('button').first().click();
  await expect.poll(() => audio.evaluate((a: HTMLAudioElement) => a.currentTime)).toBeGreaterThanOrEqual(0);

  // Editing creates version 2; the words come back changed.
  await section.getByRole('button', { name: 'Edit text' }).click();
  await segments.getByRole('button', { name: /^Edit segment/ }).first().click();
  const box = segments.getByRole('textbox');
  await box.fill('The slow green turtle walks along the river bank.');
  await section.getByRole('button', { name: 'Save' }).click();
  await expect(segments).toContainText('slow green turtle');
  await expect(section).toContainText('version 2');

  // History keeps the original and can bring it back as a new version.
  await section.getByRole('button', { name: 'History' }).click();
  await section.getByRole('button', { name: 'Restore this version' }).click();
  await expect(section).toContainText('version 3');
  await expect(segments).toContainText('quick brown');

  // Export downloads a real SRT file with timestamps.
  const [dl] = await Promise.all([page.waitForEvent('download'), section.getByRole('button', { name: 'Export SRT' }).click()]);
  expect(dl.suggestedFilename()).toBe('transcript.srt');
  const srt = readFileSync(await dl.path(), 'utf8');
  expect(srt).toMatch(/^1\n00:00:\d\d,\d{3} --> 00:00:\d\d,\d{3}\n/);
  expect(srt.toLowerCase()).toContain('quick brown');
});

test('no CSP violations or uncaught errors in the transcription session', () => {
  expect(problems).toEqual([]);
});
