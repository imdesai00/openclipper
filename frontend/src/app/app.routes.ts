import { Routes } from '@angular/router';

import { adminGuard, authGuard, guestGuard, loginGuard, setupGuard } from './core/guards';

export const routes: Routes = [
  {
    path: 'setup',
    canActivate: [setupGuard],
    title: 'Set up OpenClipper',
    loadComponent: () => import('./features/auth/setup.page').then((m) => m.SetupPage),
  },
  {
    path: 'login',
    canActivate: [loginGuard],
    title: 'Sign in',
    loadComponent: () => import('./features/auth/login.page').then((m) => m.LoginPage),
  },
  {
    path: 'accept-invite',
    canActivate: [guestGuard],
    title: 'Accept invitation',
    loadComponent: () =>
      import('./features/auth/accept-invite.page').then((m) => m.AcceptInvitePage),
  },
  {
    path: '',
    canActivate: [authGuard],
    loadComponent: () => import('./features/shell/shell.component').then((m) => m.ShellComponent),
    children: [
      {
        path: '',
        pathMatch: 'full',
        title: 'Dashboard',
        loadComponent: () =>
          import('./features/dashboard/dashboard.page').then((m) => m.DashboardPage),
      },
      {
        path: 'users',
        canActivate: [adminGuard],
        title: 'Users',
        loadComponent: () => import('./features/users/users.page').then((m) => m.UsersPage),
      },
    ],
  },
  { path: '**', redirectTo: '' },
];
