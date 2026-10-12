import { describe, expect } from "bun:test"
import { Effect } from "effect"
import { Integration } from "@opencode/core/integration"
import { Plugin } from "@opencode/core/plugin"
import { PluginHost } from "@opencode/core/plugin/host"
import { XKiroPlugin } from "@opencode/core/plugin/provider/xkiro"
import { Provider } from "@opencode/core/provider"
import { testEffect } from "../lib/effect"
import { PluginTestLayer } from "./fixture"

const it = testEffect(PluginTestLayer)

describe("XKiroPlugin", () => {
  it.effect("offers key and env connect methods and defines the xKiro provider", () =>
    Effect.gen(function* () {
      const plugin = yield* Plugin.Service
      const host = yield* PluginHost.make(plugin)
      yield* XKiroPlugin.effect(host)
      const integration = yield* (yield* Integration.Service).get(Integration.ID.make("xkiro"))
      expect(integration?.name).toBe("xKiro")
      expect(integration?.methods.map((method) => method.type).toSorted()).toEqual(["env", "key"])
      const provider = yield* (yield* Provider.Service).get(Provider.ID.make("xkiro"))
      expect(provider?.name).toBe("xKiro")
      expect(provider?.package).toBe("@opencode/ai/providers/openai-compatible")
      expect(provider?.settings?.baseURL).toBe("https://api.xkiro.com/v1")
    }),
  )
})
