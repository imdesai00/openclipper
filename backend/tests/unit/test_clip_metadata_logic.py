from __future__ import annotations

import pytest

from app.services import clip_metadata_logic as ml

pytestmark = pytest.mark.unit


def test_hashtags_are_cleaned_deduplicated_and_prefixed() -> None:
    assert ml.normalize_hashtags(["Sleep", "#sleep", "sleep time", "  #Health!  ", "", "###", "AI"]) == [
        "#Sleep",
        "#sleeptime",
        "#Health",
        "#AI",
    ]


def test_tags_are_plain_keywords() -> None:
    assert ml.normalize_tags(["#sleep", "  deep   sleep ", "Sleep", ""]) == ["sleep", "deep sleep"]


def fields(**kw):  # noqa: ANN003, ANN201
    base = {
        "title": "T",
        "description": "D",
        "short_description": "S",
        "thumbnail_text": "X",
        "hashtags": [],
        "tags": [],
    }
    base.update(kw)
    return base


def test_hard_limits_fail_and_name_the_field() -> None:
    errs, _ = ml.validate("youtube", fields(title="x" * 101, description="d" * 5001, thumbnail_text="t" * 41))
    assert len(errs) == 3 and any("Title is 101" in e for e in errs) and any("Description is 5001" in e for e in errs)
    assert ml.validate("youtube", fields(title="x" * 100, description="d" * 5000))[0] == []
    errs, _ = ml.validate("youtube", fields(tags=["a" * 60] * 10))
    assert errs and "Tags total" in errs[0]


def test_an_instagram_caption_counts_its_hashtags() -> None:
    tags = ["#" + "h" * 9] * 20  # 20 x 10 characters plus 19 separators
    assert ml.caption_length("instagram", "x" * 1990, tags) == 1990 + 1 + 20 * 10 + 19
    errs, _ = ml.validate("instagram", fields(description="x" * 1990, hashtags=tags))
    assert errs and "with its hashtags" in errs[0]
    assert (
        ml.validate("youtube", fields(description="x" * 1990, hashtags=tags))[0] == []
    )  # YouTube counts them separately


def test_hashtag_counts_are_only_warnings() -> None:
    errs, warns = ml.validate("youtube", fields(hashtags=[f"#t{i}" for i in range(20)]))
    assert errs == [] and warns and "may ignore" in warns[0]
    assert ml.validate("instagram", fields(hashtags=["#a"]))[1] == []


def test_the_answer_schema_carries_the_platform_limits() -> None:
    yt = ml.meta_model("youtube")
    with pytest.raises(ValueError):
        yt.model_validate({"title": "x" * 101})
    assert yt.model_validate({"title": "x" * 100}).title == "x" * 100  # type: ignore[attr-defined]
    with pytest.raises(ValueError):
        ml.meta_model("instagram").model_validate({"thumbnail_text": "x" * 41})
    assert ml.meta_model("generic").model_validate({}).hashtags == []  # type: ignore[attr-defined]


def test_the_prompt_names_the_platform_forbids_inventing_and_never_includes_other_clips() -> None:
    msgs = dict(ml.build_messages("youtube", clip_title="Sleep", source_title="Show", text="we slept"))
    assert "youtube" in msgs["system"].lower() and "Do not invent" in msgs["system"] and "at most 100" in msgs["system"]
    assert "we slept" in msgs["user"] and "Sleep" in msgs["user"]
    assert (
        "including the hashtags"
        in dict(ml.build_messages("instagram", clip_title="a", source_title="b", text="c"))["system"]
    )


def test_clean_normalises_a_models_answer() -> None:
    m = ml.meta_model("youtube").model_validate(
        {
            "title": "  Sleep!  ",
            "hashtags": ["sleep", "#Sleep", "good night"],
            "tags": ["#a", "b"],
            "category": " Health ",
        }
    )
    out = ml.clean("youtube", m)
    assert (
        out["title"] == "Sleep!"
        and out["hashtags"] == ["#sleep", "#goodnight"]
        and out["tags"] == ["a", "b"]
        and out["category"] == "Health"
    )
