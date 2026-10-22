#!/usr/bin/env python
"""Replay the YouTube comment auto-reply bot over real past comments (read-only).

The go-live gate for the bot (Andrew, 2026-10-22): run it locally over ~6 months of
real channel comments, review every action it WOULD have taken, iterate until each
one is right, then enable it in prod.

Nothing is posted and nothing is written: board additions are simulated in memory
(deduped across the run against the real board, read from Firestore). The same
``decide()`` the prod worker uses makes every call, with real Gemini.

Usage (needs your gcloud login for the YouTube + Gemini secrets):
    python scripts/youtube-comments/replay.py --since 2026-04-22 \
        --out /tmp/replay.html [--json /tmp/replay.json] [--limit 200]

Report: one row per comment (video, comment, decision, parsed songs, exact reply +
links), plus what the channel actually replied by hand, if it did.
"""
from __future__ import annotations

import argparse
import asyncio
import html
import json
import sys
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from backend.services import youtube_backfill as ytb  # noqa: E402
from backend.services import youtube_comment_bot as bot  # noqa: E402


class SimulatedBoard:
    """BoardPort over a read-only snapshot of the real board plus in-memory adds."""

    def __init__(self, requests):
        self.by_key = {}
        for r in requests:
            if r.status in ("open", "queued", "in_progress", "published"):
                self.by_key.setdefault(r.dedupe_key, bot.BoardMatch(
                    r.id, r.artist, r.title, r.status, r.youtube_url))
        self.added = []
        self._canon_cache = {}

    def canonicalize(self, artist, title):
        key = (artist, title)
        if key not in self._canon_cache:
            from backend.services.song_request_service import SongRequestService
            self._canon_cache[key] = asyncio.run(SongRequestService._canonicalize(None, artist, title))
        return self._canon_cache[key]

    def find_open_or_published(self, artist, title):
        return self.by_key.get(bot.song_key(artist, title))

    def add_from_comment(self, artist, title, thread):
        match = bot.BoardMatch(f"sim-{uuid.uuid4().hex[:8]}", artist, title, "open")
        self.by_key[bot.song_key(artist, title)] = match
        self.added.append((match, thread.comment_id))
        return match


def load_board():
    from backend.services.song_request_service import SongRequestService
    return SongRequestService().list_all()


KIND_LABEL = {
    "existing_video": "already on channel",
    "board_published": "made from board",
    "board_existing": "already on board",
    "board_new": "NEW board request",
}


