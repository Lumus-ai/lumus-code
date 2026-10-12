export * as Dod from "./dod.js"

import { chmod, rename, stat } from "node:fs/promises"
import path from "node:path"
import { Effect, Option, Schema } from "effect"
import type { Room } from "@opencode/schema/room"

// The project's Definition of Done, .opencode/dod.yaml, as a room shares it. The file
// belongs to the people and AIs on the host: the oc-dod board, the lead AI and a text
// editor all write it. A room only reads its items, marks who took one and records the
// host's own check of a submitted one, by editing that item's own lines, so comments,
// order and every other field stay exactly as their authors wrote them.
export const FILE = path.join(".opencode", "dod.yaml")

export class DodError extends Schema.TaggedError<DodError>()("Dod.Error", {
  message: Schema.String,
}) {}

const FINISHED = new Set(["DONE", "OUT_OF_SCOPE"])
const STEP = /^\s*\[(x|X)\]/
// Another writer can save between this read and this write; a few fresh tries settle it.
const ATTEMPTS = 3

const Leaf = Schema.Struct({
  id: Schema.String,
  what: Schema.String.pipe(Schema.optional),
  group: Schema.String.pipe(Schema.optional),
  task_state: Schema.String.pipe(Schema.optional),
  steps: Schema.Array(Schema.String).pipe(Schema.optional),
  owner: Schema.String.pipe(Schema.optional),
  owner_id: Schema.String.pipe(Schema.optional),
  depends_on: Schema.Array(Schema.String).pipe(Schema.optional),
  verification: Schema.Struct({ command: Schema.String.pipe(Schema.optional) }).pipe(Schema.optional),
})
const decodeLeaf = Schema.decodeUnknownOption(Leaf)

// Who takes an item: a room member, by the ID its token carries, and the name people see.
export type Owner = { id: string; name: string }

// Whether a holder still counts. A room answers false for a member who left or has been
// silent past the lease, so the item it held reads as free again.
export type Live = (ownerID: string) => boolean

// The items in file order, which is the order of the work. An empty list when the
// project has no DoD.
export const read = Effect.fn("Dod.read")(function* (directory: string, live?: Live) {
  const text = yield* load(directory)
  return { items: text === undefined ? [] : yield* items(text, live) } satisfies Room.Dod
})

// Marks the item as the owner's and in progress. Fails on an item that is finished, that
// someone else holds, or that waits for items not yet finished. An item whose holder's
// lease ran out passes to the new owner, whose work continues from its checked steps.
export const take = Effect.fn("Dod.take")(function* (input: {
  directory: string
  id: string
  owner: Owner
  live?: Live
}) {
  return yield* change(
    input.directory,
    input.id,
    input.live,
    (item) => refusal(item, input.owner) ?? claim(input.owner),
  )
})

// Takes the first item, in the order of the work, that the owner may start: free and with
// everything it depends on finished. The owner's own unfinished item comes back first.
export const next = Effect.fn("Dod.next")(function* (input: { directory: string; owner: Owner; live?: Live }) {
  const list = (yield* read(input.directory, input.live)).items
  const item =
    list.find((entry) => entry.ownerID === input.owner.id && !FINISHED.has(entry.state)) ??
    list.find((entry) => refusal(entry, input.owner) === undefined)
  if (!item)
    return yield* new DodError({ message: "No item is ready: the rest are finished, taken or wait for others" })
  return { id: item.id, dod: yield* take({ ...input, id: item.id }) }
})

// Frees the item. With an owner, only that owner's item; without one, as the host, any.
// An item nobody started goes back to NOT_STARTED; one with checked steps stays in progress.
export const release = Effect.fn("Dod.release")(function* (input: { directory: string; id: string; owner?: Owner }) {
  return yield* change(input.directory, input.id, undefined, (item) => {
    if (input.owner && item.owner && !owns(item, input.owner)) return `${item.id} is taken by ${item.owner}`
    return free(item)
  })
})

