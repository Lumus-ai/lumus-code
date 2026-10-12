import { expect, test } from "bun:test"
import { createAppFixture } from "./fixture/app"
import { json } from "./fixture/tui-client"
import { tmpdir } from "./fixture/fixture"

// The registry panel is opened from the bar, whose handler closes the open devtools panel first and
// hands focus back a tick later. The report from Windows is that the panel then takes no clicks and
// closes itself after about five seconds. Three checks pin that here on Linux: it opens, a click
// inside it is consumed by the panel rather than by the backdrop that dismisses it, and it is still
// there well past five seconds.
test("the registry panel opened from the bar stays open and takes clicks", async () => {
  await using state = await tmpdir()
  await using setup = await createAppFixture({
    width: 150,
    height: 34,
    state: state.path,
    config: { animations: false, debug: { devtools: true } },
    fetch: (url) => {
      if (url.pathname === "/api/orchestra/project")
        return json([{ id: "prj_demo", name: "Demo", directory: "/tmp/demo", created: 0 }])
    },
  })
  await setup.ready
  await setup.waitForFrame((frame) => frame.includes("Registry"))
  const find = (text: string) => {
    const lines = setup.captureCharFrame().split("\n")
    const y = lines.findIndex((line) => line.includes(text))
    if (y === -1) throw new Error(`no "${text}" on screen:\n${lines.join("\n")}`)
    return { x: lines[y]!.indexOf(text), y }
  }

  await setup.mockMouse.click(find(" Registry ").x + 2, find(" Registry ").y)
  await setup.waitForFrame((frame) => frame.includes("Search"))
  expect(setup.captureCharFrame()).toContain("Search")

  // A click on the panel's own row must be eaten by it: the backdrop dismisses on the same event.
  await setup.mockMouse.click(find("Search").x + 2, find("Search").y)
  await setup.renderOnce()
  expect(setup.captureCharFrame()).toContain("Search")

  // Longer than the five seconds the report gives before the panel vanishes on its own.
  await Bun.sleep(6000)
  await setup.renderOnce()
  expect(setup.captureCharFrame()).toContain("Search")
}, 20_000)
