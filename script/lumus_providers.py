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

MD_URL = "https://models.dev/api.json"          # the same public list OpenCode uses (LUMUS_MODELS_DEV_URL overrides it, e.g. for tests)
POPULAR = ["opencode", "anthropic", "github-copilot", "openai", "google", "openrouter", "vercel"]
URL_MAP = {   # providers that models.dev lists without an OpenAI-compatible URL; official OpenAI-compatible endpoints
    "openai": "https://api.openai.com/v1", "google": "https://generativelanguage.googleapis.com/v1beta/openai",
    "anthropic": "https://api.anthropic.com/v1", "groq": "https://api.groq.com/openai/v1", "cerebras": "https://api.cerebras.ai/v1",
    "mistral": "https://api.mistral.ai/v1", "xai": "https://api.x.ai/v1", "togetherai": "https://api.together.xyz/v1",
    "perplexity": "https://api.perplexity.ai", "cohere": "https://api.cohere.ai/compatibility/v1",
    "deepinfra": "https://api.deepinfra.com/v1/openai", "vercel": "https://ai-gateway.vercel.sh/v1"}
KEY_URLS = {"gemini": "https://aistudio.google.com/apikey", "google": "https://aistudio.google.com/apikey", "openrouter": "https://openrouter.ai/keys",
    "groq": "https://console.groq.com/keys", "cerebras": "https://cloud.cerebras.ai/", "openai": "https://platform.openai.com/api-keys",
    "anthropic": "https://console.anthropic.com/settings/keys", "deepseek": "https://platform.deepseek.com/api_keys",
    "nvidia": "https://build.nvidia.com/settings/api-keys", "mistral": "https://console.mistral.ai/api-keys", "xai": "https://console.x.ai/",
    "togetherai": "https://api.together.ai/settings/api-keys", "huggingface": "https://huggingface.co/settings/tokens", "opencode": "https://opencode.ai/auth",
    "vercel": "https://vercel.com/dashboard/ai-gateway"}
NOTES = {"anthropic": "через OpenAI-совместимый режим, не проверено"}
_md = {"t": 0.0, "d": None, "src": "builtin"}

def md_load():
    import time
    if _md["d"] is not None and time.time() - _md["t"] < 600: return _md["d"]
    path = os.path.join(cfg_dir(), "models-dev.json"); url = os.environ.get("LUMUS_MODELS_DEV_URL", MD_URL); data = None; src = "builtin"
    try:
        if time.time() - os.path.getmtime(path) < 86400:
            with open(path) as f: data = json.load(f); src = "cache"
    except (OSError, ValueError): pass
    if data is None and url != "off":
        try:
            with urllib.request.urlopen(url, timeout=12) as r: raw = r.read(40_000_000)
            data = json.loads(raw); src = "models.dev"
            _write600(path, raw.decode("utf-8"))
        except Exception: data = None
    if data is None:
        try:
            with open(path) as f: data = json.load(f); src = "cache (устарел)"
        except (OSError, ValueError): data = None
    if not isinstance(data, dict): data = None
    _md.update(t=time.time(), d=data, src=src if data else "builtin"); return data

def _free(m):
    c = m.get("cost")
    return None if not isinstance(c, dict) or "input" not in c else (c.get("input") == 0 and c.get("output", 0) == 0)

def md_models(mid):
    md = md_load(); p = (md or {}).get(mid) or {}
    out = [{"id": m.get("id", k), "name": m.get("name", k), "ctx": (m.get("limit") or {}).get("context", 0), "free": _free(m)} for k, m in (p.get("models") or {}).items() if isinstance(m, dict)]
    out.sort(key=lambda m: (m["free"] is not True, m["name"].lower())); return out[:600]

