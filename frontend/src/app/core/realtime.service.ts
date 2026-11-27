import { HttpClient } from '@angular/common/http';
import { Injectable, inject, signal } from '@angular/core';
import { Subject, firstValueFrom } from 'rxjs';

import { RealtimeEvent } from './models';

const MAX_BACKOFF_MS = 30_000;

/** Live job progress over a WebSocket. Reconnects with backoff; REST remains the source of truth. */
@Injectable({ providedIn: 'root' })
export class RealtimeService {
  private readonly http = inject(HttpClient);
  private ws: WebSocket | null = null;
  private wanted = false;
  private attempt = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private pinger: ReturnType<typeof setInterval> | null = null;

  readonly events = new Subject<RealtimeEvent>();
  readonly connected = signal(false);

  start(): void {
    if (this.wanted) return;
    this.wanted = true;
    void this.connect();
  }

  stop(): void {
    this.wanted = false;
    if (this.timer) clearTimeout(this.timer);
    if (this.pinger) clearInterval(this.pinger);
    this.ws?.close();
    this.ws = null;
    this.connected.set(false);
  }

  private async connect(): Promise<void> {
    if (!this.wanted) return;
    try {
      const { ticket } = await firstValueFrom(
        this.http.post<{ ticket: string }>('/api/v1/ws/ticket', null),
      );
      const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
      const ws = new WebSocket(
        `${scheme}://${location.host}/ws/jobs?ticket=${encodeURIComponent(ticket)}`,
      );
      this.ws = ws;
      ws.onopen = () => {
        this.attempt = 0;
        this.connected.set(true);
        this.pinger = setInterval(
          () => ws.readyState === WebSocket.OPEN && ws.send('ping'),
          25_000,
        );
      };
      ws.onmessage = (m) => {
        try {
          const ev = JSON.parse(String(m.data)) as RealtimeEvent;
          if (ev.type !== 'pong') this.events.next(ev);
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onclose = () => {
        this.connected.set(false);
        if (this.pinger) clearInterval(this.pinger);
        this.schedule();
      };
    } catch {
      this.schedule();
    }
  }

  private schedule(): void {
    if (!this.wanted) return;
    const delay = Math.min(MAX_BACKOFF_MS, 1000 * 2 ** this.attempt++);
    this.timer = setTimeout(() => void this.connect(), delay);
  }
}
