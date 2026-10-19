// Run: node --test infrastructure/cloudflare/requests-host/
import { test, beforeEach } from "node:test"
import assert from "node:assert/strict"
import worker, { hasExtension } from "./worker.js"

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
  assert.deepEqual(fetched, ["https://gen.nomadkaraoke.com/en/requests/?utm_source=youtube"])
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
