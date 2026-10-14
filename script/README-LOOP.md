# Lumus loop: web brain + free-model hands (experimental)

Standalone tool, not wired into the TUI yet. Branch: experimental-loop.

Idea: a strong model in a web chat (Gemini first) is the **brain** and writes the plan. A weaker free
model behind an OpenAI-compatible API is the **hands** and writes the code. Tests run automatically.
If they fail, the failing output and the current files go back to the brain in the same chat. It stops
when tests pass or after max rounds (default 5) and writes `.lumus-loop/report.md`.

## Setup (Linux)
    pip install playwright            # attaches to your Chrome, no browser download
    python3 script/lumus-loop.py chrome   # starts Chrome with a localhost-only debug port and a dedicated profile
    # log in to Gemini in that window once, keep it open
    export LUMUS_HANDS_URL=https://generativelanguage.googleapis.com/v1beta/openai   # or https://openrouter.ai/api/v1
    export LUMUS_HANDS_MODEL=gemini-3.1-flash-lite                                   # or an openrouter/free model
    export LUMUS_HANDS_KEY_FILE=~/.keys/hands.txt                                     # key is read from the file, never printed

## Use
    cd your-project
    python3 /path/to/script/lumus-loop.py init task.txt --test "pytest -q" --max-rounds 5
    python3 /path/to/script/lumus-loop.py run --adapter gemini

## Manual mode (no browser automation)
    lumus-loop.py brain-prompt            # writes .lumus-loop/round-N/brain-prompt.md; paste it into any chat
    lumus-loop.py brain-reply answer.md   # save the chat's answer to a file and feed it back
    lumus-loop.py step                    # hands write the code, tests run
Repeat until PASS or the round limit. The brain must answer with three heading lines: PLAN, HANDS TASK, STATUS
(CONTINUE or DONE). A reply in another shape is rejected, nothing changes, ask the brain again.
Optional fallback: `brain-api` with LUMUS_BRAIN_URL / LUMUS_BRAIN_MODEL / LUMUS_BRAIN_KEY_FILE.

## Safety and caveats
- The debug port is bound to 127.0.0.1 but gives full control of that Chrome profile. Use the dedicated profile
  (`~/.lumus-loop-chrome`), not your daily one, and close it when done.
- Automating a consumer chat UI may be against that service's terms. Captchas, login prompts and usage limits
  stop the loop with an error; use manual mode then.
- Selectors for the chat page live in `lumus_loop_web.py` (ADAPTERS) and can break when the page changes. The Gemini
  selectors are untested against the live site (see below). ChatGPT / Claude / DeepSeek are future adapters.
- Hands can only write files inside the project directory (paths with .., absolute paths, .git and .lumus-loop are refused,
  all checked before any write). Tests run your command with your shell: only use test commands you trust.
- One web chat per task: the chat URL is stored in `.lumus-loop/state.json` so later rounds keep context.

## Tested
Unit tests (reply parser incl. rendered-markdown replies, path safety, fail/pass loop, max rounds, rejected replies) and an
end-to-end run over CDP against a local stub chat page with real Chrome + Playwright. Not tested against the real Gemini web UI
here. Tests: `python3 -m unittest test_lumus_loop test_lumus_loop_web_stub` (in script/).
