import json, os, stat, subprocess, sys, tempfile, threading, unittest, urllib.request, urllib.error, shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import lumus_providers as pv, lumus_ui
SECRET = "sk-TEST-SECRET-123"
class API(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _j(self, code, o):
        b = json.dumps(o).encode(); self.send_response(code); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.headers.get("authorization") != "Bearer " + SECRET: return self._j(401, {"error": "no"})
        self._j(200, {"data": [{"id": "models/paid-big", "pricing": {"prompt": "0.001", "completion": "0.002"}}, {"id": "llama-free-instruct", "pricing": {"prompt": "0", "completion": "0"}}]})
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        if self.headers.get("authorization") != "Bearer " + SECRET: return self._j(401, {"error": "no"})
        self._j(200, {"choices": [{"message": {"content": "ok"}}]})
srv = ThreadingHTTPServer(("127.0.0.1", 0), API); threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = "http://127.0.0.1:%d" % srv.server_address[1]
def mode(p): return stat.S_IMODE(os.stat(p).st_mode)
class T(unittest.TestCase):
    def setUp(self): self.cfg = tempfile.mkdtemp(); os.environ["XDG_CONFIG_HOME"] = self.cfg
    def tearDown(self): shutil.rmtree(self.cfg, ignore_errors=True)
    def test_custom_setup_picks_free_model_tests_and_activates(self):
        r = pv.setup("custom", SECRET, lumus_ui.core.chat, name="Мой сервер", url=URL)
        self.assertTrue(r["ok"], r); self.assertEqual(r["model"], "llama-free-instruct")
        d = pv.load(); self.assertEqual(d["active"]["model"], "llama-free-instruct")
        self.assertEqual(os.environ["LUMUS_HANDS_URL"], URL); self.assertEqual(os.environ["LUMUS_HANDS_MODEL"], "llama-free-instruct")
        self.assertEqual(mode(pv.key_path(r["id"])), 0o600); self.assertEqual(mode(pv._json_path()), 0o600); self.assertEqual(mode(os.path.dirname(pv.key_path("x"))), 0o700)
        self.assertNotIn(SECRET, json.dumps(pv.public())); self.assertNotIn(SECRET, open(pv._json_path()).read())
        self.assertEqual(subprocess.run([sys.executable, os.path.join(HERE, "lumus_providers.py"), "has-active"], env={**os.environ}).returncode, 0)
    def test_wrong_key_gives_plain_russian_error_and_not_activated(self):
        r = pv.setup("custom", "wrong", lumus_ui.core.chat, name="X", url=URL)
        self.assertFalse(r["ok"]); self.assertIn("Ключ не подходит", r["error"]); self.assertNotIn("401", r["error"]); self.assertIsNone(pv.load()["active"])
    def test_bad_urls_and_inputs(self):
        for u in ("ftp://x", "http://evil.example.com/v1", "javascript:1", ""):
            with self.assertRaises(ValueError): pv.connect("custom", "k", name="n", url=u)
        with self.assertRaises(ValueError): pv.connect("gemini", "")          # key required
        with self.assertRaises(ValueError): pv.connect("gemini", "a\nb")
        with self.assertRaises(ValueError): pv.select("nope", "m")
    def test_models_free_flag_and_models_prefix_stripped(self):
        pid = pv.connect("custom", SECRET, name="M", url=URL); ms = pv.list_models(pid)
        self.assertEqual({m["id"]: m["free"] for m in ms}, {"paid-big": False, "llama-free-instruct": True})
    def test_disconnect_removes_key_and_active(self):
        r = pv.setup("custom", SECRET, lumus_ui.core.chat, name="D", url=URL); pv.disconnect(r["id"])
        self.assertFalse(os.path.exists(pv.key_path(r["id"]))); self.assertIsNone(pv.load()["active"]); self.assertEqual(pv.public()["connected"], [])
    def test_friendly_codes(self):
        for c, w in ((401, "Ключ"), (429, "лимит"), (404, "модел"), (503, "сбой"), (None, "связи")): self.assertIn(w.lower(), pv.friendly(c).lower())
    def test_http_endpoints_never_return_key(self):
        w = lumus_ui.Worker(tempfile.mkdtemp()); h = lumus_ui.ThreadingHTTPServer(("127.0.0.1", 0), lumus_ui.Handler); h.worker = w
        threading.Thread(target=h.serve_forever, daemon=True).start(); base = "http://127.0.0.1:%d" % h.server_address[1]
        def post(path, body):
            rq = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "X-Lumus-UI": "1"})
            try: return urllib.request.urlopen(rq).read().decode()
            except urllib.error.HTTPError as e: return e.read().decode()
        out = post("/api/providers/setup", {"id": "custom", "key": SECRET, "name": "H", "url": URL}); out += urllib.request.urlopen(base + "/api/providers").read().decode()
        out += urllib.request.urlopen(base + "/api/state").read().decode(); out += post("/api/providers/test", {"id": "custom-h", "model": "llama-free-instruct"})
        self.assertNotIn(SECRET, out); self.assertIn('"ok": true', out); self.assertIn('"provider": "H"', out)
        rq = urllib.request.Request(base + "/api/providers/setup", data=b"{}", headers={"Content-Type": "application/json"})   # no CSRF header
        with self.assertRaises(urllib.error.HTTPError) as c: urllib.request.urlopen(rq)
        self.assertEqual(c.exception.code, 403); h.shutdown()
if __name__ == "__main__": unittest.main()