def render_html(rows, counts, since, generated_at):
    def esc(s):
        return html.escape(s or "")

    def linkify(s):
        out = esc(s)
        for url in sorted(set(bot._URL_RE.findall(s or "")), key=len, reverse=True):
            out = out.replace(esc(url), f'<a href="{esc(url)}">{esc(url)}</a>')
        return out.replace("\n", "<br>")

    order = {"reply": 0, "skip": 1}
    reason_order = {"song_request": 0, "low_confidence": 1, "already_replied": 2}
    rows = sorted(rows, key=lambda r: (order[r["action"]], reason_order.get(r["reason"], 9), r["published_at"]))
    body = []
    for r in rows:
        songs = "<br>".join(
            f'{esc(s["title"])} — {esc(s["artist"])} <span class="tag">{KIND_LABEL[s["kind"]]}</span>'
            for s in r["songs"]) or "—"
        cls = r.get("classification") or {}
        parsed = ", ".join(f'{s["artist"]} – {s["title"]}' for s in cls.get("songs", []))
        cls_note = (f'{"request" if cls.get("is_song_request") else "not request"} '
                    f'{cls.get("confidence", 0):.2f} {cls.get("language", "")}'
                    + (f"<br><i>{esc(parsed)}</i>" if parsed else "")
                    + (f'<br><small>{esc(cls.get("reason"))}</small>' if cls.get("reason") else "")) if cls else ""
        reply = linkify(r["reply_text"])
        if r["reply_text_en"] and r["reply_text_en"] != r["reply_text"]:
            reply += f'<details><summary>English</summary>{linkify(r["reply_text_en"])}</details>'
        manual = "<hr>".join(linkify(t) for t in r["owner_replies"]) or ""
        body.append(f"""<tr class="{r['action']} {r['reason']}">
<td><a href="https://www.youtube.com/watch?v={esc(r['video_id'])}&lc={esc(r['comment_id'])}">{esc(r['video_title'] or r['video_id'])}</a><br><small>{esc(r['published_at'][:10])} · {esc(r['author'])}</small></td>
<td>{linkify(r['text'])}</td>
<td><b>{esc(r['action'])}</b><br><small>{esc(r['reason'])}</small><br><small>{cls_note}</small></td>
<td>{songs}</td><td>{reply}</td><td>{manual}</td></tr>""")
    summary = " · ".join(f"{k}: {v}" for k, v in counts.most_common())
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Comment bot replay</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{{--bg:#fff;--fg:#1a1a1a;--muted:#666;--line:#ddd;--reply:#eef7ee;--low:#fff6e0;--tag:#e8eefc}}
@media (prefers-color-scheme: dark){{:root{{--bg:#151515;--fg:#eee;--muted:#999;--line:#333;--reply:#1d2b1d;--low:#2e2814;--tag:#1f2840}}}}
body{{font:14px/1.4 system-ui,sans-serif;background:var(--bg);color:var(--fg);margin:16px}}
table{{border-collapse:collapse;width:100%}} td,th{{border:1px solid var(--line);padding:6px;vertical-align:top;text-align:left}}
tr.reply{{background:var(--reply)}} tr.low_confidence{{background:var(--low)}}
.tag{{background:var(--tag);border-radius:4px;padding:0 4px;font-size:12px}} small{{color:var(--muted)}} a{{color:inherit}}
label{{margin-right:12px}}
</style></head><body>
<h1>YouTube comment bot replay</h1>
<p>Comments since {esc(since)} · generated {esc(generated_at)} · nothing was posted or written.</p>
<p>{esc(summary)}</p>
<p><label><input type="checkbox" id="hideSkips"> hide plain skips (not a request / own comment)</label></p>
<table><thead><tr><th>Video</th><th>Comment</th><th>Decision</th><th>Songs</th><th>Bot reply</th><th>Channel's own reply</th></tr></thead>
<tbody>{''.join(body)}</tbody></table>
<script>
document.getElementById('hideSkips').addEventListener('change',e=>{{
 document.querySelectorAll('tr.skip.not_a_request, tr.skip.own_comment, tr.skip.empty').forEach(tr=>tr.style.display=e.target.checked?'none':'');
}});
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", required=True, help="YYYY-MM-DD; comments published on/after this date")
    ap.add_argument("--out", required=True, help="HTML report path")
    ap.add_argument("--json", help="also write raw rows as JSON")
    ap.add_argument("--limit", type=int, default=0, help="stop after N threads (0 = all)")
    ap.add_argument("--min-confidence", type=float, default=bot.DEFAULT_MIN_CONFIDENCE)
    ap.add_argument("--credentials-file", help="youtube-oauth-credentials JSON (default: Secret Manager)")
    ap.add_argument("--workers", type=int, default=8, help="parallel Gemini classifications")
    args = ap.parse_args()

    since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
    creds = (json.loads(Path(args.credentials_file).read_text()) if args.credentials_file
             else ytb.load_credentials_from_secret())
    if not creds:
        sys.exit("No YouTube credentials (gcloud login, or --credentials-file).")
    youtube = ytb.build_youtube(creds)

    t0 = time.time()
    print("Indexing channel videos...", file=sys.stderr)
    entries, _ = ytb.fetch_all_channel_entries(youtube)
    index = bot.ChannelIndex.from_entries(entries)
    print(f"  {len(index.titles)} videos, {len(index.by_key)} indexed songs", file=sys.stderr)

    board = SimulatedBoard(load_board())
    print(f"  board: {len(board.by_key)} active/published requests", file=sys.stderr)

    print("Fetching comment threads...", file=sys.stderr)
    threads = []
    for item in bot.iter_comment_threads(youtube, stop_before=since):
        threads.append(bot.load_thread(youtube, item))
        if args.limit and len(threads) >= args.limit:
            break
    print(f"  {len(threads)} threads", file=sys.stderr)

    generate = bot.gemini_generate()

    # Classification is the slow part and has no shared state, so pre-classify in
    # parallel; decide() then runs sequentially (board adds dedupe in order).
    def pre(thread):
        if thread.author_channel_id == bot.NOMAD_CHANNEL_ID or thread.owner_replies or not thread.text.strip():
            return None
        return bot.classify_comment(thread.text, index.titles.get(thread.video_id, ""), generate)

    with ThreadPoolExecutor(args.workers) as pool:
        pre_cls = list(pool.map(pre, threads))

    # Validation set: threads the channel already answered by hand get classified
    # too, so the report shows whether the bot would have spotted those requests.
    def pre_answered(thread):
        if not thread.owner_replies or thread.author_channel_id == bot.NOMAD_CHANNEL_ID:
            return None
        return bot.classify_comment(thread.text, index.titles.get(thread.video_id, ""), generate)

    with ThreadPoolExecutor(args.workers) as pool:
        answered_cls = list(pool.map(pre_answered, threads))

    rows, counts = [], Counter()
    for thread, cls, ans_cls in zip(threads, pre_cls, answered_cls):
        cached = {"cls": cls}

        def cached_generate(system_prompt, user_prompt, schema, _c=cached):
            if schema is bot.CLASSIFIER_RESPONSE_SCHEMA and _c["cls"] is not None:
                c = _c["cls"]
                return {"is_song_request": c.is_song_request, "songs": [s.__dict__ for s in c.songs],
                        "language": c.language, "confidence": c.confidence, "reason": c.reason}
            return generate(system_prompt, user_prompt, schema)

        d = bot.decide(thread, video_title=index.titles.get(thread.video_id, ""), index=index,
                       board=board, generate=cached_generate, min_confidence=args.min_confidence)
        classification = d.classification or ans_cls
        reason = d.reason
        if reason == "already_replied" and ans_cls is not None:
            reason = "already_replied_request" if ans_cls.is_song_request else "already_replied"
        counts[f"{d.action}:{reason}"] += 1
        rows.append({
            "video_id": thread.video_id, "video_title": index.titles.get(thread.video_id, ""),
            "comment_id": thread.comment_id, "author": thread.author, "text": thread.text,
            "published_at": thread.published_at, "owner_replies": thread.owner_replies,
            "action": d.action, "reason": reason,
            "classification": None if classification is None else {
                "is_song_request": classification.is_song_request, "confidence": classification.confidence,
                "language": classification.language, "reason": classification.reason,
                "songs": [s.__dict__ for s in classification.songs]},
            "songs": [s.__dict__ for s in d.songs],
            "reply_text": d.reply_text, "reply_text_en": d.reply_text_en,
        })

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    Path(args.out).write_text(render_html(rows, counts, args.since, generated_at), encoding="utf-8")
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"Done in {time.time() - t0:.0f}s: {dict(counts)}", file=sys.stderr)
    print(f"New board requests (simulated): {len(board.added)}", file=sys.stderr)
    print(args.out)


if __name__ == "__main__":
    main()
