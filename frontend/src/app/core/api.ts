import { HttpErrorResponse } from '@angular/common/http';

export type Role = 'admin' | 'editor' | 'viewer';

export interface User {
  id: string;
  email: string;
  display_name: string;
  role: Role;
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
}

export interface ApiErrorBody {
  code: string;
  message: string;
  details: unknown;
  request_id: string | null;
}

export function apiError(e: unknown): ApiErrorBody | null {
  if (e instanceof HttpErrorResponse && e.error && typeof e.error.code === 'string') {
    return e.error as ApiErrorBody;
  }
  return null;
}

/** A message that is safe to show to the user. */
export function errorMessage(e: unknown): string {
  const body = apiError(e);
  if (body) {
    if (body.code === 'validation_error' && Array.isArray(body.details) && body.details.length) {
      const first = body.details[0] as { loc?: string[]; msg?: string };
      const field = first.loc?.[first.loc.length - 1];
      return field ? `${field}: ${first.msg}` : (first.msg ?? body.message);
    }
    return body.message;
  }
  if (e instanceof HttpErrorResponse && e.status === 0) {
    return 'Cannot reach the server. Check that OpenClipper is running.';
  }
  return 'Something went wrong. Please try again.';
}
