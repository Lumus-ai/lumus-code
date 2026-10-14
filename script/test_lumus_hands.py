import contextlib, importlib.util, io, json, os, sys, tempfile, threading, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
spec = importlib.util.spec_from_file_location("loop", os.path.join(HERE, "lumus-loop.py")); loop = importlib.util.module_from_spec(spec); spec.loader.exec_module(loop)
import lumus_providers as pv

def server(code, text="### FILE: calc.py\n```\ndef add(a, b):\n    return a + b\n```\n"):
    hits = []
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"])); hits.append(1)
            b = json.dumps({"choices": [{"message": {"content": text}}]} if code == 200 else {"error": "x"}).encode()
            self.send_response(code); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
        do_GET = lambda self: (self.send_response(404), self.end_headers())
    s = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, "http://127.0.0.1:%d" % s.server_address[1], hits

class Base(unittest.TestCase):
    def setUp(self):
        self.x = tempfile.TemporaryDirectory(); self.old = os.environ.get("XDG_CONFIG_HOME"); os.environ["XDG_CONFIG_HOME"] = self.x.name
        os.environ["LUMUS_MODELS_DEV_URL"] = "off"; self.servers = []
    def tearDown(self):
        for s in self.servers: s.shutdown()
        if self.old is None: os.environ.pop("XDG_CONFIG_HOME", None)
        else: os.environ["XDG_CONFIG_HOME"] = self.old
        self.x.cleanup()
    def prov(self, name, code, text=None):
        s, url, hits = server(code, *([text] if text else [])); self.servers.append(s)
        pid = pv.connect("custom", "", name, url, "m-" + name.lower()); return pid, hits

class FileChanges(unittest.TestCase):
    def test_status_and_diff(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "a.py"), "w").write("x = 1\ny = 2\n"); open(os.path.join(d, "same.py"), "w").write("k\n")
            ch = loop.file_changes(d, [("a.py", "x = 1\ny = 3\n"), ("new.py", "hi\n"), ("same.py", "k\n")])
            self.assertEqual([c["status"] for c in ch], ["changed", "new", "same"])
            self.assertIn("-y = 2", ch[0]["diff"]); self.assertIn("+y = 3", ch[0]["diff"]); self.assertIn("+hi", ch[1]["diff"])
            with self.assertRaises(ValueError): loop.file_changes(d, [("../evil", "x")])

class Chain(Base):
    def test_chain_order_and_fallbacks(self):
        a, _ = self.prov("A", 200); b, _ = self.prov("B", 200); c, _ = self.prov("C", 200)
        pv.select(a, "m-a")
        pv.fallback(b, "add"); pv.fallback(c, "add")
        self.assertEqual([x["id"] for x in pv.chain()], [a, b, c])
        pv.fallback(c, "up"); self.assertEqual([x["id"] for x in pv.chain()], [a, c, b])
        pv.fallback(c, "remove"); self.assertEqual([x["id"] for x in pv.chain()], [a, b])
        with self.assertRaises(ValueError): pv.fallback(a, "add")      # the active one is already first
        pv.disconnect(b); self.assertEqual([x["id"] for x in pv.chain()], [a]); self.assertEqual(pv.public()["fallbacks"], [])
    def test_no_active_means_empty(self):
        self.assertEqual(pv.chain(), [])

class Failover(Base):
    def test_429_then_next(self):
        a, ha = self.prov("A", 429); b, hb = self.prov("B", 200)
        pv.select(a, "m-a"); pv.fallback(b, "add")
        out = io.StringIO()
        with contextlib.redirect_stdout(out): text, name, model = loop.chat_chain(pv.chain(), "hi")
        self.assertEqual((name, model), ("B", "m-b")); self.assertEqual((len(ha), len(hb)), (1, 1))
        self.assertIn("switched to B", out.getvalue()); self.assertIn("HTTP 429", out.getvalue())
    def test_first_ok_never_touches_backup(self):
        a, ha = self.prov("A", 200); b, hb = self.prov("B", 200)
        pv.select(a, "m-a"); pv.fallback(b, "add"); loop.chat_chain(pv.chain(), "hi"); self.assertEqual(len(hb), 0)
    def test_all_fail(self):
        a, _ = self.prov("A", 429); b, _ = self.prov("B", 500)
        pv.select(a, "m-a"); pv.fallback(b, "add")
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as e: loop.chat_chain(pv.chain(), "hi")
        self.assertIn("all hands providers failed", str(e.exception.code)); self.assertIn("HTTP 429", str(e.exception.code)); self.assertIn("HTTP 500", str(e.exception.code))

class HandsFlow(Base):
    def project(self):
        d = tempfile.mkdtemp(); tf = os.path.join(d, "t.txt"); open(tf, "w").write("make calc\n")
        with contextlib.redirect_stdout(io.StringIO()):
            loop.cmd_init([tf, "--test", "true", "--dir", d])
            rp = os.path.join(d, "r.md"); open(rp, "w").write("## PLAN\n1. x\n\n## HANDS TASK\nCreate calc.py\n\n## STATUS\nCONTINUE\n"); loop.cmd_brain_reply(d, rp)
        return d
    def test_deny_writes_nothing_allow_writes_and_failover_recorded(self):
        a, _ = self.prov("A", 429); b, _ = self.prov("B", 200); pv.select(a, "m-a"); pv.fallback(b, "add")
        d = self.project(); seen = []
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as e: loop.cmd_hands(d, lambda ch: seen.append(ch) or False)
        self.assertEqual(e.exception.code, "denied by user: files"); self.assertFalse(os.path.exists(os.path.join(d, "calc.py")))
        self.assertEqual(loop.load(d)["phase"], "need-hands"); self.assertEqual(seen[0][0]["status"], "new")
        with contextlib.redirect_stdout(io.StringIO()): loop.cmd_hands(d, lambda ch: True)
        self.assertIn("return a + b", open(os.path.join(d, "calc.py")).read()); self.assertEqual(loop.load(d)["phase"], "need-test")
        r = os.path.join(d, ".lumus-loop", "round-1", "hands-by.txt"); self.assertEqual(open(r).read().strip(), "B · m-b")
    def test_no_approver_keeps_old_behaviour(self):
        a, _ = self.prov("A", 200); pv.select(a, "m-a"); d = self.project()
        with contextlib.redirect_stdout(io.StringIO()): loop.cmd_hands(d)
        self.assertTrue(os.path.exists(os.path.join(d, "calc.py")))

if __name__ == "__main__": unittest.main()