// Frees every unfinished item a member held, for a member who left or was removed.
// Returns the IDs it freed.
export const releaseOwner = Effect.fn("Dod.releaseOwner")(function* (input: { directory: string; ownerID: string }) {
  const held = (yield* read(input.directory)).items.filter(
    (item) => item.ownerID === input.ownerID && !FINISHED.has(item.state),
  )
  yield* Effect.forEach(held, (item) =>
    change(input.directory, item.id, undefined, (current) => (current.ownerID === input.ownerID ? free(current) : {})),
  )
  return held.map((item) => item.id)
})

// The check a submitted item names, once the item is the owner's to submit.
export const command = Effect.fn("Dod.command")(function* (input: { directory: string; id: string; owner: Owner }) {
  const item = (yield* read(input.directory)).items.find((entry) => entry.id === input.id)
  if (!item) return yield* new DodError({ message: `The DoD has no item ${input.id}` })
  if (!owns(item, input.owner))
    return yield* new DodError({ message: `Take ${item.id} before submitting it; only its holder submits` })
  if (FINISHED.has(item.state)) return yield* new DodError({ message: `${item.id} is already ${finishedWord(item)}` })
  if (!item.verify)
    return yield* new DodError({
      message: `${item.id} names no verification.command to run; ask the host to accept it by hand`,
    })
  return item.verify
})

// Records the host's run of an item's check: passed makes it DONE with its evidence, failed
// makes it FAILED, and either way the holder stays on it.
export const accept = Effect.fn("Dod.accept")(function* (input: {
  directory: string
  id: string
  command: string
  exitCode: number
  summary: string
  by: string
}) {
  const passed = input.exitCode === 0
  return yield* change(
    input.directory,
    input.id,
    undefined,
    () => ({ task_state: passed ? "DONE" : "FAILED" }),
    (text) =>
      verify(text, input.id, {
        state: passed ? "PASSED" : "FAILED",
        evidence: {
          command: input.command,
          exit_code: String(input.exitCode),
          at: new Date().toISOString(),
          by: input.by,
          output_summary: input.summary,
        },
      }),
  )
})

// Why the owner may not take the item, or undefined when it may.
function refusal(item: Room.DodItem, owner: Owner) {
  if (FINISHED.has(item.state)) return `${item.id} is already ${finishedWord(item)}`
  if (item.owner && !owns(item, owner) && !item.stale) return `${item.id} is taken by ${item.owner}`
  if (item.waits && item.waits.length > 0) return `${item.id} waits for ${item.waits.join(", ")}`
}

// Items taken before owners carried an ID match by name.
function owns(item: Room.DodItem, owner: Owner) {
  return item.ownerID === undefined ? item.owner === owner.name : item.ownerID === owner.id
}

function claim(owner: Owner) {
  return { owner: owner.name, owner_id: owner.id, task_state: "IN_PROGRESS" }
}

function free(item: Room.DodItem) {
  return {
    owner: undefined,
    owner_id: undefined,
    task_state: item.state === "IN_PROGRESS" && item.done === 0 ? "NOT_STARTED" : item.state,
  }
}

function finishedWord(item: Room.DodItem) {
  return item.state === "DONE" ? "done" : "out of scope"
}

function load(directory: string) {
  const file = Bun.file(path.join(directory, FILE))
  return Effect.promise(async () => ((await file.exists()) ? await file.text() : undefined))
}

function items(text: string, live?: Live) {
  return Effect.try({
    try: () => Bun.YAML.parse(text),
    catch: (error) => new DodError({ message: `dod.yaml does not parse: ${String(error)}` }),
  }).pipe(
    Effect.map((data) => {
      const leaves = Option.getOrUndefined(
        Schema.decodeUnknownOption(Schema.Struct({ leaves: Schema.Array(Schema.Unknown) }))(data),
      )?.leaves
      const list = (leaves ?? []).flatMap((raw) => Option.toArray(decodeLeaf(raw)))
      const state = new Map(list.map((leaf) => [leaf.id, leaf.task_state?.trim() || "NOT_STARTED"]))
      return list.map((leaf): Room.DodItem => {
        const steps = leaf.steps ?? []
        const ownerID = leaf.owner?.trim() ? leaf.owner_id?.trim() || undefined : undefined
        // A dependency the file does not list cannot hold the item back.
        const waits = (leaf.depends_on ?? []).filter((id) => state.has(id) && !FINISHED.has(state.get(id)!))
        const verify = leaf.verification?.command?.trim()
        return {
          id: leaf.id,
          what: leaf.what?.trim() ?? "",
          stage: leaf.group?.trim() || "Ungrouped",
          state: state.get(leaf.id)!,
          done: steps.filter((step) => STEP.test(step)).length,
          total: steps.length,
          ...(leaf.owner?.trim() ? { owner: leaf.owner.trim() } : {}),
          ...(ownerID ? { ownerID } : {}),
          ...(ownerID && live && !live(ownerID) ? { stale: true } : {}),
          ...(waits.length > 0 ? { waits } : {}),
          ...(verify ? { verify } : {}),
        }
      })
    }),
  )
}

