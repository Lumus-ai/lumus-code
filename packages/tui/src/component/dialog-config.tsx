import { createMemo, createSignal } from "solid-js"
import { useConfig } from "../config"
import { useTheme, useThemes } from "../context/theme"
import { DialogSelect } from "../ui/dialog-select"
import { useToast } from "../ui/toast"
import { useT } from "../util/i18n"

type Setting = {
  title: string
  category: string
  description: string
  path: string[]
  default: unknown
  values?: readonly unknown[]
  labels?: readonly string[]
  step?: number
  min?: number
  max?: number
  format?: (value: unknown) => string
  keywords?: readonly string[]
}

const toggle = { values: [false, true], labels: ["off", "on"] } as const

// Ordered by how often a setting is reached for: General holds what most
// people change once after installing, Advanced what only tuning needs.
export const settings: Setting[] = [
  {
    title: "Language",
    category: "General",
    description: "Interface language of the panels, dialogs and commands",
    path: ["language"],
    default: "en",
    values: ["en", "ru"],
    labels: ["English", "Русский"],
    keywords: ["language", "russian", "english", "язык", "русский"],
  },
  {
    title: "Theme",
    category: "General",
    description: "Color theme; /themes previews them",
    path: ["theme", "name"],
    default: "opencode",
    keywords: ["color scheme", "colors"],
  },
  {
    title: "Color mode",
    category: "General",
    description: "Dark or light variant of the theme; system follows the terminal",
    path: ["theme", "mode"],
    default: "system",
    values: ["system", "dark", "light"],
    keywords: ["dark mode", "light mode", "system theme"],
  },
  {
    title: "Layout",
    category: "General",
    description: "Compact narrows the project panel and docks the sidebar beside the chat",
    path: ["layout"],
    default: "auto",
    values: ["auto", "compact", "full"],
    keywords: ["panels", "laptop", "narrow"],
  },
  {
    title: "Permissions",
    category: "General",
    description: "Ask before the AI runs commands and edits files, or accept automatically",
    path: ["session", "permissions"],
    default: "prompt",
    values: ["prompt", "autoaccept"],
    labels: ["prompt", "auto accept"],
    keywords: ["approve", "accept", "permission requests", "yolo"],
  },
  {
    title: "New session location",
    category: "General",
    description: "Start new sessions in the launch directory or in the active session's directory",
    path: ["session", "new_location"],
    default: "launch",
    values: ["launch", "inherit"],
    labels: ["launch directory", "active session"],
    keywords: ["directory", "cwd", "inherit"],
  },
  {
    title: "Animations",
    category: "Appearance",
    description: "Spinners, shimmer and other motion",
    path: ["animations"],
    default: true,
    ...toggle,
    keywords: ["motion", "effects"],
  },
  {
    title: "Mascot",
    category: "Appearance",
    description: "The Lumus Code mascot beside the prompt",
    path: ["mascot"],
    default: true,
    ...toggle,
    keywords: ["character", "sprite"],
  },
  {
    title: "Cursor shape",
    category: "Appearance",
    description: "Cursor of the text inputs; default keeps the terminal's own",
    path: ["cursor", "style"],
    default: "block",
    values: ["block", "line", "underline", "default"],
    keywords: ["caret", "cursor style"],
  },
  {
    title: "Cursor blinking",
    category: "Appearance",
    description: "Blink the input cursor",
    path: ["cursor", "blinking"],
    default: true,
    ...toggle,
    keywords: ["caret", "blink"],
  },
  {
    title: "Sidebar",
    category: "Session",
    description: "Session sidebar with context, todos and MCP; auto shows it when there is room",
    path: ["session", "sidebar"],
    default: "auto",
    values: ["hide", "auto"],
    keywords: ["side panel"],
  },
  {
    title: "Thinking",
    category: "Session",
    description: "Show the model's reasoning in the transcript",
    path: ["session", "thinking"],
    default: "hide",
    values: ["hide", "show"],
    keywords: ["reasoning", "chain of thought"],
  },
  {
    title: "Verbosity",
    category: "Session",
    description: "Low summarizes each run of tools and thoughts, high expands every group",
    path: ["session", "verbosity"],
    default: "medium",
    values: ["low", "medium", "high"],
    keywords: ["detail", "activity", "summary", "transcript"],
  },
  {
    title: "Tool grouping",
    category: "Session",
    description: "Group related reads and searches into one transcript item",
    path: ["session", "grouping"],
    default: "auto",
    values: ["none", "auto"],
    keywords: ["transcript", "messages", "reads", "searches"],
  },
  {
    title: "Markdown",
    category: "Session",
    description: "Render Markdown or show its syntax markers",
    path: ["session", "markdown"],
    default: "rendered",
    values: ["source", "rendered"],
    keywords: ["syntax", "concealment", "rendering"],
  },
  {
    title: "Transcript images",
    category: "Session",
    description: "Show attached and tool-result images inside the transcript",
    path: ["session", "image_preview"],
    default: false,
    ...toggle,
    keywords: ["attachments", "images", "tool output"],
  },
  {
    title: "TPS",
    category: "Session",
    description: "Show average tokens per second of the model",
    path: ["session", "tps"],
    default: true,
    ...toggle,
    keywords: ["tokens per second", "throughput", "speed"],
  },
  {
    title: "Scrollbar",
    category: "Session",
    description: "Scrollbar beside the transcript",
    path: ["session", "scrollbar"],
    default: false,
    ...toggle,
    keywords: ["scroll bar"],
  },
  {
    title: "Tabs",
    category: "Tabs",
    description: "Session tabs: always, never, or when the terminal supports them",
    path: ["tabs", "mode"],
    default: "auto",
    values: ["off", "on", "auto"],
  },
  {
    title: "Tab scope",
    category: "Tabs",
    description: "One set of tabs for everything or a separate set per directory",
    path: ["tabs", "scope"],
    default: "cwd",
    values: ["cwd", "global"],
    labels: ["current directory", "global"],
  },
  {
    title: "Tab layout",
    category: "Tabs",
    description: "Tabs as a strip on top or a column on the left",
    path: ["tabs", "layout"],
    default: "horizontal",
    values: ["horizontal", "vertical"],
    keywords: ["sidebar", "orientation", "left"],
  },
  {
    title: "Tab indicators",
    category: "Tabs",
    description: "Status icons on tabs, or always their numbers",
    path: ["tabs", "indicators"],
    default: "status",
    values: ["status", "numbers"],
    labels: ["status icons", "always show numbers"],
    keywords: ["tab numbers", "number mode", "status icons"],
  },
  {
    title: "Diff source",
    category: "Diffs",
    description: "What /diff opens with: branch changes, working tree, commits or the last turn",
    path: ["diffs", "source"],
    default: "branch",
    values: ["branch", "working", "committed", "turn"],
    labels: ["branch", "uncommitted", "committed", "last turn"],
    keywords: ["diff source", "changes", "turn"],
  },
  {
    title: "Diff layout",
    category: "Diffs",
    description: "Side by side or one column; auto picks by width",
    path: ["diffs", "view"],
    default: "auto",
    values: ["auto", "split", "unified"],
    keywords: ["diff layout", "split diff", "unified diff"],
  },
  {
    title: "Diff wrapping",
    category: "Diffs",
    description: "Wrap long lines in diffs",
    path: ["diffs", "wrap"],
    default: "word",
    values: ["none", "word"],
    keywords: ["diff wrap", "word wrap", "line wrap"],
  },
  {
    title: "File tree",
    category: "Diffs",
    description: "File tree beside the diff",
    path: ["diffs", "tree"],
    default: true,
    ...toggle,
    keywords: ["diff files"],
  },
  {
    title: "Single patch",
    category: "Diffs",
    description: "Show only the selected file instead of every patch",
    path: ["diffs", "single"],
    default: false,
    ...toggle,
    keywords: ["one file", "selected file"],
  },
  {
    title: "Mouse",
    category: "Input",
    description: "Clicks and scrolling with the mouse; off leaves selection to the terminal",
    path: ["mouse"],
    default: true,
    ...toggle,
    keywords: ["mouse capture"],
  },
  {
    title: "Scroll speed",
    category: "Input",
    description: "Lines scrolled per wheel tick",
    path: ["scroll", "speed"],
    default: 3,
    step: 0.25,
    min: 0.25,
    max: 10,
    format: (value) => Number(value).toFixed(2),
    keywords: ["scrolling"],
  },
  {
    title: "Editor context",
    category: "Input",
    description: "Attach the file or selection open in your editor to the prompt",
    path: ["prompt", "editor"],
    default: true,
    ...toggle,
    keywords: ["file context", "prompt context", "editor selection"],
  },
  {
    title: "Large pastes",
    category: "Input",
    description: "Show large pastes as a short placeholder or in full",
    path: ["prompt", "paste"],
    default: "compact",
    values: ["compact", "full"],
    keywords: ["paste summary", "clipboard", "pasted content"],
  },
  {
    title: "Image previews",
    category: "Input",
    description: "Preview attached images above the prompt",
    path: ["prompt", "image_preview"],
    default: false,
    ...toggle,
    keywords: ["attachments", "clipboard", "images", "prompt"],
  },
  {
    title: "Notifications",
    category: "Alerts",
    description: "System notification when the AI finishes or needs an answer",
    path: ["attention", "notifications"],
    default: false,
    ...toggle,
    keywords: ["system notifications", "desktop notifications", "alerts"],
  },
  {
    title: "Sounds",
    category: "Alerts",
    description: "Play a sound when the AI finishes or needs an answer",
    path: ["attention", "sound"],
    default: false,
    ...toggle,
    keywords: ["audio", "sound effects", "alerts"],
  },
  {
    title: "Volume",
    category: "Alerts",
    description: "Volume of the attention sounds",
    path: ["attention", "volume"],
    default: 0.4,
    step: 0.1,
    min: 0,
    max: 1,
    format: (value) => `${Math.round(Number(value) * 100)}%`,
    keywords: ["sound volume", "audio volume"],
  },
  {
    title: "Window title",
    category: "Terminal",
    description: "Show the session title in the terminal window title",
    path: ["terminal", "title"],
    default: true,
    ...toggle,
    keywords: ["terminal title", "tab title"],
  },
  {
    title: "Copy behavior",
    category: "Terminal",
    description: "Copy as soon as text is selected, or only on request",
    path: ["terminal", "copy"],
    default: process.platform === "win32" ? "manual" : "select",
    values: ["manual", "select"],
    keywords: ["selection", "clipboard"],
  },
  {
    title: "Scroll acceleration",
    category: "Advanced",
    description: "Scroll faster while the wheel keeps turning",
    path: ["scroll", "acceleration"],
    default: false,
    ...toggle,
    keywords: ["scroll acceleration"],
  },
  {
    title: "Leader timeout",
    category: "Advanced",
    description: "How long the leader key waits for the next key",
    path: ["leader", "timeout"],
    default: 2000,
    step: 250,
    min: 250,
    max: 10000,
    format: (value) => `${value} ms`,
    keywords: ["leader key", "shortcut timeout"],
  },
  {
    title: "Developer tools",
    category: "Advanced",
    description: "Bottom bar with server, UI, Tools and Registry",
    path: ["debug", "devtools"],
    default: false,
    ...toggle,
    keywords: ["debug bar", "developer tools"],
  },
]

