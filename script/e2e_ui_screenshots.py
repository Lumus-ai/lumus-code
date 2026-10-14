"""Real-Chrome end-to-end of the chat UI (playwright + local Chrome). Brain = manual mode (typed into the page like a user), hands = local fake API.
Walks: empty chat -> send task -> manual brain card -> confirm card -> allow in chat -> failed round -> 2nd brain reply -> pass -> report; themes; narrow; settings.
usage: python3 e2e_chat_screenshots.py OUTDIR   (needs google-chrome/chromium and `pip install playwright`)"""
import json, os, shutil, subprocess, sys, tempfile, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
HERE = os.path.dirname(os.path.abspath(__file__)); out = sys.argv[1] if len(sys.argv) > 1 else "shots"; os.makedirs(out, exist_ok=True)
CODE = ["def add(a, b):\n    return a - b\n", "def add(a, b):\n    return a + b\n"]; n = [0]
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"])); c = CODE[min(n[0], 1)]; n[0] += 1
        b = json.dumps({"choices": [{"message": {"content": "### FILE: add.py\n```python\n%s```" % c}}]}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
hs = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=hs.serve_forever, daemon=True).start()
proj = tempfile.mkdtemp(); os.makedirs(proj + "/tests")
open(proj + "/tests/test_add.py", "w").write("import sys, os; sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))\nimport unittest\nfrom add import add\nclass T(unittest.TestCase):\n    def test_add(self): self.assertEqual(add(2, 3), 5)\n    def test_neg(self): self.assertEqual(add(-1, 1), 0)\n")
kf = tempfile.mktemp(); open(kf, "w").write("fake\n")
env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "LUMUS_HANDS_URL": "http://127.0.0.1:%d" % hs.server_address[1], "LUMUS_HANDS_MODEL": "gemini-3.1-flash-lite", "LUMUS_HANDS_KEY_FILE": kf}
ui = subprocess.Popen([sys.executable, os.path.join(HERE, "lumus-loop.py"), "ui", "--port", "8791", "--dir", proj, "--no-open"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
BRAIN = "## PLAN\nadd(a,b) returns the sum\n\n## HANDS TASK\nCreate add.py with add(a, b) returning a + b.\n\n## STATUS\n%s"
from playwright.sync_api import sync_playwright
try:
    for _ in range(40):
        try: urllib.request.urlopen("http://127.0.0.1:8791/api/state", timeout=1); break
        except OSError: time.sleep(0.25)
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=shutil.which("google-chrome") or shutil.which("chromium"), args=["--no-sandbox"])
        ctx = b.new_context(viewport={"width": 1360, "height": 860}); pg = ctx.new_page(); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e))); pg.on("console", lambda m: m.type == "error" and errs.append(m.text))
        shot = lambda name: (pg.wait_for_timeout(350), pg.screenshot(path=os.path.join(out, name)))
        pg.goto("http://127.0.0.1:8791/"); pg.wait_for_selector("text=С чего"); pg.wait_for_function("document.querySelector('#tgCmd').textContent.includes('unittest')"); shot("01-empty-dark.png")
        pg.click("#tgManual") if "Авто" in pg.inner_text("#tgManual") else None
        pg.click("[data-fill]"); pg.fill("#cin", "Сделай add.py: функция add(a, b) возвращает сумму. Тесты лежат в tests/."); pg.keyboard.press("Enter")
        pg.wait_for_selector("#brainReply"); shot("02-manual-brain-dark.png")
        pg.fill("#brainReply", BRAIN % "CONTINUE"); pg.click("#sendReply")
        pg.wait_for_selector("text=Записать файлы в проект"); shot("03a-files-confirm-dark.png"); pg.click("[data-dec=allow]")
        pg.wait_for_selector("text=Подтверди действие"); shot("03-confirm-dark.png")
        pg.click("[data-dec=allow-chat]")
        pg.wait_for_selector("text=Тесты ✕"); pg.wait_for_selector("#brainReply"); shot("04-round2-wait-dark.png")
        pg.fill("#brainReply", BRAIN % "DONE"); pg.click("#sendReply")
        pg.wait_for_selector("text=Записать файлы в проект"); pg.wait_for_selector(".diff .dd"); shot("04b-files-diff-dark.png"); pg.click("[data-dec=allow-chat]")
        pg.wait_for_selector("text=Готово:"); shot("05-passed-dark.png")
        pg.click("#showRep"); pg.wait_for_selector("text=Отчёт"); shot("06-report-dark.png"); pg.click("#repClose")
        pg.click("#openSet"); pg.wait_for_selector(".dlg"); shot("07-settings-dark.png")
        pg.click(".tc[data-theme=light]"); shot("08-settings-light.png"); pg.click("#mclose"); shot("09-chat-light.png")
        pg.click(".seg [data-theme=min]"); shot("10-chat-min.png")
        pg.click("#newChat"); shot("11-empty-min.png")
        pg.click(".seg [data-theme=dark]")
        nctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2); np_ = nctx.new_page(); np_.goto("http://127.0.0.1:8791/"); np_.wait_for_selector("#cin"); np_.wait_for_timeout(400)
        np_.screenshot(path=os.path.join(out, "12-narrow-chat-dark.png")); np_.click("#menu"); np_.wait_for_timeout(400); np_.screenshot(path=os.path.join(out, "13-narrow-drawer-dark.png"))
        np_.click("#scrim", position={"x": 350, "y": 400}); np_.click("#openSet"); np_.wait_for_selector(".dlg"); np_.wait_for_timeout(300); np_.screenshot(path=os.path.join(out, "14-narrow-settings.png"))
        print("JS errors:", errs or "none")
        s = json.load(urllib.request.urlopen("http://127.0.0.1:8791/api/state")); print("result:", s["result"], "| hands calls:", n[0], "| rounds exit:", [r["exit"] for r in s["rounds"]])
        assert s["result"] == "PASSED in round 2" and not errs
finally:
    ui.terminate(); hs.shutdown(); shutil.rmtree(proj, ignore_errors=True)
