"""Native clients end to end, for both providers: Better Auth's bearer plugin on a real database, with the real
least-privilege login and a real production server. A native client sends no cookie and no `Origin`, signs in over
`/api/auth/sign-in/email`, reads the signed token from the `set-auth-token` header and sends it as
`Authorization: Bearer`. The same sessions serve the browser cookie, so expiry, sign-out and revocation must end
a bearer token exactly as they end a cookie.

Needs Docker (marker `db`).
"""

from __future__ import annotations

import urllib.parse
import uuid
from pathlib import Path

import pytest

from authharness import Client, Server
from conftest import render
from dbharness import install, plain, require_docker
from test_auth_database import PROVIDERS, Env

pytestmark = pytest.mark.db
PASSWORD = "a long local passphrase 42"
TRANSCRIPTS: dict[str, list] = {}


@pytest.fixture(scope="module", params=PROVIDERS)
def env(request, template_repo: Path, tmp_path_factory: pytest.TempPathFactory):
    require_docker()
    provider = request.param
    project = tmp_path_factory.mktemp(f"native-{provider}") / f"{provider}-native-app"
    render(template_repo, project, app_name=f"{provider}-native-app", zod=True, rest_api=True, database=provider, auth=True, native_client=True)
    install(project)
    environment = Env(provider, project)
    try:
        environment.start()
        built = environment.npm("build", env=environment.server_env())
        assert built.returncode == 0, plain(built.stdout)[-2000:] + plain(built.stderr)[-2000:]
        yield environment
    finally:
        environment.destroy()


@pytest.fixture(scope="module")
def main(env: Env):
    with env.server("native", AUTH_ALLOW_SIGN_UP="true", AUTH_TRUSTED_IP_HEADER="x-real-ip") as server:
        yield server


class Native:
    """What a native app does: no cookie jar, no Origin, the token kept by the caller."""

    def __init__(self, server: Server) -> None:
        self.http: Client = server.client("x-real-ip")
        self.token: str | None = None

    def call(self, method: str, path: str, body=None, token: str | None = "", headers: dict | None = None):
        sent = dict(headers or {})
        use = self.token if token == "" else token
        if use is not None:
            sent["authorization"] = f"Bearer {use}"
        return self.http.request(method, path, body, headers=sent, origin=None, cookies=False)

    def sign_up(self, label: str):
        email = f"{label}-{uuid.uuid4().hex[:8]}@example.test"
        reply = self.call("POST", "/api/auth/sign-up/email", {"email": email, "password": PASSWORD, "name": label}, token=None)
        assert reply.status == 200, (reply.status, reply.text[:200])
        self.token = reply.header("set-auth-token")
        assert self.token
        return email, reply

    def sign_in(self, email: str, password: str = PASSWORD):
        reply = self.call("POST", "/api/auth/sign-in/email", {"email": email, "password": password}, token=None)
        self.token = reply.header("set-auth-token")
        return reply


def test_sign_in_without_cookie_or_origin_returns_a_signed_token_and_it_authorizes(main: Server, env: Env) -> None:
    app = Native(main)
    email, signed_up = app.sign_up("native")
    assert "." in app.token and len(app.token) > 40  # token.signature: the same value the cookie carries
    cookie = next(line for line in signed_up.set_cookies() if "session_token" in line)
    cookie_value = urllib.parse.unquote(cookie.split("=", 1)[1].split(";")[0])
    assert cookie_value == urllib.parse.unquote(app.token)  # one session, two ways to present it
    again = Native(main)
    assert again.sign_in(email).status == 200 and again.token != app.token  # a new sign-in is a new session
    session = app.call("GET", "/api/v1/session").json()
    assert session["user"]["email"] == email and set(session) == {"user", "session"}
    assert app.call("GET", "/api/v1/sessions").json()["sessions"][0]["current"] in (True, False)
    assert app.token.split(".")[0] not in app.call("GET", "/api/v1/session").text  # the public API never repeats the token
    wrong = Native(main).sign_in(email, "wrong wrong wrong wrong")
    assert wrong.status == 401 and wrong.header("set-auth-token") is None


