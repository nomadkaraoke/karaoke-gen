import { test, expect } from "@playwright/test";

/**
 * Bulk Mode Production Tests (SAFE — no real karaoke jobs are created).
 *
 * Verifies the Bulk Mode API surface is live and behaves:
 *  - album lookup (MusicBrainz) returns artists/albums/tracklist
 *  - text-mode availability check returns results
 *  - submit credit gate rejects empty / over-limit payloads (no jobs created)
 *
 * We intentionally do NOT POST a real batch of songs here — that would spawn
 * real processing. The happy-path submit is covered by backend integration
 * tests; this spec is a deployment smoke check.
 *
 * Requires KARAOKE_ADMIN_TOKEN (a @nomadkaraoke.com admin).
 * Run with:
 *   KARAOKE_ADMIN_TOKEN=xxx npx playwright test e2e/production/bulk-mode.spec.ts --reporter=list
 */

const API_URL = "https://api.nomadkaraoke.com";
const token = process.env.KARAOKE_ADMIN_TOKEN || "";

const auth = { Authorization: `Bearer ${token}` };

test.describe("Bulk Mode API (production smoke)", () => {
  test.skip(!token, "KARAOKE_ADMIN_TOKEN not set");

  test("album artist lookup returns results", async () => {
    const res = await fetch(`${API_URL}/api/bulk/album/artists?q=Daft%20Punk`, { headers: auth });
    expect(res.status).toBe(200);
    const artists = await res.json();
    expect(Array.isArray(artists)).toBe(true);
    expect(artists.length).toBeGreaterThan(0);
    expect(artists[0]).toHaveProperty("mbid");
    expect(artists[0]).toHaveProperty("name");
  });

  test("album tracklist returns tracks with extras + availability flags", async () => {
    const artistsRes = await fetch(`${API_URL}/api/bulk/album/artists?q=Daft%20Punk`, { headers: auth });
    const artists = await artistsRes.json();
    const mbid = artists[0].mbid;

    const albumsRes = await fetch(`${API_URL}/api/bulk/album/albums?artist_mbid=${mbid}`, { headers: auth });
    expect(albumsRes.status).toBe(200);
    const albums = await albumsRes.json();
    expect(albums.length).toBeGreaterThan(0);

    const rg = albums[0].release_group_mbid;
    const tlRes = await fetch(
      `${API_URL}/api/bulk/album/tracklist?artist=${encodeURIComponent(artists[0].name)}&release_group_mbid=${rg}`,
      { headers: auth }
    );
    expect(tlRes.status).toBe(200);
    const tl = await tlRes.json();
    expect(Array.isArray(tl.tracks)).toBe(true);
    expect(tl.tracks.length).toBeGreaterThan(0);
    expect(tl.tracks[0]).toHaveProperty("is_extra");
    expect(tl.tracks[0]).toHaveProperty("available");
  });

  test("text-mode availability check returns per-track results", async () => {
    const res = await fetch(`${API_URL}/api/bulk/availability`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...auth },
      body: JSON.stringify({ tracks: [{ artist: "Queen", title: "Bohemian Rhapsody" }] }),
    });
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.results).toHaveLength(1);
    expect(data.results[0]).toHaveProperty("available");
  });

  test("submit rejects an empty batch (422, no jobs created)", async () => {
    const res = await fetch(`${API_URL}/api/bulk/submit`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...auth },
      body: JSON.stringify({ songs: [], settings: {} }),
    });
    expect(res.status).toBe(422);
  });

  test("submit rejects over-100 batch (422, no jobs created)", async () => {
    const songs = Array.from({ length: 101 }, (_, i) => ({ artist: `A${i}`, title: `T${i}` }));
    const res = await fetch(`${API_URL}/api/bulk/submit`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...auth },
      body: JSON.stringify({ songs, settings: {} }),
    });
    expect(res.status).toBe(422);
  });
});
