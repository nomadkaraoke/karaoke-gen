/**
 * @jest-environment node
 */
import {
  getTenantConfig,
  _resetInFlightForTests,
  FRESH_SECONDS,
  type TenantConfigResponse,
} from "@/lib/edge/tenantConfigCache"

const CONFIG: TenantConfigResponse = {
  tenant: {
    id: "singa",
    name: "Singa",
    subdomain: "singa",
    is_active: true,
    branding: {
      primary_color: "#ff0000",
      secondary_color: "#00ff00",
      accent_color: null,
      background_color: null,
      favicon_url: null,
      site_title: "Singa",
    },
  },
  is_default: false,
}

class MemCache {
  store = new Map<string, Response>()
  async match(key: string) {
    const r = this.store.get(key)
    return r ? r.clone() : undefined
  }
  async put(key: string, r: Response) {
    this.store.set(key, r)
  }
}

function setup(fetchImpl?: jest.Mock) {
  const cache = new MemCache()
  const fetchFn =
    fetchImpl ??
    jest.fn(async () => new Response(JSON.stringify(CONFIG), { status: 200 }))
  const background: Promise<unknown>[] = []
  let now = 1_000_000
  const deps = {
    cache,
    fetchFn: fetchFn as unknown as typeof fetch,
    waitUntil: (p: Promise<unknown>) => {
      background.push(p)
    },
    now: () => now,
  }
  return { cache, fetchFn, background, deps, advance: (ms: number) => (now += ms) }
}

const KEY = "https://singa.nomadkaraoke.com/__tenant_config__/singa"

beforeEach(() => _resetInFlightForTests())

describe("getTenantConfig (edge stale-while-revalidate)", () => {
  it("fetches on a cold miss and caches the result", async () => {
    const { fetchFn, cache, deps } = setup()
    expect(await getTenantConfig("singa", KEY, deps)).toEqual(CONFIG)
    expect(fetchFn).toHaveBeenCalledTimes(1)
    expect(cache.store.has(KEY)).toBe(true)
  })

  it("calls fetchFn unbound (Workers fetch throws 'Illegal invocation' as a method)", async () => {
    // Mimics Cloudflare's fetch: any `this` other than undefined/global throws.
    const fetchFn = jest.fn(function (this: unknown) {
      if (this !== undefined && this !== globalThis) {
        throw new TypeError("Illegal invocation: function called with incorrect `this` reference.")
      }
      return Promise.resolve(new Response(JSON.stringify(CONFIG), { status: 200 }))
    })
    const { deps } = setup(fetchFn as unknown as jest.Mock)
    expect(await getTenantConfig("singa", KEY, deps)).toEqual(CONFIG)
  })

  it("serves a fresh cache hit without touching the backend", async () => {
    const { fetchFn, deps, background, advance } = setup()
    await getTenantConfig("singa", KEY, deps)
    advance(60_000)
    expect(await getTenantConfig("singa", KEY, deps)).toEqual(CONFIG)
    expect(fetchFn).toHaveBeenCalledTimes(1)
    expect(background).toHaveLength(0)
  })

  it("serves a stale hit immediately and refreshes in the background", async () => {
    const { fetchFn, deps, background, advance } = setup()
    await getTenantConfig("singa", KEY, deps)
    advance((FRESH_SECONDS + 1) * 1000)
    expect(await getTenantConfig("singa", KEY, deps)).toEqual(CONFIG)
    expect(background).toHaveLength(1)
    await Promise.all(background)
    expect(fetchFn).toHaveBeenCalledTimes(2)
  })

  it("collapses a burst of concurrent cold misses into one backend call", async () => {
    let release!: () => void
    const gate = new Promise<void>((r) => (release = r))
    const fetchFn = jest.fn(async () => {
      await gate
      return new Response(JSON.stringify(CONFIG), { status: 200 })
    })
    const { deps } = setup(fetchFn)
    const burst = Array.from({ length: 17 }, () => getTenantConfig("singa", KEY, deps))
    release()
    const results = await Promise.all(burst)
    expect(results.every((r) => r?.tenant?.id === "singa")).toBe(true)
    expect(fetchFn).toHaveBeenCalledTimes(1)
  })

  it("returns null on a backend error so the HTML is served uninjected", async () => {
    const fetchFn = jest.fn(async () => new Response("nope", { status: 503 }))
    const { deps, cache } = setup(fetchFn)
    expect(await getTenantConfig("singa", KEY, deps)).toBeNull()
    expect(cache.store.has(KEY)).toBe(false)
  })

  it("does not cache a malformed 200 payload", async () => {
    const fetchFn = jest.fn(
      async () => new Response(JSON.stringify({ tenant: { id: "singa" }, is_default: false }), { status: 200 })
    )
    const { deps, cache } = setup(fetchFn)
    expect(await getTenantConfig("singa", KEY, deps)).toBeNull()
    expect(cache.store.has(KEY)).toBe(false)
  })

  it("passes a timeout signal so a cold backend can't hold the page", async () => {
    const { fetchFn, deps } = setup()
    await getTenantConfig("singa", KEY, deps)
    const init = (fetchFn.mock.calls[0] as unknown[])[1] as RequestInit
    expect(init.signal).toBeInstanceOf(AbortSignal)
  })

  it("returns null when the fetch aborts (timeout)", async () => {
    const fetchFn = jest.fn(async () => {
      throw new DOMException("The operation timed out.", "TimeoutError")
    })
    const { deps } = setup(fetchFn)
    expect(await getTenantConfig("singa", KEY, deps)).toBeNull()
  })

  it("keeps serving the stale copy when the background refresh fails", async () => {
    const responses = [
      new Response(JSON.stringify(CONFIG), { status: 200 }),
      new Response("down", { status: 500 }),
    ]
    const fetchFn = jest.fn(async () => responses.shift()!)
    const { deps, background, advance } = setup(fetchFn)
    await getTenantConfig("singa", KEY, deps)
    advance((FRESH_SECONDS + 1) * 1000)
    await getTenantConfig("singa", KEY, deps)
    await Promise.all(background)
    expect(await getTenantConfig("singa", KEY, deps)).toEqual(CONFIG)
  })

  it("treats entries without a fetched-at stamp (pre-deploy cache) as stale", async () => {
    const { cache, deps, background } = setup()
    await cache.put(KEY, new Response(JSON.stringify(CONFIG)))
    expect(await getTenantConfig("singa", KEY, deps)).toEqual(CONFIG)
    expect(background).toHaveLength(1)
  })
})
