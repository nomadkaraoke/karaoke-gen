"""Tests for the deterministic (no-LLM) suggestion generators + P1 conflict grouping.

Scenarios mirror the 2026-08 replay-review corpus jobs that motivated each
pattern: P4 leading-connective over-insertion (8f2305ee "And they" -> "They"),
P5 reference-majority resolution (6d0640fa "yo Mick," -> "Come here,"), and the
P1 AI self-conflict (f6439692 insert+replace both adding "you're").
"""
from __future__ import annotations

from unittest.mock import patch

from backend.services.auto_correct.deterministic import (
    deterministic_suggestions,
    leading_connective_suggestions,
    reference_majority_suggestions,
    _ref_word_streams,
)
from backend.services.auto_correct.service import (
    AutoCorrectService,
    Suggestion,
    _assign_conflict_groups,
)
from backend.services.auto_correct.settings import AutoCorrectSettings


def _ref_source(word_texts, prefix):
    """A reference source with ids <prefix>0, <prefix>1, ..."""
    return {
        "segments": [
            {
                "text": " ".join(word_texts),
                "words": [
                    {"id": f"{prefix}{i}", "text": t} for i, t in enumerate(word_texts)
                ],
            }
        ]
    }


def _seg(seg_id, words):
    return {
        "id": seg_id,
        "text": " ".join(t for _, t in words),
        "words": [{"id": wid, "text": t} for wid, t in words],
    }


# ---- Pattern 4: leading connective ----


def _p4_fixture(ref_line="They count everything", gap_ref_ids=None):
    """Segment "And they count ..." where "And" is a gap word.

    The single reference reads "They count everything" — no "And" — with the
    gap aligned to reference word r0 ("They") unless overridden.
    """
    segments = [
        _seg("seg-0", [("w0", "And"), ("w1", "they"), ("w2", "count"), ("w3", "everything")])
    ]
    refs = {"genius": _ref_source(ref_line.split(), "r")}
    correction_data = {
        "gap_sequences": [
            {
                "id": "gap-0",
                "transcribed_word_ids": ["w0"],
                "reference_word_ids": {"genius": gap_ref_ids if gap_ref_ids is not None else ["r0"]},
            }
        ],
        "anchor_sequences": [],
    }
    return segments, refs, correction_data


def test_p4_deletes_unsupported_leading_connective_and_recapitalizes() -> None:
    segments, refs, cd = _p4_fixture()
    out = deterministic_suggestions(segments, refs, cd)
    assert [s["op"] for s in out] == ["delete", "replace"]
    delete, recap = out
    assert delete["word_ids"] == ["w0"]
    assert delete["new_text"] == ""
    assert recap["word_ids"] == ["w1"]
    assert recap["new_text"] == "They"
    assert all(s["models"] == ["deterministic"] for s in out)


def test_p4_skips_when_a_reference_supports_the_connective() -> None:
    segments, refs, cd = _p4_fixture(ref_line="And they count everything")
    out = deterministic_suggestions(segments, refs, cd)
    assert out == []


def test_p4_skips_when_no_reference_evidence() -> None:
    # Gap aligned to nothing and no usable anchors -> no reading -> no delete.
    segments, refs, cd = _p4_fixture(gap_ref_ids=[])
    out = deterministic_suggestions(segments, refs, cd)
    assert out == []


def test_p4_skips_anchored_leading_connective() -> None:
    segments, refs, cd = _p4_fixture()
    cd["gap_sequences"] = []  # "And" is not a gap word
    assert deterministic_suggestions(segments, refs, cd) == []


def test_p4_skips_vocalization_run() -> None:
    # "Oh- whoa whoa whoa" — grouping vocalizations is a musical judgement.
    segments = [
        _seg("seg-0", [("w0", "Oh-"), ("w1", "whoa,"), ("w2", "whoa,"), ("w3", "whoa")])
    ]
    refs = {"genius": _ref_source(["Whoa,", "whoa,", "whoa"], "r")}
    cd = {
        "gap_sequences": [
            {
                "id": "gap-0",
                "transcribed_word_ids": ["w0"],
                "reference_word_ids": {"genius": ["r0"]},
            }
        ],
        "anchor_sequences": [],
    }
    assert deterministic_suggestions(segments, refs, cd) == []


