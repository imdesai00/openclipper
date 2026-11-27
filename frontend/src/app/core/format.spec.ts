import { describe, expect, it } from 'vitest';

import { formatBytes, formatDuration } from './format';

describe('formatBytes', () => {
  it.each([
    [null, '—'],
    [0, '0 B'],
    [999, '999 B'],
    [1000, '1.0 KB'],
    [1_500_000, '1.5 MB'],
    [1_073_741_824, '1.1 GB'],
    [250_000_000_000, '250 GB'],
  ])('%s -> %s', (n, out) => expect(formatBytes(n)).toBe(out));
});

describe('formatDuration', () => {
  it.each([
    [null, '—'],
    [0, '0:00'],
    [59.6, '1:00'],
    [75, '1:15'],
    [3600, '1:00:00'],
    [7325, '2:02:05'],
  ])('%s -> %s', (s, out) => expect(formatDuration(s)).toBe(out));
});
