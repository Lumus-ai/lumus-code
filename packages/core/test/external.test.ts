import { describe, expect, test } from "bun:test"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { External } from "../src/external"

function project() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ext-"))
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), "out-"))
  return { dir, outside }
}

describe("External", () => {
  test("labels names once", () => {
    expect(External.label("Instinct")).toBe("Instinct (external)")
    expect(External.label("Instinct (external)")).toBe("Instinct (external)")
  })
  test("writes inside the project", () => {
    const { dir } = project()
    expect(External.write(dir, "src/a.txt", "hi")).toEqual({ path: path.join("src", "a.txt"), bytes: 2 })
    expect(fs.readFileSync(path.join(dir, "src/a.txt"), "utf8")).toBe("hi")
  })
  test("refuses absolute, parent, empty and .git paths", () => {
    const { dir } = project()
    expect(() => External.write(dir, "/etc/x", "a")).toThrow(External.EditError)
    expect(() => External.write(dir, "../x", "a")).toThrow(External.EditError)
    expect(() => External.write(dir, "", "a")).toThrow(External.EditError)
    expect(() => External.write(dir, ".git/hooks/pre-commit", "a")).toThrow(External.EditError)
  })
  test("refuses a symlinked folder or file leading outside", () => {
    const { dir, outside } = project()
    fs.symlinkSync(outside, path.join(dir, "link"))
    expect(() => External.write(dir, "link/x.txt", "a")).toThrow(External.EditError)
    fs.writeFileSync(path.join(outside, "t"), "orig")
    fs.symlinkSync(path.join(outside, "t"), path.join(dir, "f"))
    expect(() => External.write(dir, "f", "a")).toThrow(External.EditError)
    expect(fs.readFileSync(path.join(outside, "t"), "utf8")).toBe("orig")
  })
  test("caps content size", () => {
    const { dir } = project()
    expect(() => External.write(dir, "big", "x".repeat(External.EDIT_LIMIT + 1))).toThrow(External.EditError)
  })
})