def test_p4_skips_two_word_segment() -> None:
    segments = [_seg("seg-0", [("w0", "And"), ("w1", "run")])]
    refs = {"genius": _ref_source(["Run"], "r")}
    cd = {
        "gap_sequences": [
            {"id": "g", "transcribed_word_ids": ["w0"], "reference_word_ids": {"genius": ["r0"]}}
        ],
        "anchor_sequences": [],
    }
    assert deterministic_suggestions(segments, refs, cd) == []


def test_p4_already_capitalized_next_word_gets_no_recap_suggestion() -> None:
    segments = [
        _seg("seg-0", [("w0", "And"), ("w1", "Mary"), ("w2", "sings"), ("w3", "loud")])
    ]
    refs = {"genius": _ref_source(["Mary", "sings", "loud"], "r")}
    cd = {
        "gap_sequences": [
            {"id": "g", "transcribed_word_ids": ["w0"], "reference_word_ids": {"genius": ["r0"]}}
        ],
        "anchor_sequences": [],
    }
    out = deterministic_suggestions(segments, refs, cd)
    assert [s["op"] for s in out] == ["delete"]


# ---- Pattern 5: reference majority ----


def _p5_fixture():
    """The 6d0640fa shape: transcription "yo Mick," where genius aligned
    "My man," on the gap, and lrclib/spotify (unaligned on the gap) read
    "Come here," after the preceding anchor."""
    segments = [
        _seg(
            "seg-0",
            [
                ("w0", "Hit"),
                ("w1", "the"),
                ("w2", "button,"),
                ("w3", "yo"),
                ("w4", "Mick,"),
                ("w5", "come"),
                ("w6", "on"),
            ],
        )
    ]
    refs = {
        "lrclib": _ref_source(["Hit", "the", "button,", "Come", "here,", "come", "on"], "l"),
        "spotify": _ref_source(["Hit", "the", "button,", "Come", "here,", "come", "on"], "s"),
        "genius": _ref_source(["Hit", "the", "button,", "My", "man,", "come", "on"], "g"),
    }
    correction_data = {
        "gap_sequences": [
            {
                "id": "gap-0",
                "transcribed_word_ids": ["w3", "w4"],
                "preceding_anchor_id": "anchor-0",
                "following_anchor_id": None,
                "reference_word_ids": {"lrclib": [], "spotify": [], "genius": ["g3", "g4"]},
            }
        ],
        "anchor_sequences": [
            {
                "id": "anchor-0",
                "transcribed_word_ids": ["w0", "w1", "w2"],
                "reference_word_ids": {
                    "lrclib": ["l0", "l1", "l2"],
                    "spotify": ["s0", "s1", "s2"],
                    "genius": ["g0", "g1", "g2"],
                },
            }
        ],
    }
    return segments, refs, correction_data


def test_p5_replaces_implausible_proper_noun_with_majority_reading() -> None:
    segments, refs, cd = _p5_fixture()
    out = deterministic_suggestions(segments, refs, cd)
    assert len(out) == 1
    s = out[0]
    assert s["op"] == "replace"
    assert s["word_ids"] == ["w3", "w4"]
    assert s["new_text"] == "Come here,"
    assert "Mick," in s["reason"]


def test_p5_requires_the_red_flag_token() -> None:
    # Same majority disagreement but the transcription is plausible lowercase
    # ("though" vs 2/3 "dog") -> the human deliberately leaves these; so do we.
    segments, refs, cd = _p5_fixture()
    segments[0]["words"][3]["text"] = "yo"
    segments[0]["words"][4]["text"] = "mick,"
    assert deterministic_suggestions(segments, refs, cd) == []


def test_p5_skips_spelling_variant_of_reference_token() -> None:
    # "Crick" ~ "Cricket": a truncation/spelling variant, the LLM's fix.
    segments, refs, cd = _p5_fixture()
    segments[0]["words"][4]["text"] = "Herre,"  # close to reference "here,"
    assert deterministic_suggestions(segments, refs, cd) == []


def test_p5_requires_two_thirds_majority() -> None:
    segments, refs, cd = _p5_fixture()
    # Make spotify read something else -> no 2/3 agreement.
    for w, t in zip(refs["spotify"]["segments"][0]["words"][3:5], ["Over", "there,"]):
        w["text"] = t
    assert deterministic_suggestions(segments, refs, cd) == []


