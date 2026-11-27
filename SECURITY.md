# Security policy

## Reporting a vulnerability
Please **do not open a public issue** for security problems. Use GitHub's private vulnerability reporting
("Security" tab → "Report a vulnerability") on this repository. Include what you found, how to reproduce it,
and the version or commit. You will get an acknowledgement; there is no bounty program.

## Supported versions
Pre-release. Only the latest commit on the default branch is supported.

## What the project does today
- Passwords: Argon2id. Login errors do not reveal whether an email exists.
- Sessions: 15-minute access tokens kept in memory only; refresh tokens are random, stored hashed, rotated on every
  use, in an httpOnly `SameSite=Strict` cookie. Re-use of a rotated token revokes the whole session family.
- Cookie-authenticated endpoints require a custom header (cross-site CSRF defence in addition to SameSite).
- Rate limits on login (per IP and per email) and on the API overall.
- Registration is closed; the first account is created through the setup wizard (serialized, one-time), later
  accounts only by admin invitation.
- The API refuses to start without `APP_ENCRYPTION_KEY` and `APP_JWT_SECRET`.
- Containers run as non-root; only the web port is published.

## Operator responsibilities
- Serve over HTTPS and set `COOKIE_SECURE=true` for anything reachable beyond localhost.
- Keep `.env` private and back up `APP_ENCRYPTION_KEY` (losing it makes stored credentials unrecoverable).
- Keep `ENABLE_API_DOCS=false` on exposed instances.
- Behind a reverse proxy, make sure it overwrites (not appends to) `X-Forwarded-For`; the bundled nginx does.

## Known limitations
See `docs/PROGRESS.md` ("Known issues"). Security headers, CSP and auth are covered by automated tests, but the
project has had **no independent security review**.
