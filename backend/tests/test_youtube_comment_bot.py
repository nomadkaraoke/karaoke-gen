"""Unit tests for the YouTube comment auto-reply decision core (no network)."""
from datetime import datetime, timezone

import pytest

from backend.services import youtube_comment_bot as bot

OWNER = bot.NOMAD_CHANNEL_ID


def _item(text="can you make Paper Moons by The Lanterns?", author_channel="UCviewer",
          replies=None, total_replies=None, published="2026-10-01T12:00:00Z", vid="vid1"):
    replies = replies or []
    return {
        "id": "thread1",
        "snippet": {
            "videoId": vid,
            "totalReplyCount": len(replies) if total_replies is None else total_replies,
            "topLevelComment": {
                "id": "c1",
                "snippet": {
                    "authorDisplayName": "Viewer",
                    "authorChannelId": {"value": author_channel},
                    "textOriginal": text,
                    "publishedAt": published,
                },
            },
        },
        "replies": {"comments": [
            {"snippet": {"authorChannelId": {"value": ch}, "textOriginal": t}} for ch, t in replies
        ]},
    }


class FakeBoard:
    def __init__(self, existing=None):
        self.existing = existing or {}
        self.added = []

    def canonicalize(self, artist, title):
        return artist.strip(), title.strip()

    def find_open_or_published(self, artist, title):
        return self.existing.get(bot.song_key(artist, title))

    def add_from_comment(self, artist, title, thread):
        m = bot.BoardMatch(f"new{len(self.added)}", artist, title, "open")
        self.added.append((artist, title, thread.comment_id))
        self.existing[bot.song_key(artist, title)] = m
        return m


def gen_returning(data, translation=None):
    calls = []

    def _gen(system_prompt, user_prompt, schema):
        calls.append((system_prompt, user_prompt, schema))
        if schema is None:
            return {"text": translation if translation is not None else user_prompt.split("\n\n", 1)[1]}
        return data

    _gen.calls = calls
    return _gen


REQUEST = {"is_song_request": True, "songs": [{"artist": "The Lanterns", "title": "Paper Moons"}],
           "language": "en", "confidence": 0.95, "reason": "asks to make it"}


def _decide(thread, gen, board=None, index=None, **kw):
    return bot.decide(thread, video_title="X - Y (Karaoke)", index=index or bot.ChannelIndex(),
                      board=board or FakeBoard(), generate=gen, **kw)


class TestThreadFromApi:
    def test_parses_owner_replies_only(self):
        t = bot.thread_from_api(_item(replies=[("UCother", "me too"), (OWNER, "on it!")]))
        assert t.owner_replies == ["on it!"]
        assert t.comment_id == "c1" and t.video_id == "vid1" and t.total_reply_count == 2

    def test_published_parses_rfc3339(self):
        assert bot.thread_from_api(_item()).published == datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


class TestSkips:
    def test_own_comment(self):
        gen = gen_returning(REQUEST)
        d = _decide(bot.thread_from_api(_item(author_channel=OWNER)), gen)
        assert (d.action, d.reason) == ("skip", "own_comment") and not gen.calls

    def test_already_replied_by_channel(self):
        gen = gen_returning(REQUEST)
        d = _decide(bot.thread_from_api(_item(replies=[(OWNER, "added!")])), gen)
        assert d.reason == "already_replied" and not gen.calls

    def test_too_old(self):
        d = _decide(bot.thread_from_api(_item()), gen_returning(REQUEST),
                    not_before=datetime(2026, 10, 2, tzinfo=timezone.utc))
        assert d.reason == "too_old"

    def test_not_a_request(self):
        d = _decide(bot.thread_from_api(_item(text="love this")),
                    gen_returning({"is_song_request": False, "songs": [], "language": "en", "confidence": 0.9}))
        assert d.reason == "not_a_request"

    def test_low_confidence_does_not_touch_board(self):
        board = FakeBoard()
        d = _decide(bot.thread_from_api(_item()), gen_returning({**REQUEST, "confidence": 0.5}), board=board)
        assert d.reason == "low_confidence" and board.added == []

    def test_classifier_exception_skips(self):
        def boom(*a):
            raise RuntimeError("quota")
        d = _decide(bot.thread_from_api(_item()), boom)
        assert (d.action, d.reason) == ("skip", "classifier_error")

    def test_request_flag_without_songs_is_not_request(self):
        d = _decide(bot.thread_from_api(_item()), gen_returning({**REQUEST, "songs": []}))
        assert d.reason == "not_a_request"


