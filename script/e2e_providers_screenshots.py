"""Real-Chrome end-to-end of providers + first-run wizard (playwright + local Chrome).
A fake local server plays 'Ollama' on 127.0.0.1:11434 (so auto-detect is real) and a second fake plays a custom OpenAI-compatible provider.
Walks: first run -> wizard auto-opens -> pick detected Ollama -> connect+test -> run a task with the provider -> settings Providers -> add custom -> load models -> switch -> narrow wizard.
usage: python3 e2e_providers_screenshots.py OUTDIR   (needs google-chrome/chromium, `pip install playwright`, port 11434 free)"""
import json, os, shutil, subprocess, sys, tempfile, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
HERE = os.path.dirname(os.path.abspath(__file__)); out = sys.argv[1] if len(sys.argv) > 1 else "shots"; os.makedirs(out, exist_ok=True)
CODE = ["def add(a, b):\n    return a + b\n"]; calls = {"hands": 0, "test": 0}
def mk(ctx_key):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _j(self, o, code=200):
            b = json.dumps(o).encode(); self.send_response(code); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
        def do_GET(self):
            if ctx_key and self.headers.get("authorization") != "Bearer " + ctx_key: return self._j({}, 401)
            if self.path.endswith("/api/tags"): return self._j({"models": [{"name": "qwen2.5-coder"}]})
            self._j({"data": [{"id": "big-paid", "pricing": {"prompt": "0.01", "completion": "0.01"}}, {"id": "mini-free-instruct", "pricing": {"prompt": "0", "completion": "0"}}]} if ctx_key else {"data": [{"id": "qwen2.5-coder"}]})
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if ctx_key and self.headers.get("authorization") != "Bearer " + ctx_key: return self._j({}, 401)
            prompt = body["messages"][0]["content"]
            if prompt.startswith("Reply with the single word"): calls["test"] += 1; return self._j({"choices": [{"message": {"content": "ok"}}]})
            calls["hands"] += 1; self._j({"choices": [{"message": {"content": "### FILE: add.py\n```python\n%s```" % CODE[0]}}]})
    return H
