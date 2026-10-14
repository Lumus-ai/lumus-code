"""Hands providers for Lumus Code (stdlib only).

Any OpenAI-compatible API can be a provider. Settings live in <config>/lumus-demo/providers.json,
each key in its own file <config>/lumus-demo/keys/<id>.key. Dirs are 0700, files 0600. A key is read
from the page once, written to its file and never sent back to the page, never logged.
The active provider is applied to LUMUS_HANDS_URL / LUMUS_HANDS_MODEL / LUMUS_HANDS_KEY_FILE.
CLI:  lumus_providers.py has-active   (exit 0 when a provider was picked in the UI)
"""
import json, os, re, sys, urllib.request, urllib.error

CATALOG = [
    {"id": "gemini", "name": "Google Gemini", "url": "https://generativelanguage.googleapis.com/v1beta/openai", "needs_key": True,
     "model": "gemini-3.1-flash-lite", "hint": "Бесплатный ключ за минуту, рекомендуем", "key_url": "https://aistudio.google.com/apikey", "recommended": True},
    {"id": "openrouter", "name": "OpenRouter", "url": "https://openrouter.ai/api/v1", "needs_key": True,
     "model": "", "hint": "Много моделей, есть бесплатные", "key_url": "https://openrouter.ai/keys"},
    {"id": "groq", "name": "Groq", "url": "https://api.groq.com/openai/v1", "needs_key": True, "model": "", "hint": "Быстрые открытые модели", "key_url": "https://console.groq.com/keys"},
    {"id": "cerebras", "name": "Cerebras", "url": "https://api.cerebras.ai/v1", "needs_key": True, "model": "", "hint": "Быстрые открытые модели", "key_url": "https://cloud.cerebras.ai/"},
    {"id": "ollama", "name": "Ollama (локально)", "url": "http://127.0.0.1:11434/v1", "needs_key": False, "model": "",
     "hint": "Модели на этом компьютере, ключ не нужен"},
]
CUSTOM_PREFIX = "custom-"

def cfg_dir():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "lumus-demo")

def friendly(code=None, what="провайдер"):
    """Plain Russian text for an API failure (no response bodies, they can carry secrets)."""
    m = {401: "Ключ не подходит. Проверь, что скопировал его целиком, без пробелов.", 403: "Ключ не подходит или у него нет доступа к этой модели.",
         404: "Такой модели или адреса нет. Выбери модель из списка.", 429: "Бесплатный лимит на сейчас исчерпан. Подожди минуту и попробуй снова.",
         400: "Провайдер не принял запрос. Попробуй другую модель."}
    if code in m: return m[code]
    if code and 500 <= code < 600: return "У провайдера сейчас сбой. Попробуй чуть позже."
    return "Нет связи с провайдером. Проверь интернет и адрес."

def _json_path(): return os.path.join(cfg_dir(), "providers.json")
def key_path(pid): return os.path.join(cfg_dir(), "keys", pid + ".key")

def _mkdir(p): os.makedirs(p, mode=0o700, exist_ok=True); os.chmod(p, 0o700)

def _write600(path, text):
    _mkdir(os.path.dirname(path))
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f: f.write(text)
    os.chmod(tmp, 0o600); os.replace(tmp, path)

def load():
    try:
        with open(_json_path()) as f: d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("providers"), dict): return d
    except (OSError, ValueError): pass
    return {"providers": {}, "active": None}

def save(d): _write600(_json_path(), json.dumps(d, ensure_ascii=False, indent=1) + "\n")

def has_key(pid):
    try: return os.path.getsize(key_path(pid)) > 0
    except OSError: return False

def _slug(s):
    import hashlib
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:30] or hashlib.sha1(s.encode()).hexdigest()[:8]

def check_url(url):
    if not re.match(r"^https?://[^\s/]+", url or ""): raise ValueError("Адрес должен начинаться с https:// (или http://127.0.0.1 для локальных)")
    if url.startswith("http://") and not re.match(r"^http://(127\.0\.0\.1|localhost)(:\d+)?(/|$)", url):
        raise ValueError("Чужие адреса только по https://")
    return url.rstrip("/")

def public(d=None):
    d = d or load(); out = []
    cat = {c["id"]: c for c in CATALOG}
    for pid, p in d["providers"].items():
        needs = p.get("needs_key", True)
        out.append({"id": pid, "name": p["name"], "url": p["url"], "model": p.get("model", ""), "models": p.get("models", [])[:300],
                    "needs_key": needs, "key_set": has_key(pid) if needs else True, "custom": pid.startswith(CUSTOM_PREFIX)})
    return {"catalog": [c for c in CATALOG if c["id"] not in d["providers"]], "connected": out, "active": d.get("active"), "ollama": detect_ollama()}

def connect(pid, key, name=None, url=None, model=None):
    d = load(); cat = {c["id"]: c for c in CATALOG}
    key = (key or "").strip()
    if len(key) > 4096 or "\n" in key or "\r" in key: raise ValueError("Ключ выглядит неверно")
    if pid in cat:
        c = cat[pid]; entry = {"name": c["name"], "url": c["url"], "needs_key": c["needs_key"], "model": model or c["model"], "models": []}
    else:
        name = (name or "").strip()[:60]
        if not name: raise ValueError("Укажи название провайдера")
        pid = CUSTOM_PREFIX + _slug(name); entry = {"name": name, "url": check_url(url), "needs_key": bool(key), "model": model or "", "models": []}
    if entry["needs_key"] and not key and not has_key(pid): raise ValueError("Вставь ключ")
    if key: _write600(key_path(pid), key + "\n")
    old = d["providers"].get(pid)
    if old: entry["models"] = old.get("models", []); entry["model"] = model or old.get("model", entry["model"])
    d["providers"][pid] = entry
    if not d.get("active") and entry["model"]: d["active"] = {"id": pid, "model": entry["model"]}
    save(d); return pid

