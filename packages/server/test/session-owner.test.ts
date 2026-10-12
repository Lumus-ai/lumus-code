import { expect } from "bun:test"
import { SessionOwnerMetadataKey } from "@opencode/schema/orchestra"
import { Effect } from "effect"
import { tmpdir } from "../../core/test/fixture/tmpdir"
import { it } from "../../core/test/lib/effect"
import { ServerFetch } from "../src/fetch"

it.live("sessions created from projects sharing a directory keep their own owner", () =>
  Effect.gen(function* () {
    const tmp = yield* Effect.acquireDisposable(Effect.promise(() => tmpdir("opencode-project-owner-")))
    const handler = yield* ServerFetch.make({
      app: { version: "test" },
      database: { path: ":memory:" },
      fs: { filewatcher: false },
      models: { fetch: false },
      config: { directory: tmp.path, project: false, content: "{}" },
    })
    const call = (route: string, method = "GET", body?: unknown) =>
      Effect.promise(async () => {
        const response = await handler(
          new Request(`http://opencode.local${route}`, {
            method,
            headers: { "content-type": "application/json" },
            body: body === undefined ? undefined : JSON.stringify(body),
          }),
        )
        const text = await response.text()
        return { status: response.status, body: text ? JSON.parse(text) : undefined }
      })

    const firstResponse = yield* call("/api/orchestra/project", "POST", {
      name: "First",
      directory: tmp.path,
    })
    const secondResponse = yield* call("/api/orchestra/project", "POST", {
      name: "Second",
      directory: tmp.path,
    })
    expect(firstResponse.status).toBe(200)
    expect(secondResponse.status).toBe(200)
    const first = firstResponse.body
    const second = secondResponse.body

    const firstResponseSession = yield* call("/api/session", "POST", {
      title: "First session",
      location: { directory: tmp.path },
      metadata: { [SessionOwnerMetadataKey]: { projectID: first.id } },
    })
    const secondResponseSession = yield* call("/api/session", "POST", {
      title: "Second session",
      location: { directory: tmp.path },
      metadata: { [SessionOwnerMetadataKey]: { projectID: second.id } },
    })
    expect(firstResponseSession.status).toBe(200)
    expect(secondResponseSession.status).toBe(200)
    const firstSession = firstResponseSession.body.data
    const secondSession = secondResponseSession.body.data

    const firstList = (yield* call(`/api/orchestra/project/${first.id}/session`)).body.data
    const secondList = (yield* call(`/api/orchestra/project/${second.id}/session`)).body.data
    expect(firstList.map((session: { id: string }) => session.id)).toContain(firstSession.id)
    expect(firstList.map((session: { id: string }) => session.id)).not.toContain(secondSession.id)
    expect(secondList.map((session: { id: string }) => session.id)).toContain(secondSession.id)
    expect(secondList.map((session: { id: string }) => session.id)).not.toContain(firstSession.id)
  }).pipe(Effect.scoped),
)
