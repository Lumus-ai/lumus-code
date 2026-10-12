#!/usr/bin/env python3
"""Minimal external-agent client for a Lumus Code room (stdlib only).

  lumus-guest.py join   <base-url> <join-code> [name]      -> prints room id and token (saved to ~/.lumus-guest.json)
  lumus-guest.py read   [count]                            -> last messages of the shared session
  lumus-guest.py answer <text>                             -> message-only post, never starts the host's model
  lumus-guest.py result <tool> <text>
  lumus-guest.py command <shell command> [reason]          -> host must allow it
  lumus-guest.py edit   <relative path> <content-file> [reason]   -> host must allow it
"""
import json, os, sys, urllib.request, urllib.error

STATE = os.path.expanduser("~/.lumus-guest.json")

def call(method, url, body=None, token=None):
    req = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode())
    req.add_header("content-type", "application/json")
    if token:
        req.add_header("authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")

def state():
    with open(STATE) as f:
        return json.load(f)

def post(payload):
    s = state()
    code, data = call("POST", f"{s['base']}/api/room/{s['room']}/guest/external", payload, s["token"])
    print(code, json.dumps(data) if not isinstance(data, str) else data)
    sys.exit(0 if code == 200 else 1)

def main(a):
    if not a:
        print(__doc__); sys.exit(2)
    cmd = a[0]
    if cmd == "join":
        base, code = a[1].rstrip("/"), a[2]
        name = a[3] if len(a) > 3 else "Instinct"
        st, data = call("POST", base + "/api/room/join", {"code": code, "name": name, "external": True})
        if st != 200:
            print(st, data); sys.exit(1)
        d = data.get("data", data)
        # field names are printed, not guessed: keep the whole reply except the token on screen
        token = d.get("token") or d.get("guest", {}).get("token")
        room = (d.get("room") or {}).get("id") or d.get("roomID")
        with open(STATE, "w") as f:
            json.dump({"base": base, "room": room, "token": token}, f)
        os.chmod(STATE, 0o600)
        print("joined room", room, "as", name, "(external); token saved to", STATE)
    elif cmd == "read":
        s = state()
        st, data = call("GET", f"{s['base']}/api/room/{s['room']}/guest/message", None, s["token"])
        print(st, json.dumps(data)[-int(a[1]) if len(a) > 1 else -4000:])
    elif cmd == "answer":
        post({"kind": "answer", "text": a[1]})
    elif cmd == "result":
        post({"kind": "tool_result", "tool": a[1], "text": a[2]})
    elif cmd == "command":
        post({"kind": "command", "command": a[1], "text": a[2] if len(a) > 2 else None})
    elif cmd == "edit":
        post({"kind": "edit", "path": a[1], "content": open(a[2]).read(), "text": a[3] if len(a) > 3 else None})
    else:
        print(__doc__); sys.exit(2)

main(sys.argv[1:])
