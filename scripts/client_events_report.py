#!/usr/bin/env python3
"""Summarise degradation telemetry (Firestore ``client_events``).

Answers "how often are real users seeing the Reconnecting pill / servers
unavailable banner, for how long, and what froze the backend?"

    python scripts/client_events_report.py              # last 7 days, customers only
    python scripts/client_events_report.py --days 30 --all   # include admin/internal/test

Needs read access to Firestore project ``nomadkaraoke`` (ADC; the read-only
``claude-readonly`` SA works). Read-only.

Collection fields (since 2026-10-03; older docs have none of the identity
fields): type, source (client|server), created_at, user_email, is_admin,
is_internal, is_test, tenant, device_fingerprint, tab_id, episode_id, url,
release, detail{...}. Server docs (type ``server_loop_stall``) add culprit /
culprits / stack / revision / instance_id.
"""
from __future__ import annotations

import argparse
import collections
from datetime import datetime, timedelta, timezone


def _tenant_from_url(url: str) -> str | None:
    """Fallback for pre-2026-10-03 docs that lack the server-derived ``tenant``."""
    host = url.split("://", 1)[-1].split("/", 1)[0].lower()
    if not host.endswith(".nomadkaraoke.com"):
        return None
    sub = host[: -len(".nomadkaraoke.com")]
    return None if (not sub or "." in sub or sub in {"gen", "www", "api", "app"}) else sub


def _who(e: dict) -> str:
    return e.get("user_email") or (
        f"fp:{e['device_fingerprint'][:10]}" if e.get("device_fingerprint") else "(anonymous)"
    )


def _is_noise(e: dict) -> bool:
    return bool(e.get("is_admin") or e.get("is_internal") or e.get("is_test"))


def summarise(events: list[dict], include_noise: bool) -> str:
    out: list[str] = []
    client = [e for e in events if e.get("source", "client") == "client" and e.get("type") != "server_loop_stall"]
    stalls = [e for e in events if e.get("type") == "server_loop_stall"]
    if not include_noise:
        dropped = sum(1 for e in client if _is_noise(e))
        client = [e for e in client if not _is_noise(e)]
        out.append(f"(excluded {dropped} admin/internal/test events; --all to include)")

    by_type = collections.Counter(e["type"] for e in client)
    out.append("\n== Client events by type")
    for t, n in by_type.most_common():
        out.append(f"  {t:22s} {n}")

    out.append("\n== Banners per day (reconnecting / unavailable / waking)")
    days: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for e in client:
        if e["type"].startswith("banner_") and e["type"] != "banner_recovered":
            days[e["created_at"].strftime("%Y-%m-%d")][e["type"]] += 1
    for d in sorted(days):
        c = days[d]
        out.append(f"  {d}  {c['banner_reconnecting']:4d} / {c['banner_unavailable']:4d} / {c['banner_waking']:4d}")

    rec = [e for e in client if e["type"] == "banner_recovered"]
    if rec:
        durs = sorted((e.get("detail") or {}).get("duration_ms") or 0 for e in rec)
        p = lambda q: durs[min(len(durs) - 1, int(q * len(durs)))] / 1000  # noqa: E731
        peaks = collections.Counter((e.get("detail") or {}).get("peak_status") for e in rec)
        out.append(
            f"\n== Episode durations ({len(rec)} recovered): p50 {p(0.5):.1f}s  p90 {p(0.9):.1f}s  "
            f"max {durs[-1] / 1000:.1f}s   peaks {dict(peaks)}"
        )

    users = collections.Counter(_who(e) for e in client if e["type"] in ("banner_reconnecting", "banner_unavailable"))
    out.append(f"\n== Affected users/devices: {len(users)}")
    for u, n in users.most_common(15):
        out.append(f"  {n:4d}  {u}")

    tenants = collections.Counter(e.get("tenant") or _tenant_from_url(e.get("url", "")) or "(consumer)" for e in client)
    out.append("\n== By tenant: " + ", ".join(f"{t}={n}" for t, n in tenants.most_common()))

    out.append(f"\n== Server event-loop stalls >=5s: {len(stalls)}")
    culprits = collections.Counter((e.get("detail") or {}).get("culprit", "?") for e in stalls)
    for c, n in culprits.most_common(15):
        durs = [(e.get("detail") or {}).get("duration_ms", 0) for e in stalls if (e.get("detail") or {}).get("culprit", "?") == c]
        out.append(f"  {n:3d}x  max {max(durs) / 1000:5.1f}s  {c}")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--all", action="store_true", help="include admin/internal/test accounts")
    args = ap.parse_args()

    from google.cloud import firestore
    from google.cloud.firestore_v1.base_query import FieldFilter

    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    db = firestore.Client(project="nomadkaraoke")
    events = [d.to_dict() for d in db.collection("client_events").where(filter=FieldFilter("created_at", ">=", since)).stream()]
    print(f"client_events since {since:%Y-%m-%d %H:%M} UTC: {len(events)} docs")
    print(summarise(events, include_noise=args.all))


if __name__ == "__main__":
    main()
