"""UI backend end-to-end in AUTO mode: web brain via CDP against the local stub chat page (real Chrome + playwright), fake hands API. Skips if missing."""
import json, os, shutil, subprocess, sys, tempfile, threading, time, unittest, urllib.request
from http.server import ThreadingHTTPServer
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import test_lumus_loop_web_stub as stub

@unittest.skipUnless(shutil.which("google-chrome") or shutil.which("chromium"), "no chrome")
class UiAuto(unittest.TestCase):
    def test_auto_run_through_ui_api(self):
        try: import playwright  # noqa
        except ImportError: self.skipTest("no playwright")
        stub.seen["prompts"].clear(); stub.seen["hands"] = 0
        page = ThreadingHTTPServer(("127.0.0.1", 0), stub.H); threading.Thread(target=page.serve_forever, daemon=True).start()
        port = page.server_address[1]; prof = tempfile.mkdtemp(); proj = tempfile.mkdtemp()
        exe = shutil.which("google-chrome") or shutil.which("chromium")
        chrome = subprocess.Popen([exe, "--headless=new", "--no-sandbox", "--remote-debugging-port=9334", "--remote-debugging-address=127.0.0.1", "--user-data-dir=" + prof, "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "LUMUS_UI_ALLOW_STUB": "1", "LUMUS_UI_CDP": "http://127.0.0.1:9334", "LUMUS_STUB_URL": "http://127.0.0.1:%d/" % port,
               "LUMUS_HANDS_URL": "http://127.0.0.1:%d" % port, "LUMUS_HANDS_MODEL": "m"}
        ui = subprocess.Popen([sys.executable, os.path.join(HERE, "lumus-loop.py"), "ui", "--port", "8794", "--dir", proj, "--no-open"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def call(path, body=None):
            h = {"Content-Type": "application/json", "X-Lumus-UI": "1"} if body is not None else {}
            with urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8794" + path, data=None if body is None else json.dumps(body).encode(), headers=h), timeout=10) as r: return json.load(r)
        try:
            for _ in range(60):
                try: urllib.request.urlopen("http://127.0.0.1:9334/json/version", timeout=1); call("/api/state"); break
                except OSError: time.sleep(0.5)
            call("/api/init", {"task": "Write add(a,b) in calc.py", "test": "python3 -B -c 'import calc; assert calc.add(2,3)==5'", "dir": proj, "adapter": "stub"})
            call("/api/run", {"adapter": "stub", "manual": False})
            for _ in range(120):
                time.sleep(1); s = call("/api/state")
                if not s["busy"]: break
            self.assertEqual(s["result"], "PASSED in round 2", (s["error"], s["log"][-5:]))
            self.assertEqual(len(stub.seen["prompts"]), 2); self.assertIn("a - b", stub.seen["prompts"][1])
            self.assertEqual([r["exit"] for r in s["rounds"]], [1, 0])
        finally:
            ui.terminate(); chrome.terminate(); page.shutdown()
if __name__ == "__main__": unittest.main()
