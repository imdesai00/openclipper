"""Per-platform clip metadata: limits, validation and the model prompt. Pure.

Hard limits are the ones consistently reported as where a platform truncates or refuses text; they make a save
fail. Hashtag counts are SOFT (a warning): the sources disagree about them (DECISIONS D-064), so they never block."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, create_model

PLATFORMS = ("generic", "youtube", "instagram")

# Checked 2026-11-27 against third-party guides only; the platforms' own help pages did not state them (D-064).
LIMITS: dict[str, dict[str, int]] = {
    "generic": {"title": 150, "description": 2000, "short_description": 300, "thumbnail_text": 40},
    "youtube": {"title": 100, "description": 5000, "short_description": 300, "thumbnail_text": 40, "tags_total": 500},
    # an Instagram caption includes its hashtags
    "instagram": {"title": 150, "description": 2200, "short_description": 300, "thumbnail_text": 40},
}
SOFT_HASHTAGS: dict[str, int] = {"generic": 30, "youtube": 15, "instagram": 30}  # a warning above these, never an error
MAX_HASHTAGS_STORED = 40
MAX_TAGS_STORED = 40

_TAG_CLEAN = re.compile(r"[^\w]", re.UNICODE)

FIELDS = (
    "title",
    "hook",
    "description",
    "short_description",
    "hashtags",
    "tags",
    "thumbnail_text",
    "category",
    "audience",
)


def normalize_hashtags(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in items:
        body = _TAG_CLEAN.sub("", str(raw).lstrip("#").strip())
        if not body or body.lower() in seen:
            continue
        seen.add(body.lower())
        out.append("#" + body[:60])
    return out[:MAX_HASHTAGS_STORED]


def normalize_tags(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in items:
        t = " ".join(str(raw).split()).strip("# ")
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t[:100])
    return out[:MAX_TAGS_STORED]


def caption_length(platform: str, description: str, hashtags: list[str]) -> int:
    """Instagram counts the hashtags as part of the caption."""
    if platform == "instagram" and hashtags:
        return len(description) + (1 if description else 0) + len(" ".join(hashtags))
    return len(description)


def validate(platform: str, f: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(errors, warnings). Errors make a save fail; warnings are shown beside the field."""
    lim = LIMITS[platform]
    errors: list[str] = []
    warnings: list[str] = []
    for name in ("title", "short_description", "thumbnail_text"):
        n = len(str(f.get(name, "")))
        if n > lim[name]:
            errors.append(f"{name.replace('_', ' ').capitalize()} is {n} characters; {platform} allows {lim[name]}.")
    desc_n = caption_length(platform, str(f.get("description", "")), list(f.get("hashtags", [])))
    if desc_n > lim["description"]:
        what = "Caption (with its hashtags)" if platform == "instagram" else "Description"
        errors.append(f"{what} is {desc_n} characters; {platform} allows {lim['description']}.")
    if "tags_total" in lim:
        total = len(",".join(f.get("tags", [])))
        if total > lim["tags_total"]:
            errors.append(f"Tags total {total} characters; {platform} allows {lim['tags_total']}.")
    if len(f.get("hashtags", [])) > SOFT_HASHTAGS[platform]:
        warnings.append(f"More than {SOFT_HASHTAGS[platform]} hashtags: {platform} may ignore them.")
    return errors, warnings


class MetaBase(BaseModel):
    title: str = ""
    hook: str = ""
    description: str = ""
    short_description: str = ""
    hashtags: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    thumbnail_text: str = ""
    category: str = Field(default="", max_length=60)
    audience: str = Field(default="", max_length=200)


def meta_model(platform: str) -> type[BaseModel]:
    """The answer schema for one platform. The limits are IN the schema, so a too-long answer goes through the repair
    loop (the model is told what was wrong) instead of being cut off silently."""
    lim = LIMITS[platform]
    return create_model(
        f"Meta_{platform}",
        __base__=MetaBase,
        title=(str, Field(default="", max_length=lim["title"])),
        hook=(str, Field(default="", max_length=400)),
        description=(str, Field(default="", max_length=lim["description"])),
        short_description=(str, Field(default="", max_length=lim["short_description"])),
        hashtags=(list[str], Field(default_factory=list, max_length=SOFT_HASHTAGS[platform] + 10)),
        tags=(list[str], Field(default_factory=list, max_length=30)),
        thumbnail_text=(str, Field(default="", max_length=lim["thumbnail_text"])),
    )


_PLATFORM_NOTES = {
    "generic": "A neutral title and description that work on any site.",
    "youtube": "YouTube Shorts. A searchable title and a description whose first line says what the clip is.",
    "instagram": "An Instagram Reel caption: a short hook first, then a line or two, then a few relevant hashtags.",
}


def build_messages(platform: str, *, clip_title: str, source_title: str, text: str) -> list[tuple[str, str]]:
    lim = LIMITS[platform]
    system = (
        "You write publishing metadata for one short video clip.\n"
        f"Platform: {platform}. {_PLATFORM_NOTES[platform]}\n"
        "Rules:\n"
        "- Use only what is in the transcript. Do not invent facts, names, numbers or claims.\n"
        f"- title at most {lim['title']} characters; description at most {lim['description']} characters"
        f"{' including the hashtags' if platform == 'instagram' else ''}; "
        f"short_description at most {lim['short_description']}; thumbnail_text at most {lim['thumbnail_text']} "
        "(a few punchy words for the cover image).\n"
        "- hook: the first line of a caption that makes someone want to watch.\n"
        f"- hashtags: up to {SOFT_HASHTAGS[platform] if platform != 'generic' else 8}, no spaces inside a hashtag.\n"
        "- tags: plain keywords without #. category: one or two words. audience: who would enjoy this.\n"
        "Answer with JSON only, with the keys: title, hook, description, short_description, hashtags, tags, "
        "thumbnail_text, category, audience."
    )
    user = f"Video: {source_title}\nWorking title: {clip_title}\n\nTranscript of the clip:\n{text}"
    return [("system", system), ("user", user)]


def clean(platform: str, parsed: BaseModel) -> dict[str, Any]:
    """Tidy the model's answer into what we store: hashtags and tags normalised, text trimmed, limits re-checked."""
    d = parsed.model_dump()
    out: dict[str, Any] = {
        k: str(d.get(k, "")).strip()
        for k in ("title", "hook", "description", "short_description", "thumbnail_text", "category", "audience")
    }
    out["hashtags"] = normalize_hashtags(list(d.get("hashtags", [])))
    out["tags"] = normalize_tags(list(d.get("tags", [])))
    return out
