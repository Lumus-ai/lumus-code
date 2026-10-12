import { expect, test } from "bun:test"
import { addressedToExternal } from "../src/util/addressed"

test("a line starting with @instinct is addressed to the external agent", () => {
  expect(addressedToExternal("@instinct run the tests")).toBe("run the tests")
  expect(addressedToExternal("  @Instinct: pwd")).toBe("pwd")
  expect(addressedToExternal("@INSTINCT, list files\nand more")).toBe("list files\nand more")
  expect(addressedToExternal("@instinct")).toBe("")
})

test("anything else goes to the host model as usual", () => {
  expect(addressedToExternal("hello @instinct")).toBeUndefined()
  expect(addressedToExternal("@instinctive thing")).toBeUndefined()
  expect(addressedToExternal("fix the bug")).toBeUndefined()
})
