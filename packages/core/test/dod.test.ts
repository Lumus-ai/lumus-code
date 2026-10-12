import { describe, expect, test } from "bun:test"
import { mkdir } from "node:fs/promises"
import path from "node:path"
import { Effect } from "effect"
import { Dod } from "@opencode/core/dod"
import { tmpdir } from "./fixture/tmpdir"

const FILE = `# The plan; comments stay.
task_id: T-1
stages:
  - name: "Stage 1"
    id: L-002
leaves:
  - id: L-001
    what: "Read the protocol"
    group: "Stage 1"
    task_state: DONE
    steps:
      - "[x] read it"

  - id: L-002
    what: "Add the check"   # keep this comment
    group: "Stage 1"
    task_state: NOT_STARTED
    steps:
      - "[x] find the spot"
      - "[ ] write it"
    depends_on: [L-001]

  - id: L-003
    what: "Document it"
`

async function project(text = FILE) {
  const dir = await tmpdir()
  await mkdir(path.join(dir.path, ".opencode"), { recursive: true })
  await Bun.write(path.join(dir.path, Dod.FILE), text)
  return dir
}

const alex = { id: "g-alex", name: "Alex" }
const bo = { id: "g-bo", name: "Bo" }

const run = <A, E>(effect: Effect.Effect<A, E>) => Effect.runPromise(effect)
const fails = <A>(effect: Effect.Effect<A, Dod.DodError>) =>
  Effect.runPromise(Effect.flip(effect)).then((error) => error.message)

