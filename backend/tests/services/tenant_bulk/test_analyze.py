"""Tests for tenant bulk-upload filename analysis.

The regex fast path is deterministic and covers the real Vocal Star convention,
so most assertions run with ``generate=None`` (no LLM). A couple of tests inject
a fake ``generate`` to exercise the LLM merge + hallucination guards.
"""
from backend.services.tenant_bulk import analyze_filenames

# The real "Bulk-Batch-1-Inputs" folder listing (see BACKLOG.md INBOX spec).
BULK_BATCH_1 = [
    "S1100-1 Eddy Grant - I Don't Wanna Dance Guide.mp3",
    "S1100-2 Eddy Grant - I Don't Wanna Dance BV.mp3",
    "S1101-1 Smokey Joes Cafe - Some Cats Know Guide.mp3",
    "S1101-2 Smokey Joes Cafe - Some Cats Know Instru.mp3",
    "S1102-2 The Brian Setzer Orchestra - Straight Up Guide.mp3",  # anomaly: -2 but Guide, no pair
    "S1103-1 Cyndi Lauper - Girls Just Want to Have Fun Guide.mp3",
    "S1103-2 Cyndi Lauper - Girls Just Want to Have Fun BV.mp3",
    "S1104-1 Herman's Hermits - There's a Kind of Hush Guide.mp3",
    "S1104-2 Herman's Hermits - There's a Kind of Hush BV.mp3",
    "S1105-1 Nat King Cole - Smile Guide.mp3",
    "S1105-2 Nat King Cole - Smile Instru.mp3",
]


class TestRegexFastPath:
    def test_pairs_clean_batch(self):
        result = analyze_filenames(BULK_BATCH_1).to_dict()
        rows = result["rows"]
        # 5 complete pairs (1100, 1101, 1103, 1104, 1105); 1102 is unpaired.
        assert len(rows) == 5
        by_mixed = {r["mixed_filename"]: r for r in rows}

        eddy = by_mixed["S1100-1 Eddy Grant - I Don't Wanna Dance Guide.mp3"]
        assert eddy["artist"] == "Eddy Grant"
        assert eddy["title"] == "I Don't Wanna Dance"
        assert eddy["instrumental_filename"] == "S1100-2 Eddy Grant - I Don't Wanna Dance BV.mp3"
        assert eddy["confidence"] == "high"

        # "Instru" label is recognised as instrumental, not just "-2".
        smokey = by_mixed["S1101-1 Smokey Joes Cafe - Some Cats Know Guide.mp3"]
        assert smokey["instrumental_filename"] == "S1101-2 Smokey Joes Cafe - Some Cats Know Instru.mp3"

        # Apostrophes / punctuation preserved in title extraction.
        herman = by_mixed["S1104-1 Herman's Hermits - There's a Kind of Hush Guide.mp3"]
        assert herman["artist"] == "Herman's Hermits"
        assert herman["title"] == "There's a Kind of Hush"

    def test_s1102_anomaly_flagged_unpaired_not_mispaired(self):
        result = analyze_filenames(BULK_BATCH_1).to_dict()
        unpaired = result["unpaired"]
        assert len(unpaired) == 1
        u = unpaired[0]
        assert u["filename"] == "S1102-2 The Brian Setzer Orchestra - Straight Up Guide.mp3"
        # Guide label -> treated as MIXED with no instrumental partner.
        assert u["role"] == "mixed"
        assert u["reason"] == "no_instrumental"
        assert u["artist"] == "The Brian Setzer Orchestra"
        assert u["title"] == "Straight Up"

    def test_non_audio_files_ignored(self):
        files = BULK_BATCH_1 + [
            "ALWAYS REMEMBER US THIS WAY CDG.fw.png",
            "prepped",  # a subfolder entry, no extension
            "notes.txt",
        ]
        result = analyze_filenames(files).to_dict()
        ignored = {i["filename"] for i in result["ignored"]}
        assert "ALWAYS REMEMBER US THIS WAY CDG.fw.png" in ignored
        assert "notes.txt" in ignored
        assert "prepped" in ignored
        # Audio pairing unaffected by the extra junk.
        assert len(result["rows"]) == 5

    def test_duplicate_filenames_deduped(self):
        files = BULK_BATCH_1 + [BULK_BATCH_1[0], BULK_BATCH_1[1]]
        result = analyze_filenames(files).to_dict()
        assert len(result["rows"]) == 5

    def test_no_scode_prefix_pairs_by_artist_title(self):
        files = [
            "Adele - Hello (Guide).mp3",
            "Adele - Hello (Instrumental).mp3",
        ]
        result = analyze_filenames(files).to_dict()
        assert len(result["rows"]) == 1
        row = result["rows"][0]
        assert row["artist"] == "Adele"
        assert row["title"] == "Hello"

    def test_empty_input(self):
        result = analyze_filenames([]).to_dict()
        assert result == {"rows": [], "unpaired": [], "ignored": []}

    def test_title_ending_in_weak_label_word_is_not_mis_roled(self):
        # "Off" is a title word here, not an instrumental label; the -1/-2 slots
        # must still pair these correctly and keep the full title.
        files = [
            "S2000-1 The Artist - Turn It Off Guide.mp3",   # mixed (Guide strong)
            "S2000-2 The Artist - Turn It Off Instru.mp3",  # instrumental
        ]
        result = analyze_filenames(files).to_dict()
        assert len(result["rows"]) == 1
        row = result["rows"][0]
        assert row["title"] == "Turn It Off"
        assert row["mixed_filename"] == "S2000-1 The Artist - Turn It Off Guide.mp3"

    def test_bare_weak_label_falls_back_to_slot(self):
        # No strong label / brackets: "Off" and "Lead" stay in the title and the
        # slot decides the role, so the pair is preserved.
        files = [
            "S2001-1 Band - Show Off.mp3",   # slot 1 -> mixed, title keeps "Off"
            "S2001-2 Band - Show Off.mp3",   # slot 2 -> instrumental
        ]
        result = analyze_filenames(files).to_dict()
        assert len(result["rows"]) == 1
        assert result["rows"][0]["title"] == "Show Off"

    def test_bracketed_weak_label_is_trusted(self):
        files = [
            "Adele - Hello (Vocals).mp3",
            "Adele - Hello (Karaoke).mp3",
        ]
        result = analyze_filenames(files).to_dict()
        assert len(result["rows"]) == 1
        row = result["rows"][0]
        assert row["title"] == "Hello"
        assert row["mixed_filename"] == "Adele - Hello (Vocals).mp3"

    def test_scode_file_with_subfolder_path_still_parses(self):
        # Frontend may send a webkitRelativePath; Path().stem uses the basename.
        files = [
            "batch/S3000-1 A - Song Guide.mp3",
            "batch/S3000-2 A - Song Instru.mp3",
        ]
        result = analyze_filenames(files).to_dict()
        assert len(result["rows"]) == 1
        assert result["rows"][0]["instrumental_filename"] == "batch/S3000-2 A - Song Instru.mp3"


