"""What users may choose for transcription. Plain data, importable without the speech library installed."""

from __future__ import annotations

# Whisper's languages, copied from faster-whisper's tokenizer (a test keeps the two in sync).
LANGUAGES = frozenset(
    "af am ar as az ba be bg bn bo br bs ca cs cy da de el en es et eu fa fi fo fr gl gu ha haw "
    "he hi hr ht hu hy id is it ja jw ka kk km kn ko la lb ln lo lt lv mg mi mk ml mn mr ms mt "
    "my ne nl nn no oc pa pl ps pt ro ru sa sd si sk sl sn so sq sr su sv sw ta te tg th tk tl "
    "tr tt uk ur uz vi yi yo zh yue".split()
)

# Offered in the UI. Sizes are the approximate download size of the CTranslate2 weights.
MODELS: dict[str, dict[str, object]] = {
    "tiny": {"mb": 75, "multilingual": True, "note": "Fastest, least accurate. Fine for testing."},
    "tiny.en": {"mb": 75, "multilingual": False, "note": "English only."},
    "base": {"mb": 145, "multilingual": True, "note": "Fast, basic accuracy."},
    "base.en": {"mb": 145, "multilingual": False, "note": "English only."},
    "small": {"mb": 465, "multilingual": True, "note": "Good balance on a CPU. The default."},
    "small.en": {"mb": 465, "multilingual": False, "note": "English only."},
    "medium": {"mb": 1500, "multilingual": True, "note": "More accurate, slow on a CPU."},
    "medium.en": {"mb": 1500, "multilingual": False, "note": "English only."},
    "large-v3-turbo": {
        "mb": 1600,
        "multilingual": True,
        "note": "Near large-v3 accuracy, much faster. Best with a GPU.",
    },
    "large-v3": {"mb": 3100, "multilingual": True, "note": "Most accurate. Needs a GPU for comfortable speed."},
    "distil-large-v3": {"mb": 1500, "multilingual": False, "note": "English only, fast and accurate."},
}


def normalize_language(value: str | None) -> str | None:
    """None or 'auto' -> None (auto-detect). Otherwise a Whisper language code, or ValueError."""
    if value is None or value.strip().lower() in ("", "auto"):
        return None
    code = value.strip().lower()
    if code not in LANGUAGES:
        raise ValueError(f"Unsupported language: {value}")
    return code
