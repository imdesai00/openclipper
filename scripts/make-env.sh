#!/usr/bin/env bash
# Create .env from .env.example with freshly generated secrets. Never overwrites an existing .env.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  echo ".env exists, leaving it alone"
  exit 0
fi

enc=$(python3 -c 'import os,base64;print(base64.b64encode(os.urandom(32)).decode())')
jwt=$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')
pg=$(python3 -c 'import secrets;print(secrets.token_hex(24))')

python3 - "$enc" "$jwt" "$pg" <<'PY'
import sys
enc, jwt, pg = sys.argv[1:4]
out = []
for line in open(".env.example"):
    if line.startswith("APP_ENCRYPTION_KEY="):
        line = f"APP_ENCRYPTION_KEY={enc}\n"
    elif line.startswith("APP_JWT_SECRET="):
        line = f"APP_JWT_SECRET={jwt}\n"
    elif line.startswith("POSTGRES_PASSWORD="):
        line = f"POSTGRES_PASSWORD={pg}\n"
    out.append(line)
open(".env", "w").writelines(out)
PY
chmod 600 .env
echo "created .env with fresh secrets (back up APP_ENCRYPTION_KEY: losing it makes stored credentials unrecoverable)"
