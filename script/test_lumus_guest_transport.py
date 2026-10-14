import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
from pathlib import Path
import time
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from lumus_guest_transport import call, save_state, validate_base, poll_messages, diagnose


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.invalid_join = True
        self.room_status = 200
        self.room_payload = {"data": [{"id": "same", "text": "[host to @instinct] hello", "author": "host", "role": "user"}], "running": False}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.send_response(200)
                self.end_headers()
                payload = b'null' if owner.invalid_join else json.dumps({"room": {"id": "new"}, "token": "fixture-only"}).encode()
                self.wfile.write(payload)

            def do_GET(self):
                owner.requests.append((self.path, self.headers.get("Authorization")))
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", owner.base + "/target")
                    self.end_headers()
                    return
                if self.path == "/slow":
                    time.sleep(0.15)
                self.send_response(owner.room_status if self.path.startswith("/api/room/") else (401 if self.path == "/denied" else 200))
                self.end_headers()
                payload = b"fixture-only" if self.path == "/denied" else b"{}"
                if self.path == "/bad":
                    payload = b"not json"
                if self.path == "/large":
                    payload = b"x" * 2_000_001
                if self.path.startswith("/api/room/"):
                    payload = json.dumps(owner.room_payload).encode()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = "http://127.0.0.1:%s" % self.server.server_port
        self.worker = threading.Thread(target=self.server.serve_forever)
        self.worker.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()

    def test_default_and_explicit_socket_timeout(self):
        with patch("lumus_guest_transport.urllib.request.build_opener") as build:
            build.return_value.open.side_effect = urllib.error.URLError("fixture-secret")
            self.assertEqual(call("GET", self.base)[0], 503)
            self.assertEqual(build.return_value.open.call_args.kwargs["timeout"], 10)
            call("GET", self.base, timeout=0.25)
            self.assertEqual(build.return_value.open.call_args.kwargs["timeout"], 0.25)
        for value in (0, -1, 121, True, float("nan"), float("inf"), "10"):
            with self.assertRaises(ValueError):
                call("GET", self.base, timeout=value)

    def test_socket_timeout_on_slow_local_server(self):
        code, message = call("GET", self.base + "/slow", timeout=0.03)
        self.assertEqual(code, 503)
        self.assertIn("timed out", message)

    def test_failed_post_is_never_retried(self):
        with patch("lumus_guest_transport.urllib.request.build_opener") as build:
            build.return_value.open.side_effect = TimeoutError("fixture-secret")
            code, message = call("POST", self.base, {"text": "fixture-secret"}, "fixture-secret")
            self.assertEqual(code, 503)
            self.assertIn("delivery unknown", message)
            self.assertNotIn("fixture-secret", message)
            self.assertEqual(build.return_value.open.call_count, 1)

    def test_doctor_read_only_and_private(self):
        for running in (False, True):
            self.room_payload["running"] = running
            with tempfile.TemporaryDirectory() as folder:
                state = os.path.join(folder, ".lumus-guest.json")
                seen = os.path.join(folder, ".lumus-guest-seen.json")
                save_state(state, {"base": self.base, "room": "private-room", "token": "fixture-only"})
                save_state(seen, {"private-cursor": ["same"]})
                before = [Path(p).read_bytes() for p in (state, seen)]
                result = subprocess.run([sys.executable, "lumus-guest.py", "doctor"], env={**os.environ, "HOME": folder}, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("running" if running else "idle", result.stdout)
                for private in (self.base, "private-room", "fixture-only", "hello", "same"):
                    self.assertNotIn(private, result.stdout + result.stderr)
                self.assertEqual(before, [Path(p).read_bytes() for p in (state, seen)])
        self.assertEqual(len(self.requests), 2)
        self.assertTrue(all(path.endswith("/guest/message") for path, _ in self.requests))

    def test_doctor_status_and_schema(self):
        connection = {"base": self.base, "room": "private-room", "token": "fixture-only"}
        for code, hint in ((401, "rejoin"), (403, "permissions"), (404, "availability")):
            self.room_status = code
            ok, message = diagnose(connection)
            self.assertFalse(ok)
            self.assertIn(hint, message)
            self.assertNotIn("fixture-only", message)
        self.room_status = 200
        for payload in (None, {}, {"data": [], "running": "false"}, {"data": {}, "running": False}, {"data": [None], "running": False}, {"data": [], "running": 1}):
            self.room_payload = payload
            self.assertEqual(diagnose(connection)[0], False)
        self.assertFalse(diagnose({})[0])
        self.assertFalse(diagnose({**connection, "base": "http://example.com"})[0])

    def test_doctor_missing_or_corrupt_state(self):
        with tempfile.TemporaryDirectory() as folder:
            for contents in (None, "fixture-private-not-json"):
                if contents:
                    with open(os.path.join(folder, ".lumus-guest.json"), "w") as target:
                        target.write(contents)
                result = subprocess.run([sys.executable, "lumus-guest.py", "doctor"], env={**os.environ, "HOME": folder}, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("join a room first", result.stdout)
                self.assertNotIn("fixture-private", result.stdout + result.stderr)
        self.assertEqual(self.requests, [])

    def test_authenticated_request(self):
        self.assertEqual(call("GET", self.base + "/ok", token="fixture-only"), (200, {}))
        self.assertEqual(self.requests, [("/ok", "Bearer fixture-only")])

    def test_redirect_never_reaches_target(self):
        code, message = call("GET", self.base + "/redirect", token="fixture-only")
        self.assertEqual(code, 302)
        self.assertIn("refused", message)
        self.assertEqual(len(self.requests), 1)

    def test_http_error_does_not_echo_secret(self):
        code, message = call("GET", self.base + "/denied", token="fixture-only")
        self.assertEqual(code, 401)
        self.assertNotIn("fixture-only", message)

    def test_invalid_and_large_json(self):
        self.assertEqual(call("GET", self.base + "/bad")[0], 502)
        self.assertEqual(call("GET", self.base + "/large")[0], 502)

    def test_endpoint_policy(self):
        for url in ("http://example.com", "https://key@example.com", "https://example.com?q=key", "https://example.com#key", "file:///tmp/a", "https://example.com:99999"):
            with self.assertRaises(ValueError):
                validate_base(url)
        for url in (self.base, "http://localhost:47475", "http://[::1]:47475", "https://room.example.com"):
            self.assertEqual(validate_base(url), url)
        with self.assertRaises(ValueError):
            call("GET", "http://example.com", token="fixture-only")

    def test_invalid_join_preserves_previous_state(self):
        with tempfile.TemporaryDirectory() as folder:
            filename = os.path.join(folder, ".lumus-guest.json")
            save_state(filename, {"base": self.base, "room": "old", "token": "fixture-only"})
            result = subprocess.run([sys.executable, "lumus-guest.py", "join", self.base, "fixture-code"], env={**os.environ, "HOME": folder}, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("previous connection kept", result.stdout)
            with open(filename) as source:
                self.assertEqual(json.load(source)["room"], "old")
            self.assertNotIn("fixture-only", result.stdout + result.stderr)

    def test_valid_join_saves_connection(self):
        self.invalid_join = False
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run([sys.executable, "lumus-guest.py", "join", self.base, "fixture-code"], env={**os.environ, "HOME": folder}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("fixture-only", result.stdout + result.stderr)
            filename = os.path.join(folder, ".lumus-guest.json")
            with open(filename) as source:
                self.assertEqual(json.load(source)["room"], "new")
            self.assertEqual(os.stat(filename).st_mode & 0o777, 0o600)

    def test_cli_poll_peek_and_room_switch(self):
        with tempfile.TemporaryDirectory() as folder:
            filename = os.path.join(folder, ".lumus-guest.json")
            def invoke(command):
                return subprocess.run([sys.executable, "lumus-guest.py", command], env={**os.environ, "HOME": folder, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True)
            save_state(filename, {"base": self.base, "room": "alpha", "token": "fixture-only"})
            self.assertIn("NEW same hello", invoke("peek").stdout)
            self.assertFalse(os.path.exists(os.path.join(folder, ".lumus-guest-seen.json")))
            self.assertIn("NEW same hello", invoke("poll").stdout)
            self.assertEqual(invoke("poll").stdout.strip(), "none")
            save_state(filename, {"base": self.base, "room": "beta", "token": "fixture-only"})
            self.assertIn("NEW same hello", invoke("poll").stdout)
            save_state(filename, {"base": self.base, "room": "alpha", "token": "fixture-only"})
            self.assertEqual(invoke("peek").stdout.strip(), "none")

    def test_cli_read_counts_messages_not_characters(self):
        with tempfile.TemporaryDirectory() as folder:
            filename = os.path.join(folder, ".lumus-guest.json")
            def invoke(*command):
                return subprocess.run([sys.executable, "lumus-guest.py", *command], env={**os.environ, "HOME": folder, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True)
            save_state(filename, {"base": self.base, "room": "alpha", "token": "fixture-only"})
            self.room_payload = {"data": [
                {"id": "m1", "text": "first", "author": "host", "role": "user"},
                {"id": "m2", "text": "second", "author": "host", "role": "user"},
                {"id": "m3", "text": "third", "author": "host", "role": "user"},
            ], "running": False}
            out = invoke("read", "2").stdout
            shown = json.loads(out)
            self.assertEqual([m["id"] for m in shown], ["m2", "m3"])
            out = invoke("read").stdout
            self.assertEqual(len(json.loads(out)), 3)
            bad = invoke("read", "abc")
            self.assertEqual(bad.returncode, 2)
            self.assertIn("count must be a number of messages", bad.stdout)

    def test_state_is_atomic_and_private(self):
        with tempfile.TemporaryDirectory() as folder:
            filename = os.path.join(folder, "state.json")
            save_state(filename, {"token": "fixture-only"})
            self.assertEqual(os.stat(filename).st_mode & 0o777, 0o600)
            with open(filename) as source:
                self.assertEqual(json.load(source), {"token": "fixture-only"})
            self.assertEqual(os.listdir(folder), ["state.json"])


class CursorTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.filename = os.path.join(self.folder.name, "seen.json")
        self.connection = {"base": "https://room.example.com", "room": "alpha"}
        self.payload = {"data": [{"id": "same", "text": "[host to @instinct] fix tests", "author": "host", "role": "user"}]}

    def tearDown(self):
        self.folder.cleanup()

    def poll(self, mark=True):
        return poll_messages(self.connection, self.payload, self.filename, mark)

    def test_two_rooms_same_id_and_switch_back(self):
        self.assertEqual(len(self.poll()), 1)
        self.assertEqual(self.poll(), [])
        self.connection["room"] = "beta"
        self.assertEqual(len(self.poll()), 1)
        self.connection["room"] = "alpha"
        self.assertEqual(self.poll(), [])
        self.connection["base"] = "https://other.example.com"
        self.assertEqual(len(self.poll()), 1)

    def test_peek_does_not_create_or_change_cursor(self):
        self.assertEqual(len(self.poll(False)), 1)
        self.assertEqual(len(self.poll(False)), 1)
        self.assertFalse(os.path.exists(self.filename))
        self.poll()
        with open(self.filename) as source:
            previous = source.read()
        self.payload["data"].append({**self.payload["data"][0], "id": "new"})
        self.assertEqual(len(self.poll(False)), 1)
        with open(self.filename) as source:
            self.assertEqual(source.read(), previous)

    def test_malformed_and_large_payload_keep_cursor(self):
        self.poll()
        with open(self.filename) as source:
            previous = source.read()
        for bad in ({}, {"data": None}, {"data": [{}]}, {"data": self.payload["data"] * 4097}):
            with self.assertRaises(ValueError):
                poll_messages(self.connection, bad, self.filename, True)
        with open(self.filename) as source:
            self.assertEqual(source.read(), previous)

    def test_filters_other_authors_and_duplicate_ids(self):
        item = self.payload["data"][0]
        self.payload["data"] = [item, item, {**item, "id": "guest", "author": "guest"}, {**item, "id": "other", "text": "general chat"}]
        self.assertEqual([row["id"] for row in self.poll()], ["same"])
        self.assertEqual(self.poll(), [])

    def test_legacy_global_ids_not_imported(self):
        save_state(self.filename, ["same"])
        self.assertEqual(len(self.poll()), 1)
        self.assertEqual(self.poll(), [])
        self.assertEqual(os.stat(self.filename).st_mode & 0o777, 0o600)

    def test_snapshot_prunes_old_ids_but_retains_pending(self):
        self.poll()
        pending = self.payload["data"][0]
        self.payload["data"] = [pending, {**pending, "id": "new"}, {**pending, "id": "new"}]
        self.assertEqual([item["id"] for item in self.poll()], ["new"])
        self.payload["data"] = [pending]
        self.assertEqual(self.poll(), [])
        with open(self.filename) as source:
            self.assertEqual(list(json.load(source).values()), [["same"]])


if __name__ == "__main__":
    unittest.main()
