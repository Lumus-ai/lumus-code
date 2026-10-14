"""Localhost web UI for lumus-loop (stdlib only, no build step, no external requests).

Run:  lumus-demo ui [--port 8787] [--dir PROJECT]   -> http://127.0.0.1:8787
Binds to 127.0.0.1 only, rejects foreign Host/Origin headers, never returns the hands API key
(only whether the key file has content). The loop itself is lumus-loop.py; this file only drives it.
"""
import contextlib, importlib.util, io, json, os, re, sys, threading, time, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("lumus_loop_core", os.path.join(HERE, "lumus-loop.py"))
core = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(core)

ADAPTERS = ("chatgpt", "gemini") + (("stub",) if os.environ.get("LUMUS_UI_ALLOW_STUB") else ())   # stub = local test page, tests only
CAP = 24000   # max chars of any one file sent to the page

class Worker:
    """Runs loop steps in one background thread. Only one job at a time."""
    def __init__(self, root):
        self.root = os.path.abspath(root); self.lock = threading.Lock(); self.thread = None
        self.stop_flag = False; self.log = []; self.error = None; self.busy = None; self.manual = False; self.adapter = "chatgpt"
    def say(self, msg):
        self.log.append("%s  %s" % (time.strftime("%H:%M:%S"), msg)); self.log = self.log[-200:]
    def running(self): return self.busy is not None

    def advance_once(self):
        """Does the next phase. Returns False when it has to wait for the user (manual brain) or the loop is finished."""
        s = core.load(self.root)
        if s["result"]: return False
        ph = s["phase"]
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                if ph == "need-brain":
                    if self.manual:
                        core.cmd_brain_prompt(self.root); return False
                    self.say("round %d: asking the brain (%s)…" % (s["round"], self.adapter))
                    core.cmd_brain_web(self.root, ["--adapter", self.adapter, "--cdp", os.environ.get("LUMUS_UI_CDP", "http://127.0.0.1:9222")])
                elif ph == "need-hands":
                    self.say("round %d: hands are writing code…" % s["round"]); core.cmd_hands(self.root)
                elif ph == "need-test":
                    self.say("round %d: running tests…" % s["round"]); core.cmd_test(self.root)
        except SystemExit as e:
            if e.code in (0, 1, None): pass          # cmd_test exits 1 on failing tests: normal
            else: self.error = str(e.code); self.say("stopped: " + self.error); return False
        except Exception as e:
            self.error = "%s: %s" % (type(e).__name__, e); self.say("error: " + self.error); return False
        tail = out.getvalue().strip().splitlines()
        if tail: self.say(tail[-1][:200])
        return True

    def job(self, mode):
        self.error = None; self.stop_flag = False
        try:
            start_round = core.load(self.root)["round"]
            while not self.stop_flag:
                if not self.advance_once(): break
                s = core.load(self.root)
                if s["result"] or (mode == "round" and s["round"] != start_round) or (mode == "round" and s["phase"] == "need-brain"): break
        finally:
            self.busy = None
            if self.stop_flag: self.say("stopped after the current step")

    def start(self, mode):
        with self.lock:
            if self.running(): return False
            self.busy = mode; self.thread = threading.Thread(target=self.job, args=(mode,), daemon=True); self.thread.start(); return True

def rd(path):
    try:
        with open(path, errors="replace") as f: t = f.read()
    except OSError: return ""
    return t if len(t) <= CAP else t[:CAP] + "\n… (cut, %d more chars)" % (len(t) - CAP)

def snapshot(w):
    root = w.root
    base = {"dir": root, "adapter": w.adapter, "manual": w.manual, "busy": w.busy, "error": w.error, "log": w.log[-40:],
            "hands": {"url": os.environ.get("LUMUS_HANDS_URL", ""), "model": os.environ.get("LUMUS_HANDS_MODEL", ""),
                      "key_set": bool(os.environ.get("LUMUS_HANDS_KEY_FILE") and os.path.isfile(os.environ["LUMUS_HANDS_KEY_FILE"]) and os.path.getsize(os.environ["LUMUS_HANDS_KEY_FILE"]) > 0)}}
    try: s = core.load(root)
    except SystemExit: base["initialized"] = False; return base
    base.update(initialized=True, round=s["round"], max_rounds=s["max_rounds"], phase=s["phase"], result=s["result"], test=s["test"],
                task=rd(os.path.join(core.sd(root), "task.md")), chat_url=bool(s.get("chat_url")))
    rounds = []
    hist = {h["round"]: h["exit"] for h in s["history"]}
    for n in range(1, s["round"] + 1):
        d = os.path.join(core.sd(root), "round-%d" % n)
        if not os.path.isdir(d): continue
        reply = rd(os.path.join(d, "hands-reply.md"))
        rounds.append({"n": n, "prompt": rd(os.path.join(d, "brain-prompt.md")), "brain_reply": rd(os.path.join(d, "brain-reply.md")),
                       "plan": rd(os.path.join(d, "plan.md")), "hands_task": rd(os.path.join(d, "hands-task.md")),
                       "files": [f for f, _ in core.parse_files(reply)] if reply else [],
                       "test_output": rd(os.path.join(d, "test-output.txt")), "exit": hist.get(n)})
    base["rounds"] = rounds; base["report"] = rd(os.path.join(core.sd(root), "report.md"))
    return base

