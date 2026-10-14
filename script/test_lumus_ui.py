"""API-level tests for lumus_ui (stdlib only, no browser): init, manual brain reply, hands, tests, host checks, key never leaked."""
import json, os, shutil, sys, tempfile, threading, time, unittest, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import lumus_ui

BRAIN = "## PLAN\nadd\n\n## HANDS TASK\nCreate add.py with add(a, b).\n\n## STATUS\nCONTINUE"
class Hands(BaseHTTPRequestHandler):
    replies = []
    def log_message(self, *a): pass
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"])); t = Hands.replies.pop(0)
        b = json.dumps({"choices": [{"message": {"content": t}}]}).encode(); self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

class UiTests(unittest.TestCase):
    def setUp(self):
        self.proj = tempfile.mkdtemp(); os.makedirs(self.proj + "/tests")
        open(self.proj + "/tests/test_add.py", "w").write("import sys, os; sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))\nimport unittest\nfrom add import add\nclass T(unittest.TestCase):\n    def test_add(self): self.assertEqual(add(2, 3), 5)\nif __name__ == '__main__': unittest.main()\n")
        self.hs = ThreadingHTTPServer(("127.0.0.1", 0), Hands); threading.Thread(target=self.hs.serve_forever, daemon=True).start()
        self.kf = tempfile.mktemp(); open(self.kf, "w").write("SECRET-KEY-VALUE\n")
        os.environ.update(LUMUS_HANDS_URL="http://127.0.0.1:%d" % self.hs.server_address[1], LUMUS_HANDS_MODEL="fake", LUMUS_HANDS_KEY_FILE=self.kf)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), lumus_ui.Handler); self.srv.worker = lumus_ui.Worker(self.proj)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start(); self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]
    def tearDown(self): self.srv.shutdown(); self.hs.shutdown(); shutil.rmtree(self.proj, ignore_errors=True)
    def call(self, path, body=None, headers=None):
        h = dict(headers or {}); data = None
        if body is not None: data = json.dumps(body).encode(); h.update({"Content-Type": "application/json", "X-Lumus-UI": "1"})
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base + path, data=data, headers=h), timeout=10) as r: return r.status, r.read()
        except urllib.error.HTTPError as e: return e.code, e.read()
    def wait_idle(self):
        for _ in range(100):
            time.sleep(0.1)
            if not json.loads(self.call("/api/state")[1])["busy"]: return json.loads(self.call("/api/state")[1])
        self.fail("worker still busy")
    def test_flow_manual_brain_fail_then_pass(self):
        Hands.replies = ["### FILE: add.py\n```python\ndef add(a, b):\n    return a - b\n```", "### FILE: add.py\n```python\ndef add(a, b):\n    return a + b\n```"]
        self.assertEqual(self.call("/api/init", {"task": "make add", "test": "python3 -B -m unittest discover -s tests", "dir": self.proj, "manual": True})[0], 200)
        self.assertEqual(self.call("/api/run", {"manual": True})[0], 200); s = self.wait_idle(); self.assertEqual(s["phase"], "need-brain")
        self.assertIn("HOW TO ANSWER", json.loads(self.call("/api/prompt")[1])["prompt"])
        self.assertEqual(self.call("/api/brain-reply", {"text": "just chatting"})[0], 400)
        self.assertEqual(self.call("/api/brain-reply", {"text": BRAIN})[0], 200)
        self.call("/api/run", {"manual": True}); s = self.wait_idle()
        self.assertEqual((s["round"], s["phase"], s["rounds"][0]["exit"]), (2, "need-brain", 1)); self.assertEqual(s["rounds"][0]["files"], ["add.py"])
        self.call("/api/brain-reply", {"text": BRAIN.replace("CONTINUE", "DONE")}); self.call("/api/run", {"manual": True}); s = self.wait_idle()
        self.assertEqual(s["result"], "PASSED in round 2", (s["error"], s["log"][-4:], s["phase"])); self.assertIn("PASSED", s["report"])
        self.assertNotIn("SECRET-KEY-VALUE", json.dumps(s)); self.assertTrue(s["hands"]["key_set"])
    def test_guards(self):
        self.assertEqual(self.call("/api/state", headers={"Host": "evil.example"})[0], 403)
        req = urllib.request.Request(self.base + "/api/stop", data=b"{}", method="POST")   # no X-Lumus-UI header
        with self.assertRaises(urllib.error.HTTPError) as c: urllib.request.urlopen(req)
        self.assertEqual(c.exception.code, 403)
        self.assertEqual(self.call("/api/init", {"task": "x", "test": "true", "dir": "/nonexistent/zzz"})[0], 400)
        self.assertEqual(self.call("/api/init", {"task": "", "test": "true"})[0], 400)
        self.assertEqual(self.call("/")[0], 200)

if __name__ == "__main__": unittest.main()
