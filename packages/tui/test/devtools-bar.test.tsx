import { expect, test } from "bun:test"
import { createAppFixture } from "./fixture/app"
import { directory, json } from "./fixture/tui-client"
import { tmpdir } from "./fixture/fixture"

// Regression: with a project present the "+ Project" item is hidden, and once a panel had
// closed, the backdrop that closes panels sorted above the open panel and took its clicks.
test("devtools panel actions take clicks after a panel was closed while a project exists", async () => {
  await using state = await tmpdir()
  await using setup = await createAppFixture({
    width: 150,
    height: 34,
    state: state.path,
    config: { animations: false, debug: { devtools: true } },
    fetch: (url) => {
      if (url.pathname === "/api/orchestra/project")
        return json([{ id: "prj_demo", name: "Demo", directory, created: 0 }])
    },
  })
  await setup.ready
  await setup.waitForFrame((frame) => frame.includes("Tools") && frame.includes("Demo") && !frame.includes("+ Project"))
  const find = (text: string) => {
    const lines = setup.captureCharFrame().split("\n")
    const y = lines.findIndex((line) => line.includes(text))
    return { x: lines[y]!.indexOf(text), y }
  }
  // Opening and closing panels re-sorts the bar; this order put the backdrop above the panel.
  for (const [item, title] of [[" UI ", "Loop"], [" Theme ", "Mode"]] as const) {
    const at = find(item)
    await setup.mockMouse.click(at.x + 2, at.y)
    await setup.waitForFrame((frame) => frame.includes(title))
    setup.mockInput.pressEscape()
    await setup.waitForFrame((frame) => !frame.includes(title))
  }

  const tools = find(" Tools ")
  await setup.mockMouse.click(tools.x + 2, tools.y)
  await setup.waitForFrame((frame) => frame.includes("[ ] Time to first draw"))
  const item = find("[ ] Time to first draw")
  await setup.mockMouse.click(item.x + 4, item.y)
  await setup.waitForFrame((frame) => frame.includes("[x] Time to first draw"))
  expect(setup.captureCharFrame()).toContain("Write debug snapshot")
})
