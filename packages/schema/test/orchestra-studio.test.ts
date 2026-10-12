import { expect, test } from "bun:test"
import { roles, templates, role } from "../src/orchestra"

test("every template member points at a built-in role", () => {
  const ids = new Set(roles.map((item) => role(item.id)))
  for (const template of templates) for (const member of template.members) expect(ids.has(member.agent)).toBe(true)
})

test("the Studio template covers the default team", () => {
  const studio = templates.find((item) => item.id === "studio")
  expect(studio?.members.map((item) => item.title)).toEqual([
    "Architect", "Developer 1", "Developer 2", "Tester", "Reviewer", "Security", "Docs",
  ])
})

test("planning and audit roles cannot edit files", () => {
  for (const id of ["architect", "reviewer", "security", "debugger", "researcher"])
    expect(roles.find((item) => item.id === id)?.readOnly).toBe(true)
  expect(roles.find((item) => item.id === "developer")?.readOnly).toBe(false)
})