def test_a_token_that_is_not_a_valid_signed_session_token_is_refused_everywhere(main: Server, env: Env) -> None:
    app = Native(main)
    app.sign_up("forger")
    raw = env.psql("select token from app_auth.session order by \"createdAt\" desc limit 1")
    value, signature = app.token.split(".", 1)
    assert urllib.parse.unquote(app.token) == f"{raw}.{urllib.parse.unquote(signature)}"  # the database holds the unsigned half; the signature is what the bearer adds
    other = Native(main)
    other.sign_up("forger-two")
    foreign = other.token.split(".", 1)[1]  # a real signature, but for another session
    forged = [raw, f"{value}.{foreign}", f"{value}.AAAA{signature[4:]}", f"{value}.", ".", "x", "null", app.token[:-3], f"{raw[::-1]}.{signature}"]
    for candidate in forged:
        for path in ("/api/v1/session", "/api/v1/items", "/api/v1/sessions"):
            reply = app.call("GET", path, token=candidate)
            assert (reply.status, reply.code) == (401, "unauthenticated"), (candidate[:8], path)
    for header in ("Basic dGVzdDp0ZXN0", "Bearer", "Bearer ", "token " + app.token, "Bearer a b"):
        assert app.http.request("GET", "/api/v1/session", headers={"authorization": header}, origin=None, cookies=False).code == "unauthenticated"
    assert app.call("GET", "/api/v1/session").status == 200  # and the genuine one still works


def test_crud_and_ownership_work_with_bearer_tokens(main: Server, env: Env) -> None:
    alice, bob = Native(main), Native(main)
    alice.sign_up("alice")
    bob.sign_up("bob")
    created = alice.call("POST", "/api/v1/items", {"title": "Bearer item", "body": "private"})
    item = created.json()
    assert created.status == 201 and created.header("location") == f"/api/v1/items/{item['id']}"
    assert alice.call("GET", f"/api/v1/items/{item['id']}").json() == item
    assert [i["id"] for i in alice.call("GET", "/api/v1/items?limit=5&offset=0").json()["items"]] == [item["id"]]
    assert alice.call("PATCH", f"/api/v1/items/{item['id']}", {"body": "changed"}).json()["body"] == "changed"
    assert bob.call("GET", "/api/v1/items").json()["items"] == []
    missing = bob.call("GET", "/api/v1/items/00000000-0000-4000-8000-000000000000")
    for reply in (bob.call("GET", f"/api/v1/items/{item['id']}"), bob.call("PATCH", f"/api/v1/items/{item['id']}", {"title": "x"}), bob.call("DELETE", f"/api/v1/items/{item['id']}")):
        assert (reply.status, reply.code, reply.text) == (404, "not_found", missing.text)
    assert env.psql(f"select title from app.items where id = '{item['id']}'") == "Bearer item"
    assert alice.call("POST", "/api/v1/items", {"title": ""}).code == "validation_failed"
    assert alice.call("DELETE", f"/api/v1/items/{item['id']}").status == 204
    assert alice.call("GET", f"/api/v1/items/{item['id']}").status == 404


def test_sign_out_ends_the_token_at_once_and_deletes_the_session(main: Server, env: Env) -> None:
    app = Native(main)
    app.sign_up("leaver")
    old = app.token
    assert app.call("POST", "/api/v1/items", {"title": "before sign out"}).status == 201
    before = int(env.psql("select count(*) from app_auth.session"))
    assert app.call("POST", "/api/auth/sign-out", {}).status == 200
    assert int(env.psql("select count(*) from app_auth.session")) == before - 1
    for path in ("/api/v1/session", "/api/v1/items", "/api/v1/sessions"):
        reply = app.call("GET", path, token=old)
        assert (reply.status, reply.code) == (401, "unauthenticated")


def test_revoking_other_sessions_and_changing_the_password_end_other_tokens(main: Server, env: Env) -> None:
    first, second, third = Native(main), Native(main), Native(main)
    email, _ = first.sign_up("revoker")
    second.sign_in(email)
    third.sign_in(email)
    assert all(client.call("GET", "/api/v1/session").status == 200 for client in (first, second, third))
    sessions = first.call("GET", "/api/v1/sessions").json()["sessions"]
    assert len(sessions) == 3 and sum(1 for s in sessions if s["current"]) == 1
    assert first.call("POST", "/api/auth/revoke-other-sessions", {}).status == 200
    assert first.call("GET", "/api/v1/session").status == 200
    assert second.call("GET", "/api/v1/session").code == "unauthenticated"
    assert third.call("GET", "/api/v1/items").code == "unauthenticated"
    fourth = Native(main)
    fourth.sign_in(email)
    changed = first.call("POST", "/api/auth/change-password", {"currentPassword": PASSWORD, "newPassword": "another long passphrase 17", "revokeOtherSessions": True})
    assert changed.status == 200
    rotated = changed.header("set-auth-token")  # a password change replaces the current session too: the client must adopt the new token
    assert rotated and rotated != first.token
    assert first.call("GET", "/api/v1/session").code == "unauthenticated"  # the previous token is dead
    first.token = rotated
    assert first.call("GET", "/api/v1/session").status == 200
    assert fourth.call("GET", "/api/v1/session").code == "unauthenticated"


