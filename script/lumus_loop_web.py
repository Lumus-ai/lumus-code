"""Web-chat brain bridge for lumus-loop (Playwright over CDP, localhost only).

Caveats (also in README-LOOP.md): site selectors can break when the page changes, captchas / usage
limits / login prompts need you; automating a consumer chat UI may be against that service's terms;
the debug port is bound to 127.0.0.1 only and gives full control of that Chrome profile, so close it
when not in use and use a dedicated profile (default ~/.lumus-loop-chrome), not your everyday one.
"""
import os, re, shutil, subprocess, sys, time

CDP_DEFAULT = "http://127.0.0.1:9222"
# One entry per service. Add ChatGPT / Claude / DeepSeek here later; selectors are the fragile part.
ADAPTERS = {
    "gemini": {  # verified 2026-10-14 against the signed-in web UI (Russian locale): input, send, reply, chat URL
        "new_chat": "https://gemini.google.com/app",
        "input": 'rich-textarea div[contenteditable="true"], div.ql-editor[contenteditable="true"]',
        "send": 'button[aria-label="Send message"], button[aria-label="Отправить сообщение"], button.send-button',
        "stop": 'button[aria-label*="Stop" i], button[aria-label*="Остановить"]',
        "reply": "message-content",
    },
    "chatgpt": {  # verified 2026-10-14 as a logged-out guest (mobile layout); signed-in layout uses data-message-author-role
        "new_chat": "https://chatgpt.com/",
        "input": "#prompt-textarea, #mobile-composer-prompt",
        "send": 'button[data-testid="send-button"], button[aria-label="Send message"]',
        "stop": '[data-testid="stop-button"]',
        "reply": '[data-message-author-role="assistant"], li[data-message-role="assistant"]',
    },
    "stub": {  # local test page, see tests
        "new_chat": os.environ.get("LUMUS_STUB_URL", "http://127.0.0.1:8765/"),
        "input": "#in", "send": "#send", "stop": "#stop-never", "reply": ".reply",
    },
}

MAX_PROMPT_CHARS = 40000   # seen 2026-10-14 on the logged-out ChatGPT guest page: 43.7k chars worked, 61.5k chars ended in "Unable to connect"
_PREFIX = re.compile(r"^\s*(ChatGPT said|ChatGPT сказал|You said)\s*:?\s*\n", re.I)
_ERRORS = re.compile(r"^(unable to connect|no internet|reconnecting|something went wrong|network error|retry\b|you.ve reached|you.re sending)", re.I)

def clean_reply(text):
    """Strips the accessibility prefix the ChatGPT page puts in front of a reply and rejects error / placeholder
    replies (they look like a finished reply to the stability check). Returns the cleaned text or raises ValueError."""
    t = _PREFIX.sub("", text or "", count=1).strip()
    if not t: raise ValueError("empty reply")
    if _ERRORS.match(t) and len(t) < 200: raise ValueError("the page showed an error instead of an answer: " + t.replace("\n", " ")[:80])
    return t

def find_chrome():
    """Chrome/Chromium (Edge as a last resort, it speaks the same debug protocol) on Linux, macOS and Windows."""
    for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        if shutil.which(n): return shutil.which(n)
    env = os.environ.get
    cands = []
    if sys.platform == "darwin":
        cands = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Chromium.app/Contents/MacOS/Chromium",
                 "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    elif sys.platform == "win32":
        for base in (env("PROGRAMFILES"), env("PROGRAMFILES(X86)"), env("LOCALAPPDATA")):
            if base: cands += [os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"), os.path.join(base, "Chromium", "Application", "chrome.exe"),
                               os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe")]
    for c in cands:
        if os.path.isfile(c): return c
    return None

def chrome_command(port=9222, profile=None):
    exe = find_chrome()
    if not exe: raise SystemExit("ERROR: Chrome/Chromium not found (install Google Chrome)")
    profile = profile or os.path.expanduser("~/.lumus-loop-chrome")
    return [exe, "--remote-debugging-port=%d" % port, "--remote-debugging-address=127.0.0.1", "--user-data-dir=" + profile,
            "--no-first-run", "https://chatgpt.com/"]

def start_chrome(port=9222):
    cmd = chrome_command(port)
    kw = {"creationflags": 0x00000008 | 0x00000200} if sys.platform == "win32" else {"start_new_session": True}     # detached, survives this terminal
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
    print("Chrome started with debug port 127.0.0.1:%d, profile %s.\nLog in (optional for ChatGPT guest mode, needed for Gemini) in that window once; keep it open." % (port, cmd[3].split("=", 1)[1]))

def ask(adapter, prompt, chat_url=None, cdp=CDP_DEFAULT, timeout=300, settle=4.0):
    """Send prompt in the task's chat (new chat if chat_url is None). Returns (reply_text, chat_url)."""
    if not (cdp.startswith("http://127.0.0.1:") or cdp.startswith("http://localhost:")):
        raise SystemExit("ERROR: CDP endpoint must be localhost")
    sel = ADAPTERS[adapter]
    if len(prompt) > MAX_PROMPT_CHARS:
        raise SystemExit("ERROR: prompt is %d chars, over the %d limit seen on the web chat; shrink the task or files" % (len(prompt), MAX_PROMPT_CHARS))
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("ERROR: pip install playwright (no browser download needed; it attaches to your Chrome)")
    with sync_playwright() as p:
        try: browser = p.chromium.connect_over_cdp(cdp)
        except Exception as e: raise SystemExit("ERROR: cannot reach Chrome on %s (%s); run: lumus-loop.py chrome" % (cdp, type(e).__name__))
        ctx = browser.contexts[0]
        page = ctx.new_page()
        try:
            page.goto(chat_url or sel["new_chat"], wait_until="domcontentloaded")
            box = page.locator(sel["input"]).first
            try: box.wait_for(state="visible", timeout=30000)
            except Exception: raise SystemExit("ERROR: chat input not found (not logged in, captcha, or the page changed); check the window")
            before = page.locator(sel["reply"]).count()
            box.click(); box.fill(prompt)
            page.locator(sel["send"]).first.click()
            end = time.time() + timeout; last = ""; stable_since = time.time()
            while time.time() < end:
                time.sleep(1)
                n = page.locator(sel["reply"]).count()
                text = page.locator(sel["reply"]).nth(n - 1).inner_text() if n > before else ""
                generating = page.locator(sel["stop"]).count() > 0
                if text != last: last, stable_since = text, time.time()
                elif text and not generating and time.time() - stable_since >= settle:
                    try: return clean_reply(text), page.url
                    except ValueError as e: raise SystemExit("ERROR: " + str(e) + "; use manual mode or retry later")
            raise SystemExit("ERROR: no complete reply within %ds (limit, captcha or changed page); use manual mode" % timeout)
        finally:
            page.close()
