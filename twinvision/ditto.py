"""Minimal Eclipse Ditto HTTP client (stdlib only)."""

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request


class Ditto:
    def __init__(self, url=None, user=None, password=None):
        self.url = (url or os.environ.get("DITTO_URL", "http://localhost:8080")).rstrip("/")
        user = user or os.environ.get("DITTO_USER", "ditto")
        password = password if password is not None else os.environ.get("DITTO_PASSWORD", "")
        self.auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

    def request(self, method, path, body=None, timeout=30):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={"Authorization": self.auth, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)

    def retry(self, method, path, body=None, attempts=20, delay=3.0):
        """Retry on connection errors, timeouts and 5xx/408 (Ditto warming up)."""
        last = None
        for _ in range(attempts):
            try:
                return self.request(method, path, body)
            except urllib.error.HTTPError as exc:
                if exc.code not in (408, 429) and exc.code < 500:
                    raise
                last = exc
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last = exc
            time.sleep(delay)
        raise RuntimeError(f"{method} {path} failed: {last}")

    def put_thing(self, thing_id, body):
        return self.retry("PUT", f"/api/2/things/{thing_id}", body)[0]

    def get_thing(self, thing_id, fields=None):
        q = f"?fields={urllib.parse.quote(fields)}" if fields else ""
        return self.retry("GET", f"/api/2/things/{thing_id}{q}")[1]

    def get_things(self, ids, fields=None):
        q = "ids=" + ",".join(ids)
        if fields:
            q += "&fields=" + urllib.parse.quote(fields)
        return self.retry("GET", f"/api/2/things?{q}")[1]
