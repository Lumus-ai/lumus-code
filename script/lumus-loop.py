#!/usr/bin/env python3
"""Brain + hands loop for Lumus Code (stdlib only, file-based).

The BRAIN is a strong model you reach through a web chat (or any other way): this tool writes
the prompt to a file and reads the reply from a file. The HANDS are a weaker model behind an
OpenAI-compatible API that writes the code. Tests run automatically; failures go back to the brain.

  lumus-loop.py init <task-file> --test "<test command>" [--dir .] [--max-rounds 5]
  lumus-loop.py brain-prompt            -> writes .lumus-loop/round-N/brain-prompt.md (paste it into the web chat)
  lumus-loop.py brain-reply <file>      -> validates the brain's reply and stores it ("-" reads stdin)
  lumus-loop.py hands                   -> hands model implements the brain's HANDS TASK, files are written
  lumus-loop.py test                    -> runs the test command; PASS ends the loop, FAIL opens the next round
  lumus-loop.py step                    -> hands + test in one go
  lumus-loop.py chrome                  -> start a dedicated Chrome with a localhost-only debug port (log in to Gemini there once)
  lumus-loop.py run [--adapter chatgpt]  -> whole loop: web brain -> hands -> tests, until pass or max rounds
  lumus-loop.py brain-web [--adapter chatgpt] [--cdp URL]  -> one brain turn through the web chat (one chat per task)
  lumus-loop.py ui [--port 8787] [--dir PROJECT] [--no-open]  -> localhost web page for the whole loop (127.0.0.1 only)
  lumus-loop.py start [--choose] [--mode ui|terminal]  -> first launch asks: local chat page or terminal (remembered in ~/.config/lumus-demo/mode)
  lumus-loop.py status | report
Optional fallback: brain-api (set LUMUS_BRAIN_URL / LUMUS_BRAIN_MODEL / LUMUS_BRAIN_KEY_FILE).
Hands config: LUMUS_HANDS_URL, LUMUS_HANDS_MODEL, LUMUS_HANDS_KEY_FILE (key is read from the file, never printed).
The loop stops after max-rounds and writes report.md. Hands only write files inside the project dir.
"""
import json, os, re, subprocess, sys, urllib.request, urllib.error

STATE_DIR = ".lumus-loop"
FORMAT = """Reply with exactly these three sections, in this order. Each heading is a line of its own with exactly this text (a markdown ## in front is fine):

## PLAN
Short numbered plan (what to build, key decisions).

## HANDS TASK
Precise instructions for a weaker coding model: which files to create or change, function names,
behaviour, edge cases. Hands see only this section plus the current files, so be explicit.

## STATUS
One word: CONTINUE (more rounds needed) or DONE (tests are expected to pass now).

Do not write the code yourself unless the HANDS TASK needs a tiny snippet. Plain markdown only."""

def sd(root): return os.path.join(root, STATE_DIR)
def load(root):
    try:
        with open(os.path.join(sd(root), "state.json")) as f: return json.load(f)
    except OSError:
        raise SystemExit("ERROR: no loop here; run init first")
def save(root, s):
    os.makedirs(sd(root), exist_ok=True)
    tmp = os.path.join(sd(root), "state.json.tmp")
    with open(tmp, "w") as f: json.dump(s, f, indent=1)
    os.replace(tmp, os.path.join(sd(root), "state.json"))
def rdir(root, n):
    p = os.path.join(sd(root), "round-%d" % n); os.makedirs(p, exist_ok=True); return p
def write(path, text):
    with open(path, "w") as f: f.write(text)
def read(path, default=""):
    try:
        with open(path) as f: return f.read()
    except OSError: return default

def parse_brain_reply(text):
    """Returns (plan, hands_task, status) or raises ValueError. Tolerates code fences around the whole reply."""
    t = text.strip()
    m = re.match(r"^```[a-zA-Z]*\n([\s\S]*)\n```$", t)
    if m: t = m.group(1)
    parts = {}
    for m in re.finditer(r"^[ \t]*(?:#{1,6}[ \t]*)?\**[ \t]*(PLAN|HANDS TASK|STATUS)[ \t]*\**:?[ \t]*$", t, re.M | re.I):
        parts[m.group(1).upper()] = m.end()
    names = ["PLAN", "HANDS TASK", "STATUS"]
    if any(n not in parts for n in names):
        raise ValueError("reply must contain the heading lines PLAN, HANDS TASK, STATUS (## markdown headings are fine)")
    order = sorted((pos, n) for n, pos in parts.items())
    out = {}
    for i, (pos, n) in enumerate(order):
        end = len(t)
        if i + 1 < len(order):
            nxt = order[i + 1][0]
            end = nxt - len(re.search(r"[^\n]*$", t[pos:nxt]).group(0))
        out[n] = t[pos:end].strip()
    if not out["HANDS TASK"]:
        raise ValueError("HANDS TASK is empty")
    status = out["STATUS"].split()[0].upper().strip(".*`") if out["STATUS"] else ""
    if status not in ("CONTINUE", "DONE"):
        raise ValueError("STATUS must be CONTINUE or DONE")
    return out["PLAN"], out["HANDS TASK"], status

