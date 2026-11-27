import AxeBuilder from '@axe-core/playwright';
import { execFileSync, spawnSync } from 'node:child_process';
import { BrowserContext, Page, expect, test } from '@playwright/test';

// The whole editing flow in a real browser against the real stack: real speech, real transcription, a real render in
// the container, and the downloaded file inspected with ffprobe/ffmpeg. Runs after transcription.spec.ts (it needs the
// speech model that spec downloads). SKIPPED (not passed) without espeak-ng.
const ADMIN = { email: 'ada@example.com', password: 'correct horse battery' };
const SPEECH =
  'The quick brown fox jumps over the lazy dog near the river. Artificial intelligence is changing how people edit video and make short clips. Please remember to subscribe and thank you for watching.';

test.describe.configure({ mode: 'serial' });

let ctx: BrowserContext;
let page: Page;
const problems: string[] = [];
let editorUrl = '';

function has(cmd: string, arg = '--version'): boolean {
  try {
    execFileSync(cmd, [arg], { stdio: 'ignore' });
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

/** Raw RGB of one frame of a file, via ffmpeg. */
function frame(file: string, t: number, w: number, h: number): Buffer {
  return execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-ss', String(t), '-i', file, '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], {
    maxBuffer: 1 << 28,
  });
}

/** Rows (first, last) that contain caption-coloured pixels (white or yellow), and how many such pixels there are. */
function inkRows(rgb: Buffer, w: number, h: number, fromRow: number): { first: number; last: number; count: number } {
  let first = -1;
  let last = -1;
  let count = 0;
  for (let y = fromRow; y < h; y++) {
    let any = false;
    for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 3;
      const r = rgb[i], g = rgb[i + 1], b = rgb[i + 2];
      if ((r > 215 && g > 215 && b > 215) || (r > 215 && g > 170 && b < 90)) {
        any = true;
        count++;
      }
    }
    if (any) {
      if (first < 0) first = y;
      last = y;
    }
  }
  return { first, last, count };
}