// Reads the file, lets decide() refuse with a message or name the field values to write,
// lets more() edit the result further, and replaces the file atomically only if nobody
// saved it in between.
function change(
  directory: string,
  id: string,
  live: Live | undefined,
  decide: (item: Room.DodItem) => string | Record<string, string | undefined>,
  more: (text: string) => string | undefined = (text) => text,
): Effect.Effect<Room.Dod, DodError> {
  const file = path.join(directory, FILE)
  const attempt = (left: number): Effect.Effect<Room.Dod, DodError> =>
    Effect.gen(function* () {
      const before = yield* load(directory)
      if (before === undefined) return yield* new DodError({ message: "This project has no DoD (.opencode/dod.yaml)" })
      const list = yield* items(before, live)
      const item = list.find((entry) => entry.id === id)
      if (!item) return yield* new DodError({ message: `The DoD has no item ${id}` })
      const fields = decide(item)
      if (typeof fields === "string") return yield* new DodError({ message: fields })
      const edited = edit(before, id, fields)
      const after = edited === undefined ? undefined : more(edited)
      if (after === undefined)
        return yield* new DodError({ message: `Could not find item ${id} in dod.yaml to edit it` })
      const saved = yield* Effect.promise(async () => {
        if ((await Bun.file(file).text()) !== before) return false
        const temporary = path.join(path.dirname(file), ".dod.yaml.room.tmp")
        await Bun.write(temporary, after)
        await chmod(temporary, (await stat(file)).mode & 0o777)
        await rename(temporary, file)
        return true
      })
      if (saved) return { items: yield* items(after) }
      if (left <= 1) return yield* new DodError({ message: "dod.yaml keeps changing; try again" })
      return yield* attempt(left - 1)
    })
  return attempt(ATTEMPTS)
}

// Sets or removes top-level scalar fields of one item, touching no other line. The item is
// the sequence entry holding `id: <id>`; its keys sit at one indent, and a field missing
// there is added after task_state, or at the end of the item.
export function edit(text: string, id: string, fields: Record<string, string | undefined>) {
  const lines = text.split("\n")
  const found = locate(lines, id)
  if (!found) return
  const { start, end, indent } = found
  // The item's last line with content, so a new field lands above the blank lines between items.
  const last = findLast(lines, end - 1, (line) => line.trim() !== "")
  const key = (name: string) => (line: string, index: number) =>
    (index === 0 ? line.slice(indent - 2).replace(/^- /, "") : line.slice(indent)).startsWith(`${name}:`) &&
    (index === 0 || leading(line) === indent)
  const block = lines.slice(start, end)
  const result = Object.entries(fields).reduce(
    (state, [name, value]) => {
      const index = state.block.findIndex(key(name))
      const entry = `${name}: ${name === "task_state" ? value : JSON.stringify(value)}`
      const line = `${" ".repeat(indent)}${entry}`
      // The entry's first line carries the sequence dash; removing it would merge the item into the one above.
      if (index === 0)
        return value === undefined
          ? state
          : { ...state, block: state.block.with(0, `${" ".repeat(indent - 2)}- ${entry}`) }
      if (index >= 0 && value === undefined) return { ...state, block: state.block.toSpliced(index, 1) }
      if (index >= 0) return { ...state, block: state.block.with(index, line) }
      if (value === undefined) return state
      // New fields follow task_state, or the field added just before them, in the order given.
      const anchor = state.added ?? state.block.findIndex(key("task_state"))
      const position = anchor >= 0 ? anchor + 1 : state.last + 1
      return { block: state.block.toSpliced(position, 0, line), last: state.last + 1, added: position }
    },
    { block, last: last - start, added: undefined as number | undefined },
  )
  return [...lines.slice(0, start), ...result.block, ...lines.slice(end)].join("\n")
}

