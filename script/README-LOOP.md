> **DEMO VERSION (branch `demo`).** A working prototype, not a finished product.
> Works (tested): the file-based loop (brain prompt -> reply parsing -> free-model hands -> tests -> failures back to the brain, max 5 rounds);
> a real ChatGPT guest brain + Gemini flash-lite hands run that failed 2 rounds and passed in round 3; unit tests and a stub-page Playwright test.
> NOT tested: the Playwright auto-mode on the real ChatGPT / Gemini sites (the real-site page steps were driven by hand in a cloud browser),
> install.sh on Arch/Archcraft (tested only with a fresh fake HOME on another Linux box), signed-in ChatGPT, long sessions, usage-limit and captcha behaviour, Claude/DeepSeek adapters, any use inside the Lumus TUI.
> Web UI (`lumus-demo ui`): tested with a fake brain/hands (API tests, real-Chrome screenshots, auto mode against the stub page); NOT tested against the real ChatGPT/Gemini pages.
> Details are in "Tested" below.

# Lumus loop: web brain + free-model hands (experimental)

Standalone tool (`lumus-demo`), not wired into the TUI yet. Lives in script/ of the `demo` branch.

Idea: a strong model in a web chat (ChatGPT web is the default adapter, Gemini is the second one) is the **brain** and writes the plan. A weaker free
model behind an OpenAI-compatible API is the **hands** and writes the code. Tests run automatically.
If they fail, the failing output and the current files go back to the brain in the same chat. It stops
when tests pass or after max rounds (default 5) and writes `.lumus-loop/report.md`.

## Install (one command, Linux, needs git + python3 + Chrome)
    curl -fsSL https://raw.githubusercontent.com/Lumus-ai/lumus-code/demo/install.sh | bash
It clones this branch to ~/lumus-code-demo, makes a private venv with playwright, creates ~/.keys/hands.txt (empty, mode 600) and a
`lumus-demo` launcher in ~/.local/bin (add it to PATH if the installer says so). No sudo, nothing deleted, no secrets. Update: `lumus-demo update`.

## Use
    nano ~/.keys/hands.txt                 # paste your own Gemini API key once
    lumus-demo chrome                      # Chrome with a local debug port; ChatGPT works logged out
    cd ~/myproj                            # a folder with your tests (e.g. tests/) and a task.txt describing the task
    lumus-demo init task.txt --test "python3 -m unittest discover -s tests"
    lumus-demo run                         # chatgpt adapter is the default; add --adapter gemini for Gemini
**Web page instead of the terminal:** `lumus-demo ui` opens http://127.0.0.1:8787 (this machine only). Create the loop from a form, watch rounds
(brain prompt/reply, hands files, test output), Run / Run next round / Stop, manual brain mode (copy prompt, paste reply), report. The API key is never shown.
Other commands: `brain-prompt`, `brain-reply <file>`, `step`, `status`, `report`.

## Manual setup (without the installer)
    pip install playwright            # attaches to your Chrome, no browser download
    lumus-demo chrome   # starts Chrome with a localhost-only debug port and a dedicated profile
    # ChatGPT works logged out (guest); for Gemini log in in that window once. Keep it open
    export LUMUS_HANDS_URL=https://generativelanguage.googleapis.com/v1beta/openai   # or https://openrouter.ai/api/v1
    export LUMUS_HANDS_MODEL=gemini-3.1-flash-lite                                   # or an openrouter/free model
    export LUMUS_HANDS_KEY_FILE=~/.keys/hands.txt                                     # key is read from the file, never printed

## Use
    cd your-project
    lumus-demo init task.txt --test "pytest -q" --max-rounds 5
    lumus-demo run                      # default adapter chatgpt; add --adapter gemini for Gemini

## Manual mode (no browser automation)
    lumus-demo brain-prompt            # writes .lumus-loop/round-N/brain-prompt.md; paste it into any chat
    lumus-demo brain-reply answer.md   # save the chat's answer to a file and feed it back
    lumus-demo step                    # hands write the code, tests run
Repeat until PASS or the round limit. The brain must answer with three heading lines: PLAN, HANDS TASK, STATUS
(CONTINUE or DONE). A reply in another shape is rejected, nothing changes, ask the brain again.
Optional fallback: `brain-api` with LUMUS_BRAIN_URL / LUMUS_BRAIN_MODEL / LUMUS_BRAIN_KEY_FILE.

## Safety and caveats
- The debug port is bound to 127.0.0.1 but gives full control of that Chrome profile. Use the dedicated profile
  (`~/.lumus-loop-chrome`), not your daily one, and close it when done.
- Automating a consumer chat UI may be against that service's terms. Captchas, login prompts and usage limits
  stop the loop with an error; use manual mode then.
- Selectors for the chat page live in `lumus_loop_web.py` (ADAPTERS) and can break when the page changes. Gemini and ChatGPT
  (guest) selectors were checked by hand against the live sites on 2026-10-14. Claude / DeepSeek are future adapters.
- Hands can only write files inside the project directory (paths with .., absolute paths, .git and .lumus-loop are refused,
  all checked before any write). Tests run your command with your shell: only use test commands you trust.
- One web chat per task: the chat URL is stored in `.lumus-loop/state.json` so later rounds keep context.

## Tested (2026-10-14)
Automatic (unit / stub): `python3 -m unittest test_lumus_loop test_lumus_loop_web_stub` in script/ (reply parser, path safety, fail/pass
loop, max rounds, reply cleaning, big-file prompt handling, and the Playwright path against a local stub chat page with real Chrome).

Real ChatGPT guest web + Gemini flash-lite hands, driven through a cloud browser: the Playwright path could NOT be used there
(the cloud browser gives no CDP port, and a datacenter Chrome gets a Cloudflare "Just a moment" page), so the page steps (type
prompt, click send, wait until the reply stops changing) were run by hand with the same selectors the adapter uses. The loop
itself (prompt files, reply parsing, hands, tests, next round) ran automatically. On a real run (a small regex engine,
checked against Python's re) the hands failed round 1 and round 2 (real failing tests), the brain got the failing output in
the same chat and the tests passed in round 3. Two easier tasks passed in round 1.
Seen limits on the guest page: a 43.7k char prompt worked, a 61.5k char prompt ended in "Unable to connect" (the loop now
refuses prompts over 40k chars and treats such error texts as failure); a 600 line / 7.6k char reply came back complete.
Web UI (`lumus_ui.py`): `test_lumus_ui.py` (API: init, manual brain, fail then pass, host/header guards, key never returned), `test_lumus_ui_stub.py` (auto mode through the UI backend against the stub chat page in real Chrome), `e2e_ui_screenshots.py` (real Chrome, desktop + narrow screenshots, no JS errors).
Not tested: the Playwright path on the real ChatGPT/Gemini pages, long sessions, a signed-in ChatGPT, usage-limit messages.
