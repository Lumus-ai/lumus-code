export * as NoteTool from "./note.js"

import type { Context } from "@opencode/plugin/effect/plugin"
import { ToolFailure } from "@opencode/ai"
import { Note } from "@opencode/schema/note"
import { Session } from "@opencode/schema/session"
import { optional } from "@opencode/schema/schema"
import { Effect, Result, Schema } from "effect"
import { NoteStore } from "../../note.js"
import { Permission } from "../../permission.js"

export const name = "note"

/** Notes folder relative to the working directory, and what the permission ask names. */
const FOLDER = ".opencode/notes"

export const Input = Schema.Struct({
  action: Schema.Literals(["list", "read", "create", "edit", "update"]).annotate({
    description: "List notes, read one, create one, rewrite or append its body, or change its metadata",
  }),
  name: optional(
    Schema.String.annotate({
      description: "File name of the note without .md: 2 to 60 lowercase latin letters, digits and hyphens",
    }),
  ),
  title: optional(Schema.String.annotate({ description: "Human readable title, any language" })),
  body: optional(Schema.String.annotate({ description: "Markdown body of the note" })),
  mode: optional(
    Schema.Literals(["replace", "append"]).annotate({ description: "How edit writes the body, replace by default" }),
  ),
  status: optional(Note.Status.annotate({ description: "Note status" })),
  tags: optional(Schema.Array(Note.Tag).annotate({ description: "Lowercase latin tags" })),
  length: optional(
    Note.Length.annotate({
      description: "How much to write into the note from its own chat: brief, balanced or detailed",
    }),
  ),
  // Models often send the stamp as a JSON string ("1791730573129"); refusing it made every
  // write fail validation and the model retry the same call forever, so both forms are read.
  expectedMtime: optional(
    Schema.Union([Schema.Number, Schema.String]).annotate({
      description: "File mtime in epoch ms from an earlier read, as a number; required by edit and update",
    }),
  ),
})
export type Input = typeof Input.Type

export const Output = Schema.Struct({
  output: Schema.String,
  name: optional(Schema.String),
  updated: optional(Schema.Number),
})
export type Output = typeof Output.Type

export const description = [
  "Read and write the project notes: markdown files in .opencode/notes/, one file per note, and the file name is the note id.",
  "",
  "Actions:",
  "  list — every note as `- name [status] title`, then its tags and its `updated` file mtime, newest first; an `updated` value from here can be passed straight back as expectedMtime.",
  "  read — one note with its full body; the `updated` number in its output is the file mtime to pass back as expectedMtime.",
  "  create — a new note from title and body; pass a short latin name, or omit it to derive one from the title.",
  "  edit — rewrite or append the body; pass name, body, optional mode and expectedMtime from a read.",
  "  update — change title, status, tags or length without touching the body; pass name and expectedMtime.",
  "",
  "Every note has its own dedicated chat that the user opens from the Notes panel; a note cannot be bound to another chat.",
  "",
  "Writing rules: edit and update refuse to write when expectedMtime does not match the file, which is what happens when the user or another agent changed the note. On that refusal read the note again and re-apply the change, never invent a fresh mtime.",
  "",
  "When to write a note: a plan, a decision, a spec or a summary that will still matter after this chat ends. Do not write a note that only restates the chat, and do not create one for a trivial answer.",
].join("\n")

export const Plugin = {
  id: "opencode.tool.note",
  effect: Effect.fn("NoteTool.Plugin")(function* (ctx: Context) {
    const notes = yield* NoteStore.Service
    const permission = yield* Permission.Service

    /**
     * The note this chat is the dedicated chat of, read from the session's own metadata:
     * that binding is what the scope rule below is checked against. It lives in session
     * metadata and only this session knows it, which is why it is read per call. A chat
     * with no binding is an ordinary chat and stays unrestricted.
     */
    const noteBoundTo = Effect.fn("NoteTool.noteBoundTo")(function* (sessionID: Session.ID) {
      // An empty id is a call outside any session, so there is no metadata to read a
      // binding from and nothing to look up.
      if (sessionID === "") return undefined
      const session = yield* ctx.session.get({ sessionID }).pipe(Effect.orElseSucceed(() => undefined))
      return session === undefined ? undefined : Note.chatOf(session.metadata)
    })

    const listing = Effect.fn("NoteTool.listing")(function* () {
      const found = yield* notes.list()
      if (found.length === 0) return `No notes yet in ${FOLDER}.`
      return found.map(row).join("\n")
    })

    const reading = Effect.fn("NoteTool.reading")(function* (input: Input) {
      const note = yield* notes.get(yield* needName(input))
      return reported(note)
    })

    const run = Effect.fn("NoteTool.run")(function* (input: Input) {
      switch (input.action) {
        case "list":
          return { output: yield* listing(), name: undefined, updated: undefined }
        case "read":
          return yield* reading(input)
        case "create":
          return reported(
            yield* notes.create({
              name: input.name,
              title: input.title ?? "",
              body: input.body,
              status: input.status,
              tags: input.tags,
              length: input.length,
            }),
          )
        case "edit":
          return reported(
            yield* notes.edit({
              name: yield* needName(input),
              body: input.body ?? "",
              mode: input.mode,
              expectedMtime: yield* needStamp(input),
            }),
          )
        case "update":
          return reported(
            yield* notes.update({
              name: yield* needName(input),
              title: input.title,
              status: input.status,
              tags: input.tags,
              length: input.length,
              expectedMtime: yield* needStamp(input),
            }),
          )
      }
    })

    yield* ctx.tool
      .transform((editor) =>
        editor.add({
          name,
          options: { codemode: false },
          description,
          input: Input,
          output: Output,
          execute: (input, context) =>
            Effect.gen(function* () {
              const bound = yield* noteBoundTo(context.sessionID)
              if (bound !== undefined && refusedInsideNote(input, bound)) yield* refuse(bound, input.action)
              if (writes(input.action)) {
                yield* permission.assert({
                  action: name,
                  resources: [FOLDER],
                  save: [FOLDER],
                  sessionID: context.sessionID,
                  agent: context.agent,
                  source: { type: "tool", messageID: context.messageID, id: context.id },
                })
              }
              const result = yield* run(input)
              return { output: result, content: result.output, metadata: { name: result.name } }
              // A blocked permission and a stale mtime both belong in the model output as text,
              // not as a crash: the model can react to either by reading and trying again.
            }).pipe(Effect.mapError(failed)),
        }),
      )
      .pipe(Effect.orDie)
  }),
}

