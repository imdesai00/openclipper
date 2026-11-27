#!/usr/bin/env python3
"""Generate THIRD_PARTY_LICENSES.md from the real dependency trees.

Python: runtime (non-dev) packages from backend/uv.lock, license read from installed metadata.
npm:    production packages of the frontend (what is bundled into the app).
Anything that is not clearly permissive is flagged for human review.

Usage: scripts/gen_licenses.py   (needs `uv sync` in backend/ and `npm ci` in frontend/)
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PERMISSIVE = re.compile(
    r"^(MIT|MIT-0|BSD|BSD-2-Clause|BSD-3-Clause|ISC|Apache-2\.0|Apache License|Apache Software License|0BSD|"
    r"PSF-2\.0|Python-2\.0|Unlicense|CC0-1\.0|BlueOak-1\.0\.0|Zlib|HPND|PSF|"
    r"BSD License|MIT License|ISC License)",
    re.I,
)
# Accepted copyleft that doesn't impose obligations on our code when used as a library/dep unmodified.
REVIEWED = {
    "certifi": "MPL-2.0 (CA bundle data, used unmodified)",
    # Metadata was ambiguous; confirmed by reading the license file shipped in each package (2026-11-27).
    "prompt-toolkit": "BSD-3-Clause (license file checked)",
    "wcwidth": "MIT (license file checked)",
    "click-plugins": "BSD-3-Clause (license file checked)",
    "python-dateutil": "Apache-2.0 / BSD dual (license file checked)",
    "combine-errors": "MIT (License.md checked; package.json has no license field)",
    "protobuf": "BSD-3-Clause (LICENSE file in the wheel checked)",
    "flatbuffers": "Apache-2.0 (metadata License and OSI classifier agree; the wheel ships no license file, upstream is google/flatbuffers)",
    "tokenizers": "Apache-2.0 (OSI classifier; metadata has no license field; Rust dependencies in the wheel's SBOM are MIT/Apache/BSD/Unlicense/Zlib expressions)",
    "tqdm": "MIT with MPL-2.0 for some files (LICENCE file checked); MPL is file-level and tqdm is used unmodified",
    # Checked 2026-11-27 by inspecting the installed wheel (see DECISIONS D-045). Needs a human to confirm for distribution.
    "av": "BSD-3-Clause for PyAV itself, BUT the wheel bundles FFmpeg libraries built with libx264/libx265 and "
          "--enable-version3 (GPL-3.0-or-later components). Used only because faster-whisper imports it; we never call it. "
          "Whoever redistributes the openclipper-asr image must honour the GPL for those libraries.",
}


def run(cmd: list[str], cwd: Path) -> str:
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout  # noqa: S603


def python_rows() -> list[tuple[str, str, str]]:
    # --extra asr: the speech worker image ships these too (see the Dockerfile `asr` target)
    out = run(["uv", "export", "--no-dev", "--extra", "asr", "--frozen", "--no-hashes", "--no-emit-project"],
              ROOT / "backend")
    names = sorted({m.group(1).lower() for m in re.finditer(r"^([A-Za-z0-9_.\-]+)==", out, re.M)})
    venv_site = next((ROOT / "backend/.venv/lib").glob("python3.*/site-packages"))
    dists = {d.metadata["Name"].lower().replace("_", "-"): d
             for d in metadata.distributions(path=[str(venv_site)])}
    rows = []
    for n in names:
        d = dists.get(n.replace("_", "-"))
        if d is None:  # platform-specific / optional, not installed on Linux: not part of what ships
            continue
        lic = d.metadata.get("License-Expression") or d.metadata.get("License") or ""
        if not lic or len(lic) > 80:
            cls = [c.split("::")[-1].strip() for c in d.metadata.get_all("Classifier") or []
                   if c.startswith("License ::")]
            lic = "; ".join(cls) or lic.splitlines()[0][:80] if lic else "UNKNOWN"
        rows.append((n, d.version, lic.strip()))
    return rows


def npm_rows() -> list[tuple[str, str, str]]:
    tree = json.loads(run(["npm", "ls", "--omit=dev", "--all", "--json", "--long"], ROOT / "frontend"))
    seen: dict[str, tuple[str, str, str]] = {}

    def walk(deps: dict) -> None:  # type: ignore[type-arg]
        for name, info in (deps or {}).items():
            lic = info.get("license") or "UNKNOWN"
            if isinstance(lic, dict):
                lic = lic.get("type", "UNKNOWN")
            if "version" not in info:  # optional peer dependency that is not installed
                continue
            seen[name] = (name, info["version"], str(lic))
            walk(info.get("dependencies", {}))

    walk(tree.get("dependencies", {}))
    return sorted(seen.values())


def flag(lic: str, name: str) -> str:
    if name in REVIEWED:
        return f"REVIEWED: {REVIEWED[name]}"
    return "" if PERMISSIVE.match(lic) else "REVIEW"


def table(rows: list[tuple[str, str, str]]) -> str:
    lines = ["| Package | Version | License | Flag |", "|---|---|---|---|"]
    for n, v, lic in rows:
        lines.append(f"| {n} | {v} | {lic} | {flag(lic, n)} |")
    return "\n".join(lines)


def main() -> int:
    py, js = python_rows(), npm_rows()
    flagged = [r for r in py + js if flag(r[2], r[0]) == "REVIEW"]
    doc = f"""# Third-party licenses