def test_p5_ignores_segment_initial_capitalization() -> None:
    # A capitalized SEGMENT-FIRST word is sentence case, not a proper noun.
    segments = [_seg("seg-0", [("w0", "Mick,"), ("w1", "come"), ("w2", "on")])]
    refs = {
        "lrclib": _ref_source(["Quick,", "come", "on"], "l"),
        "spotify": _ref_source(["Quick,", "come", "on"], "s"),
        "genius": _ref_source(["Slick,", "come", "on"], "g"),
    }
    cd = {
        "gap_sequences": [
            {
                "id": "gap-0",
                "transcribed_word_ids": ["w0"],
                "reference_word_ids": {"lrclib": ["l0"], "spotify": ["s0"], "genius": ["g0"]},
            }
        ],
        "anchor_sequences": [],
    }
    assert deterministic_suggestions(segments, refs, cd) == []


def test_p5_skips_stale_gap_word_ids() -> None:
    segments, refs, cd = _p5_fixture()
    cd["gap_sequences"][0]["transcribed_word_ids"] = ["w3", "gone"]
    assert deterministic_suggestions(segments, refs, cd) == []


def test_generators_never_raise_on_garbage() -> None:
    assert deterministic_suggestions([], {}, None) == []
    assert deterministic_suggestions([{"weird": True}], {"x": {}}, {"gap_sequences": [{}]}) == []


# ---- Pattern 1: insert/replace self-conflict grouping ----


def _s(id_, op, word_ids, new_text, confidence=0.9, consensus=1):
    return Suggestion(
        id=id_,
        op=op,
        word_ids=word_ids,
        segment_ids=["seg-0"],
        original_text="fire" if op != "insert_after" else "",
        new_text=new_text,
        reason="r",
        category="mishearing",
        confidence=confidence,
        models=["m"],
        consensus=consensus,
        total_models=1,
        conflict_group=None,
    )


def test_p1_insert_and_replace_adding_same_token_share_a_group() -> None:
    # f6439692: insert_after "fire" -> "you're" AND replace "fire" ->
    # "fire, you're" both applied -> "you're you're". Must become a conflict.
    insert = _s("i", "insert_after", ["w-fire"], "you're", confidence=0.75)
    replace = _s("r", "replace", ["w-fire"], "fire, you're", confidence=0.95)
    suggestions = [insert, replace]
    _assign_conflict_groups(suggestions)
    assert insert.conflict_group is not None
    assert insert.conflict_group == replace.conflict_group


def test_p1_disjoint_tokens_stay_independent() -> None:
    # Replacing a word AND inserting different text after it is legitimate.
    insert = _s("i", "insert_after", ["w-fire"], "gasoline")
    replace = _s("r", "replace", ["w-fire"], "fire,")
    suggestions = [insert, replace]
    _assign_conflict_groups(suggestions)
    assert insert.conflict_group is None
    assert replace.conflict_group is None


def test_p1_two_inserts_same_anchor_same_token_share_a_group() -> None:
    a = _s("a", "insert_after", ["w-fire"], "you're")
    b = _s("b", "insert_after", ["w-fire"], "you're baby")
    suggestions = [a, b]
    _assign_conflict_groups(suggestions)
    assert a.conflict_group is not None
    assert a.conflict_group == b.conflict_group


def test_p1_insert_vs_delete_not_grouped() -> None:
    insert = _s("i", "insert_after", ["w-fire"], "you're")
    delete = _s("d", "delete", ["w-fire"], "")
    suggestions = [insert, delete]
    _assign_conflict_groups(suggestions)
    assert insert.conflict_group is None


def test_assign_conflict_groups_is_idempotent_and_resets() -> None:
    a = _s("a", "replace", ["w1"], "x")
    b = _s("b", "replace", ["w2"], "y")
    a.conflict_group = b.conflict_group = "stale-group"
    suggestions = [a, b]
    _assign_conflict_groups(suggestions)
    assert a.conflict_group is None
    assert b.conflict_group is None


def test_word_overlap_grouping_still_works() -> None:
    a = _s("a", "replace", ["w1", "w2"], "x")
    b = _s("b", "delete", ["w2"], "")
    suggestions = [a, b]
    _assign_conflict_groups(suggestions)
    assert a.conflict_group is not None
    assert a.conflict_group == b.conflict_group


# ---- pipeline integration: suggest() folds deterministic suggestions in ----


