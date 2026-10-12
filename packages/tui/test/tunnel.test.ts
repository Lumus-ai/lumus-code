import { expect, test } from "bun:test"
import { findTunnelURL, start, TunnelError, type Spawn } from "../src/util/tunnel"

const fake = (chunks: string[], exit?: number): Spawn => () => {
  let killed = false
  const encoder = new TextEncoder()
  return {
    stderr: new ReadableStream({
      async start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
        if (exit === undefined) await Bun.sleep(5)
        else controller.close()
      },
    }),
    kill: () => {
      killed = true
    },
    exited: exit === undefined ? new Promise<number>(() => {}) : Promise.resolve(exit),
    get killed() {
      return killed
    },
  } as never
}

test("finds the trycloudflare address in cloudflared output", () => {
  expect(findTunnelURL("INF |  https://quarterly-glen-francis-sandra.trycloudflare.com  |")).toBe(
    "https://quarterly-glen-francis-sandra.trycloudflare.com",
  )
  expect(findTunnelURL("https://evil.example.com")).toBeUndefined()
})

test("start returns the address even when it arrives split over chunks", async () => {
  const tunnel = await start(47475, fake(["INF Requesting tunnel\nINF https://abc-def.try", "cloudflare.com ready\n"]))
  expect(tunnel.url).toBe("https://abc-def.trycloudflare.com")
  tunnel.stop()
})

test("start fails clearly when cloudflared is missing or exits", async () => {
  await expect(
    start(1, () => {
      throw new Error("ENOENT")
    }),
  ).rejects.toThrow("not installed")
  await expect(start(1, fake(["error\n"], 1))).rejects.toBeInstanceOf(TunnelError)
})
