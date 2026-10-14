import { RGBA } from "@opentui/core"
import { createEffect, createSignal, For, onCleanup, Show } from "solid-js"

// A separate two-cell margin, never an overlay on transcript or permission controls.
export function CosmosRail(props: { enabled?: boolean; color?: RGBA; height: number; animations: boolean }) {
  const [phase, setPhase] = createSignal(0)
  createEffect(() => {
    if (!props.enabled || !props.animations || !props.color) return
    const timer = setInterval(() => setPhase((value) => (value + 1) % 4), 1200)
    onCleanup(() => clearInterval(timer))
  })
  const color = () => {
    const value = RGBA.clone(props.color ?? RGBA.fromInts(0, 0, 0, 0))
    value.a *= props.animations ? [0.35, 0.5, 0.65, 0.5][phase()]! : 0.5
    return value
  }
  return (
    <Show when={props.enabled && props.color}>
      <box width={2} flexShrink={0} height="100%" flexDirection="column">
        <For each={Array.from({ length: Number.isFinite(props.height) ? Math.max(0, Math.min(200, Math.floor(props.height))) : 0 }, (_, row) => row)}>
          {(row) => (
            <text height={1} fg={color()}>
              {row % 5 === 1 ? "· " : row % 11 === 3 ? " ⋆" : "  "}
            </text>
          )}
        </For>
      </box>
    </Show>
  )
}
