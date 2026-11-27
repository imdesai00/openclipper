import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import { Role, User } from '../../core/api';

export interface Invite {
  email: string;
  role: Role;
  invite_token: string;
  expires_at: string;
}

@Injectable({ providedIn: 'root' })
export class UsersService {
  private readonly http = inject(HttpClient);

  async listAll(): Promise<User[]> {
    const out: User[] = [];
    let cursor: string | null = null;
    do {
      const params: Record<string, string> = { limit: '100' };
      if (cursor) params['cursor'] = cursor;
      const page: { items: User[]; next_cursor: string | null } = await firstValueFrom(
        this.http.get<{ items: User[]; next_cursor: string | null }>('/api/v1/users', { params }),
      );
      out.push(...page.items);
      cursor = page.next_cursor;
    } while (cursor);
    return out;
  }

  invite(email: string, role: Role): Promise<Invite> {
    return firstValueFrom(this.http.post<Invite>('/api/v1/users/invite', { email, role }));
  }

  update(id: string, patch: { role?: Role; is_active?: boolean }): Promise<User> {
    return firstValueFrom(this.http.patch<User>(`/api/v1/users/${id}`, patch));
  }
}
