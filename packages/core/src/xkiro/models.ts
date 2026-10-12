export * as XKiroModels from "./models.js"

import { Option, Schema } from "effect"

export const providerID = "xkiro"
export const baseURL = "https://api.xkiro.com/v1"

const Tier = Schema.Literals(["free", "paid", "premium"])

const RemoteModel = Schema.Struct({
  id: Schema.String,
  display_name: Schema.optional(Schema.String),
  access_tier: Schema.optional(Schema.String),
  context_length: Schema.optional(Schema.Number),
  max_output_tokens: Schema.optional(Schema.Number),
  capabilities: Schema.optional(
    Schema.Struct({
      vision: Schema.optional(Schema.Boolean),
      tools: Schema.optional(Schema.Boolean),
      reasoning: Schema.optional(Schema.Boolean),
    }),
  ),
  pricing: Schema.optional(
    Schema.Struct({
      input: Schema.optional(Schema.Number),
      output: Schema.optional(Schema.Number),
      cache_read: Schema.optional(Schema.Number),
      cache_write: Schema.optional(Schema.Number),
    }),
  ),
  reasoning_efforts: Schema.optional(Schema.Struct({ levels: Schema.Array(Schema.String) })),
})

const Response = Schema.Struct({ data: Schema.Array(Schema.Unknown) })
const decodeResponse = Schema.decodeUnknownSync(Response)
const decodeModel = Schema.decodeUnknownOption(RemoteModel)

export type Tier = typeof Tier.Type

export type Spec = {
  readonly id: string
  readonly name: string
  readonly tier: Tier | "unknown"
  readonly tools: boolean
  readonly vision: boolean
  readonly reasoning: boolean
  readonly context: number
  readonly output: number
  readonly cost: { readonly input: number; readonly output: number; readonly cacheRead: number; readonly cacheWrite: number }
  readonly efforts: readonly string[]
}

const tierOrder = { free: 0, paid: 1, premium: 2, unknown: 3 } as const

function natural(value: number | undefined) {
  return value !== undefined && Number.isFinite(value) && value > 0 ? Math.trunc(value) : 0
}

function money(value: number | undefined) {
  return value !== undefined && Number.isFinite(value) && value >= 0 ? value : 0
}

/** "Name [free]" so the tier is visible in the model picker. */
export function label(name: string, tier: Spec["tier"]) {
  return tier === "unknown" ? name : `${name} [${tier}]`
}

/** Fetch the public model list. The endpoint does not need a key, but one is sent when present. */
export async function load(apiKey?: string, url = baseURL): Promise<readonly Spec[]> {
  const response = await fetch(`${url.replace(/\/+$/, "")}/models`, {
    headers: apiKey ? { Authorization: `Bearer ${apiKey}` } : {},
    signal: AbortSignal.timeout(5_000),
  })
  if (!response.ok) throw new Error(`Failed to fetch xKiro models: ${response.status}`)
  return parse(await response.json())
}

/** Map the raw payload. A malformed entry is skipped so it cannot hide the rest. Free models sort first. */
export function parse(payload: unknown): readonly Spec[] {
  const seen = new Set<string>()
  const specs = decodeResponse(payload).data.flatMap((raw): Spec[] => {
    const model = Option.getOrUndefined(decodeModel(raw))
    if (!model || model.id.length === 0 || seen.has(model.id)) return []
    seen.add(model.id)
    const tier = Option.getOrUndefined(Schema.decodeUnknownOption(Tier)(model.access_tier)) ?? "unknown"
    return [
      {
        id: model.id,
        name: label(model.display_name?.trim() || model.id, tier),
        tier,
        tools: model.capabilities?.tools ?? false,
        vision: model.capabilities?.vision ?? false,
        reasoning: model.capabilities?.reasoning ?? false,
        context: natural(model.context_length),
        output: natural(model.max_output_tokens),
        cost: {
          input: tier === "free" ? 0 : money(model.pricing?.input),
          output: tier === "free" ? 0 : money(model.pricing?.output),
          cacheRead: tier === "free" ? 0 : money(model.pricing?.cache_read),
          cacheWrite: tier === "free" ? 0 : money(model.pricing?.cache_write),
        },
        efforts: model.reasoning_efforts?.levels ?? [],
      },
    ]
  })
  const rank = (id: string) => (preferred.includes(id) ? preferred.indexOf(id) : preferred.length)
  return specs.toSorted(
    (a, b) => tierOrder[a.tier] - tierOrder[b.tier] || rank(a.id) - rank(b.id) || a.name.localeCompare(b.name),
  )
}

const Usage = Schema.Struct({
  plan: Schema.optional(Schema.NullOr(Schema.String)),
  free_tokens: Schema.optional(
    Schema.Struct({ used_today: Schema.Number, limit_per_day: Schema.Number, remaining: Schema.Number }),
  ),
  wallet: Schema.optional(Schema.NullOr(Schema.Struct({ balance_usd: Schema.String }))),
})
const decodeUsage = Schema.decodeUnknownOption(Usage)

export type UsageSummary = { readonly freeLeft?: number; readonly balanceUSD?: number; readonly plan?: string }

/** Pick the numbers worth showing from GET /v1/usage. */
export function parseUsage(payload: unknown): UsageSummary | undefined {
  const usage = Option.getOrUndefined(decodeUsage(payload))
  if (!usage) return undefined
  const balance = usage.wallet ? Number(usage.wallet.balance_usd) : undefined
  return {
    freeLeft: usage.free_tokens?.remaining,
    balanceUSD: balance !== undefined && Number.isFinite(balance) ? balance : undefined,
    plan: usage.plan ?? undefined,
  }
}

/** "xKiro - free left today: 299.6M tokens, wallet $0.00". Needs a key; returns the plain name without one. */
export function title(usage: UsageSummary | undefined) {
  if (!usage) return "xKiro"
  const parts: string[] = []
  if (usage.freeLeft !== undefined) parts.push(`free left today: ${compact(usage.freeLeft)} tokens`)
  if (usage.balanceUSD !== undefined) parts.push(`wallet $${usage.balanceUSD.toFixed(2)}`)
  return parts.length ? `xKiro - ${parts.join(", ")}` : "xKiro"
}

function compact(value: number) {
  if (value >= 1e9) return `${(value / 1e9).toFixed(1)}B`
  if (value >= 1e6) return `${(value / 1e6).toFixed(1)}M`
  if (value >= 1e3) return `${(value / 1e3).toFixed(0)}K`
  return String(Math.trunc(value))
}

/** Free to call and does not count against limits (xKiro docs). */
export async function loadUsage(apiKey: string, url = baseURL) {
  const response = await fetch(`${url.replace(/\/+$/, "")}/usage`, {
    headers: { Authorization: `Bearer ${apiKey}` },
    signal: AbortSignal.timeout(5_000),
  })
  if (!response.ok) return undefined
  return parseUsage(await response.json())
}

// Free coding-capable models the picker should show first.
const preferred = ["qwen/qwen3-coder-plus:free", "qwen/qwen3.7-plus:free", "deepseek/deepseek-v4-flash"]