class TestRouting:
    def test_new_board_request(self):
        board = FakeBoard()
        d = _decide(bot.thread_from_api(_item()), gen_returning(REQUEST), board=board)
        assert d.action == "reply"
        assert [s.kind for s in d.songs] == ["board_new"]
        assert board.added == [("The Lanterns", "Paper Moons", "c1")]
        assert "request=new0" in d.reply_text and "utm_medium=comment_reply" in d.reply_text
        assert '"Paper Moons" by The Lanterns' in d.reply_text

    def test_existing_board_request_is_linked_not_duplicated(self):
        key = bot.song_key("The Lanterns", "Paper Moons")
        board = FakeBoard({key: bot.BoardMatch("r9", "The Lanterns", "Paper Moons", "open")})
        d = _decide(bot.thread_from_api(_item()), gen_returning(REQUEST), board=board)
        assert d.songs[0].kind == "board_existing" and board.added == []
        assert "request=r9" in d.reply_text

    def test_published_board_request_links_video(self):
        key = bot.song_key("The Lanterns", "Paper Moons")
        board = FakeBoard({key: bot.BoardMatch("r9", "The Lanterns", "Paper Moons", "published",
                                               "https://youtu.be/pub1")})
        d = _decide(bot.thread_from_api(_item()), gen_returning(REQUEST), board=board)
        assert d.songs[0].kind == "board_published"
        assert "Already made" in d.reply_text and "https://youtu.be/pub1" in d.reply_text

    def test_existing_channel_video_wins(self):
        index = bot.ChannelIndex({bot.song_key("The Lanterns", "Paper Moons"): "vidX"})
        board = FakeBoard()
        d = _decide(bot.thread_from_api(_item()), gen_returning(REQUEST), board=board, index=index)
        assert d.songs[0].kind == "existing_video" and board.added == []
        assert "https://youtu.be/vidX" in d.reply_text

    def test_multiple_songs_mixed_and_deduped(self):
        data = {**REQUEST, "songs": [{"artist": "The Lanterns", "title": "Paper Moons"},
                                     {"artist": "the lanterns", "title": "paper moons"},
                                     {"artist": "Glass Harbor", "title": "Tin Sky"}]}
        index = bot.ChannelIndex({bot.song_key("Glass Harbor", "Tin Sky"): "vidG"})
        d = _decide(bot.thread_from_api(_item()), gen_returning(data), index=index)
        assert [s.kind for s in d.songs] == ["board_new", "existing_video"]
        assert d.reply_text.index("Already made") < d.reply_text.index("requests board")

    def test_caps_songs(self):
        data = {**REQUEST, "songs": [{"artist": f"A{i}", "title": f"T{i}"} for i in range(6)]}
        d = _decide(bot.thread_from_api(_item()), gen_returning(data))
        assert len(d.songs) == bot.MAX_SONGS_PER_COMMENT


class TestVagueRequest:
    VAGUE = {"is_song_request": False, "is_vague_request": True, "songs": [], "language": "en",
             "confidence": 0.9}

    def test_vague_ask_gets_generic_board_link_and_no_board_write(self):
        board = FakeBoard()
        d = _decide(bot.thread_from_api(_item(text="more johnnie please")), gen_returning(self.VAGUE), board=board)
        assert (d.action, d.reason) == ("reply", "vague_request")
        assert board.added == [] and d.songs == []
        assert "request=" not in d.reply_text and "utm_medium=comment_reply" in d.reply_text

    def test_low_confidence_vague_skips(self):
        d = _decide(bot.thread_from_api(_item(text="more?")), gen_returning({**self.VAGUE, "confidence": 0.5}))
        assert d.reason == "low_confidence"

    def test_vague_flag_ignored_when_songs_named(self):
        c = bot.parse_classification({**REQUEST, "is_vague_request": True})
        assert c.is_song_request and not c.is_vague_request


class TestTranslation:
    def test_non_english_translated_when_urls_survive(self):
        def gen(system_prompt, user_prompt, schema):
            if schema is None:
                text = user_prompt.split("\n\n", 1)[1]
                return {"text": "¡Gracias! " + " ".join(bot._URL_RE.findall(text))}
            return {**REQUEST, "language": "es"}
        d = _decide(bot.thread_from_api(_item(text="¿puedes hacer Paper Moons?")), gen)
        assert d.reply_text.startswith("¡Gracias!") and d.reply_text_en != d.reply_text

    def test_falls_back_to_english_if_url_mangled(self):
        d = _decide(bot.thread_from_api(_item()), gen_returning({**REQUEST, "language": "es"},
                                                                translation="Gracias sin enlace"))
        assert d.reply_text == d.reply_text_en

    def test_english_never_calls_translator(self):
        gen = gen_returning(REQUEST)
        _decide(bot.thread_from_api(_item()), gen)
        assert all(schema is not None for _, _, schema in gen.calls)