function writes(action: Input["action"]) {
  return action !== "list" && action !== "read"
}

/**
 * A note's dedicated chat works on that note and nothing else: it never creates
 * another one, never browses the folder, and never reads or writes a second note.
 *
 * The name is compared through `Note.name`, the same folding the store applies before
 * it resolves a path, so a model that asks for `Room-Plan` is talking about the bound
 * note `room-plan` and is not refused for spelling. A name that is not a slug at all
 * cannot be the bound note either, so it is refused too — the store would refuse it
 * moments later with a worse message.
 */
function refusedInsideNote(input: Input, bound: Note.Slug) {
  if (input.action === "create" || input.action === "list") return true
  if (input.name === undefined) return false
  return Result.match(Note.name(input.name), {
    onSuccess: (slug) => slug !== bound,
    onFailure: () => true,
  })
}

/**
 * Refusal as model output rather than as a crash: the model reads it and answers in
 * chat instead of retrying, and nothing on disk is touched.
 */
const refuse = (note: string, action: Input["action"]) =>
  Effect.fail(
    new ToolFailure({
      message: `This chat is the dedicated chat of the note "${note}". It works only on that note: ${action} is refused, and no other file or note will be changed here. Reply in chat instead.`,
    }),
  )

const needName = (input: Input) =>
  input.name === undefined
    ? Effect.fail(new ToolFailure({ message: `note ${input.action} needs a name` }))
    : Effect.succeed(input.name)

/** The store refuses a write it did not read, so the caller has to hand back what it saw. */
const needStamp = (input: Input) => {
  if (input.expectedMtime === undefined)
    return Effect.fail(new ToolFailure({ message: `note ${input.action} needs expectedMtime from an earlier read` }))
  const stamp = Number(input.expectedMtime)
  if (!Number.isFinite(stamp))
    return Effect.fail(
      new ToolFailure({
        message: `expectedMtime must be the number from the "updated" value of a read, got ${JSON.stringify(input.expectedMtime)}`,
      }),
    )
  return Effect.succeed(stamp)
}

/**
 * The reported `updated` is the file mtime, because that is the value the store
 * checks. It is written into the text too: `content` is the only part of a tool
 * result the model ever sees, so a stamp left only in the structured output would
 * never come back. The frontmatter timestamp is not printed — it is `Date.now()`
 * taken before the write, while the file mtime keeps sub-millisecond precision
 * that `Date.now()` cannot, so the two differ by a millisecond often enough to
 * make every later write fail.
 */
function reported(note: NoteStore.Info) {
  const meta = [
    note.frontmatter.status,
    note.frontmatter.tags.join(", "),
    note.frontmatter.length ? `length ${note.frontmatter.length}` : "",
    `updated ${note.mtime}`,
  ]
    .filter((part) => part !== "")
    .join(" · ")
  return {
    output: [`# ${note.frontmatter.title || note.name}`, meta, "", note.body].join("\n"),
    name: note.name,
    updated: note.mtime,
  }
}

/**
 * A listing row carries the same `updated` value the `read` action reports, because
 * that is the value the store checks: it is the note's own mtime, already floored the
 * way the store floors it, so a stamp copied from a listing is a stamp that writes.
 * The frontmatter timestamp would be wrong here exactly as it was in the read text.
 */
function row(note: NoteStore.Info) {
  const meta = [note.frontmatter.tags.length ? `tags ${note.frontmatter.tags.join(", ")}` : "", `updated ${note.mtime}`]
    .filter((part) => part !== "")
    .join(" · ")
  return `- ${note.name} [${note.frontmatter.status}] ${note.frontmatter.title || "(untitled)"} · ${meta}`
}

function failed(error: unknown) {
  if (error instanceof ToolFailure) return error
  const message = error instanceof Error ? error.message : String(error)
  return new ToolFailure({ message: `Note action failed: ${message}`, error })
}
