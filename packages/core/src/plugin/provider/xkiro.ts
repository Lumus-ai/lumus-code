import { Effect, Semaphore, Stream } from "effect"
import { define } from "@opencode/plugin/effect/plugin"
import { Money } from "@opencode/schema/money"
import { Bus } from "../../bus.js"
import { Credential } from "../../credential.js"
import { Integration } from "../../integration.js"
import { Model } from "../../model.js"
import { XKiroModels } from "../../xkiro/models.js"
import type { PluginInternal } from "../internal.js"

const integrationID = Integration.ID.make(XKiroModels.providerID)

/** Built-in xKiro connector: paste a key in Connect, every model from GET /v1/models shows up with its tier. */
export const XKiroPlugin = define({
  id: "lumus.provider.xkiro",
  effect: Effect.fn(function* (ctx) {
    const bus = yield* Bus.Service
    const loading = Semaphore.makeUnsafe(1)
    const loaded: { models: readonly XKiroModels.Spec[] } = { models: [] }

    yield* ctx.integration.transform((editor) => {
      editor.update(integrationID, (integration) => (integration.name = "xKiro"))
      editor.method.update({ integrationID, method: { type: "key", label: "Paste xKiro API key" } })
      editor.method.update({ integrationID, method: { type: "env", names: ["XKIRO_API_KEY"] } })
    })

    yield* ctx.provider.transform((providers) => {
      providers.update(XKiroModels.providerID, (provider) => {
        provider.name = "xKiro"
        provider.package = "@opencode/ai/providers/openai-compatible"
        provider.settings = { baseURL: XKiroModels.baseURL, provider: XKiroModels.providerID }
      })
      for (const spec of loaded.models) {
        providers.models.update(XKiroModels.providerID, spec.id, (model) => {
          model.modelID = Model.ID.make(spec.id)
          model.name = spec.name
          model.capabilities = {
            tools: spec.tools,
            input: spec.vision ? ["text", "image"] : ["text"],
            output: ["text"],
          }
          if (spec.context > 0) model.limit.context = spec.context
          if (spec.output > 0) model.limit.output = spec.output
          model.cost = [
            {
              input: Money.USDPerMillionTokens.make(spec.cost.input),
              output: Money.USDPerMillionTokens.make(spec.cost.output),
              cache: {
                read: Money.USDPerMillionTokens.make(spec.cost.cacheRead),
                write: Money.USDPerMillionTokens.make(spec.cost.cacheWrite),
              },
            },
          ]
          model.variants = spec.efforts.map((effort) => ({
            id: Model.VariantID.make(effort),
            settings: { reasoningEffort: effort },
          }))
        })
      }
    })

    const load = Effect.fn("XKiroPlugin.load")(function* () {
      const connection = yield* ctx.integration.connection.active(XKiroModels.providerID)
      const credential = connection
        ? yield* ctx.integration.connection.resolve(connection).pipe(Effect.orElseSucceed(() => undefined))
        : undefined
      const apiKey = credential?.type === "key" ? credential.key : process.env.XKIRO_API_KEY
      const models = yield* Effect.tryPromise({
        try: () => XKiroModels.load(apiKey || undefined),
        catch: (cause) => cause,
      }).pipe(
        Effect.catch((cause) => Effect.logWarning("failed to sync xKiro models", { cause }).pipe(Effect.as(undefined))),
      )
      if (models) loaded.models = models
    })
    const refresh = () => loading.withPermit(load().pipe(Effect.andThen(ctx.provider.reload())))
    yield* bus.subscribe(Credential.Event.Switched).pipe(
      Stream.filter((event) => event.data.integrationID === integrationID),
      Stream.runForEach(refresh),
      Effect.forkScoped({ startImmediately: true }),
    )
    yield* refresh().pipe(Effect.forkScoped)
  }),
} satisfies PluginInternal.InternalPlugin)