def _run_single_model(llm_suggestions, segments, refs, correction_data):
    service = AutoCorrectService()

    def fake_call(model, system_prompt, user_prompt, *, job_id):
        return {"suggestions": llm_suggestions}, None

    with patch.object(service, "_cache_get", return_value=None), \
            patch.object(service, "_cache_put"), \
            patch.object(service, "_call_model", side_effect=fake_call):
        return service.suggest(
            job_id="job-1",
            segments=segments,
            reference_lyrics=refs,
            artist="A",
            title="T",
            settings=AutoCorrectSettings(),
            correction_data=correction_data,
        )


def test_suggest_appends_deterministic_suggestions() -> None:
    segments, refs, cd = _p4_fixture()
    result = _run_single_model([], segments, refs, cd)
    assert [s.op for s in result.suggestions] == ["delete", "replace"]
    assert all(s.models == ["deterministic"] for s in result.suggestions)


def test_suggest_dedupes_identical_llm_and_deterministic() -> None:
    segments, refs, cd = _p4_fixture()
    llm = [
        {
            "op": "delete",
            "start_idx": 0,
            "end_idx": 0,
            "new_text": "",
            "reason": "spurious leading word",
            "category": "mishearing",
            "confidence": 0.8,
        }
    ]
    result = _run_single_model(llm, segments, refs, cd)
    deletes = [s for s in result.suggestions if s.op == "delete"]
    assert len(deletes) == 1
    assert "deterministic" in deletes[0].models
    assert deletes[0].confidence == 0.9  # bumped to the deterministic confidence


def test_suggest_groups_conflicting_llm_and_deterministic() -> None:
    segments, refs, cd = _p4_fixture()
    llm = [
        {
            "op": "replace",
            "start_idx": 0,
            "end_idx": 0,
            "new_text": "And,",
            "reason": "punctuation",
            "category": "formatting",
            "confidence": 0.7,
        }
    ]
    result = _run_single_model(llm, segments, refs, cd)
    on_w0 = [s for s in result.suggestions if s.word_ids == ["w0"]]
    assert len(on_w0) == 2
    assert on_w0[0].conflict_group is not None
    assert on_w0[0].conflict_group == on_w0[1].conflict_group


def test_suggest_without_correction_data_still_works() -> None:
    segments, refs, _ = _p4_fixture()
    service = AutoCorrectService()

    def fake_call(model, system_prompt, user_prompt, *, job_id):
        return {"suggestions": []}, None

    with patch.object(service, "_cache_get", return_value=None), \
            patch.object(service, "_cache_put"), \
            patch.object(service, "_get_storage", side_effect=RuntimeError("no gcs")), \
            patch.object(service, "_call_model", side_effect=fake_call):
        result = service.suggest(
            job_id="job-1",
            segments=segments,
            reference_lyrics=refs,
            artist="A",
            title="T",
            settings=AutoCorrectSettings(),
        )
    assert result.suggestions == []


def test_deterministic_respects_min_confidence_setting() -> None:
    segments, refs, cd = _p4_fixture()
    service = AutoCorrectService()

    def fake_call(model, system_prompt, user_prompt, *, job_id):
        return {"suggestions": []}, None

    with patch.object(service, "_cache_get", return_value=None), \
            patch.object(service, "_cache_put"), \
            patch.object(service, "_call_model", side_effect=fake_call):
        result = service.suggest(
            job_id="job-1",
            segments=segments,
            reference_lyrics=refs,
            artist="A",
            title="T",
            settings=AutoCorrectSettings(min_confidence=0.95),
            correction_data=cd,
        )
    assert result.suggestions == []


# ---- module-level helpers ----


def test_ref_word_streams_indexes_ids() -> None:
    streams = _ref_word_streams({"genius": _ref_source(["a", "b"], "g")})
    texts, index = streams["genius"]
    assert texts == ["a", "b"]
    assert index == {"g0": 0, "g1": 1}


def test_direct_generator_calls_share_stream_shape() -> None:
    segments, refs, cd = _p4_fixture()
    streams = _ref_word_streams(refs)
    assert leading_connective_suggestions(segments, cd, streams)
    segments5, refs5, cd5 = _p5_fixture()
    streams5 = _ref_word_streams(refs5)
    assert reference_majority_suggestions(segments5, cd5, streams5)
