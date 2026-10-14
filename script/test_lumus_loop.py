import json, os, subprocess, sys, tempfile, threading, time, unittest, shutil
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import importlib.util
spec = importlib.util.spec_from_file_location("loop", os.path.join(HERE, "lumus-loop.py")); loop = importlib.util.module_from_spec(spec); spec.loader.exec_module(loop)

GOOD = "## PLAN\n1. add function\n\n## HANDS TASK\nCreate calc.py with add(a, b) returning a+b.\n\n## STATUS\nCONTINUE\n"

class ParseTests(unittest.TestCase):
    def test_valid_and_fenced(self):
        self.assertEqual(loop.parse_brain_reply(GOOD)[2], "CONTINUE")
        self.assertEqual(loop.parse_brain_reply("```markdown\n" + GOOD.strip() + "\n```")[1], "Create calc.py with add(a, b) returning a+b.")
    def test_rendered_markdown_without_hashes(self):
        # what inner_text of a rendered chat reply looks like: headings lose their ##
        plain = "PLAN\n1. add\n\nHANDS TASK\nCreate calc.py with add(a, b).\n\nSTATUS\nDONE"
        self.assertEqual(loop.parse_brain_reply(plain), ("1. add", "Create calc.py with add(a, b).", "DONE"))
        bold = "**PLAN**\nx\n**HANDS TASK**\ny\n**STATUS**\nCONTINUE."
        self.assertEqual(loop.parse_brain_reply(bold)[2], "CONTINUE")
    def test_rejects_bad(self):
        for bad in ["no headings", GOOD.replace("CONTINUE", "MAYBE"), GOOD.replace("Create calc.py with add(a, b) returning a+b.", "")]:
            with self.assertRaises(ValueError): loop.parse_brain_reply(bad)
    def test_files_and_paths(self):
        f = loop.parse_files("### FILE: a/b.py\n```python\nx=1\n```\n### FILE: c.txt\n```\nhi\n```")
        self.assertEqual(f, [("a/b.py", "x=1\n"), ("c.txt", "hi\n")])
        with tempfile.TemporaryDirectory() as d:
            for bad in ["../x", "/etc/passwd", ".git/config", ".lumus-loop/state.json", "a/../../x"]:
                with self.assertRaises(ValueError): loop.safe_path(d, bad)
            loop.safe_path(d, "ok/file.py")

class Fake(BaseHTTPRequestHandler):
    replies = []
    def do_POST(self):
        self.rfile.read(int(self.headers["content-length"]))
        body = json.dumps({"choices": [{"message": {"content": Fake.replies.pop(0)}}]}).encode()
        self.send_response(200); self.send_header("content-type", "application/json"); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass

def run(cwd, *args, env=None):
    return subprocess.run([sys.executable, os.path.join(HERE, "lumus-loop.py"), *args], cwd=cwd, capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **(env or {})})

class LoopTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.srv = HTTPServer(("127.0.0.1", 0), Fake); threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.env = {"LUMUS_HANDS_URL": "http://127.0.0.1:%d" % self.srv.server_port, "LUMUS_HANDS_MODEL": "m"}
        open(os.path.join(self.d, "task.txt"), "w").write("Write add(a,b) in calc.py")
        self.assertEqual(run(self.d, "init", "task.txt", "--test", "python3 -c 'import calc; assert calc.add(2,3)==5'", "--max-rounds", "2").returncode, 0)
    def tearDown(self): self.srv.shutdown(); shutil.rmtree(self.d)
    def brain(self, text):
        self.assertEqual(run(self.d, "brain-prompt").returncode, 0)
        open(os.path.join(self.d, "reply.md"), "w").write(text)
        r = run(self.d, "brain-reply", "reply.md"); self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
    def test_fail_then_pass(self):
        self.brain(GOOD)
        Fake.replies = ["### FILE: calc.py\n```python\ndef add(a, b):\n    return a - b\n```"]
        r = run(self.d, "step", env=self.env); self.assertEqual(r.returncode, 1); self.assertIn("TEST FAIL", r.stdout)
        prompt = open(os.path.join(self.d, ".lumus-loop/round-2/brain-prompt.md")).read() if run(self.d, "brain-prompt").returncode == 0 else ""
        self.assertIn("AssertionError", prompt); self.assertIn("a - b", prompt)   # failure + current code go back to the brain
        self.brain(GOOD.replace("CONTINUE", "DONE"))
        Fake.replies = ["### FILE: calc.py\n```python\ndef add(a, b):\n    return a + b\n```"]
        r = run(self.d, "step", env=self.env); self.assertEqual(r.returncode, 0); self.assertIn("PASSED in round 2", r.stdout)
        self.assertIn("passed", open(os.path.join(self.d, ".lumus-loop/report.md")).read())
        self.assertNotEqual(run(self.d, "brain-prompt").returncode, 0)  # finished loops do not continue
    def test_max_rounds_stops_with_report(self):
        for _ in range(2):
            self.brain(GOOD); Fake.replies = ["### FILE: calc.py\n```python\ndef add(a, b):\n    return 0\n```"]
            run(self.d, "step", env=self.env)
        self.assertIn("STOPPED", open(os.path.join(self.d, ".lumus-loop/report.md")).read())
    def test_unsafe_hands_path_writes_nothing(self):
        self.brain(GOOD); Fake.replies = ["### FILE: ok.py\n```\nx\n```\n### FILE: ../evil.py\n```\nx\n```"]
        r = run(self.d, "hands", env=self.env); self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.d, "ok.py"))); self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.d), "evil.py")))
    def test_bad_brain_reply_rejected_state_unchanged(self):
        run(self.d, "brain-prompt"); open(os.path.join(self.d, "r.md"), "w").write("just chatting")
        r = run(self.d, "brain-reply", "r.md"); self.assertNotEqual(r.returncode, 0); self.assertIn("REJECTED", r.stdout + r.stderr)
        self.assertEqual(json.load(open(os.path.join(self.d, ".lumus-loop/state.json")))["phase"], "need-brain")

class WebReplyTests(unittest.TestCase):
    def test_clean_reply(self):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import lumus_loop_web as w
        self.assertEqual(w.clean_reply("ChatGPT said:\nPLAN\n\nx"), "PLAN\n\nx")
        self.assertEqual(w.clean_reply("PLAN\nx"), "PLAN\nx")
        for bad in ("ChatGPT said:\nUnable to connect\n•\nRetry", "ChatGPT said:\nNo internet\n•\nReconnecting...", "ChatGPT said:\n", ""):
            with self.assertRaises(ValueError): w.clean_reply(bad)
    def test_real_chatgpt_shape_parses(self):   # shape seen on the real guest page: prefix line, bare headings, no ## marks
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import lumus_loop_web as w
        lp = importlib.machinery.SourceFileLoader("ll", os.path.join(os.path.dirname(os.path.abspath(__file__)), "lumus-loop.py")).load_module()
        plan, hands, status = lp.parse_brain_reply(w.clean_reply("ChatGPT said:\nPLAN\n\nBuild rx.\n\nHANDS TASK\n\nCreate rx.py\n\n1. parse\n\nSTATUS\n\nCONTINUE"))
        self.assertEqual((plan, status), ("Build rx.", "CONTINUE")); self.assertIn("1. parse", hands)
    def test_project_files_skips_big_but_keeps_small(self):
        lp = importlib.machinery.SourceFileLoader("ll2", os.path.join(os.path.dirname(os.path.abspath(__file__)), "lumus-loop.py")).load_module()
        d = tempfile.mkdtemp(); open(os.path.join(d, "a_big.py"), "w").write("x" * 20000); open(os.path.join(d, "z_small.py"), "w").write("ok = 1\n")
        out = lp.project_files(d); self.assertIn("ok = 1", out); self.assertIn("omitted", out); self.assertIn("a_big.py", out)

if __name__ == "__main__": unittest.main()
