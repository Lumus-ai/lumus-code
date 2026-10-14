"""End-to-end through the CDP bridge against a local stub chat page (needs Chrome + playwright). Skips if missing."""
import json, os, shutil, subprocess, sys, tempfile, threading, time, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lumus_loop_web as web
PAGE = """<html><body><textarea id=in></textarea><button id=send onclick="go()">Send</button><div id=log></div>
<script>function go(){var t=document.getElementById('in').value;fetch('/ask',{method:'POST',body:t}).then(r=>r.text()).then(x=>{setTimeout(()=>{var d=document.createElement('div');d.className='reply';d.style.whiteSpace='pre-wrap';d.textContent=x;document.getElementById('log').appendChild(d)},1500)})}</script></body></html>"""
BRAIN = ["## PLAN\n1. add\n\n## HANDS TASK\nCreate calc.py with add(a, b).\n\n## STATUS\nCONTINUE\n",
         "## PLAN\n1. fix sign\n\n## HANDS TASK\nadd must return a+b.\n\n## STATUS\nDONE\n"]
HANDS = ["### FILE: calc.py\n```python\ndef add(a, b):\n    return a - b\n```", "### FILE: calc.py\n```python\ndef add(a, b):\n    return a + b\n```"]
seen = {"prompts": [], "hands": 0}
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("content-type", "text/html"); self.end_headers(); self.wfile.write(PAGE.encode())
    def do_POST(self):
        body = self.rfile.read(int(self.headers["content-length"])).decode()
        if self.path == "/ask": seen["prompts"].append(body); out = BRAIN[len(seen["prompts"]) - 1]
        else:
            out = json.dumps({"choices": [{"message": {"content": HANDS[seen["hands"]]}}]}); seen["hands"] += 1
        self.send_response(200); self.end_headers(); self.wfile.write(out.encode())
    def log_message(self, *a): pass

@unittest.skipUnless(shutil.which("google-chrome") or shutil.which("chromium"), "no chrome")
class WebStub(unittest.TestCase):
    def test_full_loop_over_cdp(self):
        try: import playwright  # noqa
        except ImportError: self.skipTest("no playwright")
        srv = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
        port = srv.server_port; prof = tempfile.mkdtemp(); d = tempfile.mkdtemp()
        exe = shutil.which("google-chrome") or shutil.which("chromium")
        chrome = subprocess.Popen([exe, "--headless=new", "--no-sandbox", "--remote-debugging-port=9333", "--remote-debugging-address=127.0.0.1", "--user-data-dir=" + prof, "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            import urllib.request
            for _ in range(60):
                try: urllib.request.urlopen('http://127.0.0.1:9333/json/version', timeout=1); break
                except OSError: time.sleep(0.5)
            open(os.path.join(d, "t.txt"), "w").write("Write add(a,b) in calc.py")
            env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "LUMUS_STUB_URL": "http://127.0.0.1:%d/" % port, "LUMUS_HANDS_URL": "http://127.0.0.1:%d" % port, "LUMUS_HANDS_MODEL": "m"}
            run = lambda *a: subprocess.run([sys.executable, os.path.join(HERE, "lumus-loop.py"), *a], cwd=d, env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(run("init", "t.txt", "--test", "python3 -c 'import calc; assert calc.add(2,3)==5'").returncode, 0)
            r = run("run", "--adapter", "stub", "--cdp", "http://127.0.0.1:9333")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr); self.assertIn("PASSED in round 2", r.stdout)
            self.assertEqual(len(seen["prompts"]), 2)
            self.assertIn("AssertionError", seen["prompts"][1])   # failing test output reached the brain through the page
            self.assertIn("a - b", seen["prompts"][1])
        finally:
            chrome.terminate(); srv.shutdown()
if __name__ == "__main__": unittest.main()
