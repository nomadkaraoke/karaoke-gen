// kjbox.cc/.well-known/* : app-link association files for the Nomad Karaoke app.
// Must match what the kjbox box serves on sing.nomadkaraoke.com/.well-known/.
// The fingerprint list is substituted at deploy time by ../well_known.py from Pulumi config
// `kjboxcc_android_sha256_fingerprints`.
const ANDROID_SHA256_FINGERPRINTS = /*FINGERPRINTS*/[]/*END*/;

const ASSETLINKS = ANDROID_SHA256_FINGERPRINTS.length === 0 ? [] : [{
  relation: ["delegate_permission/common.handle_all_urls"],
  target: {
    namespace: "android_app",
    package_name: "com.nomadkaraoke.mobile",
    sha256_cert_fingerprints: ANDROID_SHA256_FINGERPRINTS,
  },
}];

const AASA = {
  applinks: {
    details: [{
      appIDs: ["55B76HN73D.com.nomadkaraoke.mobile"],
      components: [{ "/": "/", "?": { t: "?*" } }, { "/": "/*" }],
    }],
  },
};

const FILES = {
  "/.well-known/assetlinks.json": ASSETLINKS,
  "/.well-known/apple-app-site-association": AASA,
};

export default {
  async fetch(request) {
    const url = new URL(request.url);
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method Not Allowed", { status: 405, headers: { Allow: "GET, HEAD" } });
    }
    const body = FILES[url.pathname];
    if (body === undefined) return new Response("Not Found", { status: 404 });
    return new Response(request.method === "HEAD" ? null : JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json", "Cache-Control": "public, max-age=300" },
    });
  },
};
