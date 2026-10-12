import { Plugin } from "@opencode/plugin/tui"
import { useConfig } from "../../config"

// The mascot itself lives in the prompt; this registers the command that shows or hides it.
export default Plugin.define({
  id: "opencode.mascot",
  setup(context) {
    context.ui.slot({
      append: "app",
      render() {
        const config = useConfig()
        context.keymap.layer(() => ({
          mode: "global",
          commands: [
            {
              id: "mascot.toggle",
              title: config.data.mascot === false ? "Show mascot" : "Hide mascot",
              group: "System",
              palette: true,
              run() {
                void config
                  .update((draft) => {
                    draft.mascot = config.data.mascot === false
                  })
                  .catch((error: unknown) => context.ui.toast.show({ message: String(error), variant: "error" }))
                context.ui.dialog.clear()
              },
            },
          ],
        }))
        return null
      },
    })
  },
})
