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
        path: 'projects',
        title: 'Projects',
        loadComponent: () =>
          import('./features/projects/projects.page').then((m) => m.ProjectsPage),
      },
      {
        path: 'projects/:id',
        title: 'Project',
        loadComponent: () => import('./features/projects/project.page').then((m) => m.ProjectPage),
      },
      {
        path: 'sources/:id',
        title: 'Video',
        loadComponent: () => import('./features/sources/source.page').then((m) => m.SourcePage),
      },
      {
        path: 'settings',
        canActivate: [adminGuard],
        title: 'Settings',
        loadComponent: () =>
          import('./features/settings/settings.page').then((m) => m.SettingsPage),
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
