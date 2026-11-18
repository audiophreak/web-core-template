"""Helpers for the end-to-end authentication tests: a real production server and a small HTTP client.

Servers are `next start` processes of the generated application's production build, on a random loopback
port, with the environment the test chooses. Nothing here prints a secret: failures show status codes and
problem codes, and the logs are searched for secrets, not displayed.
"""

from __future__ import annotations

import http.client
import itertools
import json
import os
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dbharness import free_port

_ADDRESSES = itertools.count(1 + (uuid.uuid4().int % 60000))


@dataclass
class Reply:
    status: int
    headers: list[tuple[str, str]]
    text: str

    def header(self, name: str) -> str | None:
        for key, value in self.headers:
            if key.lower() == name.lower():
                return value
        return None

    def set_cookies(self) -> list[str]:
        return [value for key, value in self.headers if key.lower() == "set-cookie"]

    def json(self):
        return json.loads(self.text)

    @property
    def code(self) -> str | None:
        """The stable `code` of a problem response, if the body is one."""
        try:
            return json.loads(self.text).get("code")
        except (ValueError, AttributeError):
            return None


@dataclass
class Client:
    """A browser stand-in: one origin, a cookie jar, no redirects, and every response body recorded."""

    base: str
    forwarded: str | None = None
    jar: dict[str, str] = field(default_factory=dict)
    transcript: list[str] = field(default_factory=list)

    def cookie_header(self) -> str:
        return "; ".join(f"{name}={value}" for name, value in self.jar.items())

    def request(self, method: str, path: str, body=None, headers: dict[str, str] | None = None, origin: str | None = "", cookies: bool = True, raw: bytes | None = None) -> Reply:
        parts = urlsplit(self.base)
        connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=30)
        sent = {"accept": "application/json", **(headers or {})}
        if origin == "":
            origin = self.base  # the browser's own origin, as a browser would send on a POST
        if origin is not None and method not in ("GET", "HEAD"):
            sent["origin"] = origin
        if cookies and self.jar:
            sent["cookie"] = self.cookie_header()
        if self.forwarded is not None:
            n = next(_ADDRESSES)  # a fresh, never repeated address per request: each one gets its own rate-limit bucket
            sent[self.forwarded] = f"10.{(n >> 16) & 255}.{(n >> 8) & 255}.{n & 255}"
        payload = raw
        if body is not None:
            payload = json.dumps(body).encode()
            sent.setdefault("content-type", "application/json")
        connection.request(method, path, body=payload, headers=sent)
        response = connection.getresponse()
        text = response.read().decode("utf-8", "replace")
        reply = Reply(response.status, response.getheaders(), text)
        connection.close()
        for line in reply.set_cookies():
            name, _, rest = line.partition("=")
            value = rest.split(";")[0]
            if value == "" or "max-age=0" in line.lower():
                self.jar.pop(name, None)
            else:
                self.jar[name] = value
        self.transcript.append(text)
        return reply

    def get(self, path: str, **kw) -> Reply:
        return self.request("GET", path, **kw)

    def post(self, path: str, body=None, **kw) -> Reply:
        return self.request("POST", path, body, **kw)


class Server:
    """`next start` for an already built application, with an explicit environment."""

    def __init__(self, app: Path, env: dict[str, str], log: Path) -> None:
        self.app = app
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.env = {**os.environ, **env, "PORT": str(self.port), "BETTER_AUTH_URL": env.get("BETTER_AUTH_URL", self.base)}
        self.env.pop("NODE_ENV", None)
        self.log = log
        self.process: subprocess.Popen | None = None

    def start(self) -> "Server":
        handle = self.log.open("ab")
        self.process = subprocess.Popen(
            ["npx", "--no-install", "next", "start", "-p", str(self.port), "-H", "127.0.0.1"],
            cwd=self.app,
            env=self.env,
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
        deadline = time.time() + 90
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise AssertionError("the server exited while starting; see its log")
            try:
                connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
                connection.request("GET", "/api/v1/app-info")
                if connection.getresponse().status == 200:
                    connection.close()
                    return self
            except OSError:
                pass
            time.sleep(0.5)
        raise AssertionError("the server did not become ready in time")

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()

    def client(self, forwarded: str | None = None) -> Client:
        return Client(self.base, forwarded=forwarded)

    def logs(self) -> str:
        return self.log.read_text(errors="replace") if self.log.exists() else ""

    def __enter__(self) -> "Server":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


def sign_up(client: Client, label: str = "user", password: str = "a long local passphrase 42") -> tuple[str, str]:
    """Registers a user through the real endpoint; returns (email, password)."""
    email = f"{label}-{uuid.uuid4().hex[:8]}@example.test"
    reply = client.post("/api/auth/sign-up/email", {"email": email, "password": password, "name": label})
    assert reply.status == 200, f"sign-up answered {reply.status} {reply.code}"
    return email, password