class Handler(BaseHTTPRequestHandler):
    server_version = "lumus-ui"
    def log_message(self, *a): pass
    def _ok_host(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"): return False
        org = self.headers.get("Origin")
        return not org or re.match(r"^http://(127\.0\.0\.1|localhost)(:\d+)?$", org) is not None
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        b = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store"); self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'")
        self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if not self._ok_host(): return self._send(403, {"error": "forbidden"})
        w = self.server.worker
        if self.path in ("/", "/index.html"):
            return self._send(200, open(os.path.join(HERE, "ui", "index.html"), "rb").read(), "text/html; charset=utf-8")
        if self.path == "/favicon.ico": return self._send(204, b"", "image/x-icon")
        if self.path.startswith("/api/state"): return self._send(200, snapshot(w))
        if self.path.startswith("/api/prompt"):
            try:
                s = core.load(w.root); text = core.brain_prompt_text(w.root, s)
                core.write(os.path.join(core.rdir(w.root, s["round"]), "brain-prompt.md"), text); return self._send(200, {"prompt": text})
            except SystemExit as e: return self._send(400, {"error": str(e.code)})
        return self._send(404, {"error": "not found"})
    def do_POST(self):
        if not self._ok_host() or self.headers.get("X-Lumus-UI") != "1": return self._send(403, {"error": "forbidden"})
        w = self.server.worker
        try: n = int(self.headers.get("Content-Length") or 0); data = json.loads(self.rfile.read(min(n, 2_000_000)) or b"{}")
        except ValueError: return self._send(400, {"error": "bad json"})
        p = self.path
        if p == "/api/init":
            if w.running(): return self._send(409, {"error": "a step is running"})
            task = str(data.get("task", "")).strip(); test = str(data.get("test", "")).strip()
            d = str(data.get("dir") or w.root).strip(); mr = int(data.get("max_rounds") or 5)
            if not task or not test: return self._send(400, {"error": "task and test command are required"})
            if not os.path.isdir(d): return self._send(400, {"error": "project folder does not exist: " + d})
            w.root = os.path.abspath(d); w.adapter = data.get("adapter") if data.get("adapter") in ADAPTERS else "chatgpt"
            w.manual = bool(data.get("manual")); w.log = []; w.error = None
            tf = os.path.join(core.sd(w.root), "task-input.txt"); os.makedirs(core.sd(w.root), exist_ok=True); core.write(tf, task + "\n")
            try:
                with contextlib.redirect_stdout(io.StringIO()): core.cmd_init([tf, "--test", test, "--dir", w.root, "--max-rounds", str(mr)])
            except SystemExit as e: return self._send(400, {"error": str(e.code)})
            w.say("loop ready in " + w.root); return self._send(200, snapshot(w))
        if p == "/api/settings":
            if data.get("adapter") in ADAPTERS: w.adapter = data["adapter"]
            if "manual" in data: w.manual = bool(data["manual"])
            return self._send(200, snapshot(w))
        if p in ("/api/run", "/api/round"):
            if "manual" in data: w.manual = bool(data["manual"])
            if data.get("adapter") in ADAPTERS: w.adapter = data["adapter"]
            return self._send(200 if w.start("run" if p == "/api/run" else "round") else 409, snapshot(w))
        if p == "/api/stop": w.stop_flag = True; return self._send(200, snapshot(w))
        if p == "/api/brain-reply":
            if w.running(): return self._send(409, {"error": "a step is running"})
            tmp = os.path.join(core.sd(w.root), "ui-brain-reply.md"); core.write(tmp, str(data.get("text", "")))
            try:
                with contextlib.redirect_stdout(io.StringIO()): core.cmd_brain_reply(w.root, tmp)
            except SystemExit as e: return self._send(400, {"error": str(e.code)})
            w.say("brain reply accepted"); return self._send(200, snapshot(w))
        return self._send(404, {"error": "not found"})

def serve(port=8787, root=".", open_browser=True):
    w = Worker(root); srv = ThreadingHTTPServer(("127.0.0.1", port), Handler); srv.worker = w
    url = "http://127.0.0.1:%d" % srv.server_address[1]
    print("Lumus UI on", url, "(Ctrl+C to stop). Project folder:", w.root, flush=True)
    if open_browser:
        try: webbrowser.open(url)
        except Exception: pass
    try: srv.serve_forever()
    except KeyboardInterrupt: print("bye")
    return srv

if __name__ == "__main__":
    a = sys.argv[1:]; opt = lambda n, d: a[a.index(n) + 1] if n in a else d
    serve(int(opt("--port", 8787)), opt("--dir", "."), "--no-open" not in a)
