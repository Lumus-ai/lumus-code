"""External room transport. Redirects never carry room bearer credentials."""
import ipaddress
import json
import math
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request


def validate_base(value):
    url = urllib.parse.urlsplit(value.strip())
    if url.scheme not in ("http", "https") or not url.hostname:
        raise ValueError("Room URL must use HTTPS or loopback HTTP")
    if url.username or url.password or url.query or url.fragment:
        raise ValueError("Room URL must not contain credentials, query or fragment")
    # Parsing the port also rejects invalid/out-of-range values.
    url.port
    try:
        loopback = ipaddress.ip_address(url.hostname).is_loopback
    except ValueError:
        loopback = url.hostname.lower() == "localhost"
    if url.scheme == "http" and not loopback:
        raise ValueError("Remote room URLs require HTTPS")
    return urllib.parse.urlunsplit(url).rstrip("/")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


REQUEST_TIMEOUT = 10


def call(method, url, body=None, token=None, timeout=REQUEST_TIMEOUT):
    """One attempt. Timeout limits socket operations, not total wall time/DNS.

    A failed POST may have reached the host. Never retry it automatically.
    """
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 120:
        raise ValueError("Room socket timeout must be between 0 and 120 seconds")
    # Validate transport even for loaded connection state, not just join.
    parsed = urllib.parse.urlsplit(url)
    validate_base(urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", "")))
    request = urllib.request.Request(
        url, method=method, data=None if body is None else json.dumps(body).encode()
    )
    request.add_header("content-type", "application/json")
    if token:
        request.add_header("authorization", "Bearer " + token)
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=timeout) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                return 502, "Room response too large"
            try:
                return response.status, json.loads(raw or b"null")
            except (ValueError, UnicodeDecodeError):
                return 502, "Room returned invalid JSON"
    except urllib.error.HTTPError as error:
        if 300 <= error.code < 400:
            return error.code, "Room redirect refused; use the final HTTPS room URL"
        if error.code == 401:
            return 401, "Room authentication failed; rejoin with a current host-provided join code"
        if error.code == 403:
            return 403, "Room access denied; ask the host to check guest permissions"
        if error.code == 404:
            return 404, "Room or endpoint not found; check the host URL and room availability"
        # Never print arbitrary response bodies: they can echo the bearer token.
        return error.code, "Room request failed (HTTP %s)" % error.code
    except (urllib.error.URLError, TimeoutError, OSError):
        message = "Room connection failed or socket timed out; check the URL and host availability"
        if method.upper() != "GET":
            message += "; delivery unknown, check with the host before retrying"
        return 503, message


def save_state(filename, value):
    directory = os.path.dirname(os.path.abspath(filename))
    descriptor, temporary = tempfile.mkstemp(prefix=".lumus-guest-", dir=directory)
    try:
        with os.fdopen(descriptor, "w") as target:
            json.dump(value, target)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, filename)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def poll_messages(connection, payload, filename, mark_seen):
    """Snapshot-scoped IDs, separated by endpoint and room. Peek never writes.

    Legacy global IDs are ignored once: current addressed messages may repeat.
    This is single-client snapshot deduplication, not a durable delivery/ack queue.
    IDs removed from the snapshot can repeat if the server later returns them.
    Concurrent poll processes should not share a cursor file.
    """
    import hashlib
    messages = validate_snapshot(payload)
    namespace = hashlib.sha256(json.dumps([validate_base(connection["base"]), connection["room"]]).encode()).hexdigest()
    stored = {}
    if os.path.exists(filename):
        with open(filename) as source:
            stored = json.load(source)
    # Legacy global IDs have no room provenance; importing them would suppress another room.
    if isinstance(stored, list):
        stored = {}
    if not isinstance(stored, dict) or any(not isinstance(key, str) or not isinstance(ids, list)
           or any(not isinstance(item, str) for item in ids) for key, ids in stored.items()):
        raise ValueError("Invalid room cursor file; repair it before polling")
    seen = set(stored.get(namespace, []))
    current = []
    new = []
    for item in messages:
        if item.get("author") != "host" or item.get("role") != "user" or not item["text"].startswith("[host to @instinct]"):
            continue
        if item["id"] in current:
            continue
        current.append(item["id"])
        if item["id"] not in seen:
            new.append(item)
    if mark_seen:
        if namespace not in stored and len(stored) >= 128:
            raise ValueError("Room cursor limit reached; archive the cursor file before joining another room")
        stored[namespace] = current
        save_state(filename, stored)
    return new


def diagnose(connection):
    """Read-only authenticated probe. No messages, identifiers or credentials in output."""
    if not isinstance(connection, dict) or any(not isinstance(connection.get(key), str) or not connection[key]
                                               for key in ("base", "room", "token")):
        return False, "Invalid saved connection; rejoin with a current host-provided join code"
    try:
        base = validate_base(connection["base"])
        room = urllib.parse.quote(connection["room"], safe="")
        code, payload = call("GET", base + "/api/room/" + room + "/guest/message", token=connection["token"])
    except (ValueError, OSError):
        return False, "Invalid saved connection; check the host URL and rejoin"
    if code != 200:
        return False, "HTTP %s: %s" % (code, payload)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list) or not isinstance(payload.get("running"), bool):
        return False, "Room returned an incompatible message snapshot; check host/client versions"
    try:
        validate_snapshot(payload)
    except ValueError:
        return False, "Room returned an incompatible message snapshot; check host/client versions"
    return True, "Connected; host model is " + ("running" if payload["running"] else "idle")


def validate_snapshot(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("Invalid room message snapshot; cursor unchanged")
    messages = payload["data"]
    if len(messages) > 4096:
        raise ValueError("Room snapshot exceeds 4096 messages; cursor unchanged")
    if any(not isinstance(item, dict) or not isinstance(item.get("id"), str)
           or not isinstance(item.get("text"), str) for item in messages):
        raise ValueError("Invalid room message entry; cursor unchanged")
    return messages
