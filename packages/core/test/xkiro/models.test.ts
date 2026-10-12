import { expect, test } from "bun:test"
import { ProviderPlugins } from "@opencode/core/plugin/provider"
import { XKiroModels } from "@opencode/core/xkiro/models"

const payload = {
  data: [
    {
      id: "google/gemini-3.6-flash",
      display_name: "Gemini 3.6 Flash",
      access_tier: "paid",
      pricing: { input: 0.75, output: 3.75, cache_read: 0.075, cache_write: 0.04 },
      capabilities: { vision: true, tools: true, reasoning: true },
      context_length: 1000000,
      max_output_tokens: 65536,
      reasoning_efforts: { levels: ["low", "medium", "high"], default: "medium" },
    },
    {
      id: "qwen/qwen3-coder-plus:free",
      display_name: "Qwen3 Coder Plus (Free)",
      access_tier: "free",
      pricing: { input: 9, output: 9 },
      capabilities: { tools: true },
      context_length: 1048576,
    },
    { id: "x/premium", access_tier: "premium", capabilities: {} },
    { id: "x/odd-tier", access_tier: "weird" },
    { id: "bad", context_length: "huge" },
    { id: "qwen/qwen3-coder-plus:free" },
  ],
}

test("xkiro plugin is registered", () => {
  expect(ProviderPlugins.map((item) => item.id)).toContain("lumus.provider.xkiro")
})

test("maps the /v1/models payload with tier labels, free first", () => {
  const specs = XKiroModels.parse(payload)
  expect(specs.map((item) => item.id)).toEqual([
    "qwen/qwen3-coder-plus:free",
    "google/gemini-3.6-flash",
    "x/premium",
    "x/odd-tier",
  ])
  expect(specs[0]).toMatchObject({ name: "Qwen3 Coder Plus (Free) [free]", tier: "free", tools: true, context: 1048576, output: 0 })
  // free models are never priced, whatever the payload says
  expect(specs[0]!.cost).toEqual({ input: 0, output: 0, cacheRead: 0, cacheWrite: 0 })
  expect(specs[1]).toMatchObject({
    name: "Gemini 3.6 Flash [paid]",
    vision: true,
    reasoning: true,
    efforts: ["low", "medium", "high"],
    cost: { input: 0.75, output: 3.75, cacheRead: 0.075, cacheWrite: 0.04 },
  })
  expect(specs[2]).toMatchObject({ name: "x/premium [premium]", tools: false })
  expect(specs[3]).toMatchObject({ name: "x/odd-tier", tier: "unknown" })
})

test("rejects a malformed envelope", () => {
  expect(() => XKiroModels.parse({ nope: [] })).toThrow()
})

test("load sends the key when given and hits /models", async () => {
  const seen: Array<string | null> = []
  const server = Bun.serve({
    port: 0,
    fetch: (request) => {
      expect(new URL(request.url).pathname).toBe("/v1/models")
      seen.push(request.headers.get("Authorization"))
      return Response.json(payload)
    },
  })
  try {
    const url = `${server.url.origin}/v1`
    expect((await XKiroModels.load("k1", url)).length).toBe(4)
    await XKiroModels.load(undefined, url)
    expect(seen).toEqual(["Bearer k1", null])
  } finally {
    await server.stop(true)
  }
})

test("load fails on a non-OK response", async () => {
  const server = Bun.serve({ port: 0, fetch: () => new Response("no", { status: 401 }) })
  try {
    await expect(XKiroModels.load("bad", `${server.url.origin}/v1`)).rejects.toThrow("401")
  } finally {
    await server.stop(true)
  }
})
