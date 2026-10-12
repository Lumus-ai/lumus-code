import { RGBA } from "@opentui/core"

// The Lumus Code mascot from MaskotOpencode.aseprite (13x15), redrawn at 11x6
// so it fits inside the prompt with room around it.
// "1" is the body, "2" the eyes, "." is transparent.
const ART = [
  "1.........1",
  "11.11111.11",
  ".112111211.",
  ".112111211.",
  ".1.11111.1.",
  "...11.11...",
]

export const MASCOT_PALETTE = {
  body: RGBA.fromInts(11, 15, 20),
  eye: RGBA.fromInts(77, 86, 95),
  // Unused in the drawing but part of its palette: the eyes light up with it when the mascot needs you.
  glow: RGBA.fromInts(142, 156, 171),
}

const EYE_COLUMNS = [3, 7]
const EYES = { open: [2, 3], closed: [3], wide: [2, 3], sad: [3, 4] }
const LEGS = { left: [3, 4], right: [6, 7], row: 5 }

// Four rows, the height of an empty prompt. The art rests two pixels below the top
// so a hop still clears the prompt's edge; below it the prompt's half-row bottom
// edge keeps a pixel of room.
export const MASCOT_WIDTH = ART[0]!.length
export const MASCOT_HEIGHT = 4
const TOP = 2

export type MascotMood = "idle" | "sleep" | "busy" | "attention" | "error" | "done" | "poke"

export type MascotPose = {
  eyes: "open" | "closed" | "wide" | "sad"
  look: -1 | 0 | 1
  lift: 0 | 1
  step: "none" | "left" | "right"
}

const REST: MascotPose = { eyes: "open", look: 0, lift: 0, step: "none" }

// Returns the pose at `t` ms into the mood and how long it holds, so the
// component schedules exactly one timer per visible change instead of polling.
export function mascotPose(mood: MascotMood, t: number): { pose: MascotPose; hold: number } {
  if (mood === "sleep") return { pose: { ...REST, eyes: "closed", lift: t % 6000 < 3000 ? 0 : 1 }, hold: 3000 - (t % 3000) }
  if (mood === "error") return blinking({ ...REST, eyes: "sad" }, t, 6100)
  if (mood === "attention") {
    const phase = t % 1400
    if (phase < 200) return { pose: { ...REST, eyes: "wide", lift: 1 }, hold: 200 - phase }
    return { pose: { ...REST, eyes: "wide" }, hold: 1400 - phase }
  }
  if (mood === "done" || mood === "poke") {
    // Two short hops, then the mood ends and the caller falls back to idle.
    const phase = Math.min(t, 1200)
    const lift = phase < 180 || (phase >= 400 && phase < 580) ? 1 : 0
    const eyes = mood === "poke" && phase < 400 ? "closed" : "open"
    const next = [180, 400, 580, 1200].find((edge) => edge > phase) ?? 1200
    return { pose: { ...REST, eyes, lift }, hold: Math.max(1, next - phase) }
  }
  if (mood === "busy") {
    // Slow steps in place with a glance now and then: alive, not attention-grabbing.
    const step = Math.floor(t / 700) % 2 === 0 ? "left" : "right"
    const cycle = t % 8400
    const look = cycle < 1400 ? 1 : cycle >= 4200 && cycle < 5600 ? -1 : 0
    return { pose: { ...REST, step, look }, hold: 700 - (t % 700) }
  }
  const cycle = t % 13000
  const look = cycle >= 8000 && cycle < 9200 ? -1 : cycle >= 9200 && cycle < 10400 ? 1 : 0
  const lift = cycle >= 3000 && cycle < 3600 ? 1 : 0
  const blink = blinking({ ...REST, look, lift }, t, 4300)
  const edges = [3000, 3600, 8000, 9200, 10400, 13000].map((edge) => edge - cycle).filter((value) => value > 0)
  return { pose: blink.pose, hold: Math.min(blink.hold, ...edges) }
}

// The blink closes the cycle, so the still frame at t = 0 (animations off) has open eyes.
function blinking(pose: MascotPose, t: number, period: number) {
  const phase = t % period
  if (phase >= period - 140) return { pose: { ...pose, eyes: "closed" as const }, hold: period - phase }
  return { pose, hold: period - 140 - phase }
}

// The upper and lower pixel of one terminal cell; undefined is transparent.
export type MascotCell = { top?: RGBA; bottom?: RGBA }

// Renders a pose as rows of cells, two pixels each. `body` replaces the body colour
// where the drawn one would vanish into the backdrop. A hop lifts the sprite one
// pixel inside its fixed box.
export function mascotCells(pose: MascotPose, body = MASCOT_PALETTE.body): MascotCell[][] {
  const pixels = posePixels(pose, body)
  return Array.from({ length: MASCOT_HEIGHT }, (_, row) =>
    Array.from({ length: MASCOT_WIDTH }, (_, x) =>
      ({ top: pixels[row * 2 - TOP + pose.lift]?.[x], bottom: pixels[row * 2 + 1 - TOP + pose.lift]?.[x] }),
    ),
  )
}

function posePixels(pose: MascotPose, body: RGBA) {
  return ART.map((line, y) =>
    Array.from(line, (char, x) => {
      const eye = eyePixel(pose, x, y)
      if (eye !== undefined) return eye ? (pose.eyes === "wide" ? MASCOT_PALETTE.glow : MASCOT_PALETTE.eye) : body
      if (char === "1" && lifted(pose, x, y)) return undefined
      if (char === "1") return body
      if (char === "2") return MASCOT_PALETTE.eye
      return undefined
    }),
  )
}

// undefined: not an eye cell; true: eye colour; false: body colour.
function eyePixel(pose: MascotPose, x: number, y: number) {
  const socket = EYE_COLUMNS.some((column) => Math.abs(x - column) <= 1) && y >= 1 && y <= 4
  // Eyes stay on the head: a glance never paints the gaps beside the horns.
  if (!socket || ART[y]![x] === ".") return undefined
  if (!EYE_COLUMNS.some((column) => x === column + pose.look)) return false
  return EYES[pose.eyes].includes(y)
}

function lifted(pose: MascotPose, x: number, y: number) {
  if (pose.step === "none" || y !== LEGS.row) return false
  return (pose.step === "left" ? LEGS.left : LEGS.right).includes(x)
}