class TestLLMPass:
    def test_llm_pairs_leftovers_regex_missed(self):
        # Two files the regex can't confidently pair (no label, no S-code).
        files = [
            "eddygrant_dance_vox.mp3",
            "eddygrant_dance_backing.mp3",
        ]

        def fake_generate(system_prompt, user_prompt):
            assert "eddygrant_dance_vox.mp3" in user_prompt
            return {
                "rows": [
                    {
                        "artist": "Eddy Grant",
                        "title": "I Don't Wanna Dance",
                        "mixed_filename": "eddygrant_dance_vox.mp3",
                        "instrumental_filename": "eddygrant_dance_backing.mp3",
                        "confidence": "medium",
                    }
                ],
                "unpaired": [],
            }

        result = analyze_filenames(files, generate=fake_generate).to_dict()
        assert len(result["rows"]) == 1
        row = result["rows"][0]
        assert row["mixed_filename"] == "eddygrant_dance_vox.mp3"
        assert row["confidence"] == "medium"
        assert result["unpaired"] == []

    def test_llm_hallucinated_filenames_rejected(self):
        files = ["mystery_track.mp3"]

        def fake_generate(system_prompt, user_prompt):
            return {
                "rows": [
                    {
                        "artist": "Made",
                        "title": "Up",
                        "mixed_filename": "mystery_track.mp3",
                        "instrumental_filename": "does_not_exist.mp3",  # hallucinated
                    }
                ],
                "unpaired": [],
            }

        result = analyze_filenames(files, generate=fake_generate).to_dict()
        # Row rejected because the instrumental isn't a real input file.
        assert result["rows"] == []
        assert len(result["unpaired"]) == 1
        assert result["unpaired"][0]["filename"] == "mystery_track.mp3"

    def test_llm_error_falls_back_to_regex(self):
        def boom(system_prompt, user_prompt):
            raise RuntimeError("model unavailable")

        # Regex handles the clean batch; LLM only runs on leftovers (none here),
        # but even a raising generate must never break analysis.
        files = BULK_BATCH_1 + ["weird_unparseable_name.mp3"]
        result = analyze_filenames(files, generate=boom).to_dict()
        assert len(result["rows"]) == 5
        leftover = {u["filename"] for u in result["unpaired"]}
        assert "weird_unparseable_name.mp3" in leftover
