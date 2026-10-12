export * as External from "./external"

import fs from "node:fs"
import path from "node:path"

// Rules for an outside agent that joined a room as external. It may write only inside the
// shared project, and only small files, and every write waits for the host's permission.
export const EDIT_LIMIT = 200_000
export const NAME_SUFFIX = " (external)"

// The name every room view shows for an external guest, so no one mistakes it for a person.
export function label(name: string) {
  return name.endsWith(NAME_SUFFIX) ? name : name + NAME_SUFFIX
}

export class EditError extends Error {}

// The absolute file an edit may write, or an EditError. The path must be relative, stay
// inside the project once symlinks are resolved, and the content must fit the cap.
export function resolveEdit(directory: string, file: string, content: string) {
  if (file.trim() === "" || file.includes("\0")) throw new EditError("Path is empty or invalid")
  if (path.isAbsolute(file)) throw new EditError("Path must be relative to the project")
  if (Buffer.byteLength(content) > EDIT_LIMIT) throw new EditError(`Content is over ${EDIT_LIMIT} bytes`)
  const root = fs.realpathSync(directory)
  const target = path.resolve(root, file)
  if (!inside(root, target)) throw new EditError("Path leaves the project")
  // Resolve the deepest part that exists, so a symlinked folder cannot lead outside.
  let probe = target
  while (!fs.existsSync(probe)) {
    const parent = path.dirname(probe)
    if (parent === probe) break
    probe = parent
  }
  if (!inside(root, fs.realpathSync(probe))) throw new EditError("Path leaves the project through a link")
  if (fs.existsSync(target) && fs.lstatSync(target).isSymbolicLink()) throw new EditError("Path is a link")
  // Inside .git a write could plant a hook that runs later.
  const rel = path.relative(root, target).split(path.sep)
  if (rel.includes(".git")) throw new EditError("The .git folder is off limits")
  return target
}

function inside(root: string, target: string) {
  const rel = path.relative(root, target)
  return rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel))
}

export function write(directory: string, file: string, content: string) {
  const target = resolveEdit(directory, file, content)
  fs.mkdirSync(path.dirname(target), { recursive: true })
  // Re-check after creating folders in case the race created a link.
  resolveEdit(directory, file, content)
  fs.writeFileSync(target, content)
  return { path: path.relative(fs.realpathSync(directory), target), bytes: Buffer.byteLength(content) }
}
