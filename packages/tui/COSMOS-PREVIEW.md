# Cosmos rail preview

Developer-only component preview, not an installed session feature. It is not
connected to the session renderer or the Experiments dialog. No settings key is
advertised yet. Normal Lumus Code screens do not change.

`src/component/cosmos-rail.tsx` renders a separate two-cell margin. A caller must
pass `enabled={true}` and an explicit decorative color; without either it takes
no space. `animations={false}` gives a static, dim rail. Animation changes opacity
at 1.2-second intervals without moving glyphs or transcript text. Height is
bounded to 200 rows and non-finite heights produce no star rows.

Run from packages/tui after frozen workspace dependency installation:

```sh
bun test test/component/cosmos-rail.test.tsx test/component/session-tabs-rail.test.tsx
bun x tsgo --project tsconfig.cosmos-preview.json
```

The tests render real OpenTUI component fixtures at 32 and 72 columns, check
transcript/permission text placement, off/missing color, reduced motion, moving
opacity and bounded heights. They are not full application/session tests.
The focused typecheck covers only the component and its test, not the whole TUI.

Before user-facing session integration: wire through the existing Experiments
registry and config.experimental gate, use a dedicated optional theme decoration
role, verify Gemini/light/dark, resize, sidebar and permission layouts with actual
session fixtures, and finish the full TUI typecheck. Do not overlay stars on text
or borrow permission/execution colors for decoration.
