/**
 * KaraokeHunt request interceptor — Cloudflare Worker on create.karaokehunt.com.
 *
 * The retired KaraokeHunt mobile app (unchangeable binary, stores unpublished
 * 2026-09-13) still POSTs song requests to
 * https://create.karaokehunt.com/create_karaoke_video and then shows a
 * hardcoded "check your email in 5-10 minutes" modal, ignoring the response.
 *
 * This Worker answers the app instantly and forwards the payload to the gen
 * backend (POST /api/karaokehunt/request), which auto-converts the requester
 * into a Nomad Karaoke user + job (or requests-board entry). The forward runs
 * via ctx.waitUntil so the app's awaited call never blocks on the conversion
 * (flacfetch search can take ~40s); even if the subrequest is reaped, the
 * backend finishes its handler independently.
 *
 * Deployed by deploy.sh (NOT Pulumi/wrangler): zone karaokehunt.com
 * (02440ab623269c428be9e65a16bee280) in Andrew's Cloudflare account.
 * FORWARDER_SECRET binding = Secret Manager `karaokehunt-forwarder-secret`.
 */
export default {
  async fetch(request, env, ctx) {
    const respond = (obj, status = 200) =>
      new Response(JSON.stringify(obj), {
        status,
        headers: { "content-type": "application/json" },
      });

    if (request.method !== "POST") {
      return respond({ status: "ok", service: "karaokehunt-interceptor" });
    }

    const body = await request.text();
    const forward = fetch("https://api.nomadkaraoke.com/api/karaokehunt/request", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-kh-forwarder-secret": env.FORWARDER_SECRET,
        "x-kh-client-ip": request.headers.get("cf-connecting-ip") || "",
        "user-agent": request.headers.get("user-agent") || "karaokehunt-app",
      },
      body,
    }).catch((err) => console.log("forward failed:", err && err.message));
    ctx.waitUntil(forward);

    // The app ignores the body; succeed instantly so nothing user-visible hangs.
    return respond({ status: "success" });
  },
};