def disconnect(pid):
    d = load()
    if pid not in d["providers"]: raise ValueError("Такого провайдера нет")
    del d["providers"][pid]
    if (d.get("active") or {}).get("id") == pid: d["active"] = None
    try: os.remove(key_path(pid))
    except OSError: pass
    save(d)

def _auth_headers(pid):
    try:
        with open(key_path(pid)) as f: k = f.read().strip()
    except OSError: k = ""
    return {"authorization": "Bearer " + k} if k else {}

def list_models(pid, timeout=25):
    d = load(); p = d["providers"].get(pid)
    if not p: raise ValueError("Такого провайдера нет")
    req = urllib.request.Request(p["url"].rstrip("/") + "/models", headers=_auth_headers(pid))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: raw = json.load(r)
    except urllib.error.HTTPError as e: raise ValueError(friendly(401 if e.code == 400 else e.code))
    except (urllib.error.URLError, OSError, ValueError): raise ValueError(friendly(None))
    items = raw.get("data", raw.get("models", [])) if isinstance(raw, dict) else raw
    out = []
    for m in items if isinstance(items, list) else []:
        if not isinstance(m, dict) or not m.get("id") and not m.get("name"): continue
        mid = re.sub(r"^models/", "", str(m.get("id") or m.get("name")))
        pr = m.get("pricing") if isinstance(m.get("pricing"), dict) else None
        free = None
        if pr is not None and "prompt" in pr: free = str(pr.get("prompt")) in ("0", "0.0") and str(pr.get("completion", "0")) in ("0", "0.0")
        out.append({"id": mid, "free": free})
    out.sort(key=lambda m: m["id"]); out = out[:400]
    p["models"] = out; save(d); return out

def select(pid, model):
    d = load()
    if pid not in d["providers"]: raise ValueError("Такого провайдера нет")
    model = (model or "").strip()
    if not model or len(model) > 200 or re.search(r"\s", model): raise ValueError("Укажи модель")
    d["providers"][pid]["model"] = model; d["active"] = {"id": pid, "model": model}; save(d); apply_env(d); return d

def test(pid, model, chat):
    d = load(); p = d["providers"].get(pid)
    if not p: raise ValueError("Такого провайдера нет")
    model = (model or p.get("model") or "").strip()
    if not model: raise ValueError("Сначала выбери модель")
    try: text = chat(p["url"], model, key_path(pid) if p.get("needs_key", True) else None, "Reply with the single word: ok", 45)
    except SystemExit as e:
        m = re.search(r"HTTP (\d+)", str(e.code)); raise ValueError(friendly(int(m.group(1)) if m else None))
    return (text or "").strip()[:80]

def pick_model(models, current=""):
    """Easiest sensible default: keep current, else first free model, else a 'flash/instruct/llama' one, else first."""
    ids = [m["id"] for m in models]
    if current and current in ids: return current
    free = [m["id"] for m in models if m.get("free")]
    pool = free or ids
    for kw in ("flash", "instruct", "llama", "gemma", "qwen"):
        for i in pool:
            if kw in i.lower() and "vision" not in i.lower() and "embed" not in i.lower(): return i
    return pool[0] if pool else ""

def setup(pid, key, chat, model=None, name=None, url=None):
    """One click: save key, find a model, test it, make it active. Returns {ok, id, model, reply|error}."""
    was = set(load()["providers"]); pid = connect(pid, key, name, url, model)
    d = load(); p = d["providers"][pid]; res = {"id": pid, "name": p["name"]}
    def fail(msg, bad_key=False):
        if bad_key and pid not in was: disconnect(pid)    # do not keep a new entry with a key that does not work
        res.update(ok=False, error=msg); return res
    lerr = ""
    try: models = list_models(pid) if not model else []
    except ValueError as e: models = []; lerr = str(e)
    if lerr and lerr.startswith("Ключ"): return fail(lerr, True)
    m = (model or "").strip() or pick_model(models, p.get("model", ""))
    if not m: return fail(lerr or "Ключ сохранён, но модель выбрать не вышло. Открой «Настройки → Провайдеры» и вставь название модели.")
    try: reply = test(pid, m, chat)
    except ValueError as e: return dict(fail(str(e), str(e).startswith("Ключ")), model=m)
    select(pid, m); res.update(ok=True, model=m, reply=reply); return res

def detect_ollama(timeout=0.4):
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=timeout) as r: n = len(json.load(r).get("models", []))
        return {"found": True, "models": n}
    except Exception: return {"found": False, "models": 0}

def apply_env(d=None):
    d = d or load(); a = d.get("active")
    if not a or a.get("id") not in d["providers"]: return False
    p = d["providers"][a["id"]]
    os.environ["LUMUS_HANDS_URL"] = p["url"]; os.environ["LUMUS_HANDS_MODEL"] = a["model"]
    os.environ["LUMUS_HANDS_KEY_FILE"] = key_path(a["id"]) if p.get("needs_key", True) else ""
    return True

if __name__ == "__main__":
    if sys.argv[1:] == ["has-active"]: d = load(); sys.exit(0 if d.get("active") and d["active"].get("id") in d["providers"] else 1)
    print(__doc__)
