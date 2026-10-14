/** @jsxImportSource @opentui/solid */
import { RGBA } from "@opentui/core"
import { testRender } from "@opentui/solid"
import { expect, test } from "bun:test"
import { createSignal, Show } from "solid-js"
import { CosmosRail } from "../../src/component/cosmos-rail"

test.each([72, 32])("cosmos occupies only two margin cells at width %s; off removes it", async (width) => {
  const [enabled, setEnabled] = createSignal(true)
  const app = await testRender(
    () => (
      <box width="100%" height="100%" flexDirection="row">
        <Show when={enabled()}>
          <CosmosRail enabled color={RGBA.fromHex("#9aa0a6")} animations={false} height={12} />
        </Show>
        <box flexGrow={1}>
          <text>Transcript remains readable</text>
          <text>Permission: allow or deny</text>
        </box>
      </box>
    ),
    { width, height: 12 },
  )
  try {
    app.renderer.start()
    await app.waitForFrame((frame) => frame.includes("Transcript remains readable"))
    const frame = app.captureCharFrame()
    expect(frame.split("\n")[0].indexOf("Transcript")).toBe(2)
    expect(frame.split("\n")[1].startsWith("· Permission")).toBe(true)
    await Bun.write(`/tmp/cosmos-${width}.json`, JSON.stringify(app.captureSpans()))
    setEnabled(false)
    await app.waitForFrame((frame) => frame.split("\n")[0].startsWith("Transcript"))
    expect(app.captureCharFrame()).not.toContain("⋆")
  } finally {
    app.renderer.destroy()
  }
})

test("missing decorative role adds no rail; custom themes keep prior layout", async () => {
  const app = await testRender(
    () => (
      <box width="100%" height="100%" flexDirection="row">
        <CosmosRail enabled animations={false} height={10} />
        <text>Custom theme</text>
      </box>
    ),
    { width: 40, height: 10 },
  )
  try {
    app.renderer.start()
    await app.waitForFrame((frame) => frame.startsWith("Custom theme"))
    expect(app.captureCharFrame()).not.toContain("·")
  } finally {
    app.renderer.destroy()
  }
})

test("motion updates opacity but never moves text or star cells", async () => {
  const [moving, setMoving] = createSignal(true)
  const app = await testRender(
    () => (
      <box width="100%" height="100%" flexDirection="row">
        <CosmosRail enabled color={RGBA.fromHex("#9aa0a6")} animations={moving()} height={10} />
        <text>Still transcript</text>
      </box>
    ),
    { width: 40, height: 10 },
  )
  try {
    app.renderer.start()
    await app.waitForFrame((frame) => frame.includes("Still transcript"))
    const chars = app.captureCharFrame()
    const initial = app.captureSpans().lines[1].spans[0].fg.r
    await new Promise((resolve) => setTimeout(resolve, 1300))
    await app.renderOnce()
    expect(app.captureCharFrame()).toBe(chars)
    expect(app.captureSpans().lines[1].spans[0].fg.r).not.toBe(initial)
    setMoving(false)
    await app.renderOnce()
    const still = app.captureSpans().lines[1].spans[0].fg.r
    await new Promise((resolve) => setTimeout(resolve, 1300))
    await app.renderOnce()
    expect(app.captureSpans().lines[1].spans[0].fg.r).toBe(still)
  } finally {
    app.renderer.destroy()
  }
})


test("disabled by default; malformed height is bounded", async () => {
  const [enabled, setEnabled] = createSignal(false)
  const [height, setHeight] = createSignal(Number.POSITIVE_INFINITY)
  const app = await testRender(
    () => <box flexDirection="row"><CosmosRail enabled={enabled()} color={RGBA.fromHex("#9aa0a6")} animations={false} height={height()} /><text>Safe preview</text></box>,
    { width: 40, height: 10 },
  )
  try {
    app.renderer.start()
    await app.waitForFrame((frame) => frame.startsWith("Safe preview"))
    setEnabled(true)
    await app.renderOnce()
    expect(app.captureCharFrame()).not.toContain("·")
    setHeight(1000000)
    await app.renderOnce()
    expect(app.captureCharFrame()).toContain("·")
    setHeight(-5)
    await app.renderOnce()
    expect(app.captureCharFrame()).not.toContain("·")
  } finally {
    app.renderer.destroy()
  }
})