def parse_files(text):
    """Hands reply: '### FILE: path' followed by a fenced block. Returns [(path, content)]."""
    files = []
    for m in re.finditer(r"^###\s*FILE:\s*(\S+)\s*\n```[^\n]*\n([\s\S]*?)\n```", text, re.M):
        files.append((m.group(1), m.group(2) + "\n"))
    return files

def safe_path(root, rel):
    if os.path.isabs(rel) or ".." in rel.replace("\\", "/").split("/") or rel.startswith(STATE_DIR) or rel.startswith(".git"):
        raise ValueError("unsafe path from hands model: " + rel)
    full = os.path.realpath(os.path.join(root, rel))
    if not full.startswith(os.path.realpath(root) + os.sep):
        raise ValueError("path escapes the project: " + rel)
    return full

def project_files(root, limit=12000):
    out, used, skipped = [], 0, []
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".git", STATE_DIR, "node_modules", "__pycache__")]
        for n in sorted(names):
            p = os.path.join(base, n)
            try: body = open(p).read()
            except (OSError, UnicodeDecodeError): continue
            chunk = "### %s\n```\n%s\n```\n" % (os.path.relpath(p, root), body)
            if used + len(chunk) > limit:   # skip files that do not fit, keep looking for smaller ones
                skipped.append(os.path.relpath(p, root)); continue
            out.append(chunk); used += len(chunk)
    if skipped: out.append("(omitted, too large for the prompt: %s)\n" % ", ".join(skipped))
    return "".join(out)

