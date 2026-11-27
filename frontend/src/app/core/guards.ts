import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';

import { AuthService } from './auth.service';

/** Signed-in users only. Fresh installs go to the setup wizard, everyone else to login. */
export const authGuard: CanActivateFn = async () => {
  const auth = inject(AuthService);
  const router = inject(Router);
  if (auth.user()) return true;
  return router.parseUrl((await auth.setupRequired()) ? '/setup' : '/login');
};

/** Pages for signed-out users (accept-invite): signed-in users are sent home. */
export const guestGuard: CanActivateFn = () => {
  const router = inject(Router);
  return inject(AuthService).user() ? router.parseUrl('/') : true;
};

export const loginGuard: CanActivateFn = async () => {
  const auth = inject(AuthService);
  const router = inject(Router);
  if (auth.user()) return router.parseUrl('/');
  return (await auth.setupRequired()) ? router.parseUrl('/setup') : true;
};

/** The wizard is only reachable while no admin exists. */
export const setupGuard: CanActivateFn = async () => {
  const auth = inject(AuthService);
  const router = inject(Router);
  if (auth.user()) return router.parseUrl('/');
  return (await auth.setupRequired()) ? true : router.parseUrl('/login');
};

export const adminGuard: CanActivateFn = () => {
  const router = inject(Router);
  return inject(AuthService).isAdmin() ? true : router.parseUrl('/');
};
