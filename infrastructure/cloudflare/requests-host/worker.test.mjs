// Run: node --test infrastructure/cloudflare/requests-host/
import { test, beforeEach } from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import worker, { hasExtension, pickLocale, cookieLocale, LOCALES } from "./worker.js"

let fetched
beforeEach(() => {
  fetched = []
  globalThis.fetch = async (req) => {
    fetched.push(req.url)
    return new Response("upstream", { status: 200, headers: { "content-type": "text/html" } })
  }
})

test("/ serves the board page from gen, keeping the query string", async () => {
  const res = await worker.fetch(new Request("https://requests.nomadkaraoke.com/?utm_source=youtube"))
  assert.equal(res.status, 200)
  assert.match(res.headers.get("vary"), /Accept-Language/)
  assert.deepEqual(fetched, ["https://gen.nomadkaraoke.com/en/requests/?utm_source=youtube"])
})

test("/ picks the board language from Accept-Language", async () => {
  await worker.fetch(new Request("https://requests.nomadkaraoke.com/", {
    headers: { "accept-language": "es-MX,es;q=0.9,en;q=0.8" },
  }))
  assert.deepEqual(fetched, ["https://gen.nomadkaraoke.com/es/requests/"])
})

test("/ prefers the language-switcher cookie over Accept-Language", async () => {
  await worker.fetch(new Request("https://requests.nomadkaraoke.com/", {
    headers: { "accept-language": "es", cookie: "a=b; nk_requests_locale=ja" },
  }))
  assert.deepEqual(fetched, ["https://gen.nomadkaraoke.com/ja/requests/"])
})

test("cookieLocale ignores unknown values", () => {
  assert.equal(cookieLocale("nk_requests_locale=xx"), null)
  assert.equal(cookieLocale("nk_requests_locale=../app"), null)
  assert.equal(cookieLocale(null), null)
})

test("/{locale}/ forces a language; unknown 2-letter paths go to gen", async () => {
  await worker.fetch(new Request("https://requests.nomadkaraoke.com/de/?utm_source=x", {
    headers: { "accept-language": "fr" },
  }))
  await worker.fetch(new Request("https://requests.nomadkaraoke.com/ja"))
  const res = await worker.fetch(new Request("https://requests.nomadkaraoke.com/xx/"))
  assert.deepEqual(fetched, [
    "https://gen.nomadkaraoke.com/de/requests/?utm_source=x",
    "https://gen.nomadkaraoke.com/ja/requests/",
  ])
  assert.equal(res.status, 302)
})

test("pickLocale honours q-weights, primary subtags and falls back to en", () => {
  assert.equal(pickLocale("xx,ja;q=0.5,de;q=0.9"), "de")
  assert.equal(pickLocale("pt-BR"), "pt")
  assert.equal(pickLocale("nn-NO"), "nb")
  assert.equal(pickLocale("fr;q=0"), "en")
  assert.equal(pickLocale(""), "en")
  assert.equal(pickLocale(null), "en")
})

test("LOCALES mirrors frontend/i18n/routing.ts", () => {
  const routing = readFileSync(new URL("../../../frontend/i18n/routing.ts", import.meta.url), "utf8")
  const block = routing.slice(routing.indexOf("locales: ["), routing.indexOf("]", routing.indexOf("locales: [")))
  assert.deepEqual([...block.matchAll(/'([a-z]{2})'/g)].map((m) => m[1]), LOCALES)
})

test("static assets are proxied from gen", async () => {
  await worker.fetch(new Request("https://requests.nomadkaraoke.com/_next/static/chunks/abc.js"))
  await worker.fetch(new Request("https://requests.nomadkaraoke.com/en/requests/index.txt?_rsc=1"))
  assert.deepEqual(fetched, [
    "https://gen.nomadkaraoke.com/_next/static/chunks/abc.js",
    "https://gen.nomadkaraoke.com/en/requests/index.txt?_rsc=1",
  ])
})

test("other page paths redirect to the same path on gen", async () => {
  const res = await worker.fetch(new Request("https://requests.nomadkaraoke.com/en/app/?x=1"))
  assert.equal(res.status, 302)
  assert.equal(res.headers.get("location"), "https://gen.nomadkaraoke.com/en/app/?x=1")
  assert.deepEqual(fetched, [])
})

test("hasExtension only looks at the last path segment", () => {
  assert.equal(hasExtension("/favicon.ico"), true)
  assert.equal(hasExtension("/v1.2/app/"), false)
  assert.equal(hasExtension("/en/requests"), false)
})