def chat(url, model, keyfile, prompt, timeout=120):
    key = open(keyfile).read().strip() if keyfile else ""
    req = urllib.request.Request(url.rstrip("/") + "/chat/completions", method="POST",
        data=json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}]}).encode())
    req.add_header("content-type", "application/json")
    if key: req.add_header("authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        raise SystemExit("ERROR: model API HTTP %s" % e.code)  # never echo bodies: they can carry secrets
    except (urllib.error.URLError, OSError, KeyError, ValueError):
        raise SystemExit("ERROR: model API call failed or returned an unexpected shape")

def brain_prompt_text(root, s):
    n = s["round"]
    parts = ["# Brain request, round %d of %d\n" % (n, s["max_rounds"]), "## TASK\n" + read(os.path.join(sd(root), "task.md")).strip() + "\n",
             "## TEST COMMAND\n" + s["test"] + "\n"]
    if n > 1:
        prev = rdir(root, n - 1)
        parts.append("## YOUR PREVIOUS PLAN\n" + read(os.path.join(prev, "plan.md")).strip() + "\n")
        parts.append("## TEST OUTPUT (failing)\n```\n" + read(os.path.join(prev, "test-output.txt")).strip() + "\n```\n")
    parts.append("## CURRENT FILES\n" + (project_files(root) or "(none yet)\n"))
    parts.append("## HOW TO ANSWER\n" + FORMAT + "\n")
    return "\n".join(parts)

def cmd_init(a):
    root = "."; test = None; maxr = 5; task = None; i = 0
    while i < len(a):
        if a[i] == "--test": test = a[i + 1]; i += 2
        elif a[i] == "--dir": root = a[i + 1]; i += 2
        elif a[i] == "--max-rounds": maxr = int(a[i + 1]); i += 2
        else: task = a[i]; i += 1
    if not task or not test or not 1 <= maxr <= 20: raise SystemExit("usage: init <task-file> --test CMD [--dir D] [--max-rounds 1-20]")
    os.makedirs(sd(root), exist_ok=True)
    write(os.path.join(sd(root), "task.md"), open(task).read())
    save(root, {"round": 1, "max_rounds": maxr, "test": test, "phase": "need-brain", "history": [], "result": None})
    print("loop ready in", os.path.abspath(root), "- next: brain-prompt")

def cmd_brain_prompt(root):
    s = load(root)
    if s["result"]: raise SystemExit("loop already finished: " + s["result"])
    p = os.path.join(rdir(root, s["round"]), "brain-prompt.md")
    write(p, brain_prompt_text(root, s)); print("PROMPT FILE:", p, "\nPaste it into the web chat, then: brain-reply <file with the answer>")

def cmd_brain_reply(root, src):
    s = load(root)
    if s["result"]: raise SystemExit("loop already finished: " + s["result"])
    text = sys.stdin.read() if src == "-" else open(src).read()
    try: plan, hands, status = parse_brain_reply(text)
    except ValueError as e: raise SystemExit("REJECTED: %s\nAsk the brain to follow the format again." % e)
    d = rdir(root, s["round"])
    write(os.path.join(d, "brain-reply.md"), text); write(os.path.join(d, "plan.md"), plan); write(os.path.join(d, "hands-task.md"), hands)
    s["phase"] = "need-hands"; s["brain_status"] = status; save(root, s)
    print("brain reply accepted (status %s). next: hands (or step)" % status)

def cmd_brain_api(root):
    s = load(root); cmd_brain_prompt(root)
    text = chat(os.environ["LUMUS_BRAIN_URL"], os.environ["LUMUS_BRAIN_MODEL"], os.environ.get("LUMUS_BRAIN_KEY_FILE"), brain_prompt_text(root, s))
    tmp = os.path.join(sd(root), "brain-api-reply.md"); write(tmp, text); cmd_brain_reply(root, tmp)

def opt(a, name, default):
    return a[a.index(name) + 1] if name in a else default

def cmd_brain_web(root, a):
    import lumus_loop_web as web
    s = load(root); cmd_brain_prompt(root)
    prompt = brain_prompt_text(root, s)
    text, url = web.ask(opt(a, "--adapter", "chatgpt"), prompt, s.get("chat_url"), opt(a, "--cdp", web.CDP_DEFAULT))
    s = load(root); s["chat_url"] = url; save(root, s)   # later rounds continue in the same chat
    tmp = os.path.join(sd(root), "brain-web-reply.md"); write(tmp, text); cmd_brain_reply(root, tmp)

def cmd_run(root, a):
    while not load(root)["result"]:
        if load(root)["phase"] == "need-brain": cmd_brain_web(root, a)
        cmd_hands(root)
        try: cmd_test(root)
        except SystemExit as e:
            if e.code not in (0, 1): raise   # 1 = tests failed, loop goes on; anything else is a real error
    s = load(root); print(s["result"]); print(read(os.path.join(sd(root), "report.md")))

def cmd_hands(root):
    s = load(root)
    if s["phase"] != "need-hands": raise SystemExit("no brain reply waiting; run brain-prompt / brain-reply first")
    d = rdir(root, s["round"])
    prompt = ("You are the hands: you write code exactly as instructed.\n\n## INSTRUCTIONS FROM THE BRAIN\n" + read(os.path.join(d, "hands-task.md")) +
              "\n\n## CURRENT FILES\n" + (project_files(root) or "(none)") +
              "\n\n## ANSWER FORMAT\nFor every file you create or change, output the COMPLETE file as:\n### FILE: relative/path.ext\n```\nfull content\n```\nNo other files, no prose needed.")
    text = chat(os.environ["LUMUS_HANDS_URL"], os.environ["LUMUS_HANDS_MODEL"], os.environ.get("LUMUS_HANDS_KEY_FILE"), prompt)
    write(os.path.join(d, "hands-reply.md"), text)
    files = parse_files(text)
    if not files: raise SystemExit("hands returned no ### FILE blocks; see %s" % os.path.join(d, "hands-reply.md"))
    written = []
    for rel, body in files:
        full = safe_path(root, rel)   # all paths are checked before any is written
    for rel, body in files:
        full = safe_path(root, rel); os.makedirs(os.path.dirname(full), exist_ok=True); write(full, body); written.append(rel)
    s["phase"] = "need-test"; save(root, s); print("hands wrote:", ", ".join(written))

def cmd_test(root):
    s = load(root)
    if s["phase"] not in ("need-test",): raise SystemExit("run hands first")
    try:
        r = subprocess.run(s["test"], shell=True, cwd=root, capture_output=True, text=True, timeout=300)
        out, code = (r.stdout + r.stderr), r.returncode
    except subprocess.TimeoutExpired:
        out, code = "test command timed out after 300s", 124
    out = out[-6000:]
    d = rdir(root, s["round"]); write(os.path.join(d, "test-output.txt"), out)
    s["history"].append({"round": s["round"], "exit": code})
    if code == 0:
        s["result"] = "PASSED in round %d" % s["round"]
    elif s["round"] >= s["max_rounds"]:
        s["result"] = "STOPPED: tests still failing after %d rounds" % s["max_rounds"]
    else:
        s["round"] += 1; s["phase"] = "need-brain"
    save(root, s)
    if s["result"]: write_report(root, s)
    print("TEST", "PASS" if code == 0 else "FAIL (exit %d)" % code, "|", s["result"] or "next: brain-prompt for round %d" % s["round"])
    sys.exit(0 if code == 0 else 1)

def write_report(root, s):
    lines = ["# Lumus loop report", "", "Result: " + s["result"], "Test command: " + s["test"], ""]
    for h in s["history"]: lines.append("- round %d: tests %s" % (h["round"], "passed" if h["exit"] == 0 else "failed (exit %d)" % h["exit"]))
    lines += ["", "Claims are the loop's own record: brain plans and hands code are model output; only the test exit codes above were measured here."]
    write(os.path.join(sd(root), "report.md"), "\n".join(lines) + "\n")

TERMINAL_HELP = """Режим «Терминал». Команды (в папке проекта):
  lumus-demo init task.txt --test "python3 -m unittest discover -s tests"   создать задачу
  lumus-demo run                                                            весь цикл: мозг -> руки -> тесты
  lumus-demo status | report                                                состояние / отчёт
  lumus-demo chrome                                                         окно Chrome для мозга (ChatGPT без входа)
Сменить выбор: lumus-demo --choose   (страница: lumus-demo ui)"""

def cmd_start(a):
    cfg = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "lumus-demo", "mode")
    mode = opt(a, "--mode", None)
    if mode not in (None, "ui", "terminal"): raise SystemExit("--mode must be ui or terminal")
    if not mode and "--choose" not in a: mode = read(cfg).strip() or None
    asked = False
    if mode not in ("ui", "terminal"):
        print("Lumus Code. Где будем работать?\n  1) Локальная страница в браузере (рекомендуем, адрес 127.0.0.1)\n  2) Терминал")
        try: ans = input("Выбор [1]: ").strip() or "1"
        except EOFError: ans = "1"
        mode = "terminal" if ans.startswith("2") else "ui"; asked = True
    if asked:
        try: os.makedirs(os.path.dirname(cfg), exist_ok=True); write(cfg, mode + "\n"); print("Выбор запомнен (%s). Сменить: lumus-demo --choose" % cfg)
        except OSError as e: print("Не удалось запомнить выбор:", e)
    if mode == "terminal": print(TERMINAL_HELP); return
    import lumus_ui; return lumus_ui.serve(int(opt(a, "--port", 8787)), opt(a, "--dir", "."), "--no-open" not in a)