export function settingID(setting: Setting) {
  return setting.path.join(".")
}

export function DialogConfig(props: { current?: string }) {
  const config = useConfig()
  const toast = useToast()
  const themes = useThemes()
  const theme = useTheme().surface("dialog")
  const t = useT()
  const current = Math.max(
    0,
    settings.findIndex((setting) => settingID(setting) === props.current),
  )
  const [selected, setSelected] = createSignal(current)
  const [saving, setSaving] = createSignal(false)

  const stored = (setting: Setting) =>
    setting.path.reduce<unknown>((result, key) => {
      if (!result || typeof result !== "object") return undefined
      return (result as Record<string, unknown>)[key]
    }, config.data)
  const value = (setting: Setting) => {
    if (settingID(setting) === "theme.name") return stored(setting) ?? themes.selected
    return stored(setting) ?? setting.default
  }
  const values = (setting: Setting) =>
    settingID(setting) === "theme.name"
      ? Object.keys(themes.all()).sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }))
      : setting.values
  const display = (setting: Setting) => {
    const current = value(setting)
    if (setting.format) return setting.format(current)
    const index = setting.values?.indexOf(current)
    return t(index === undefined || index < 0 ? String(current) : (setting.labels?.[index] ?? String(current)))
  }
  const changed = (setting: Setting) => stored(setting) !== undefined && value(setting) !== setting.default
  const options = createMemo(() =>
    settings.map((setting, index) => ({
      title: t(setting.title),
      category: t(setting.category),
      // English words stay searchable when the dialog reads in another language.
      searchText: [setting.title, setting.category, t(setting.description), ...(setting.keywords ?? [])].join(" "),
      footer: (changed(setting) ? "● " : "") + display(setting),
      footerColor: changed(setting) ? theme.text.formfield.selected : undefined,
      value: index,
    })),
  )

  async function write(next: unknown, index: number) {
    const setting = settings[index]
    setSaving(true)
    await config
      .update((draft) => {
        const parent = setting.path.slice(0, -1).reduce<Record<string, unknown>>((result, key) => {
          if (!result[key] || typeof result[key] !== "object") result[key] = {}
          return result[key] as Record<string, unknown>
        }, draft)
        if (next === undefined) delete parent[setting.path.at(-1)!]
        else parent[setting.path.at(-1)!] = next
      })
      .catch(toast.error)
      .finally(() => setSaving(false))
  }

  function change(direction: number, index = selected()) {
    if (saving()) return
    const setting = settings[index]
    const current = value(setting)
    const choices = values(setting)
    const next = choices
      ? choices[(choices.indexOf(current) + direction + choices.length) % choices.length]
      : Math.min(setting.max!, Math.max(setting.min!, Number(current) + direction * setting.step!))
    if (next === current) return
    return write(next, index)
  }

  // Removing the key, rather than writing the default, keeps the config file
  // free of values the user never chose and follows future default changes.
  function reset(index = selected()) {
    if (saving() || stored(settings[index]) === undefined) return
    return write(undefined, index)
  }

  return (
    <DialogSelect
      title={t("Settings")}
      options={options()}
      focusTarget={current}
      filterThreshold={0.7}
      onMove={(option) => setSelected(option.value)}
      onSelect={(option) => void change(1, option.value)}
      footer={
        <box>
          <text fg={theme.text.muted} wrapMode="word">
            {t(settings[selected()]?.description ?? "")}
          </text>
          <text fg={theme.text.muted}>{`←/→ ${t("change")} · ctrl+r ${t("reset")}`}</text>
        </box>
      }
      bindings={[
        {
          bind: "left",
          title: "Previous value",
          group: "Settings",
          run: () => void change(-1),
        },
        {
          bind: "right",
          title: "Next value",
          group: "Settings",
          run: () => void change(1),
        },
        {
          bind: "ctrl+r",
          title: "Reset to default",
          group: "Settings",
          run: () => void reset(),
        },
      ]}
    />
  )
}
