import type { RGBA } from "@opentui/core"
import { createEffect, createMemo, createSignal, For, on, onCleanup } from "solid-js"
import { useTheme } from "../context/theme"
import { useConfig } from "../config"
import { tint } from "../theme/color"
import { MASCOT_HEIGHT, MASCOT_WIDTH, mascotCells, mascotPose, type MascotMood } from "../ui/mascot-sprite"

// Falls asleep after this long without anything happening, so an idle screen goes still.
const SLEEP_AFTER = 3 * 60 * 1000
// Hops for done and poke last this long before the mascot settles back.
const HOP = 1200

export type MascotState = "idle" | "busy" | "attention" | "error"

// The Lumus Code mascot. It mirrors the session: steps in place while the model
// works, looks up wide-eyed when a permission or question waits, hops once when a
// run finishes, droops after a failure and sleeps when nothing happens. Clicking
// it makes it hop.
export function Mascot(props: { state: MascotState; backdrop: RGBA }) {
  const theme = useTheme()
  const config = useConfig().data
  const [hop, setHop] = createSignal<"done" | "poke">()
  const [sleeping, setSleeping] = createSignal(false)
  const [wake, setWake] = createSignal(0)
  const [elapsed, setElapsed] = createSignal(0)

  const mood = createMemo((): MascotMood => hop() ?? (sleeping() && props.state === "idle" ? "sleep" : props.state))

  createEffect(
    on(
      () => props.state,
      (next, previous) => {
        // Hops end on their timer, which only runs with animations on.
        if (previous === "busy" && next === "idle" && config.animations !== false) setHop("done")
      },
    ),
  )

  createEffect(
    on([() => props.state, wake], ([next]) => {
      setSleeping(false)
      if (next !== "idle") return
      const timer = setTimeout(() => setSleeping(true), SLEEP_AFTER)
      onCleanup(() => clearTimeout(timer))
    }),
  )

  createEffect(() => {
    const current = mood()
    setElapsed(0)
    if (config.animations === false) return
    const start = performance.now()
    const schedule = (delay: number) => {
      const timer = setTimeout(() => {
        const t = performance.now() - start
        if ((current === "done" || current === "poke") && t >= HOP) return setHop(undefined)
        setElapsed(t)
        schedule(mascotPose(current, t).hold)
      }, delay)
      onCleanup(() => clearTimeout(timer))
    }
    schedule(mascotPose(current, 0).hold)
  })

  const pose = createMemo(() => mascotPose(mood(), elapsed()).pose, undefined, {
    equals: (a, b) => a.eyes === b.eyes && a.look === b.look && a.lift === b.lift && a.step === b.step,
  })
  // The body is near black; on a backdrop just as dark it would vanish, so lift it towards the muted text.
  const body = createMemo(() =>
    luminance(props.backdrop) < 0.09 ? tint(props.backdrop, theme.text.muted, 0.35) : undefined,
  )
  const cells = createMemo(() => mascotCells(pose(), body()))

  return (
    <box
      flexShrink={0}
      width={MASCOT_WIDTH}
      height={MASCOT_HEIGHT}
      onMouseUp={() => {
        setWake((value) => value + 1)
        if (config.animations !== false) setHop("poke")
      }}
    >
      {/* Every cell is the same glyph with both colours set: changing glyphs or clearing a
          background between frames can leave stale halves on screen. */}
      <For each={cells()}>
        {(row) => (
          <text selectable={false}>
            <For each={row}>
              {(cell) => (
                <span style={{ fg: cell.top ?? props.backdrop, bg: cell.bottom ?? props.backdrop }}>▀</span>
              )}
            </For>
          </text>
        )}
      </For>
    </box>
  )
}

function luminance(color: RGBA) {
  return 0.2126 * color.r + 0.7152 * color.g + 0.0722 * color.b
}
