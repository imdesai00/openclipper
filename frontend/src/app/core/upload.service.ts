import { Injectable, inject } from '@angular/core';
import * as tus from 'tus-js-client';

import { AuthService } from './auth.service';

export interface UploadCallbacks {
  onProgress(percent: number, sent: number, total: number): void;
  onDone(sourceId: string): void;
  onError(message: string): void;
  /** The connection is gone; the upload stays paused and continues by itself when it returns. */
  onWaiting(): void;
  onResumed(): void;
}

const WAIT_RETRY_MS = 30_000;

export interface UploadHandle {
  abort(): void;
}

function responseOf(err: Error): tus.DetailedError['originalResponse'] {
  return (err as tus.DetailedError).originalResponse;
}

function messageFrom(err: Error): string {
  const body = (err as tus.DetailedError).originalResponse?.getBody();
  if (body) {
    try {
      const j = JSON.parse(body) as { message?: string };
      if (j.message) return j.message;
    } catch {
      /* not JSON */
    }
  }
  return 'The upload failed. Check your connection and try again.';
}

@Injectable({ providedIn: 'root' })
export class UploadService {
  private readonly auth = inject(AuthService);

  /** Resumable upload (tus). Survives dropped connections and page reloads. */
  start(file: File, projectId: string, cb: UploadCallbacks): UploadHandle {
    let sourceId: string | null = null;
    let aborted = false;
    let waiting = false;
    let waitTimer: ReturnType<typeof setTimeout> | null = null;
    let onlineListener: (() => void) | null = null;

    const stopWaiting = (): void => {
      if (waitTimer) clearTimeout(waitTimer);
      if (onlineListener) window.removeEventListener('online', onlineListener);
      waitTimer = onlineListener = null;
    };
    const markWaiting = (): void => {
      if (!waiting) {
        waiting = true;
        cb.onWaiting();
      }
    };
    const waitForConnection = (): void => {
      markWaiting();
      const resume = (): void => {
        stopWaiting();
        if (aborted) return;
        upload.start(); // continues from the offset the server reports
      };
      onlineListener = resume;
      window.addEventListener('online', resume);
      waitTimer = setTimeout(resume, WAIT_RETRY_MS); // also covers "online, but the server is unreachable"
    };

    const upload = new tus.Upload(file, {
      endpoint: '/api/v1/uploads',
      chunkSize: 50 * 1024 * 1024,
      retryDelays: [0, 1000, 3000, 5000, 10000, 20000],
      metadata: { filename: file.name, project_id: projectId, attested: 'true' },
      // tus-js-client refuses to retry while navigator.onLine is false, which would fail a big upload the
      // moment the network drops. Retry network errors and server hiccups ourselves.
      onShouldRetry: (err) => {
        const status = responseOf(err)?.getStatus() ?? 0;
        if (status === 0) markWaiting(); // tell the user straight away, while we keep retrying
        return (
          status === 0 ||
          status === 401 ||
          status === 409 ||
          status === 423 ||
          status === 429 ||
          status >= 500
        );
      },
      removeFingerprintOnSuccess: true, // a finished upload must not be "resumed" when the file is added again
      fingerprint: async (f) =>
        ['oc', projectId, (f as File).name, f.size, (f as File).lastModified].join('-'),
      onBeforeRequest: async (req) => {
        if (this.auth.expiresInMs() < 60_000) await this.auth.refresh(); // long uploads outlive a 15-minute token
        const token = this.auth.accessToken();
        if (token) req.setHeader('Authorization', `Bearer ${token}`);
      },
      onAfterResponse: (_req, res) => {
        const id = res.getHeader('X-Source-Id');
        if (id) sourceId = id;
      },
      onProgress: (sent, total) => {
        if (waiting) {
          waiting = false;
          cb.onResumed();
        }
        cb.onProgress(total ? (sent / total) * 100 : 0, sent, total);
      },
      onSuccess: () =>
        sourceId
          ? cb.onDone(sourceId)
          : cb.onError('The upload finished but no source was created.'),
      onError: (err) => {
        if (aborted) return;
        if (!responseOf(err))
          waitForConnection(); // network failure, not a refusal by the server
        else cb.onError(messageFrom(err));
      },
    });
    void upload.findPreviousUploads().then((prev) => {
      if (prev.length) upload.resumeFromPreviousUpload(prev[0]);
      upload.start();
    });
    return {
      abort: () => {
        aborted = true;
        stopWaiting();
        void upload.abort(true); // true also deletes the partial upload on the server
      },
    };
  }
}
