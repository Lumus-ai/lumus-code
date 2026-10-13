import json
import os
import tempfile
import threading
import unittest
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from lumus_guest_transport import call, save_state, validate_base


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

    def test_state_is_atomic_and_private(self):
        with tempfile.TemporaryDirectory() as folder:
            filename = os.path.join(folder, "state.json")
            save_state(filename, {"token": "fixture-only"})
            self.assertEqual(os.stat(filename).st_mode & 0o777, 0o600)
            with open(filename) as source:
                self.assertEqual(json.load(source), {"token": "fixture-only"})
            self.assertEqual(os.listdir(folder), ["state.json"])


if __name__ == "__main__":
    unittest.main()
