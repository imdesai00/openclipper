import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { StatusBadgeComponent } from './status-badge.component';

describe('StatusBadgeComponent', () => {
  function render(status: string): HTMLElement {
    const f = TestBed.createComponent(StatusBadgeComponent);
    f.componentRef.setInput('status', status);
    f.detectChanges();
    return f.nativeElement;
  }

  it('always shows the status as text, not just a colour', () => {
    expect(render('ready').textContent?.trim()).toBe('Ready');
    expect(render('failed').textContent?.trim()).toBe('Failed');
    expect(render('skipped').textContent?.trim()).toBe('Reused');
  });

  it('falls back to the raw value for unknown statuses', () => {
    expect(render('weird').textContent?.trim()).toBe('weird');
  });
});