class TestParseClassification:
    @pytest.mark.parametrize("data", [None, [], "x", {"songs": "nope"}])
    def test_malformed(self, data):
        c = bot.parse_classification(data)
        assert not c.is_song_request and c.songs == []

    def test_confidence_clamped_and_blank_songs_dropped(self):
        c = bot.parse_classification({"is_song_request": True, "confidence": 3,
                                      "songs": [{"artist": "", "title": "x"}, {"artist": "A", "title": "B"}]})
        assert c.confidence == 1.0 and [s.title for s in c.songs] == ["B"]


class TestUnsafeNames:
    @pytest.mark.parametrize("title", [
        "Win free stuff at http://evil.example", "visit www.spam.test", "free-gift.com",
        "dm @scammer", "<b>x</b>", "line one\nline two", "x" * 101,
    ])
    def test_unsafe_song_names_are_dropped(self, title):
        c = bot.parse_classification({**REQUEST, "songs": [{"artist": "The Lanterns", "title": title}]})
        assert c.songs == [] and not c.is_song_request

    def test_unsafe_artist_dropped_but_safe_song_kept(self):
        c = bot.parse_classification({**REQUEST, "songs": [
            {"artist": "spam.xyz", "title": "Paper Moons"}, {"artist": "Glass Harbor", "title": "Tin Sky"}]})
        assert [s.title for s in c.songs] == ["Tin Sky"]

    def test_normal_punctuation_survives(self):
        c = bot.parse_classification({**REQUEST, "songs": [{"artist": "fun.", "title": "Walking the Dog II"},
                                                           {"artist": "Los Campesinos!", "title": "kms"}]})
        assert len(c.songs) == 2


class TestChannelIndex:
    def test_from_entries_high_confidence_non_translated_only(self):
        idx = bot.ChannelIndex.from_entries([
            {"video_id": "v1", "yt_title": "A - B (Karaoke)", "artist": "A", "song_title": "B",
             "parse_confidence": "high"},
            {"video_id": "v2", "yt_title": "C - D karaoke", "artist": "C", "song_title": "D",
             "parse_confidence": "medium"},
            {"video_id": "v3", "yt_title": "A - B (With Translation into Spanish) (Karaoke)",
             "artist": "A", "song_title": "B", "parse_confidence": "high",
             "translation_language_name": "Spanish"},
        ])
        assert idx.find("a", "b") == "v1" and idx.find("C", "D") is None
        assert set(idx.titles) == {"v1", "v2", "v3"}


class FakeYouTube:
    """Minimal chainable stand-in for the googleapiclient resource."""

    def __init__(self, pages, reply_pages=None):
        self.pages = pages
        self.reply_pages = reply_pages or []
        self.inserted = []
        self.retries = []

    def commentThreads(self):
        return self

    def comments(self):
        return self

    def list(self, **kw):
        self._kw = kw
        return self

    def insert(self, **kw):
        self.inserted.append(kw)
        self._kw = {"insert": True}
        return self

    def execute(self, num_retries=0):
        self.retries.append((("insert" if self._kw.get("insert") else "list"), num_retries))
        if self._kw.get("insert"):
            return {"id": "reply1"}
        if "parentId" in self._kw:
            return self.reply_pages.pop(0)
        return self.pages.pop(0)


class TestApiHelpers:
    def test_iter_stops_before_cutoff(self):
        yt = FakeYouTube([
            {"items": [_item(published="2026-10-05T00:00:00Z")], "nextPageToken": "p2"},
            {"items": [_item(published="2026-09-01T00:00:00Z"), _item(published="2026-08-01T00:00:00Z")],
             "nextPageToken": "p3"},
        ])
        got = list(bot.iter_comment_threads(yt, stop_before=datetime(2026, 9, 15, tzinfo=timezone.utc)))
        assert len(got) == 1 and yt.pages == []

    def test_load_thread_fetches_truncated_owner_replies(self):
        yt = FakeYouTube([], reply_pages=[{"items": [
            {"snippet": {"authorChannelId": {"value": OWNER}, "textOriginal": "done"}}]}])
        t = bot.load_thread(yt, _item(replies=[("UCa", "x")], total_replies=7))
        assert t.owner_replies == ["done"]

    def test_post_reply(self):
        yt = FakeYouTube([])
        assert bot.post_reply(yt, "c1", "hi") == "reply1"
        assert yt.inserted[0]["body"]["snippet"] == {"parentId": "c1", "textOriginal": "hi"}


    def test_reads_retry_but_posts_never_do(self):
        yt = FakeYouTube([{"items": [_item()]}], reply_pages=[{"items": []}])
        list(bot.iter_comment_threads(yt))
        bot.load_thread(yt, _item(replies=[("UCa", "x")], total_replies=7))
        bot.post_reply(yt, "c1", "hi")
        assert yt.retries == [("list", bot.READ_RETRIES), ("list", bot.READ_RETRIES), ("insert", 0)]