test.beforeAll(async ({ browser }) => {
  ctx = await browser.newContext({ acceptDownloads: true, viewport: { width: 1400, height: 1000 } });
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

test('upload a video with speech and transcribe it', async ({}, testInfo) => {
  test.skip(!has('espeak-ng') || !has('ffmpeg', '-version'), 'SKIPPED: espeak-ng or ffmpeg is not installed');
  test.setTimeout(300_000);
  const wav = testInfo.outputPath('talk.wav');
  const mp4 = testInfo.outputPath('talk.mp4');
  execFileSync('espeak-ng', ['-s', '150', '-w', wav, SPEECH]);
  // a plain dark picture, so any bright pixel in the finished clip is a caption
  execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=0x20242c:s=640x360:r=25', '-i', wav, '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', mp4]);

  await page.getByRole('link', { name: 'Projects' }).click();
  await page.getByLabel('New project name').fill('Clip check');
  await page.getByRole('button', { name: 'Create project' }).click();
  await expect(page.getByRole('heading', { name: 'Clip check' })).toBeVisible();
  await page.getByRole('checkbox', { name: /I own this content/ }).check();
  await page.locator('#file').setInputFiles(mp4);
  const row = page.getByRole('listitem').filter({ hasText: 'talk' });
  await expect(row.getByText('Ready')).toBeVisible({ timeout: 90_000 });
  await row.getByRole('link', { name: 'talk' }).first().click();

  const section = page.getByRole('region', { name: 'Transcript' });
  await section.getByLabel('Speech model').selectOption('tiny');
  await section.getByRole('button', { name: 'Transcribe' }).click();
  await expect(section.getByRole('list', { name: 'Transcript segments' })).toContainText('quick brown', { timeout: 180_000 });
});

test('make a clip, edit it in the editor and save a new version', async () => {
  test.skip(!has('espeak-ng'), 'SKIPPED: espeak-ng is not installed');
  test.setTimeout(120_000);
  const clips = page.getByRole('region', { name: 'Clips' });
  await clips.getByLabel('Starts at').fill('0:00');
  await clips.getByLabel('Ends at').fill('0:12');
  await clips.getByRole('button', { name: 'Make a clip' }).click();
  await expect(page.getByLabel('Clip name')).toBeVisible({ timeout: 30_000 });
  editorUrl = page.url();
  await expect(page.getByText('version 1')).toBeVisible();
  await expect(page.locator('video')).toBeVisible();
  await expect(page.getByTestId('words')).toContainText('quick');
  await a11y(page, 'clip editor');

  // the captions appear over the picture while the clip plays, drawn by the server's own compiler
  await page.getByRole('button', { name: 'Play clip' }).click();
  await expect(page.getByTestId('caption-preview')).toBeVisible({ timeout: 20_000 });
  await page.getByRole('button', { name: 'Pause' }).click();

  await page.getByRole('tab', { name: 'Captions' }).click();
  await page.getByLabel('Template').selectOption({ label: 'Karaoke' });
  await expect(page.getByText('unsaved changes')).toBeVisible();
  await page.getByRole('button', { name: /Save as version 2/ }).click();
  await expect(page.getByText('version 2')).toBeVisible();
  await expect(page.getByText('unsaved changes')).toHaveCount(0);
  await page.getByLabel('Template').selectOption({ label: 'Bold highlight' });
  await page.getByRole('button', { name: /Save as version 3/ }).click();
  await expect(page.getByText('version 3')).toBeVisible();
});

test('render the final clip, download it, and check the real file', async ({}, testInfo) => {
  test.skip(!has('espeak-ng'), 'SKIPPED: espeak-ng is not installed');
  test.setTimeout(300_000);
  await page.getByRole('tab', { name: 'Renders' }).click();
  await page.getByRole('button', { name: 'Render the final clip' }).click();
  const item = page.getByRole('listitem', { name: /Final render of version 3/ });
  await expect(item.getByText(/Quality checks:/)).toBeVisible({ timeout: 240_000 });
  await expect(item).toContainText('1080×1920');
  await expect(item).not.toContainText('Quality checks: failed');
  await a11y(page, 'clip editor with a finished render');

  const [dl] = await Promise.all([page.waitForEvent('download'), item.getByRole('link', { name: 'Download the MP4' }).click()]);
  expect(dl.suggestedFilename()).toMatch(/\.mp4$/);
  const file = testInfo.outputPath('final.mp4');
  await dl.saveAs(file);

  const info = JSON.parse(execFileSync('ffprobe', ['-v', 'error', '-show_streams', '-show_format', '-of', 'json', file]).toString());
  const v = info.streams.find((s: { codec_type: string }) => s.codec_type === 'video');
  const a = info.streams.find((s: { codec_type: string }) => s.codec_type === 'audio');
  expect([v.codec_name, v.width, v.height, v.pix_fmt, a.codec_name]).toEqual(['h264', 1080, 1920, 'yuv420p', 'aac']);
  expect(Math.abs(parseFloat(info.format.duration) - 12)).toBeLessThan(0.3);

  const loud = spawnSync('ffmpeg', ['-hide_banner', '-nostats', '-i', file, '-af', 'ebur128=peak=true', '-f', 'null', '-'], { encoding: 'utf8' });
  const lufs = /I:\s+(-?[\d.]+) LUFS/g;
  let integrated = NaN;
  for (const m of loud.stderr.matchAll(lufs)) integrated = parseFloat(m[1]);
  expect(Math.abs(integrated - -14), `integrated loudness ${integrated} LUFS`).toBeLessThan(2);

  // captions are burned into the render: bright text in the lower part of a dark picture
  const ink = inkRows(frame(file, 3.0, 1080, 1920), 1080, 1920, 1100);
  expect(ink.count, 'caption pixels in the rendered frame').toBeGreaterThan(500);
  testInfo.annotations.push({ type: 'render-ink', description: JSON.stringify(ink) });
});

test('the hand-edited text is checked against platform limits and survives a reload', async () => {
  test.skip(!has('espeak-ng'), 'SKIPPED: espeak-ng is not installed');
  await page.getByRole('tab', { name: 'Titles and text' }).click();
  await page.getByRole('tab', { name: 'YouTube' }).click();
  const title = page.locator('#meta-title');
  await title.fill('x'.repeat(101));
  await expect(page.getByText('(101 / 100)')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save YouTube text' })).toBeDisabled();
  await title.fill('Why the quick brown fox matters');
  await page.getByRole('button', { name: 'Save YouTube text' }).click();
  await expect(page.getByText('Saved.')).toBeVisible();
  await page.reload();
  await page.getByRole('tab', { name: 'Titles and text' }).click();
  await page.getByRole('tab', { name: 'YouTube' }).click();
  await expect(page.locator('#meta-title')).toHaveValue('Why the quick brown fox matters');
  await expect(page.getByText('edited by you')).toBeVisible();
  await a11y(page, 'clip text tab');
});

test('the dashboard counts the clip and the source page lists it', async () => {
  test.skip(!has('espeak-ng'), 'SKIPPED: espeak-ng is not installed');
  await page.getByRole('link', { name: 'Dashboard' }).click();
  await expect(page.getByRole('term').filter({ hasText: 'Clips rendered' })).toBeVisible();
  const dd = page.locator('dl[aria-label="Totals"] > div').filter({ hasText: 'Clips rendered' }).locator('dd');
  await expect(dd).toHaveText(/^[1-9]/);
  await expect(page.getByRole('region', { name: 'Recent clips' }).getByRole('link').first()).toBeVisible();
  await page.goto(editorUrl);
  await expect(page.getByLabel('Clip name')).toBeVisible();
});

test('no CSP violations or uncaught errors in the clip session', () => {
  expect(problems).toEqual([]);
});
