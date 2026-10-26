# Requests board overhaul (2026-10-26)

Trigger: job 8695c32e / 94c83a7e investigation (user scottrcopeland@yahoo.co.uk). Findings: a
vote moved between requests left a stale YouTube-comment "claim"; the daily pick took result 0
because `select_best` failed on remote (dict) results; 0-vote requests were pickable.

Andrew's decisions (2026-10-26):
1. Stricter caps to push people to paid credits: max 1 submission/day AND max 1 vote/day.
   Submitting counts as the day's vote, so a submission is refused (409 `vote_already_used`)
   if today's vote is spent elsewhere. UX: confirm before spending the daily vote; warm
   thank-you after submit/vote pointing at credit packs for more/faster tracks.
2. A request on the board is ALWAYS linked to a real user email (enforced in the service).
   Picker needs `vote_count >= 1` and an owner who still up-votes it.
3. YouTube comment bot no longer creates requests; it replies concisely with how to request.
4. Removing the last up-vote deletes the request, after a confirmation (409 `would_delete_request`
   unless `confirm_delete=true`). Replaces the old claim/unclaim logic.
6. Daily pick audio: confident exact artist+title match only (lossless torrent w/ seeders, else
   Spotify); otherwise park at AWAITING_AUDIO_SELECTION so the owner chooses in the gen UI.
7. Review email for community picks says the song came from the requests board.
Also: deleting a community job reopens its request (owner excluded from re-pick).

Testing: unit tests in backend/tests (service rules, routes, picker, bot, audio pick), Jest for
the board client; emulator tests where the service already has them.
