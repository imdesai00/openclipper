"""Operator commands: `python -m app.cli <command>`.

rotate-encryption-key   Re-wrap every stored secret's data key with the CURRENT master key.
                        Set APP_ENCRYPTION_KEY to the new key and APP_ENCRYPTION_KEY_PREVIOUS to the old one first.
                        The secrets themselves are never decrypted. Afterwards the old key can be removed.
check-encryption        Verify that every stored secret can be decrypted with the configured keys.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db.models import EncryptedCredential
from app.db.session import make_engine, make_sessionmaker
from app.security.crypto import CryptoError, Sealed, Vault


async def _run(command: str, dry_run: bool) -> int:
    settings = get_settings()
    vault = Vault.from_settings(settings)
    engine = make_engine(settings.database_url, poolclass=NullPool)
    done = skipped = bad = 0
    try:
        async with make_sessionmaker(engine)() as s:
            for c in (await s.execute(select(EncryptedCredential).with_for_update())).scalars():
                aad = f"ai-credential|{c.provider_id}".encode()
                sealed = Sealed(c.ciphertext, c.wrapped_dek, c.key_version)
                try:
                    if command == "check-encryption":
                        vault.decrypt(sealed, aad)
                        done += 1
                    elif vault.needs_rotation(sealed):
                        if not dry_run:
                            n = vault.rewrap(sealed, aad)
                            c.wrapped_dek, c.key_version = n.wrapped_dek, n.key_version
                        done += 1
                    else:
                        skipped += 1
                except CryptoError:
                    bad += 1
                    print(
                        f"cannot open credential {c.id} (provider {c.provider_id}): wrong key or damaged record",
                        file=sys.stderr,
                    )
            if command == "rotate-encryption-key" and not dry_run and not bad:
                await s.commit()
            elif command == "rotate-encryption-key" and bad:
                await s.rollback()
                print("Nothing was changed because some records could not be opened.", file=sys.stderr)
    finally:
        await engine.dispose()
    verb = {
        "check-encryption": "readable",
        "rotate-encryption-key": "would be re-wrapped" if dry_run else "re-wrapped",
    }[command]
    print(f"{done} {verb}, {skipped} already current, {bad} failed")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="python -m app.cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("command", choices=["rotate-encryption-key", "check-encryption"])
    ap.add_argument("--dry-run", action="store_true", help="report what would change without writing")
    a = ap.parse_args()
    return asyncio.run(_run(a.command, a.dry_run))


if __name__ == "__main__":
    sys.exit(main())