Generated by `scripts/gen_licenses.py` from the actual dependency trees. Do not edit by hand.
OpenClipper itself is AGPL-3.0-only (see `LICENSE`).

## Needs human review ({len(flagged)})
{chr(10).join(f"- {n} {v}: {lic}" for n, v, lic in flagged) or "None. Every runtime dependency below either matched a permissive license pattern or was checked by hand (marked REVIEWED)."}

## Container images (not generated; reviewed by hand)
| Image | License | Note |
|---|---|---|
| postgres:16-alpine | PostgreSQL License | permissive |
| valkey/valkey:8-alpine | BSD-3-Clause | chosen over Redis >= 7.4, which is source-available (RSALv2/SSPLv1), see DECISIONS D-016 |
| nginxinc/nginx-unprivileged | BSD-2-Clause | |
| ffmpeg / ffprobe (Debian package inside the backend image) | GPL-2.0-or-later (Debian builds include x264 and other GPL parts) | Run as a separate process through an argument list; never linked into OpenClipper's code, so it does not change our license. Anyone redistributing the image must honour the GPL for that package. |
| yt-dlp (optional `ingest` image only) | Unlicense | Downloads from sites may break their terms of service, see README |
| Deno (optional `ingest` image only) | MIT | JavaScript runtime that yt-dlp needs for YouTube |
| chrislusf/seaweedfs:4.55 (CI and the planned `s3` profile) | Apache-2.0 | Used for the S3 contract tests |
| ollama/ollama:0.40.18 | MIT | Runs local models. **Model weights are not part of OpenClipper and carry their own licenses** (for example Llama and Gemma have custom terms; Qwen 2.5 small models are Apache-2.0). Whoever downloads a model accepts its license. |
| Whisper model weights (downloaded at runtime, not shipped) | MIT | the Systran faster-whisper tiny, base and small model cards list `license:mit` (checked 2026-11-27); the original OpenAI Whisper repository is MIT. Other models in the list (medium, large-v3, distil-large-v3) were not individually checked: check a model's card before using it commercially. |
| python:3.12-slim, node:22-alpine | PSF / MIT (build tooling) | |

## Development / test tooling (not distributed)
Playwright (Apache-2.0), @axe-core/playwright and axe-core (MPL-2.0, used only in tests, never bundled),
Vitest (MIT), ESLint (MIT), Ruff (MIT), mypy (MIT), pytest (MIT).

## Python runtime dependencies (backend)
{table(py)}

## npm production dependencies (frontend bundle)
{table(js)}
"""
    (ROOT / "THIRD_PARTY_LICENSES.md").write_text(doc)
    print(f"wrote THIRD_PARTY_LICENSES.md: {len(py)} python, {len(js)} npm, {len(flagged)} flagged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
