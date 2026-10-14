"""Chat UI backend tests (stdlib only): test-command detection, confirm-action flow (allow / allow-in-chat / deny / stop), page has no external requests."""
import json, os, re, shutil, sys, tempfile, threading, time, unittest, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import lumus_ui

BRAIN = "## PLAN\nadd\n\n## HANDS TASK\nCreate add.py with add(a, b).\n\n## STATUS\nCONTINUE"
class Hands(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        b = json.dumps({"choices": [{"message": {"content": "### FILE: add.py\n```python\ndef add(a, b):\n    return a + b\n```"}}]}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

class ChatTests(unittest.TestCase):
    def setUp(self):
        self.proj = tempfile.mkdtemp(); os.makedirs(self.proj + "/tests")
        open(self.proj + "/tests/test_add.py", "w").write("import sys, os; sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))\nimport unittest\nfrom add import add\nclass T(unittest.TestCase):\n    def test_add(self): self.assertEqual(add(2, 3), 5)\n    def test_neg(self): self.assertEqual(add(-1, 1), 0)\n")
        self.hs = ThreadingHTTPServer(("127.0.0.1", 0), Hands); threading.Thread(target=self.hs.serve_forever, daemon=True).start()
        self.kf = tempfile.mktemp(); open(self.kf, "w").write("K\n")
        os.environ.update(LUMUS_HANDS_URL="http://127.0.0.1:%d" % self.hs.server_address[1], LUMUS_HANDS_MODEL="fake", LUMUS_HANDS_KEY_FILE=self.kf)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), lumus_ui.Handler); self.srv.worker = lumus_ui.Worker(self.proj)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start(); self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]
    def tearDown(self): self.srv.shutdown(); self.hs.shutdown(); shutil.rmtree(self.proj, ignore_errors=True)
    def call(self, path, body=None):
        h = {"Content-Type": "application/json", "X-Lumus-UI": "1"} if body is not None else {}
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(), headers=h), timeout=10) as r: return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e: return e.code, json.loads(e.read())
    def until(self, pred, what):
        for _ in range(100):
            time.sleep(0.1); s = self.call("/api/state")[1]
            if pred(s): return s
        self.fail("timeout waiting for " + what)
    def start(self, confirm=True):
        self.call("/api/init", {"task": "make add", "test": "python3 -B -m unittest discover -s tests", "dir": self.proj, "manual": True, "confirm": confirm})
        self.call("/api/run", {"manual": True}); self.until(lambda s: not s["busy"], "brain wait")
        self.assertEqual(self.call("/api/brain-reply", {"text": BRAIN})[0], 200)
        self.call("/api/run", {"manual": True})
    def test_detect(self):
        d = self.call("/api/detect?dir=" + self.proj)[1]; self.assertEqual(d["cmd"], "python3 -m unittest discover -s tests"); self.assertIn("2", d["note"])
        empty = tempfile.mkdtemp(); self.assertEqual(self.call("/api/detect?dir=" + empty)[1]["cmd"], ""); shutil.rmtree(empty)
    def test_confirm_allow(self):
        self.start(); s = self.until(lambda s: s["pending"], "pending")
        self.assertEqual(s["pending"]["cmd"], "python3 -B -m unittest discover -s tests"); self.assertEqual(s["phase"], "need-test")
        self.assertFalse(os.path.exists(self.proj + "/.lumus-loop/round-1/test-output.txt"))     # not run before the user says yes
        self.assertEqual(self.call("/api/confirm", {"decision": "allow"})[0], 200)
        s = self.until(lambda s: s["result"], "result"); self.assertEqual(s["result"], "PASSED in round 1"); self.assertFalse(s["allow_chat"])
    def test_confirm_deny(self):
        self.start(); self.until(lambda s: s["pending"], "pending"); self.call("/api/confirm", {"decision": "deny"})
        s = self.until(lambda s: not s["busy"], "idle"); self.assertEqual(s["error"], "denied by user"); self.assertIsNone(s["result"]); self.assertEqual(s["phase"], "need-test")
    def test_confirm_allow_chat_and_stop(self):
        self.start(); self.until(lambda s: s["pending"], "pending"); self.call("/api/confirm", {"decision": "allow-chat"})
        s = self.until(lambda s: s["result"], "result"); self.assertTrue(s["allow_chat"])
        self.call("/api/init", {"task": "t", "test": "true", "dir": self.proj, "manual": True, "confirm": True})   # new chat: permission is reset
        self.assertFalse(self.call("/api/state")[1]["allow_chat"])
        self.call("/api/run", {"manual": True}); self.until(lambda s: not s["busy"], "brain wait"); self.call("/api/brain-reply", {"text": BRAIN}); self.call("/api/run", {"manual": True})
        self.until(lambda s: s["pending"], "pending 2"); self.call("/api/stop", {}); s = self.until(lambda s: not s["busy"], "stopped"); self.assertIsNone(s["pending"]); self.assertIsNone(s["result"])
    def test_no_confirm_without_ask(self):
        self.assertEqual(self.call("/api/confirm", {"decision": "allow"})[0], 409)
    def test_page_is_local_only(self):
        html = urllib.request.urlopen(self.base + "/").read().decode()
        self.assertEqual(re.findall(r"(?:src|href)=[\"']https?://", html), []); self.assertNotIn("@import", html); self.assertNotIn("cdn.", html)
        self.assertIn("Новый чат", html)
if __name__ == "__main__": unittest.main()

class StartChooser(unittest.TestCase):
    def run_start(self, cfg, args, stdin=""):
        import subprocess
        return subprocess.run([sys.executable, os.path.join(HERE, "lumus-loop.py"), "start"] + args, input=stdin, capture_output=True, text=True, timeout=30, env={**os.environ, "XDG_CONFIG_HOME": cfg, "PYTHONDONTWRITEBYTECODE": "1"})
    def test_choice_is_asked_once_and_remembered(self):
        cfg = tempfile.mkdtemp()
        r = self.run_start(cfg, [], "2\n"); self.assertIn("Где будем работать", r.stdout); self.assertIn("Режим «Терминал»", r.stdout)
        self.assertEqual(open(cfg + "/lumus-demo/mode").read().strip(), "terminal")
        r = self.run_start(cfg, []); self.assertNotIn("Где будем работать", r.stdout); self.assertIn("Режим «Терминал»", r.stdout)       # remembered: no question
        r = self.run_start(cfg, ["--choose"], "2\n"); self.assertIn("Где будем работать", r.stdout)                                      # --choose asks again
        self.assertNotEqual(self.run_start(cfg, ["--mode", "bogus"]).returncode, 0); shutil.rmtree(cfg)
