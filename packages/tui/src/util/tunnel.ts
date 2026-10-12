// An online link for a hosted room: runs `cloudflared tunnel --url http://127.0.0.1:<port>` and
// reads the public https address it prints. The tunnel only carries traffic to this server;
// the room still needs its join code, and the host decides what the guest may do.
export const TUNNEL_URL = /https:\/\/[a-z0-9-]+\.trycloudflare\.com/i

export function findTunnelURL(text: string) {
  return TUNNEL_URL.exec(text)?.[0]
}

export type Tunnel = { readonly url: string; readonly stop: () => void }

export class TunnelError extends Error {}

export type Spawn = (command: string[]) => {
  readonly stderr: ReadableStream<Uint8Array>
  readonly kill: () => void
  readonly exited: Promise<number>
}

const spawnProcess: Spawn = (command) =>
  Bun.spawn(command, { stdout: "ignore", stderr: "pipe", stdin: "ignore" }) as unknown as ReturnType<Spawn>

export async function start(port: number, spawn: Spawn = spawnProcess, timeoutMs = 30_000): Promise<Tunnel> {
  let child: ReturnType<Spawn>
  try {
    child = spawn(["cloudflared", "tunnel", "--no-autoupdate", "--url", `http://127.0.0.1:${port}`])
  } catch {
    throw new TunnelError(
      "cloudflared is not installed. Install it (https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) and try again.",
    )
  }
  const stop = () => {
    try {
      child.kill()
    } catch {}
  }
  const reader = child.stderr.getReader()
  const decoder = new TextDecoder()
  let seen = ""
  const deadline = Date.now() + timeoutMs
  const exited = child.exited.then((code) => ({ exited: code }))
  while (Date.now() < deadline) {
    const next = await Promise.race([
      reader.read().then((chunk) => ({ chunk })),
      exited,
      Bun.sleep(Math.max(0, deadline - Date.now())).then(() => ({ timeout: true as const })),
    ])
    if ("timeout" in next) break
    if ("exited" in next) throw new TunnelError(`cloudflared stopped (exit ${next.exited}) before it gave an address`)
    if (next.chunk.done) throw new TunnelError("cloudflared closed without giving an address")
    seen += decoder.decode(next.chunk.value, { stream: true })
    const url = findTunnelURL(seen)
    if (url) {
      // Keep draining so the process never blocks on a full pipe.
      void (async () => {
        while (!(await reader.read().catch(() => ({ done: true }))).done) {}
      })()
      return { url, stop }
    }
  }
  stop()
  throw new TunnelError("cloudflared did not give an address in time")
}