def test_an_expired_session_stops_authorizing_the_bearer_token(main: Server, env: Env) -> None:
    app = Native(main)
    email, _ = app.sign_up("expiring")
    assert app.call("GET", "/api/v1/session").status == 200
    env.psql(f"update app_auth.session set \"expiresAt\" = now() - interval '1 minute' where \"userId\" = (select id from app_auth.\"user\" where email = '{email}')")
    assert app.call("GET", "/api/v1/session").code == "unauthenticated"
    assert app.call("GET", "/api/v1/items").code == "unauthenticated"


def test_cookie_clients_are_unchanged_and_share_the_same_session(main: Server, env: Env) -> None:
    browser = main.client("x-real-ip")
    email = f"both-{uuid.uuid4().hex[:6]}@example.test"
    reply = browser.post("/api/auth/sign-up/email", {"email": email, "password": PASSWORD, "name": "both"})
    cookie = next(line for line in reply.set_cookies() if "session_token" in line)
    assert cookie.startswith("better-auth.session_token=") and "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Secure" not in cookie
    assert browser.post("/api/v1/items", {"title": "via cookie"}, origin="https://evil.example").code == "origin_not_allowed"
    assert browser.post("/api/v1/items", {"title": "via cookie"}).status == 201
    token = reply.header("set-auth-token")
    app = Native(main)
    assert app.call("GET", "/api/v1/items", token=token).json()["items"][0]["title"] == "via cookie"  # same user, same data
    assert browser.post("/api/auth/sign-out", {}).status == 200  # ending it from the browser ends the bearer token too
    assert app.call("GET", "/api/v1/session", token=token).code == "unauthenticated"


def test_an_untrusted_origin_is_still_refused_even_with_a_valid_token(main: Server, env: Env) -> None:
    app = Native(main)
    app.sign_up("origin")
    reply = app.http.request("POST", "/api/v1/items", {"title": "evil"}, headers={"authorization": f"Bearer {app.token}"}, origin="https://evil.example", cookies=False)
    assert (reply.status, reply.code) == (403, "origin_not_allowed")
    assert env.psql("select count(*) from app.items where title = 'evil'") == "0"
    assert app.http.request("POST", "/api/auth/sign-in/email", {"email": "a@example.test", "password": PASSWORD}, origin="https://evil.example", cookies=False).status == 403


def test_bearer_sign_ins_share_the_rate_limit_and_a_forged_address_does_not_bypass_the_default(env: Env) -> None:
    with env.server("native-default", AUTH_ALLOW_SIGN_UP="true") as server:  # no trusted address header: one bucket per route
        client = server.client()
        statuses = [client.request("POST", "/api/auth/sign-in/email", {"email": "nobody@example.test", "password": PASSWORD}, headers={"x-forwarded-for": f"203.0.113.{i}"}, origin=None, cookies=False).status for i in range(8)]
    assert statuses[-1] == 429 and 401 in statuses


def test_no_token_reached_a_log(main: Server, env: Env) -> None:
    app = Native(main)
    app.sign_up("logs")
    app.call("GET", "/api/v1/session")
    tokens = [t for t in env.psql("select token from app_auth.session").splitlines() if t]
    logs = main.logs() + "".join(path.read_text(errors="replace") for path in env.logs.glob("*.log"))
    assert tokens and not any(token in logs for token in tokens) and app.token not in logs


def test_the_scenario_has_the_same_outcome_on_both_providers(main: Server, env: Env) -> None:
    steps: list[tuple] = []

    def note(label: str, reply) -> None:
        steps.append((label, reply.status, reply.code, sorted(reply.json()) if reply.text.startswith("{") else None))

    alice, bob = Native(main), Native(main)
    note("anonymous", alice.call("GET", "/api/v1/items", token=None))
    alice.sign_up("sc-a")
    note("session", alice.call("GET", "/api/v1/session"))
    first = alice.call("POST", "/api/v1/items", {"title": "Scenario item"})
    note("create", first)
    bob.sign_up("sc-b")
    note("foreign read", bob.call("GET", f"/api/v1/items/{first.json()['id']}"))
    note("forged", alice.call("GET", "/api/v1/session", token="forged.token"))
    old = alice.token
    note("sign out", alice.call("POST", "/api/auth/sign-out", {}))
    note("after sign out", alice.call("GET", "/api/v1/session", token=old))
    TRANSCRIPTS[env.provider] = steps
    assert [step[1] for step in steps] == [401, 200, 201, 404, 401, 200, 401]
    if len(TRANSCRIPTS) == 2:
        assert TRANSCRIPTS["postgres"] == TRANSCRIPTS["supabase"]