def entries():
    md = md_load(); out = []
    local = {"id": "ollama", "name": "Ollama (локально)", "url": "http://127.0.0.1:11434/v1", "needs_key": False, "supported": True, "note": "", "popular": False,
             "models_n": 0, "free_n": 0, "key_url": "", "doc": "https://ollama.com", "env": "", "model": ""}
    if not md:
        for c in CATALOG:
            e = dict(c, supported=True, note="", popular=c["id"] in POPULAR, models_n=0, free_n=0, doc="", env="")
            if c["id"] != "ollama": out.append(e)
        out.append(local); return out
    for k, p in md.items():
        if not isinstance(p, dict): continue
        url = URL_MAP.get(k) or p.get("api") or ""; npm = str(p.get("npm", "")); sup, note = True, NOTES.get(k, "")
        if k == "github-copilot": sup, note = False, "вход через GitHub, пока не поддерживается"
        elif any(x in npm for x in ("bedrock", "vertex", "azure")): sup, note = False, "облачный аккаунт, пока не поддерживается"
        elif not url: sup, note = False, "нет адреса API: добавь как свой провайдер"
        ms = [m for m in (p.get("models") or {}).values() if isinstance(m, dict)]
        loc = url.startswith("http://127.0.0.1") or url.startswith("http://localhost")
        out.append({"id": k, "name": str(p.get("name", k)), "url": url, "needs_key": not loc, "supported": sup, "note": note, "popular": k in POPULAR,
                    "models_n": len(ms), "free_n": sum(1 for m in ms if _free(m) is True), "key_url": KEY_URLS.get(k, ""), "doc": str(p.get("doc", "")),
                    "env": (p.get("env") or [""])[0], "model": ""})
    if not any(e["id"] == "ollama" for e in out): out.append(local)
    out.sort(key=lambda e: (POPULAR.index(e["id"]) if e["id"] in POPULAR else 999, e["name"].lower())); return out

def find_entry(pid):
    for e in entries():
        if e["id"] == pid: return e
    return None

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
    for pid, p in d["providers"].items():
        needs = p.get("needs_key", True)
        ms = p.get("models") or (md_models(p["md_id"]) if p.get("md_id") else [])
        out.append({"id": pid, "name": p["name"], "url": p["url"], "model": p.get("model", ""), "models": ms[:600],
                    "needs_key": needs, "key_set": has_key(pid) if needs else True, "custom": pid.startswith(CUSTOM_PREFIX)})
    cat = [{k: v for k, v in e.items() if k != "url"} for e in entries() if e["id"] not in d["providers"]]
    return {"catalog": cat, "connected": out, "active": d.get("active"), "ollama": detect_ollama(), "source": _md["src"]}

def connect(pid, key, name=None, url=None, model=None):
    d = load(); key = (key or "").strip(); c = None if pid == "custom" or pid.startswith(CUSTOM_PREFIX) else find_entry(pid)
    if c is not None and not c["supported"]: raise ValueError("Этот провайдер пока не поддерживается" + (": " + c["note"] if c["note"] else ""))
    if len(key) > 4096 or "\n" in key or "\r" in key: raise ValueError("Ключ выглядит неверно")
    if c is not None:
        entry = {"name": c["name"], "url": c["url"], "needs_key": c["needs_key"], "model": model or c.get("model", ""), "models": [], "md_id": pid}
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
    if p.get("md_id"):
        known = {m["id"]: m for m in md_models(p["md_id"])}
        for m in out:
            if m["free"] is None and m["id"] in known: m["free"] = known[m["id"]]["free"]; m["ctx"] = known[m["id"]]["ctx"]; m["name"] = known[m["id"]]["name"]
    out.sort(key=lambda m: (m["free"] is not True, m["id"])); out = out[:600]
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
    if not models and p.get("md_id") and not model: models = md_models(p["md_id"])
    if not model and models and not any(x.get("free") is True for x in models) and all(x.get("free") is False for x in models):
        return fail("Ключ сохранён. У этого провайдера нет бесплатных моделей, поэтому сам выбирать не стал (платные тратят деньги). Открой «Модели» у провайдера и выбери модель сам.")
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
