import { Instance } from "@opencode/core/instance/service"
import { NoteStore } from "@opencode/core/note"
import { Permission } from "@opencode/core/permission"
import { Room } from "@opencode/core/room"
import { Peer } from "@opencode/core/peer"
import { Plot } from "@opencode/core/plot"
import { Passport } from "@opencode/core/passport"
import { Dod } from "@opencode/core/dod"
import { External } from "@opencode/core/external"
import path from "node:path"
import { Session } from "@opencode/core/session"
import { Model } from "@opencode/core/model"
import { Agent } from "@opencode/core/agent"
import { Command } from "@opencode/core/command"
import { AbsolutePath } from "@opencode/core/schema"
import { Orchestra } from "@opencode/schema/orchestra"
import {
  CommandExecutionError,
  CommandNotFoundError,
  ForbiddenError,
  InvalidRequestError,
  PermissionNotFoundError,
  RoomNotFoundError,
  UnauthorizedError,
} from "@opencode/protocol/errors"
import { hostname } from "node:os"
import { randomBytes } from "node:crypto"
import { DateTime, Effect, Option, Predicate, Stream } from "effect"
import type { SessionMessage } from "@opencode/core/session/message"
import { HttpServerRequest } from "effect/unstable/http"
import { HttpApiBuilder, HttpApiSchema } from "effect/unstable/httpapi"
import { Api } from "../api"
import { ServerAuth } from "../auth"
import { locationErrors, sessionInfo } from "../location"
import { ServerRooms } from "../rooms"
import { missingSession } from "./session-error"
import { messagePage } from "./message"

function missingRequest(id: Permission.ID) {
  return new PermissionNotFoundError({ requestID: id, message: `Permission request not found: ${id}` })
}

const CHAT_LIMIT = 100

function chatMessage(message: SessionMessage.Info): Room.Message[] {
  const created = DateTime.toEpochMillis(message.time.created)
  if (message.type === "user") {
    const room = message.metadata?.room
    const author = Predicate.isObject(room) && Predicate.isObject(room.guest) ? room.guest.name : undefined
    return [
      {
        id: message.id,
        role: "user" as const,
        author: typeof author === "string" ? author : "host",
        text: message.text,
        created,
      },
    ]
  }
  if (message.type === "synthetic") {
    // An outside agent's answer or tool result: shown as an assistant line labeled external.
    const room = message.metadata?.room
    if (!Predicate.isObject(room) || room.external !== true || !Predicate.isObject(room.guest)) return []
    const author = typeof room.guest.name === "string" ? room.guest.name : External.label("external")
    return [{ id: message.id, role: "assistant" as const, author, text: message.text, created, external: true }]
  }
  if (message.type !== "assistant") return []
  const text = message.content.flatMap((part) => (part.type === "text" ? [part.text] : [])).join("\n")
  return text ? [{ id: message.id, role: "assistant" as const, text, created }] : []
}

const RANK: Record<Room.Role, number> = { viewer: 0, member: 1, helper: 2, cohost: 3 }

// Sessions a project room lists; enough for a shared project, bounded for a busy host.
const SESSION_LIMIT = 200

// Guests a room keeps; joining an open room needs no code, so a flood of joins stops here.
const MEMBER_LIMIT = 100

// Requests that run a command on the host for a guest. Only the host answers them, so a
// cohost or helper cannot approve its own command.
const HOST_ONLY = new Set(["room.run", "room.check", "room.edit"])

// Control and format characters in a name would reach the host's terminal, notes and feed.
const UNSAFE_NAME = /[\p{Cc}\p{Cf}\u2028\u2029]/u

// Loopback is left out: tunnels such as Porthole deliver every guest from it, so a ban on it would ban them all.
function remoteAddress(request: HttpServerRequest.HttpServerRequest) {
  const address = Option.getOrUndefined(request.remoteAddress)?.replace(/^::ffff:/, "")
  if (!address || address === "::1" || address.startsWith("127.")) return
  return address
}

function invalidDod(error: Dod.DodError) {
  return new InvalidRequestError({ message: error.message })
}

// A plot taken for a DoD item names the item first, so freeing the item finds it.
function dodPurpose(id: string, owner: string) {
  return `DoD ${id} for ${owner}`
}

// A guest reads its own items marked, so neither its client nor its AI matches by name.
function forGuest(dod: Room.Dod, member: Room.Member): Room.Dod {
  return { ...dod, items: dod.items.map((item) => (item.ownerID === member.id ? { ...item, mine: true } : item)) }
}

// The handover note of a DoD item: L-003 keeps its notes in dod-l-003.
function handoverName(id: string) {
  return `dod-${id
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")}`.slice(0, 60)
}

// The last lines of a check's output, short enough for one line of the DoD.
function outputSummary(output: string, exitCode: number) {
  const tail = output.trim().split("\n").slice(-3).join(" / ").slice(0, 300)
  return tail || `exit code ${exitCode}, no output`
}

function missingRoom(error: Room.NotFoundError) {
  return Effect.fail(new RoomNotFoundError({ roomID: error.roomID, message: `Room not found: ${error.roomID}` }))
}

