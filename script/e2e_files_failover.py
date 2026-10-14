"""Real-Chrome end-to-end: file confirm card with diff (deny, then allow) and provider failover (A answers 429, B answers).
Brain = manual mode. Providers are two fake local OpenAI-compatible servers added as custom providers through the page's API.
usage: python3 e2e_files_failover.py OUTDIR   (needs google-chrome/chromium and `pip install playwright`)"""
import json, os, shutil, subprocess, sys, tempfile, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
HERE = os.path.dirname(os.path.abspath(__file__)); out = sys.argv[1] if len(sys.argv) > 1 else "shots"; os.makedirs(out, exist_ok=True)
hits = {"A": 0, "B": 0}
def mk(name, code):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self): self.send_response(200); b = b'{"data":[{"id":"m"}]}'; self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"])); hits[name] += 1
            b = json.dumps({"choices": [{"message": {"content": "### FILE: add.py\n```python\ndef add(a, b):\n    return a + b\n```"}}]} if code == 200 else {"error": "limit"}).encode()
            self.send_response(code); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    s = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=s.serve_forever, daemon=True).start(); return s
A, B = mk("A", 429), mk("B", 200)
proj = tempfile.mkdtemp(); os.makedirs(proj + "/tests"); cfg = tempfile.mkdtemp()
open(proj + "/add.py", "w").write("def add(a, b):\n    return a - b\n")
open(proj + "/tests/test_add.py", "w").write("import sys, os; sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))\nimport unittest\nfrom add import add\nclass T(unittest.TestCase):\n    def test_add(self): self.assertEqual(add(2, 3), 5)\n")
env = {k: v for k, v in os.environ.items() if not k.startswith("LUMUS_HANDS")}; env.update(PYTHONDONTWRITEBYTECODE="1", XDG_CONFIG_HOME=cfg, LUMUS_MODELS_DEV_URL="off")
ui = subprocess.Popen([sys.executable, os.path.join(HERE, "lumus-loop.py"), "ui", "--port", "8793", "--dir", proj, "--no-open"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
BASE = "http://127.0.0.1:8793"
def api(p, body=None):
    h = {"Content-Type": "application/json", "X-Lumus-UI": "1"}
    return json.load(urllib.request.urlopen(urllib.request.Request(BASE + p, data=None if body is None else json.dumps(body).encode(), headers=h), timeout=20))
BRAIN = "## PLAN\nfix add\n\n## HANDS TASK\nFix add.py so add(a, b) returns a + b.\n\n## STATUS\nDONE"
from playwright.sync_api import sync_playwright
try:
    for _ in range(40):
        try: urllib.request.urlopen(BASE + "/api/state", timeout=1); break
        except OSError: time.sleep(0.25)
    for nm, srv in (("Провайдер A (лимит)", A), ("Провайдер B (запасной)", B)):
        r = api("/api/providers/connect", {"id": "custom", "name": nm, "url": "http://127.0.0.1:%d" % srv.server_address[1], "key": "", "model": "m"})
    ids = [c["id"] for c in r["connected"]]; api("/api/providers/select", {"id": ids[0], "model": "m"}); r = api("/api/providers/fallback", {"id": ids[1], "op": "add"})
    assert r["fallbacks"] == [ids[1]]
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=shutil.which("google-chrome") or shutil.which("chromium"), args=["--no-sandbox"])
        pg = b.new_context(viewport={"width": 1360, "height": 860}).new_page(); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e))); pg.on("console", lambda m: m.type == "error" and errs.append(m.text))
        shot = lambda name: (pg.wait_for_timeout(350), pg.screenshot(path=os.path.join(out, name)))
        pg.goto(BASE + "/"); pg.wait_for_selector("text=С чего"); pg.wait_for_function("document.querySelector('#tgCmd').textContent.includes('unittest')")
        pg.click("#tgManual") if "Авто" in pg.inner_text("#tgManual") else None
        pg.click("#openSet"); pg.wait_for_selector(".dlg"); pg.click("text=Провайдеры"); pg.wait_for_selector("text=Резерв 1"); shot("f1-providers-chain-dark.png"); pg.click("#mclose")
        pg.fill("#cin", "Почини add.py: add(a, b) должна возвращать сумму. Тесты в tests/."); pg.keyboard.press("Enter")
        pg.wait_for_selector("#brainReply"); pg.fill("#brainReply", BRAIN); pg.click("#sendReply")
        pg.wait_for_selector("text=Записать файлы в проект"); pg.wait_for_selector(".diff .dd"); shot("f2-files-diff-failover-dark.png")
        assert "return a - b" in open(proj + "/add.py").read(), "file changed before the answer"
        pg.click("[data-dec=deny]"); pg.wait_for_selector("text=Запись файлов отклонена"); shot("f3-denied-dark.png")
        assert "return a - b" in open(proj + "/add.py").read(), "file changed after deny"
        hits_after_deny = dict(hits)
        pg.click("#resume"); pg.wait_for_selector("text=Записать файлы в проект"); pg.click("[data-dec=allow]")
        pg.wait_for_selector("text=Подтверди действие"); assert "return a + b" in open(proj + "/add.py").read()
        pg.click("[data-dec=allow]"); pg.wait_for_selector("text=Готово:"); shot("f4-passed-dark.png")
        s = api("/api/state"); print("result:", s["result"], "| hands_by:", s["rounds"][0]["hands_by"], "| hits:", hits, "| after deny:", hits_after_deny)
        print("log:", [l for l in s["log"] if "hands:" in l]); print("JS errors:", errs or "none")
        assert s["result"] == "PASSED in round 1" and s["rounds"][0]["hands_by"].startswith("Провайдер B") and hits["A"] >= 1 and hits["B"] >= 1 and not errs
finally:
    ui.terminate(); A.shutdown(); B.shutdown(); shutil.rmtree(proj, ignore_errors=True); shutil.rmtree(cfg, ignore_errors=True)