// Sets the item's verification.state and replaces its verification.evidence with the given
// fields, leaving the rest of the verification block as written. Undefined when the item
// has no block-style verification to edit.
export function verify(text: string, id: string, input: { state: string; evidence: Record<string, string> }) {
  const lines = text.split("\n")
  const found = section(lines, id)
  if (!found) return
  const pad = " ".repeat(found.inner)
  const state = found.child("state")
  const stated =
    state < 0
      ? lines.toSpliced(found.at + 1, 0, `${pad}state: ${input.state}`)
      : lines.with(state, `${pad}state: ${input.state}`)
  // The state line may have moved what follows it, so the block is found again.
  const again = section(stated, id)!
  const evidence = again.child("evidence")
  const close = (from: number) => findLast(stated, from - 1, (line) => line.trim() !== "") + 1
  const end =
    evidence < 0
      ? close(again.end)
      : (() => {
          const after = stated
            .slice(evidence + 1, again.end)
            .findIndex((line) => line.trim() !== "" && leading(line) <= found.inner)
          return after < 0 ? close(again.end) : evidence + 1 + after
        })()
  const start = evidence < 0 ? end : evidence
  return stated
    .toSpliced(
      start,
      end - start,
      `${pad}evidence:`,
      ...Object.entries(input.evidence).map(
        ([name, value]) => `${pad}  ${name}: ${name === "exit_code" ? value : JSON.stringify(value)}`,
      ),
    )
    .join("\n")
}

// An item's block-style verification: its key line, the line after its block, and its children.
function section(lines: string[], id: string) {
  const found = locate(lines, id)
  if (!found) return
  const at = lines.findIndex(
    (line, index) =>
      index > found.start &&
      index < found.end &&
      leading(line) === found.indent &&
      /^\s*verification:\s*(#.*)?$/.test(line),
  )
  if (at < 0) return
  const inner = found.indent + 2
  const close = lines.slice(at + 1, found.end).findIndex((line) => line.trim() !== "" && leading(line) <= found.indent)
  const end = close < 0 ? found.end : at + 1 + close
  return {
    at,
    end,
    inner,
    child: (name: string) =>
      lines.findIndex(
        (line, index) =>
          index > at && index < end && leading(line) === inner && line.trimStart().startsWith(`${name}:`),
      ),
  }
}

// The item holding `id: <id>` in the leaves section: its first line, the line after it,
// and the indent of its keys.
function locate(lines: string[], id: string) {
  const escaped = id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
  const idLine = new RegExp(`^(\\s*)(- )?id:\\s*(["']?)${escaped}\\3\\s*(#.*)?$`)
  // Other sections, such as stages or a handover, may list the same id; only leaves are items.
  const leaves = lines.findIndex((line) => /^leaves:\s*(#.*)?$/.test(line))
  const at = leaves < 0 ? -1 : lines.findIndex((line, index) => index > leaves && idLine.test(line))
  if (at < 0) return
  const match = idLine.exec(lines[at])!
  const indent = match[1].length + (match[2] ? 2 : 0)
  const dash = new RegExp(`^\\s{${indent - 2}}- `)
  const start = match[2] ? at : findLast(lines, at, (line) => dash.test(line))
  if (start < 0) return
  const after = lines.slice(start + 1).findIndex((line) => line.trim() !== "" && leading(line) < indent)
  const end = after < 0 ? lines.length : start + 1 + after
  return { start, end, indent }
}

function leading(line: string) {
  return line.length - line.trimStart().length
}

function findLast(lines: string[], from: number, test: (line: string) => boolean) {
  const index = lines
    .slice(0, from + 1)
    .toReversed()
    .findIndex(test)
  return index < 0 ? -1 : from - index
}
