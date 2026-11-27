"""EXTERNAL tier: real hosted providers. Runs only with credentials; otherwise SKIPPED, never passed.

TEST_OPENROUTER_API_KEY=... TEST_OPENROUTER_MODEL=meta-llama/llama-3.2-3b-instruct:free
(also TEST_OPENAI_*, TEST_ANTHROPIC_*, TEST_GEMINI_*, TEST_GROQ_*; each needs _API_KEY and _MODEL)
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest

from tests.conftest import make_client
from tests.helpers import API, add_provider, ask, discover, route, set_mode, setup_admin

pytestmark = [pytest.mark.integration, pytest.mark.external]
KINDS = ["openrouter", "openai", "anthropic", "gemini", "groq"]


@pytest.mark.parametrize("kind", KINDS)
async def test_real_hosted_provider(app_factory, kind: str) -> None:  # type: ignore[no-untyped-def]
    key, model = os.environ.get(f"TEST_{kind.upper()}_API_KEY"), os.environ.get(f"TEST_{kind.upper()}_MODEL")
    if not key or not model:
        pytest.skip(f"SKIPPED (no credentials): set TEST_{kind.upper()}_API_KEY and TEST_{kind.upper()}_MODEL")
    app = await app_factory()
    async with make_client(app) as c:
        h = await setup_admin(c)
        p = await add_provider(c, h, kind=kind, name=kind, api_key=key, base_url=None, is_local=False)
        t = (await c.post(f"{API}/providers/{p['id']}/test", headers=h)).json()
        assert t["ok"], t["message"]
        models = await discover(c, h, p["id"])
        assert models, "the provider listed no models"
        m = next((x for x in models if x["model_id"] == model or x["model_id"].endswith(model)), None)
        assert m is not None, f"{model} is not in the provider's model list"
        await route(c, h, "highlight", m["id"])
        await set_mode(c, h, "REMOTE")
        r = await ask(c, h, "Reply with the single word: pong", max_tokens=16)
        assert r.status_code == 200, r.text
        out = r.json()
        assert "pong" in out["text"].lower() and out["tokens_in"] > 0 and out["tokens_out"] > 0
        assert out["cost_est"] is None or Decimal(out["cost_est"]) >= 0
        assert key not in r.text and key not in (await c.get(f"{API}/providers", headers=h)).text
