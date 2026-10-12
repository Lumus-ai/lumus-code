import { expect, test } from "bun:test"
import { testRender } from "@opentui/solid"
import type { RoomDod } from "@opencode/client/promise"
import { createSignal } from "solid-js"
import { ConfigProvider } from "../../src/config"
import { ThemeProvider } from "../../src/context/theme"
import { ToastProvider } from "../../src/ui/toast"
import { RoomDodRows } from "../../src/component/room-dod"
import { TestTuiContexts } from "../fixture/tui-environment"
import { createTuiResolvedConfig } from "../fixture/tui-runtime"

const board: RoomDod = {
  items: [
    { id: "auth", what: "Login works", stage: "build", state: "IN_PROGRESS", done: 1, total: 2 },
    { id: "docs", what: "Docs updated", stage: "build", state: "DONE", done: 1, total: 1 },
  ],
}

async function render(load: () => Promise<RoomDod>) {
  const [hover, setHover] = createSignal<string>()
  return testRender(
    () => (
      <TestTuiContexts>
        <ConfigProvider config={createTuiResolvedConfig()}>
          <ThemeProvider mode="dark" source={{ discover: async () => ({}) }}>
            <ToastProvider>
              <RoomDodRows
                id="room"
                load={load}
                release={load}
                feed={() => new Promise(() => {})}
                hover={hover}
                setHover={setHover}
              />
            </ToastProvider>
          </ThemeProvider>
        </ConfigProvider>
      </TestTuiContexts>
    ),
    { width: 80, height: 12 },
  )
}

async function frameWith(setup: Awaited<ReturnType<typeof render>>, text: string) {
  const started = Date.now()
  while (Date.now() - started < 3_000) {
    await setup.renderOnce()
    const frame = setup.captureCharFrame()
    if (frame.includes(text)) return frame
    await Bun.sleep(20)
  }
  throw new Error(`Timed out waiting for "${text}":\n${setup.captureCharFrame()}`)
}

test("a DoD that cannot be read says why instead of listing nothing", async () => {
  const setup = await render(() => Promise.reject(new Error("host unreachable")))
  try {
    const frame = await frameWith(setup, "DoD could not be read")
    expect(frame).toContain("host unreachable")
  } finally {
    setup.renderer.destroy()
  }
})

test("a failed refresh keeps the board and says since when it is stale", async () => {
  const outcomes = [Promise.resolve(board)]
  const setup = await render(() => outcomes.shift() ?? Promise.reject(new Error("connection reset")))
  try {
    await frameWith(setup, "1/2")
    // The panel polls every five seconds; the second read fails.
    const frame = await waitLong(setup, "DoD not updated since")
    expect(frame).toContain("connection reset")
    expect(frame).toContain("1/2")
  } finally {
    setup.renderer.destroy()
  }
}, 12_000)

async function waitLong(setup: Awaited<ReturnType<typeof render>>, text: string) {
  const started = Date.now()
  while (Date.now() - started < 8_000) {
    await setup.renderOnce()
    const frame = setup.captureCharFrame()
    if (frame.includes(text)) return frame
    await Bun.sleep(100)
  }
  throw new Error(`Timed out waiting for "${text}":\n${setup.captureCharFrame()}`)
}
