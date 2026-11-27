import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, from, switchMap, throwError } from 'rxjs';

import { AuthService } from './auth.service';

const PUBLIC = ['/auth/login', '/auth/refresh', '/auth/accept-invite', '/auth/logout', '/setup/'];

export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(AuthService);
  const router = inject(Router);
  if (!req.url.startsWith('/api/') || PUBLIC.some((p) => req.url.includes(p))) {
    return next(req);
  }
  const withToken = (t: string | null) =>
    t ? req.clone({ setHeaders: { Authorization: `Bearer ${t}` } }) : req;

  return next(withToken(auth.accessToken())).pipe(
    catchError((err: HttpErrorResponse) => {
      const code: string | undefined = err.error?.code;
      if (err.status === 401 && code === 'token_expired') {
        return from(auth.refresh()).pipe(
          switchMap((ok) => {
            if (ok) return next(withToken(auth.accessToken()));
            void router.navigateByUrl('/login');
            return throwError(() => err);
          }),
        );
      }
      if (err.status === 401 && (code === 'account_disabled' || code === 'invalid_token')) {
        auth.clear();
        void router.navigateByUrl('/login');
      }
      return throwError(() => err);
    }),
  );
};