describe("Dod", () => {
  test("reads the items in file order with their step counts", async () => {
    await using dir = await project()
    const dod = await run(Dod.read(dir.path))
    expect(dod.items).toEqual([
      { id: "L-001", what: "Read the protocol", stage: "Stage 1", state: "DONE", done: 1, total: 1 },
      { id: "L-002", what: "Add the check", stage: "Stage 1", state: "NOT_STARTED", done: 1, total: 2 },
      { id: "L-003", what: "Document it", stage: "Ungrouped", state: "NOT_STARTED", done: 0, total: 0 },
    ])
  })

  test("a project without a DoD has no items", async () => {
    await using dir = await tmpdir()
    expect((await run(Dod.read(dir.path))).items).toEqual([])
  })

  test("taking an item edits only its owner and state lines", async () => {
    await using dir = await project()
    const dod = await run(Dod.take({ directory: dir.path, id: "L-002", owner: alex }))
    expect(dod.items[1]).toMatchObject({ id: "L-002", state: "IN_PROGRESS", owner: "Alex", ownerID: "g-alex" })
    const text = await Bun.file(path.join(dir.path, Dod.FILE)).text()
    expect(text).toBe(
      FILE.replace(
        '    task_state: NOT_STARTED\n    steps:\n      - "[x] find',
        '    task_state: IN_PROGRESS\n    owner: "Alex"\n    owner_id: "g-alex"\n    steps:\n      - "[x] find',
      ),
    )
  })

  test("an item without task_state gets the owner at its end, before the blank line", async () => {
    await using dir = await project(FILE + '\n  - id: L-004\n    what: "Last"\n\n')
    await run(Dod.take({ directory: dir.path, id: "L-003", owner: bo }))
    const text = await Bun.file(path.join(dir.path, Dod.FILE)).text()
    expect(text).toContain(
      '  - id: L-003\n    what: "Document it"\n    owner: "Bo"\n    owner_id: "g-bo"\n    task_state: IN_PROGRESS\n\n  - id: L-004',
    )
  })

  test("someone else's or a finished item cannot be taken", async () => {
    await using dir = await project()
    await run(Dod.take({ directory: dir.path, id: "L-002", owner: alex }))
    expect(await fails(Dod.take({ directory: dir.path, id: "L-002", owner: bo }))).toBe("L-002 is taken by Alex")
    expect(await fails(Dod.take({ directory: dir.path, id: "L-001", owner: bo }))).toBe("L-001 is already done")
    expect(await fails(Dod.take({ directory: dir.path, id: "L-404", owner: bo }))).toBe("The DoD has no item L-404")
  })

  test("releasing gives the item back; only its owner or the host may", async () => {
    await using dir = await project()
    await run(Dod.take({ directory: dir.path, id: "L-003", owner: alex }))
    expect(await fails(Dod.release({ directory: dir.path, id: "L-003", owner: bo }))).toBe("L-003 is taken by Alex")
    const dod = await run(Dod.release({ directory: dir.path, id: "L-003", owner: alex }))
    expect(dod.items[2]).toEqual({
      id: "L-003",
      what: "Document it",
      stage: "Ungrouped",
      state: "NOT_STARTED",
      done: 0,
      total: 0,
    })
    // Started work stays in progress for whoever picks it up next.
    await run(Dod.take({ directory: dir.path, id: "L-002", owner: alex }))
    const host = await run(Dod.release({ directory: dir.path, id: "L-002" }))
    expect(host.items[1]).toEqual({
      id: "L-002",
      what: "Add the check",
      stage: "Stage 1",
      state: "IN_PROGRESS",
      done: 1,
      total: 2,
    })
    expect(await Bun.file(path.join(dir.path, Dod.FILE)).text()).not.toContain("owner:")
  })

  test("a namesake cannot give back or take over another member's item", async () => {
    await using dir = await project()
    await run(Dod.take({ directory: dir.path, id: "L-003", owner: alex }))
    const namesake = { id: "g-alex-2", name: "Alex" }
    expect(await fails(Dod.release({ directory: dir.path, id: "L-003", owner: namesake }))).toBe(
      "L-003 is taken by Alex",
    )
    expect(await fails(Dod.take({ directory: dir.path, id: "L-003", owner: namesake }))).toBe("L-003 is taken by Alex")
  })

  test("an item whose holder's lease ran out reads stale and passes to a new taker", async () => {
    await using dir = await project()
    await run(Dod.take({ directory: dir.path, id: "L-003", owner: alex }))
    const gone = (id: string) => id !== alex.id
    expect((await run(Dod.read(dir.path, gone))).items[2]).toMatchObject({ owner: "Alex", stale: true })
    const dod = await run(Dod.take({ directory: dir.path, id: "L-003", owner: bo, live: gone }))
    expect(dod.items[2]).toMatchObject({ owner: "Bo", ownerID: "g-bo", state: "IN_PROGRESS" })
  })

  test("an item waits for its unfinished dependencies, and next skips it", async () => {
    await using dir = await project(
      FILE.replace("    task_state: DONE\n", "    task_state: NOT_STARTED\n") +
        '\n  - id: L-004\n    what: "Ship"\n    depends_on: [L-404]\n',
    )
    expect((await run(Dod.read(dir.path))).items[1].waits).toEqual(["L-001"])
    expect(await fails(Dod.take({ directory: dir.path, id: "L-002", owner: bo }))).toBe("L-002 waits for L-001")
    // A dependency the file does not list holds nothing back.
    expect((await run(Dod.read(dir.path))).items[3].waits).toBeUndefined()
    await run(Dod.take({ directory: dir.path, id: "L-001", owner: alex }))
    expect((await run(Dod.next({ directory: dir.path, owner: bo }))).id).toBe("L-003")
    // The owner's own unfinished item comes back before a new one.
    expect((await run(Dod.next({ directory: dir.path, owner: alex }))).id).toBe("L-001")
  })

  test("releasing a member's items frees only theirs", async () => {
    await using dir = await project()
    await run(Dod.take({ directory: dir.path, id: "L-002", owner: alex }))
    await run(Dod.take({ directory: dir.path, id: "L-003", owner: bo }))
    expect(await run(Dod.releaseOwner({ directory: dir.path, ownerID: alex.id }))).toEqual(["L-002"])
    const items = (await run(Dod.read(dir.path))).items
    expect(items[1].owner).toBeUndefined()
    expect(items[2].owner).toBe("Bo")
  })

  test("the host's check records evidence and decides DONE or FAILED", async () => {
    await using dir = await project(
      FILE.replace(
        '      - "[ ] write it"\n',
        '      - "[ ] write it"\n    verification:\n      state: NOT_CHECKED\n      command: "bun test x"   # the oracle\n      evidence:\n        command: "old"\n        exit_code: 1\n',
      ),
    )
    expect(await fails(Dod.command({ directory: dir.path, id: "L-002", owner: alex }))).toBe(
      "Take L-002 before submitting it; only its holder submits",
    )
    await run(Dod.take({ directory: dir.path, id: "L-002", owner: alex }))
    expect(await run(Dod.command({ directory: dir.path, id: "L-002", owner: alex }))).toBe("bun test x")
    expect(await fails(Dod.command({ directory: dir.path, id: "L-003", owner: alex }))).toBe(
      "Take L-003 before submitting it; only its holder submits",
    )
    const input = { directory: dir.path, id: "L-002", command: "bun test x", summary: "1 fail", by: "Alex" }
    const failed = await run(Dod.accept({ ...input, exitCode: 1 }))
    expect(failed.items[1].state).toBe("FAILED")
    const passed = await run(Dod.accept({ ...input, exitCode: 0, summary: "3 pass" }))
    expect(passed.items[1]).toMatchObject({ state: "DONE", owner: "Alex" })
    const text = await Bun.file(path.join(dir.path, Dod.FILE)).text()
    const block = text.slice(text.indexOf("    verification:"), text.indexOf("    depends_on: [L-001]"))
    expect(block).toMatch(
      /^    verification:\n      state: PASSED\n      command: "bun test x"   # the oracle\n      evidence:\n        command: "bun test x"\n        exit_code: 0\n        at: "[^"]+"\n        by: "Alex"\n        output_summary: "3 pass"\n$/,
    )
    expect(text).not.toContain("1 fail")
  })
})
