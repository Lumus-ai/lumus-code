import json
import os
import tempfile
import threading
import unittest
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from lumus_guest_transport import call, save_state, validate_base, poll_messages


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.invalid_join = True
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
                self.send_response(401 if self.path == "/denied" else 200)
                self.end_headers()
                payload = b"fixture-only" if self.path == "/denied" else b"{}"
                if self.path == "/bad":
                    payload = b"not json"
                if self.path == "/large":
                    payload = b"x" * 2_000_001
                if self.path.startswith("/api/room/"):
                    payload = json.dumps({"data": [{"id": "same", "text": "[host to @instinct] hello", "author": "host", "role": "user"}]}).encode()
                self.wfile.write(payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = "http://127.0.0.1:%s" % self.server.server_port
        self.worker = threading.Thread(target=self.server.serve_forever)
        self.worker.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()

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
