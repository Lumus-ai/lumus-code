#!/usr/bin/env python3
"""Minimal external-agent client for a Lumus Code room (stdlib only).

  lumus-guest.py join   <base-url> <join-code> [name]      -> prints room id; token saved privately to ~/.lumus-guest.json
  lumus-guest.py doctor                                    -> read-only connection check; no messages or credentials printed
  lumus-guest.py poll                                      -> new "@instinct" lines from the host (marks them seen)
  lumus-guest.py peek                                      -> same as poll but does not mark lines seen
  lumus-guest.py read   [count]                            -> last messages of the shared session
  lumus-guest.py answer <text>                             -> message-only post, never starts the host's model
  lumus-guest.py result <tool> <text>
  lumus-guest.py prompt <text>                             -> ask the host model to do it; host must approve the full text
  lumus-guest.py wait [max-seconds]                        -> wait until the host model stops, print the newest assistant/user lines
  lumus-guest.py command <shell command> [reason]          -> host must allow it
  lumus-guest.py edit   <relative path> <content-file> [reason]   -> host must allow it
"""
import json, os, sys, urllib.request, urllib.error
from lumus_guest_transport import call, validate_base, save_state, poll_messages, diagnose

STATE = os.path.expanduser("~/.lumus-guest.json")

def state():
    with open(STATE) as f:
        return json.load(f)

import re
# Refused on this side even if the host would click allow; the agent must ask the user first.
DESTRUCTIVE = re.compile(
    r"(\brm\s+(-[a-zA-Z]*[rf]|--recursive|--force)|\bmkfs|\bdd\s+if=|\bshutdown\b|\breboot\b|:\(\)\s*\{|"
    r"chmod\s+-R|chown\s+-R|git\s+push\s+.*--force|git\s+reset\s+--hard|git\s+clean\s+-[a-z]*f|"
    r"(curl|wget)[^|]*\|\s*(sudo\s+)?(ba)?sh|>\s*/dev/sd|\bsudo\b|\bkill(all)?\s+-9|\bdrop\s+(table|database)\b)",
    re.I,
)

def post(payload):
    if payload.get("kind") == "command" and DESTRUCTIVE.search(payload.get("command") or ""):
        print("REFUSED: looks destructive; ask the user in plain words first")
        sys.exit(3)
    s = state()
    code, data = call("POST", f"{s['base']}/api/room/{s['room']}/guest/external", payload, s["token"])
    print(code, json.dumps(data) if not isinstance(data, str) else data)
    sys.exit(0 if code == 200 else 1)

def main(a):
    if not a:
        print(__doc__); sys.exit(2)
    cmd = a[0]
    if cmd == "join":
        base, code = validate_base(a[1]), a[2]
        name = a[3] if len(a) > 3 else "Instinct"
        st, data = call("POST", base + "/api/room/join", {"code": code, "name": name, "external": True})
        if st != 200:
            print(st, data); sys.exit(1)
        d = data.get("data", data) if isinstance(data, dict) else {}
        if not isinstance(d, dict):
            d = {}
        # field names are printed, not guessed: keep the whole reply except the token on screen
        guest = d.get("guest") if isinstance(d.get("guest"), dict) else {}
        joined = d.get("room") if isinstance(d.get("room"), dict) else {}
        token = d.get("token") or guest.get("token")
        room = joined.get("id") or d.get("roomID")
        if not isinstance(token, str) or not token or not isinstance(room, str) or not room:
            print("ERROR: invalid join response; previous connection kept")
            sys.exit(1)
        save_state(STATE, {"base": base, "room": room, "token": token})
        print("joined room", room, "as", name, "(external); token saved to", STATE)
    elif cmd == "doctor":
        try:
            connection = state()
        except (ValueError, OSError):
            print("ERROR: saved connection missing or unreadable; join a room first")
            sys.exit(1)
        ok, message = diagnose(connection)
        print(("OK: " if ok else "ERROR: ") + message)
        sys.exit(0 if ok else 1)
    elif cmd == "read":
        s = state()
        st, data = call("GET", f"{s['base']}/api/room/{s['room']}/guest/message", None, s["token"])
        print(st, json.dumps(data)[-int(a[1]) if len(a) > 1 else -4000:])
        sys.exit(0 if st == 200 else 1)
    elif cmd in ("poll", "peek"):
        s = state()
        st, data = call("GET", f"{s['base']}/api/room/{s['room']}/guest/message", None, s["token"])
        if st != 200:
            print("ERROR", st, str(data)[:200]); sys.exit(1)
        seen_file = os.path.expanduser("~/.lumus-guest-seen.json")
        new = poll_messages(s, data, seen_file, cmd == "poll")
        for m in new:
            print("NEW", m["id"], m["text"][len("[host to @instinct]"):].strip())
        if not new:
            print("none")
    elif cmd == "prompt":
        post({"kind": "prompt", "text": a[1]})
    elif cmd == "wait":
        import time
        s = state()
        end = time.time() + (int(a[1]) if len(a) > 1 else 300)
        time.sleep(3)
        while True:
            st, data = call("GET", f"{s['base']}/api/room/{s['room']}/guest/message", None, s["token"])
            if st != 200:
                print("ERROR", st, str(data)[:200]); sys.exit(1)
            if not data.get("running") or time.time() > end:
                break
            time.sleep(5)
        print("running" if data.get("running") else "idle")
        for m in data["data"][-6:]:
            print(f"[{m.get('author') or m['role']}] {m['text'][:1500]}")
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

if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except (ValueError, OSError) as error:
        print("ERROR:", str(error))
        sys.exit(1)
