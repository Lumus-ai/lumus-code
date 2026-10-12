// A host line that starts with "@instinct" is for the external agent. It is posted to the room
// chat without running the host's model.
export const ADDRESSEE = "instinct"

export function addressedToExternal(text: string) {
  const match = /^\s*@instinct\b[:,]?\s*([\s\S]*)$/i.exec(text)
  if (!match) return undefined
  return match[1]!.trim()
}
