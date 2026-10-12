/** @jsxImportSource @opentui/solid */
import { expect, test } from "bun:test"
import { RGBA } from "@opentui/core"
import { testRender } from "@opentui/solid"
import { createSignal } from "solid-js"
import { ConfigProvider } from "../../src/config"
import { ThemeProvider } from "../../src/context/theme"
import { Mascot, type MascotState } from "../../src/component/mascot"
import { MASCOT_HEIGHT, MASCOT_PALETTE, MASCOT_WIDTH, mascotCells, mascotPose } from "../../src/ui/mascot-sprite"
import { emptyThemeSource } from "../fixture/fixture"
import { TestTuiContexts } from "../fixture/tui-environment"
import { createTuiResolvedConfig } from "../fixture/tui-runtime"

test("the still frame has open eyes, so a disabled animation never freezes mid-blink", () => {
  for (const mood of ["idle", "busy", "error"] as const) expect(mascotPose(mood, 0).pose.eyes).not.toBe("closed")
})

test("idle blinks briefly and every hold lands exactly on the next change", () => {
  const changes = Array.from({ length: 12 }).reduce<{ t: number; eyes: string; hold: number }[]>((list) => {
    const t = list.reduce((sum, item) => sum + item.hold, 0)
    const next = mascotPose("idle", t)
    return [...list, { t, eyes: next.pose.eyes, hold: next.hold }]
  }, [])
  const blink = changes.find((change) => change.eyes === "closed")
  expect(blink?.t).toBeLessThan(5000)
  expect(blink!.hold).toBeLessThanOrEqual(140)
  expect(mascotPose("idle", blink!.t + blink!.hold).pose.eyes).toBe("open")
})

test("busy steps from foot to foot while a request runs", () => {
  const first = mascotPose("busy", 0)
  expect(first.pose.step).toBe("left")
  expect(mascotPose("busy", first.hold).pose.step).toBe("right")
})

test("attention opens the eyes wide and sleep closes them", () => {
  expect(mascotPose("attention", 500).pose.eyes).toBe("wide")
  expect(mascotPose("sleep", 500).pose.eyes).toBe("closed")
})

test("cells fill a fixed four-row box whatever the pose, so the prompt never jumps", () => {
  const rest = mascotCells(mascotPose("idle", 0).pose)
  const hop = mascotCells({ eyes: "wide", look: 1, lift: 1, step: "left" })
  expect(MASCOT_HEIGHT).toBe(4)
  for (const cells of [rest, hop]) {
    expect(cells).toHaveLength(MASCOT_HEIGHT)
    for (const row of cells) expect(row).toHaveLength(MASCOT_WIDTH)
  }
})

test("a lifted body colour replaces the near-black one and keeps the eyes", () => {
  const lifted = RGBA.fromInts(60, 60, 60)
  const colors = (cells: ReturnType<typeof mascotCells>) => cells.flat().flatMap((cell) => [cell.top, cell.bottom])
  const plain = colors(mascotCells(mascotPose("idle", 0).pose))
  const drawn = colors(mascotCells(mascotPose("idle", 0).pose, lifted))
  expect(plain).toContain(MASCOT_PALETTE.body)
  expect(drawn).not.toContain(MASCOT_PALETTE.body)
  expect(drawn).toContain(lifted)
  expect(drawn).toContain(MASCOT_PALETTE.eye)
})

function renderMascot(initial: MascotState) {
  const [state, setState] = createSignal<MascotState>(initial)
  return testRender(
    () => (
      <TestTuiContexts>
        <ConfigProvider config={createTuiResolvedConfig({ animations: false })}>
          <ThemeProvider mode="dark" source={emptyThemeSource}>
            <Mascot state={state()} backdrop={RGBA.fromInts(22, 30, 40)} />
          </ThemeProvider>
        </ConfigProvider>
      </TestTuiContexts>
    ),
    { width: 16, height: 6 },
  ).then((app) => ({ app, setState }))
}

test("the mascot renders inside its box and follows the session state", async () => {
  const { app, setState } = await renderMascot("busy")
  try {
    await app.waitForFrame((frame) => frame.includes("▀"))
    const busy = JSON.stringify(app.captureSpans())
    setState("attention")
    await app.renderOnce()
    expect(JSON.stringify(app.captureSpans())).not.toBe(busy)
    const rows = app.captureCharFrame().split("\n").filter((line) => line.trim())
    expect(rows).toHaveLength(MASCOT_HEIGHT)
    for (const line of rows) expect(line.trimEnd().length).toBe(MASCOT_WIDTH)
  } finally {
    app.renderer.destroy()
  }
})

test("switching states leaves no stale pixels from earlier frames", async () => {
  const fresh = await renderMascot("idle")
  const changed = await renderMascot("idle")
  try {
    await fresh.app.waitForFrame((frame) => frame.includes("▀"))
    await changed.app.waitForFrame((frame) => frame.includes("▀"))
    // busy -> idle would hop after a finished run; with animations off it must not stick mid-air.
    for (const state of ["error", "attention", "busy", "idle", "error", "idle"] as const) {
      changed.setState(state)
      await changed.app.renderOnce()
    }
    expect(JSON.stringify(changed.app.captureSpans())).toBe(JSON.stringify(fresh.app.captureSpans()))
  } finally {
    fresh.app.renderer.destroy()
    changed.app.renderer.destroy()
  }
})