o = ThreadingHTTPServer(("127.0.0.1", 11434), mk(None)); threading.Thread(target=o.serve_forever, daemon=True).start()
KEY = "sk-e2e-secret-key"; c = ThreadingHTTPServer(("127.0.0.1", 0), mk(KEY)); threading.Thread(target=c.serve_forever, daemon=True).start()
proj = tempfile.mkdtemp(); os.makedirs(proj + "/tests"); cfg = tempfile.mkdtemp()
open(proj + "/tests/test_add.py", "w").write("import sys, os; sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))\nimport unittest\nfrom add import add\nclass T(unittest.TestCase):\n    def test_add(self): self.assertEqual(add(2, 3), 5)\n")
env = {k: v for k, v in os.environ.items() if not k.startswith("LUMUS_HANDS")}; env.update(PYTHONDONTWRITEBYTECODE="1", XDG_CONFIG_HOME=cfg)
ui = subprocess.Popen([sys.executable, os.path.join(HERE, "lumus-loop.py"), "ui", "--port", "8792", "--dir", proj, "--no-open"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
BRAIN = "## PLAN\nadd(a,b) returns the sum\n\n## HANDS TASK\nCreate add.py with add(a, b) returning a + b.\n\n## STATUS\nDONE"
from playwright.sync_api import sync_playwright
try:
    for _ in range(40):
        try: urllib.request.urlopen("http://127.0.0.1:8792/api/state", timeout=1); break
        except OSError: time.sleep(0.25)
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=shutil.which("google-chrome") or shutil.which("chromium"), args=["--no-sandbox"])
        pg = b.new_context(viewport={"width": 1360, "height": 860}).new_page(); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e))); pg.on("console", lambda m: m.type == "error" and "401" not in m.text and errs.append(m.text))
        shot = lambda name, page=None: ((page or pg).wait_for_timeout(350), (page or pg).screenshot(path=os.path.join(out, name)))
        pg.goto("http://127.0.0.1:8792/"); pg.wait_for_selector("text=Давай подключим модель"); pg.wait_for_selector("text=Найдена на этом компьютере"); shot("p1-wizard-dark.png")
        pg.click("[data-wz=ollama]"); pg.wait_for_selector("[data-wzgo]"); shot("p2-wizard-step2-dark.png")
        pg.click("[data-wzgo]"); pg.wait_for_selector("text=Готово ✓"); shot("p3-wizard-done-dark.png")
        assert calls["test"] == 1, calls
        pg.click("[data-wzclose]"); pg.wait_for_function("document.body.innerText.includes('Руки: Ollama')")
        pg.click("#tgManual") if "Авто" in pg.inner_text("#tgManual") else None
        pg.click("[data-fill]"); pg.fill("#cin", "Сделай add.py: функция add(a, b) возвращает сумму."); pg.keyboard.press("Enter")
        pg.wait_for_selector("#brainReply"); pg.fill("#brainReply", BRAIN); pg.click("#sendReply")
        pg.wait_for_selector("text=Подтверди действие"); pg.click("[data-dec=allow]"); pg.wait_for_selector("text=Готово:"); assert calls["hands"] == 1, calls
        pg.click("#chipHands"); pg.wait_for_selector("text=Подключено"); shot("p4-providers-dark.png")
        pg.click("[data-popen=custom]"); pg.fill("#pcn", "Мой сервер"); pg.fill("#pcu", "http://127.0.0.1:%d" % c.server_address[1]); pg.fill("#pck", KEY); pg.click("[data-pconn=custom]")
        pg.wait_for_selector("text=Мой сервер"); pg.click("[data-pid=custom-moy-server] [data-pact=models]") if pg.query_selector("[data-pid=custom-moy-server]") else None
        pg.wait_for_selector("text=Моделей: 2"); pg.click("[data-pvfree]"); shot("p5-providers-models-dark.png")
        pg.fill("[data-pm=custom-moy-server]", "mini-free-instruct"); pg.click("[data-pid=custom-moy-server] [data-pact=test]"); pg.wait_for_selector("text=Ответ модели: ok")
        pg.click("[data-pid=custom-moy-server] [data-pact=select]"); pg.wait_for_selector("text=Активный: mini-free-instruct"); shot("p6-providers-switched-dark.png")
        st = json.load(urllib.request.urlopen("http://127.0.0.1:8792/api/state")); assert st["hands"]["provider"] == "Мой сервер" and st["hands"]["model"] == "mini-free-instruct", st["hands"]
        pg.click(".seg [data-theme=light]") if pg.query_selector(".seg [data-theme=light]") else None
        pg.click("#mclose")
        sp = os.path.join(cfg, "lumus-demo"); allfiles = [os.path.join(r, f) for r, _, fs in os.walk(sp) for f in fs]
        assert all(oct(os.stat(f).st_mode & 0o777) == "0o600" for f in allfiles), [(f, oct(os.stat(f).st_mode)) for f in allfiles]
        assert KEY not in json.dumps(st) and KEY not in pg.content(), "key leaked to page"
        nctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2); np_ = nctx.new_page()
        o.shutdown(); o.server_close()   # nothing detected now, wizard falls back to key providers
        np_.goto("http://127.0.0.1:8792/"); np_.wait_for_selector("#cin"); np_.click("#menu"); np_.click("#openSet"); np_.wait_for_selector(".dlg"); np_.click("[data-tab='2']"); np_.wait_for_selector("text=Подключено"); np_.wait_for_timeout(300)
        np_.screenshot(path=os.path.join(out, "p7-narrow-providers.png")); np_.click("[data-wzopen]"); np_.wait_for_selector("text=Давай подключим модель"); np_.wait_for_timeout(300); np_.screenshot(path=os.path.join(out, "p8-narrow-wizard.png"))
        print("JS errors:", errs or "none", "| calls:", calls, "| files 0600:", len(allfiles))
        assert not errs
finally:
    ui.terminate(); shutil.rmtree(proj, ignore_errors=True); shutil.rmtree(cfg, ignore_errors=True)