export const RoomHandler = HttpApiBuilder.group(Api, "server.room", (handlers) =>
  Effect.gen(function* () {
    const rooms = yield* Room.Service
    const codes = yield* ServerRooms.Service
    const sessions = yield* Session.Service
    const instances = yield* Instance.Service
    const auth = yield* ServerAuth.Config
    const peers = yield* Peer.Service
    const plots = yield* Plot.Service

    // Guest routes skip the server credential; the room token is the only proof,
    // and it only opens the room it was issued for, while the host keeps its guest a member.
    const guestOf = Effect.fnUntraced(function* (roomID: Room.ID) {
      const request = yield* HttpServerRequest.HttpServerRequest
      const bearer = /^Bearer\s+(.+)$/i.exec(request.headers.authorization ?? "")?.[1]
      const token = bearer ? ServerRooms.verifyToken(auth, bearer) : undefined
      if (!token || token.roomID !== roomID)
        return yield* new UnauthorizedError({ message: "Room token is missing, expired or for another room" })
      const room = yield* rooms.get(roomID).pipe(Effect.catchTag("Room.NotFoundError", missingRoom))
      const member = yield* rooms.member(roomID, token.guest.id)
      if (!member) return yield* new UnauthorizedError({ message: "The host removed you from this room" })
      codes.touch(member.id)
      return { room, guest: token.guest, member }
    })

    // Viewers read; posting needs member, answering permission requests needs helper, and
    // switching models or agents and running commands needs cohost.
    const guestWho = Effect.fnUntraced(function* (roomID: Room.ID, needs: Exclude<Room.Role, "viewer">) {
      const joined = yield* guestOf(roomID)
      // An outside agent has the external route only; every other guest action is refused.
      if (joined.member.external === true)
        return yield* new ForbiddenError({ message: "External agents may only use the external route" })
      if (RANK[joined.member.role] < RANK[needs])
        return yield* new ForbiddenError({
          message:
            needs === "cohost"
              ? "Only cohosts pick the model, the agent and commands in this room"
              : needs === "helper"
                ? "The host answers permission requests in this room"
                : "You may only watch this room",
        })
      return joined
    })

    // A session the room shares: its own, or for a project room any session under the
    // project's directory. Others read as missing so a guest learns nothing of them.
    const inRoom = Effect.fnUntraced(function* (room: Room.Info, sessionID?: Session.ID) {
      const id = sessionID ?? room.sessionID
      const session = yield* sessions.get(id).pipe(Effect.catchTag("Session.NotFoundError", missingSession))
      if (id === room.sessionID) return session
      if (room.directory !== undefined && Orchestra.contains(room.directory, session.location.directory)) return session
      return yield* missingSession(new Session.NotFoundError({ sessionID: id }))
    })

    // Host-wide lists such as models and agents, read where the room's own session lives.
    const atRoom = <A, E, R>(room: Room.Info, effect: Effect.Effect<A, E, R>) =>
      Effect.gen(function* () {
        const session = yield* sessions
          .get(room.sessionID)
          .pipe(Effect.catchTag("Session.NotFoundError", missingSession))
        return yield* effect.pipe(instances.provide(session), locationErrors, Effect.orDie)
      })

    const memberOf = Effect.fnUntraced(function* (roomID: Room.ID, guestID: string) {
      yield* rooms.get(roomID).pipe(Effect.catchTag("Room.NotFoundError", missingRoom))
      const member = yield* rooms.member(roomID, guestID)
      if (!member) return yield* new InvalidRequestError({ message: "This guest is not in the room", field: "guestID" })
      return member
    })

    // Notes live in the session's location, which a guest never names, so the session decides where to look.
    const boundNote = Effect.fnUntraced(function* (sessionID: Session.ID) {
      const session = yield* sessionInfo(sessions, sessionID)
      return yield* NoteStore.Service.use((notes) => notes.bound(sessionID)).pipe(
        instances.provide(session),
        locationErrors,
        Effect.orDie,
      )
    })

    // The project a room shares: its directory, or the folder of its one session.
    const roomDirectory = Effect.fnUntraced(function* (room: Room.Info) {
      if (room.directory !== undefined) return room.directory
      const session = yield* sessions.get(room.sessionID).pipe(Effect.catchTag("Session.NotFoundError", missingSession))
      return session.location.directory
    })

    // A member keeps a DoD item while it stays in some room on this server and was heard
    // from within the lease; a restart counts as hearing from everyone once.
    const live = Effect.fnUntraced(function* () {
      const ids = new Set(
        (yield* Effect.forEach(yield* rooms.list(), (room) => rooms.members(room.id)))
          .flat()
          .map((member) => member.id),
      )
      const now = Date.now()
      return (ownerID: string) =>
        ids.has(ownerID) && now - (codes.seen(ownerID) ?? codes.started) < ServerRooms.LEASE_MS
    })

    const ownerOf = (member: Room.Member): Dod.Owner => ({ id: member.id, name: member.name })

    const readHandover = (room: Room.Info, id: string) =>
      atRoom(
        room,
        NoteStore.Service.use((notes) => notes.get(handoverName(id))).pipe(
          Effect.map((note): Room.DodNote => ({ name: note.name, text: note.body })),
          Effect.orElseSucceed(() => undefined),
        ),
      )

    // Adds a dated entry to the item's handover note, starting the note on its first entry.
    const writeHandover = (room: Room.Info, id: string, who: string, text: string) => {
      const name = handoverName(id)
      const entry = `\n## ${new Date().toISOString().slice(0, 16).replace("T", " ")} · ${who}\n\n${text.trim()}\n`
      return atRoom(
        room,
        NoteStore.Service.use((notes) =>
          notes.get(name).pipe(
            Effect.flatMap((note) => notes.edit({ name, body: entry, mode: "append", expectedMtime: note.mtime })),
            Effect.catchTag("NoteStore.NotFoundError", () =>
              notes.create({ name, title: `DoD ${id}`, body: entry.trimStart(), tags: ["dod"] }),
            ),
          ),
        ),
      )
    }

    // A member who left or was removed gives back what it held, and the room hears of it.
    const releaseMember = Effect.fnUntraced(function* (room: Room.Info, member: Room.Member) {
      const directory = yield* roomDirectory(room).pipe(Effect.orElseSucceed(() => undefined))
      if (directory === undefined) return
      const freed = yield* Dod.releaseOwner({ directory, ownerID: member.id }).pipe(Effect.orElseSucceed(() => []))
      yield* Effect.forEach(freed, (id) => freeDodPlots(directory, id))
      freed.forEach((id) => codes.record(room.id, { kind: "release", who: member.name, item: id }))
    })

    // Plots that were taken for a DoD item go with it, whichever session holds them.
    const freeDodPlots = Effect.fnUntraced(function* (directory: string, id: string) {
      const held = (yield* plots.list(directory)).filter((plot) => plot.purpose.startsWith(`DoD ${id} for `))
      yield* Effect.forEach(held, (plot) =>
        plot.members[0] ? plots.release({ sessionID: plot.members[0], directory, id: plot.id }) : Effect.void,
      )
    })

    return handlers
      .handle("room.list", () => rooms.list().pipe(Effect.map((data) => ({ data }))))
      .handle(
        "room.create",
        Effect.fn(function* (ctx) {
          yield* sessions.get(ctx.payload.sessionID).pipe(Effect.catchTag("Session.NotFoundError", missingSession))
          return { data: yield* rooms.create(ctx.payload) }
        }),
      )
      .handle(
        "room.update",
        Effect.fn(function* (ctx) {
          return {
            data: yield* rooms
              .update(ctx.params.roomID, ctx.payload)
              .pipe(Effect.catchTag("Room.NotFoundError", missingRoom)),
          }
        }),
      )
      .handle(
        "room.remove",
        Effect.fn(function* (ctx) {
          yield* rooms.remove(ctx.params.roomID).pipe(Effect.catchTag("Room.NotFoundError", missingRoom))
          return HttpApiSchema.NoContent.make()
        }),
      )
      .handle(
        "room.code",
        Effect.fn(function* (ctx) {
          yield* rooms.get(ctx.params.roomID).pipe(Effect.catchTag("Room.NotFoundError", missingRoom))
          return yield* codes.issueCode(ctx.params.roomID)
        }),
      )
      .handle("room.public", () =>
        rooms.list().pipe(
          Effect.map((list) => ({
            host: hostname(),
            // A closed room stays in the list, because a device needs to see it exists
            // to ask its host for the code; the name does not, so it is left out. This
            // route answers without a credential, and the name was the only thing about
            // a closed room that gave a passer-by anything.
            rooms: list.map((room) => ({
              id: room.id,
              name: room.open === true ? room.name : undefined,
              open: room.open === true,
            })),
          })),
        ),
      )
      .handle(
        "room.join",
        Effect.fn(function* (ctx) {
          const external = ctx.payload.external === true
          const name = external ? External.label(ctx.payload.name.trim()) : ctx.payload.name.trim()
          if (name === "" || name.length > 64 + (external ? External.NAME_SUFFIX.length : 0))
            return yield* new InvalidRequestError({ message: "Name must be 1 to 64 characters", field: "name" })
          if (UNSAFE_NAME.test(name))
            return yield* new InvalidRequestError({ message: "Name has characters that cannot be shown", field: "name" })
          const address = remoteAddress(yield* HttpServerRequest.HttpServerRequest)
          // Without a password every route is open to anyone who reaches the server, so it
          // shares rooms only with this computer.
          if (!ServerAuth.required(auth) && address !== undefined)
            return yield* new UnauthorizedError({ message: "The host must set a server password to share rooms" })
          const code = ctx.payload.code?.trim()
          const roomID = code ? yield* codes.redeemCode(code) : ctx.payload.roomID
          // A code can outlive its room; both cases read the same to the guest.
          const room = roomID ? yield* rooms.get(roomID).pipe(Effect.orElseSucceed(() => undefined)) : undefined
          if (!room || (!code && !room.open))
            return yield* new UnauthorizedError({
              message: code ? "Join code is wrong or expired" : "This room needs the join code its host shows",
            })
          const device = ctx.payload.device?.trim() || undefined
          if (Room.banned(yield* rooms.bans(room.id), { device, address }))
            return yield* new UnauthorizedError({ message: "The host banned you from this room" })
          if ((yield* rooms.members(room.id)).length >= MEMBER_LIMIT)
            return yield* new InvalidRequestError({ message: "This room is full" })
          const role = Room.joinRole(room)
          const issued = ServerRooms.issueToken(auth, room.id, name)
          const member = yield* rooms.saveMember(room.id, {
            id: issued.guest.id,
            name,
            // Anyone on the network may enter an open room, so only a code earns more than member.
            // An external agent never gets more than member, whatever the room's default.
            role: (!code || external) && RANK[role] > RANK.member ? "member" : role,
            device,
            address,
            joined: Date.now(),
            ...(external ? { external: true } : {}),
          })
          codes.record(room.id, { kind: "join", who: name })
          return { ...issued, role: member.role, room }
        }),
      )
      .handle(
        "room.member.list",
        Effect.fn(function* (ctx) {
          yield* rooms.get(ctx.params.roomID).pipe(Effect.catchTag("Room.NotFoundError", missingRoom))
          const now = Date.now()
          return {
            data: (yield* rooms.members(ctx.params.roomID)).map((member) => {
              const seen = codes.seen(member.id)
              return { ...member, seen, online: seen !== undefined && now - seen < ServerRooms.ONLINE_MS }
            }),
          }
        }),
      )
      .handle(
        "room.member.update",
        Effect.fn(function* (ctx) {
          const member = yield* memberOf(ctx.params.roomID, ctx.params.guestID)
          return { data: yield* rooms.saveMember(ctx.params.roomID, { ...member, role: ctx.payload.role }) }
        }),
      )
      .handle(
        "room.member.remove",
        Effect.fn(function* (ctx) {
          const member = yield* memberOf(ctx.params.roomID, ctx.params.guestID)
          yield* rooms.removeMember(ctx.params.roomID, ctx.params.guestID)
          const room = yield* rooms.get(ctx.params.roomID).pipe(Effect.catchTag("Room.NotFoundError", missingRoom))
          codes.record(room.id, { kind: "remove", who: member.name })
          yield* releaseMember(room, member)
          return HttpApiSchema.NoContent.make()
        }),
      )
      .handle(
        "room.member.ban",
        Effect.fn(function* (ctx) {
          const member = yield* memberOf(ctx.params.roomID, ctx.params.guestID)
          const ban = yield* rooms.ban(ctx.params.roomID, {
            id: randomBytes(9).toString("base64url"),
            name: member.name,
            device: member.device,
            address: member.address,
            created: Date.now(),
          })
          yield* rooms.removeMember(ctx.params.roomID, member.id)
          const room = yield* rooms.get(ctx.params.roomID).pipe(Effect.catchTag("Room.NotFoundError", missingRoom))
          codes.record(room.id, { kind: "ban", who: member.name })
          yield* releaseMember(room, member)
          return { data: ban }
        }),
      )
      .handle(
        "room.ban.list",
        Effect.fn(function* (ctx) {
          yield* rooms.get(ctx.params.roomID).pipe(Effect.catchTag("Room.NotFoundError", missingRoom))
          return { data: yield* rooms.bans(ctx.params.roomID) }
        }),
      )
      .handle(
        "room.ban.remove",
        Effect.fn(function* (ctx) {
          yield* rooms.get(ctx.params.roomID).pipe(Effect.catchTag("Room.NotFoundError", missingRoom))
          yield* rooms.unban(ctx.params.roomID, ctx.params.banID)
          return HttpApiSchema.NoContent.make()
        }),
      )
      .handle(
        "room.guest.session.messages",
        Effect.fn(function* (ctx) {
          const joined = yield* guestOf(ctx.params.roomID)
          const session = yield* inRoom(joined.room, ctx.query.sessionID)
          return yield* messagePage(sessions, session.id, ctx.query)
        }),
      )
      .handle(
        "room.guest.get",
        Effect.fn(function* (ctx) {
          const joined = yield* guestOf(ctx.params.roomID)
          return {
            room: joined.room,
            guest: joined.guest,
            role: joined.member.role,
            session: yield* inRoom(joined.room, ctx.query.sessionID),
          }
        }),
      )
      .handle(
        "room.guest.log",
        Effect.fn(function* (ctx) {
          const joined = yield* guestOf(ctx.params.roomID)
          const session = yield* inRoom(joined.room, ctx.query.sessionID)
          return sessions
            .log({ sessionID: session.id, after: ctx.query.after, follow: ctx.query.follow })
            .pipe(Stream.orDie)
        }),
      )
      .handle(
        "room.guest.messages",
        Effect.fn(function* (ctx) {
          const joined = yield* guestOf(ctx.params.roomID)
          const session = yield* inRoom(joined.room, ctx.query.sessionID)
          const messages = yield* sessions
            .messages({ sessionID: session.id, limit: CHAT_LIMIT, order: "desc" })
            .pipe(Effect.catchTag("Session.NotFoundError", missingSession), Effect.orDie)
          const note = yield* boundNote(session.id)
          // An external agent's posts wait in the inbox until the host model's next turn, so
          // the chat adds them from there; once promoted they come with the messages.
          const seen = new Set(messages.map((message) => message.id))
          const waiting = (yield* sessions.inbox(session.id).pipe(Effect.orElseSucceed(() => []))).flatMap((item) => {
            if (item.type !== "synthetic" || seen.has(item.id)) return []
            const room = item.payload.metadata?.room
            if (!Predicate.isObject(room) || room.external !== true || !Predicate.isObject(room.guest)) return []
            const author = typeof room.guest.name === "string" ? room.guest.name : External.label("external")
            return [
              {
                id: item.id,
                role: "assistant" as const,
                author,
                text: item.payload.text,
                created: DateTime.toEpochMillis(item.time.created),
                external: true,
              },
            ]
          })
          return {
            data: [...messages.toReversed().flatMap(chatMessage), ...waiting].toSorted((a, b) => a.created - b.created),
            running: (yield* sessions.active).has(session.id),
            note: note && { name: note.name, title: note.frontmatter.title || note.name },
          }
        }),
      )
      .handle(
        "room.guest.note",
        Effect.fn(function* (ctx) {
          const joined = yield* guestOf(ctx.params.roomID)
          const session = yield* inRoom(joined.room, ctx.query.sessionID)
          return { data: (yield* boundNote(session.id)) ?? null }
        }),
      )
      .handle(
        "room.guest.prompt",
        Effect.fn(function* (ctx) {
          const joined = yield* guestWho(ctx.params.roomID, "member")
          const session = yield* inRoom(joined.room, ctx.payload.sessionID)
          if (ctx.payload.text.trim() === "")
            return yield* new InvalidRequestError({ message: "Message is empty", field: "text" })
          return {
            data: yield* sessions
              .prompt({
                sessionID: session.id,
                text: ctx.payload.text,
                // The server stamps the author from the token, so a guest cannot speak as someone else.
                metadata: {
                  room: { id: joined.room.id, guest: joined.guest, ...(ctx.payload.ai ? { ai: true } : {}) },
                },
              })
              .pipe(Effect.catchTag("Session.NotFoundError", missingSession), Effect.orDie),
          }
        }),
      )
      .handle(
        "room.guest.external",
        Effect.fn(function* (ctx) {
          const joined = yield* guestOf(ctx.params.roomID)
          if (joined.member.external !== true)
            return yield* new ForbiddenError({ message: "This route is for guests that joined as external" })
          const session = yield* inRoom(joined.room, ctx.payload.sessionID)
          const body = ctx.payload
          const who = joined.member.name
          if (body.kind === "answer" || body.kind === "tool_result") {
            const text = (body.text ?? "").trim()
            if (text === "") return yield* new InvalidRequestError({ message: "Message is empty", field: "text" })
            if (text.length > 20_000) return yield* new InvalidRequestError({ message: "Message is too long", field: "text" })
            const tool = body.kind === "tool_result" ? (body.tool ?? "tool").slice(0, 80) : undefined
            // Admitted without resuming: the host model is not called. The server stamps the author.
            yield* sessions
              .synthetic({
                sessionID: session.id,
                text: tool ? `[${who} - result of ${tool}]\n${text}` : `[${who}] ${text}`,
                description: `External agent ${who}`,
                metadata: { room: { id: joined.room.id, guest: joined.guest, external: true, kind: body.kind } },
                resume: false,
              })
              .pipe(Effect.catchTag("Session.NotFoundError", missingSession), Effect.orDie)
            codes.record(joined.room.id, { kind: "external", who, detail: body.kind })
            return { data: { kind: body.kind } }
          }
          // A command or an edit waits for the host's answer in the permission prompt, every time.
          const ask = (action: string, resource: string, metadata: Record<string, unknown>) =>
            Permission.Service.use((permission) =>
              permission.confirm({
                action,
                resources: [resource],
                metadata: {
                  guest: who,
                  external: true,
                  ...(body.text?.trim() ? { reason: body.text.trim().slice(0, 500) } : {}),
                  ...metadata,
                },
                sessionID: session.id,
              }),
            ).pipe(
              instances.provide(session),
              locationErrors,
              Effect.catchTag("LocationNotFoundError", Effect.die),
              Effect.catch(() => Effect.fail(new ForbiddenError({ message: "The host did not allow this request" }))),
            )
          if (body.kind === "command") {
            const command = (body.command ?? "").trim()
            if (!command) return yield* new InvalidRequestError({ message: "Command is empty", field: "command" })
            yield* ask("room.run", command, { command })
            codes.record(joined.room.id, { kind: "run", who, detail: command })
            const run = yield* Effect.tryPromise({
              try: (signal) => Passport.run(command, session.location.directory, signal),
              catch: (error) => new InvalidRequestError({ message: `Could not run the command: ${String(error)}` }),
            })
            return { data: { kind: body.kind, run } }
          }
          const file = body.path ?? ""
          const content = body.content ?? ""
          const directory = joined.room.directory ?? session.location.directory
          // Checked before asking so the host is never prompted for an edit that cannot happen.
          yield* Effect.try({
            try: () => External.resolveEdit(directory, file, content),
            catch: (error) => new InvalidRequestError({ message: String((error as Error).message), field: "path" }),
          })
          yield* ask("room.edit", file, { path: file, bytes: Buffer.byteLength(content) })
          const edit = yield* Effect.try({
            try: () => External.write(directory, file, content),
            catch: (error) => new InvalidRequestError({ message: String((error as Error).message), field: "path" }),
          })
          codes.record(joined.room.id, { kind: "edit", who, detail: edit.path })
          return { data: { kind: body.kind, edit } }
        }),
      )
      .handle(
        "room.guest.permission.list",
        Effect.fn(function* (ctx) {
          const joined = yield* guestOf(ctx.params.roomID)
          const session = yield* inRoom(joined.room, ctx.query.sessionID)
          const requests = yield* Permission.Service.use((permission) => permission.forSession(session.id)).pipe(
            instances.provide(session),
            locationErrors,
            Effect.orDie,
          )
          return { data: requests }
        }),
      )
      .handle(
        "room.guest.permission.reply",
        Effect.fn(function* (ctx) {
          const joined = yield* guestWho(ctx.params.roomID, "helper")
          const session = yield* inRoom(joined.room, ctx.payload.sessionID)
          yield* Effect.gen(function* () {
            const permission = yield* Permission.Service
            const request = yield* permission.get(ctx.params.requestID)
            if (!request || request.sessionID !== session.id) return yield* missingRequest(ctx.params.requestID)
            if (HOST_ONLY.has(request.action))
              return yield* new ForbiddenError({ message: "Only the host answers this request" })
            yield* permission
              .reply({ requestID: ctx.params.requestID, reply: ctx.payload.decision, message: ctx.payload.message })
              .pipe(Effect.catchTag("Permission.NotFoundError", () => missingRequest(ctx.params.requestID)))
          }).pipe(instances.provide(session), locationErrors, Effect.catchTag("LocationNotFoundError", Effect.die))
          return HttpApiSchema.NoContent.make()
        }),
      )
      .handle("room.link.list", () => peers.links().pipe(Effect.map((data) => ({ data }))))
      .handle("room.link.save", (ctx) => peers.saveLink(ctx.payload).pipe(Effect.as(HttpApiSchema.NoContent.make())))
      .handle("room.link.remove", (ctx) =>
        peers.removeLink(ctx.payload).pipe(Effect.as(HttpApiSchema.NoContent.make())),
      )
      .handle(
        "room.guest.passport",
        Effect.fn(function* (ctx) {
          const joined = yield* guestOf(ctx.params.roomID)
          const session = yield* inRoom(joined.room, undefined)
          const directory = joined.room.directory ?? session.location.directory
          const held = yield* plots.list(directory)
          return {
            ...Passport.read(directory),
            plots: held.map((plot) => ({
              team: plot.team,
              machine: plot.machine,
              purpose: plot.purpose,
              paths: plot.paths.map((item) => path.relative(directory, item) || "."),
              expires: plot.expires,
            })),
          }
        }),
      )
      .handle(
        "room.guest.run",
        Effect.fn(function* (ctx) {
          const joined = yield* guestWho(ctx.params.roomID, "cohost")
          const session = yield* inRoom(joined.room, ctx.payload.sessionID)
          const command = ctx.payload.command.trim()
          if (!command) return yield* new InvalidRequestError({ message: "Command is empty", field: "command" })
          // The host sees the request in the session and decides; nothing runs before that.
          yield* Permission.Service.use((permission) =>
            permission.confirm({
              action: "room.run",
              resources: [command],
              metadata: { guest: joined.guest.name, command },
              sessionID: session.id,
            }),
          ).pipe(
            instances.provide(session),
            locationErrors,
            Effect.catchTag("LocationNotFoundError", Effect.die),
            Effect.catch(() => Effect.fail(new ForbiddenError({ message: "The host did not allow this command" }))),
          )
          codes.record(joined.room.id, { kind: "run", who: joined.member.name, detail: command })
          return yield* Effect.tryPromise({
            try: (signal) => Passport.run(command, session.location.directory, signal),
            catch: (error) => new InvalidRequestError({ message: `Could not run the command: ${String(error)}` }),
          })
        }),
      )
      .handle(
        "room.dod.get",
        Effect.fn(function* (ctx) {
          const room = yield* rooms.get(ctx.params.roomID).pipe(Effect.catchTag("Room.NotFoundError", missingRoom))
          return yield* Dod.read(yield* roomDirectory(room), yield* live()).pipe(Effect.mapError(invalidDod))
        }),
      )
      .handle(
        "room.dod.release",
        Effect.fn(function* (ctx) {
          const room = yield* rooms.get(ctx.params.roomID).pipe(Effect.catchTag("Room.NotFoundError", missingRoom))
          const directory = yield* roomDirectory(room)
          const dod = yield* Dod.release({ directory, id: ctx.payload.id }).pipe(Effect.mapError(invalidDod))
          yield* freeDodPlots(directory, ctx.payload.id)
          codes.record(room.id, { kind: "release", who: "host", item: ctx.payload.id })
          return dod
        }),
      )
      .handle(
        "room.guest.dod.get",
        Effect.fn(function* (ctx) {
          const joined = yield* guestOf(ctx.params.roomID)
          const dod = yield* Dod.read(yield* roomDirectory(joined.room), yield* live()).pipe(
            Effect.mapError(invalidDod),
          )
          return forGuest(dod, joined.member)
        }),
      )
      .handle(
        "room.guest.dod.claim",
        Effect.fn(function* (ctx) {
          const joined = yield* guestWho(ctx.params.roomID, "member")
          const session = yield* inRoom(joined.room, ctx.payload.sessionID)
          const directory = yield* roomDirectory(joined.room)
          const owner = ownerOf(joined.member)
          const record = (kind: Room.EventKind, item: string, who = owner.name) =>
            codes.record(joined.room.id, { kind, who, item })
          if (!ctx.payload.take) {
            const id = ctx.payload.id?.trim()
            if (!id) return yield* new InvalidRequestError({ message: "Name the item to give back", field: "id" })
            if (ctx.payload.handover?.trim()) yield* writeHandover(joined.room, id, owner.name, ctx.payload.handover)
            const dod = yield* Dod.release({ directory, id, owner }).pipe(Effect.mapError(invalidDod))
            yield* freeDodPlots(directory, id)
            record("release", id)
            return forGuest(dod, joined.member)
          }
          const alive = yield* live()
          const before = yield* Dod.read(directory, alive).pipe(Effect.mapError(invalidDod))
          const taken = ctx.payload.next
            ? yield* Dod.next({ directory, owner, live: alive }).pipe(Effect.mapError(invalidDod))
            : yield* Effect.gen(function* () {
                const id = ctx.payload.id?.trim()
                if (!id)
                  return yield* new InvalidRequestError({ message: "Name the item to take, or pass next", field: "id" })
                return {
                  id,
                  dod: yield* Dod.take({ directory, id, owner, live: alive }).pipe(Effect.mapError(invalidDod)),
                }
              })
          const previous = before.items.find((item) => item.id === taken.id)
          // An item taken over from a holder whose lease ran out drops that holder's plots too.
          if (previous?.stale && previous.ownerID !== owner.id) {
            yield* freeDodPlots(directory, taken.id)
            record("expire", taken.id, previous.owner ?? "?")
          }
          if (previous?.ownerID !== owner.id) record("take", taken.id)
          const result = {
            ...forGuest(taken.dod, joined.member),
            taken: taken.id,
            note: yield* readHandover(joined.room, taken.id),
          }
          const paths = ctx.payload.paths ?? []
          if (paths.length === 0) return result
          // A refused plot undoes a fresh take, so the item is not left marked for work that cannot start.
          return yield* plots
            .claim({ sessionID: session.id, directory, paths, purpose: dodPurpose(taken.id, owner.name) })
            .pipe(
              Effect.as(result),
              Effect.catchTag("Plot.TakenError", (error) =>
                Effect.gen(function* () {
                  if (previous?.ownerID !== owner.id) {
                    yield* Dod.release({ directory, id: taken.id, owner }).pipe(Effect.ignore)
                    record("release", taken.id)
                  }
                  return yield* new ForbiddenError({ message: error.message })
                }),
              ),
            )
        }),
      )
      .handle(
        "room.guest.dod.submit",
        Effect.fn(function* (ctx) {
          const joined = yield* guestWho(ctx.params.roomID, "member")
          const directory = yield* roomDirectory(joined.room)
          const owner = ownerOf(joined.member)
          const id = ctx.payload.id.trim()
          const command = yield* Dod.command({ directory, id, owner }).pipe(Effect.mapError(invalidDod))
          if (ctx.payload.handover?.trim()) yield* writeHandover(joined.room, id, owner.name, ctx.payload.handover)
          // dod.yaml can change under the host (an edit by the AI, a pull), so the host approves
          // each check command; "always" remembers that exact command.
          const session = yield* inRoom(joined.room, undefined)
          yield* Permission.Service.use((permission) =>
            permission.confirm({
              action: "room.check",
              resources: [command],
              save: [command],
              metadata: { guest: joined.guest.name, command, item: id },
              sessionID: session.id,
            }),
          ).pipe(
            instances.provide(session),
            locationErrors,
            Effect.catchTag("LocationNotFoundError", Effect.die),
            Effect.catch(() => Effect.fail(new ForbiddenError({ message: "The host did not allow this check" }))),
          )
          const result = yield* Effect.tryPromise({
            try: (signal) => Passport.run(command, directory, signal),
            catch: (error) => new InvalidRequestError({ message: `Could not run the check: ${String(error)}` }),
          })
          const dod = yield* Dod.accept({
            directory,
            id,
            command,
            exitCode: result.exitCode,
            summary: outputSummary(result.output, result.exitCode),
            by: owner.name,
          }).pipe(Effect.mapError(invalidDod))
          const passed = result.exitCode === 0
          if (passed) yield* freeDodPlots(directory, id)
          codes.record(joined.room.id, {
            kind: passed ? "pass" : "fail",
            who: owner.name,
            item: id,
            ...(passed ? {} : { detail: outputSummary(result.output, result.exitCode).slice(0, 120) }),
          })
          return { ...forGuest(dod, joined.member), check: { exitCode: result.exitCode, output: result.output } }
        }),
      )
      .handle(
        "room.guest.feed",
        Effect.fn(function* (ctx) {
          const joined = yield* guestOf(ctx.params.roomID)
          return yield* codes.feed(joined.room.id, ctx.query.after ?? 0, ctx.query.wait)
        }),
      )
      .handle(
        "room.feed",
        Effect.fn(function* (ctx) {
          const room = yield* rooms.get(ctx.params.roomID).pipe(Effect.catchTag("Room.NotFoundError", missingRoom))
          return yield* codes.feed(room.id, ctx.query.after ?? 0, ctx.query.wait)
        }),
      )
      .handle(
        "room.guest.leave",
        Effect.fn(function* (ctx) {
          const joined = yield* guestOf(ctx.params.roomID)
          yield* rooms.removeMember(joined.room.id, joined.member.id)
          codes.record(joined.room.id, { kind: "leave", who: joined.member.name })
          yield* releaseMember(joined.room, joined.member)
          return HttpApiSchema.NoContent.make()
        }),
      )
      .handle(
        "room.guest.session.list",
        Effect.fn(function* (ctx) {
          const joined = yield* guestOf(ctx.params.roomID)
          const directory = joined.room.directory
          if (directory === undefined) return { data: [yield* sessions.get(joined.room.sessionID).pipe(Effect.orDie)] }
          const page = yield* sessions.list({ parentID: null, limit: SESSION_LIMIT })
          return {
            data: page.data.filter((session) => Orchestra.contains(directory, session.location.directory)),
          }
        }),
      )
      .handle(
        "room.guest.session.create",
        Effect.fn(function* (ctx) {
          const joined = yield* guestWho(ctx.params.roomID, "member")
          if (joined.room.directory === undefined)
            return yield* new InvalidRequestError({ message: "This room shares one session; the host starts others" })
          // A guest's new session runs on the room session's model, the one the host picked; left
          // unset it would fall to the first model in the host's catalog.
          const shared = yield* sessions.get(joined.room.sessionID).pipe(Effect.option)
          return {
            data: yield* sessions
              .create({
                title: ctx.payload.title?.trim() || undefined,
                location: { directory: AbsolutePath.make(joined.room.directory) },
                model: Option.getOrUndefined(shared)?.model,
              })
              .pipe(Effect.orDie),
          }
        }),
      )
      .handle(
        "room.guest.model.list",
        Effect.fn(function* (ctx) {
          const joined = yield* guestOf(ctx.params.roomID)
          return {
            data: yield* atRoom(
              joined.room,
              Model.Service.use((models) => models.available()),
            ),
          }
        }),
      )
      .handle(
        "room.guest.agent.list",
        Effect.fn(function* (ctx) {
          const joined = yield* guestOf(ctx.params.roomID)
          return {
            data: yield* atRoom(
              joined.room,
              Agent.Service.use((agents) => agents.list()),
            ),
          }
        }),
      )
      .handle(
        "room.guest.command.list",
        Effect.fn(function* (ctx) {
          const joined = yield* guestOf(ctx.params.roomID)
          return {
            data: yield* atRoom(
              joined.room,
              Command.Service.use((commands) => commands.list()),
            ),
          }
        }),
      )
      .handle(
        "room.guest.session.model",
        Effect.fn(function* (ctx) {
          const joined = yield* guestWho(ctx.params.roomID, "cohost")
          const session = yield* inRoom(joined.room, ctx.params.sessionID)
          yield* sessions
            .switchModel({ sessionID: session.id, model: ctx.payload.model })
            .pipe(Effect.catchTag("Session.NotFoundError", missingSession))
          return HttpApiSchema.NoContent.make()
        }),
      )
      .handle(
        "room.guest.session.agent",
        Effect.fn(function* (ctx) {
          const joined = yield* guestWho(ctx.params.roomID, "cohost")
          const session = yield* inRoom(joined.room, ctx.params.sessionID)
          yield* sessions
            .switchAgent({ sessionID: session.id, agent: ctx.payload.agent })
            .pipe(Effect.catchTag("Session.NotFoundError", missingSession))
          return HttpApiSchema.NoContent.make()
        }),
      )
      .handle(
        "room.guest.session.command",
        Effect.fn(function* (ctx) {
          const joined = yield* guestWho(ctx.params.roomID, "cohost")
          const session = yield* inRoom(joined.room, ctx.params.sessionID)
          yield* sessions.command({ sessionID: session.id, command: ctx.payload.name, text: ctx.payload.text }).pipe(
            Effect.catchTag("Session.NotFoundError", missingSession),
            Effect.catchTag("Command.NotFoundError", (error) =>
              Effect.fail(new CommandNotFoundError({ command: error.command, message: error.message })),
            ),
            Effect.catchTag("Command.ExecutionError", (error) =>
              Effect.fail(new CommandExecutionError({ command: error.command, message: error.message })),
            ),
            locationErrors,
          )
          return HttpApiSchema.NoContent.make()
        }),
      )
  }),
)
