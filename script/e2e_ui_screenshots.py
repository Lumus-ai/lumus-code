"""Optional: drives the UI in real Chrome (Playwright), manual-brain flow with a fake hands API, and saves screenshots (desktop + narrow). Usage: python3 e2e_ui_screenshots.py OUTDIR"""
import json, os, shutil, subprocess, sys, tempfile, threading, time, http.server
from playwright.sync_api import sync_playwright
HERE = os.path.dirname(os.path.abspath(__file__)); OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/shots"; os.makedirs(OUT, exist_ok=True)
REPLIES = ["### FILE: add.py\n```python\ndef add(a, b):\n    return a - b\n```", "### FILE: add.py\n```python\ndef add(a, b):\n    return a + b\n```"]
class H(http.server.BaseHTTPRequestHandler):
    n = 0
    def log_message(self, *a): pass
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"])); txt = REPLIES[min(H.n, 1)]; H.n += 1
        b = json.dumps({"choices": [{"message": {"content": txt}}]}).encode(); self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
BRAIN = "## PLAN\n1. add()\n\n## HANDS TASK\nCreate add.py with add(a, b) returning the sum.\n\n## STATUS\nCONTINUE"
BRAIN2 = "## PLAN\nFix the sign bug.\n\n## HANDS TASK\nadd() must return a + b, not a - b.\n\n## STATUS\nDONE"
def main():
    hs = http.server.ThreadingHTTPServer(("127.0.0.1", 8792), H); threading.Thread(target=hs.serve_forever, daemon=True).start()
    proj = tempfile.mkdtemp(); os.makedirs(proj + "/tests")
    open(proj + "/tests/test_add.py", "w").write("import sys, os; sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))\nimport unittest\nfrom add import add\nclass T(unittest.TestCase):\n    def test_add(self): self.assertEqual(add(2, 3), 5)\nif __name__ == '__main__': unittest.main()\n")
    kf = tempfile.mktemp(); open(kf, "w").write("dummy-not-a-real-key\n")
    env = dict(os.environ, LUMUS_HANDS_URL="http://127.0.0.1:8792", LUMUS_HANDS_MODEL="fake-hands", LUMUS_HANDS_KEY_FILE=kf)
    ui = subprocess.Popen([sys.executable, os.path.join(HERE, "lumus-loop.py"), "ui", "--port", "8793", "--dir", proj, "--no-open"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5); res = {}
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=shutil.which("google-chrome"), args=["--no-sandbox"], headless=True)
            pg = b.new_page(viewport={"width": 1280, "height": 900}); errs = []
            pg.on("pageerror", lambda e: errs.append(str(e))); pg.on("console", lambda m: m.type == "error" and errs.append(m.text))
            pg.goto("http://127.0.0.1:8793/"); pg.wait_for_selector("#bInit")
            pg.screenshot(path=OUT + "/1-desktop-start.png")
            pg.fill("#task", "Create add.py with add(a, b) that returns the sum of two numbers. Tests are in tests/test_add.py."); pg.check("#manual"); pg.click("#bInit")
            pg.locator('#chipRound').filter(has_text='round 1').wait_for()
            pg.click("#bRun"); pg.wait_for_selector("#manualBox", state="visible", timeout=10000)
            pg.fill("#reply", BRAIN); pg.click("#bReply")
            pg.click("#bRun"); pg.locator('#chipRound').filter(has_text='round 2').wait_for(timeout=20000)
            pg.wait_for_selector("#manualBox", state="visible"); pg.screenshot(path=OUT + "/2-desktop-round1-failed.png", full_page=True)
            res["round1_failed_chip"] = "tests fail" in pg.inner_text("#rounds_list")
            pg.fill("#reply", BRAIN2); pg.click("#bReply"); pg.click("#bRun")
            pg.locator('#chipResult').filter(has_text='PASSED').wait_for(timeout=20000)
            pg.screenshot(path=OUT + "/3-desktop-passed.png", full_page=True)
            res["passed"] = pg.inner_text("#chipResult"); res["rounds_shown"] = pg.locator(".round").count(); res["key_hidden"] = "dummy-not-a-real-key" not in pg.content()
            pg.click("#bReport"); pg.wait_for_selector("#dlg[open]"); pg.screenshot(path=OUT + "/4-desktop-report.png"); pg.click("#dclose")
            m = b.new_page(viewport={"width": 390, "height": 844}); m.goto("http://127.0.0.1:8793/"); m.wait_for_selector(".round"); m.screenshot(path=OUT + "/5-narrow-passed.png", full_page=True)
            res["narrow_hscroll"] = m.evaluate("() => document.documentElement.scrollWidth > innerWidth + 1")
            r = b.new_page(viewport={"width": 1280, "height": 700}, reduced_motion="reduce"); r.goto("http://127.0.0.1:8793/"); r.wait_for_selector("#stars"); res["reduced_motion_ok"] = True
            res["js_errors"] = errs
        res["fake_hands_calls"] = H.n
    finally:
        ui.terminate()
    print(json.dumps(res, indent=1))
main()
