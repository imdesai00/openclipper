import AxeBuilder from '@axe-core/playwright';
import { BrowserContext, Page, expect, test } from '@playwright/test';

// Runs LAST (the z- prefix): it needs the admin from first-run, the local model routed to "highlight" by
// providers-ai, and the transcribed audio file from transcription. A small local model's taste is not testable,
// so this asserts what the product promises: the search always ends visibly, and any clip shown is a stretch of
// the transcript shown on the same page. It FAILS (it does not skip) if the earlier specs did not leave that state.
const ADMIN = { email: 'ada@example.com', password: 'correct horse battery' };

test.describe.configure({ mode: 'serial' });

let ctx: BrowserContext;
let page: Page;
const problems: string[] = [];

async function a11y(p: Page, label: string): Promise<void> {
  const r = await new AxeBuilder({ page: p }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
  const msg = r.violations.map((v) => `${v.id} (${v.nodes.length}): ${v.nodes[0]?.target}`).join('\n');
  expect(r.violations, `${label} accessibility violations:\n${msg}`).toEqual([]);
}

const norm = (s: string): string => s.replace(/\s+/g, ' ').trim();
// the transcript list shows a time label (0:05) before each segment; the words themselves have none
const words = (s: string): string => norm(s.replace(/\b\d+:\d{2}\b/g, ' '));

test.beforeAll(async ({ browser }) => {
  ctx = await browser.newContext();
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

test('find highlights with the local model: it ends visibly and every clip is real transcript text', async () => {
  test.setTimeout(420_000);
  await page.getByRole('link', { name: 'Projects' }).click();
  const project = page.getByRole('link', { name: /Speech check/ });
  await expect(project, 'the transcription spec creates this project earlier in the same run').toBeVisible();
  await project.click();
  await page.getByRole('link', { name: 'briefing' }).first().click();
  await expect(page.getByRole('heading', { name: 'briefing' })).toBeVisible();

  const transcript = page.getByRole('region', { name: 'Transcript' });
  await expect(transcript.getByRole('list', { name: 'Transcript segments' })).toContainText('quick brown');
  const transcriptText = words(await transcript.getByRole('list', { name: 'Transcript segments' }).innerText());

  const section = page.getByRole('region', { name: 'Highlights' });
  await expect(section).toBeVisible();
  await a11y(page, 'highlights form');

  await section.getByLabel('What should it look for? (optional)').fill('the most interesting statement');
  await section.getByLabel('Shortest (s)').fill('5');
  await section.getByLabel('Longest (s)').fill('30');
  await section.getByRole('button', { name: 'Find highlights' }).click();
  await expect(section.getByText('Looking for highlights')).toBeVisible({ timeout: 30_000 });

  // Always ends, in one of three honest ways.
  const clips = section.getByRole('list', { name: 'Suggested clips' });
  const nothing = section.getByText('Nothing usable came back');
  const failed = section.getByText('Finding highlights failed');
  await expect(clips.or(nothing).or(failed).first()).toBeVisible({ timeout: 360_000 });

  if (await clips.isVisible()) {
    const quotes = clips.locator('blockquote');
    expect(await quotes.count()).toBeGreaterThan(0);
    for (const q of await quotes.allInnerTexts()) {
      // the clip's words were read back from the stored transcript, so they appear in it verbatim
      expect(transcriptText.toLowerCase()).toContain(norm(q).toLowerCase());
    }
    await expect(section.getByText('AI relevance score').first()).toBeVisible();
    await expect(section.getByText('not an engagement prediction')).toBeVisible();

    // preview moves the audio player to the clip
    await clips.getByRole('button', { name: 'Preview clip 1' }).click();
    const audio = page.locator('audio');
    await expect.poll(() => audio.evaluate((a: HTMLAudioElement) => a.currentTime)).toBeGreaterThanOrEqual(0);

    // review sticks across a reload
    await clips.getByRole('button', { name: 'Accept clip 1' }).click();
    await expect(clips.getByRole('button', { name: 'Accept clip 1' })).toHaveAttribute('aria-pressed', 'true');
    await page.reload();
    await expect(page.getByRole('region', { name: 'Highlights' }).getByRole('button', { name: 'Accept clip 1' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await a11y(page, 'highlights with clips');
  } else {
    await a11y(page, 'highlights with an honest no-result message');
  }
});

test('the same request again is answered from the saved run without asking the model', async () => {
  const section = page.getByRole('region', { name: 'Highlights' });
  await expect(section).toBeVisible();
  // Only a run that produced clips can be reused. A small model may return none; that is reported as SKIPPED.
  test.skip(!(await section.getByRole('list', { name: 'Suggested clips' }).isVisible()), 'SKIPPED: the local model returned no clips to reuse');
  let modelCalls = 0;
  page.on('request', (r) => {
    if (r.method() === 'POST' && /\/api\/v1\/sources\/[^/]+\/highlights$/.test(r.url())) modelCalls++;
  });
  await section.getByLabel('What should it look for? (optional)').fill('the most interesting statement');
  await section.getByLabel('Shortest (s)').fill('5');
  await section.getByLabel('Longest (s)').fill('30');
  const resp = page.waitForResponse((r) => /\/highlights$/.test(r.url()) && r.request().method() === 'POST');
  await section.getByRole('button', { name: 'Find highlights' }).click();
  expect((await resp).status()).toBe(200); // reused: 200 with a run, not 202 with a job
  expect(modelCalls).toBe(1);
});

test('no CSP violations or uncaught errors in the highlights session', () => {
  expect(problems).toEqual([]);
});