def main(a):
    if not a: print(__doc__); raise SystemExit(2)
    c, rest, root = a[0], a[1:], "."
    if c == "init": return cmd_init(rest)
    if c == "brain-prompt": return cmd_brain_prompt(root)
    if c == "brain-reply": return cmd_brain_reply(root, rest[0])
    if c == "brain-api": return cmd_brain_api(root)
    if c == "ui":
        import lumus_ui; return lumus_ui.serve(int(opt(rest, "--port", 8787)), opt(rest, "--dir", "."), "--no-open" not in rest)
    if c == "start": return cmd_start(rest)
    if c == "chrome":
        import lumus_loop_web as web; return web.start_chrome()
    if c == "brain-web": return cmd_brain_web(root, rest)
    if c == "run": return cmd_run(root, rest)
    if c == "hands": return cmd_hands(root)
    if c == "test": return cmd_test(root)
    if c == "step": cmd_hands(root); return cmd_test(root)
    if c == "status": s = load(root); print(json.dumps({k: s[k] for k in ("round", "max_rounds", "phase", "result", "history")}, indent=1)); return
    if c == "report": print(read(os.path.join(sd(root), "report.md"), "(no report yet)")); return
    print(__doc__); raise SystemExit(2)

if __name__ == "__main__": main(sys.argv[1:])
