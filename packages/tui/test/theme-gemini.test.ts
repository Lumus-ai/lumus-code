import { describe, expect, test } from "bun:test"
import { allThemes, parseTheme } from "../src/theme"

describe("gemini theme", () => {
  test("is built in and parses for dark and light", () => {
    const source = allThemes().gemini
    expect(source).toBeDefined()
    const document = parseTheme(source!, "gemini")
    expect(document).toBeDefined()
  })
  test("keeps the other built-in themes", () => {
    const names = Object.keys(allThemes())
    for (const name of ["opencode", "vesper", "dracula", "tokyonight"]) expect(names).toContain(name)
  })
})
