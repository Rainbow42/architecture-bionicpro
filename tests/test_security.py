import time
from urllib.parse import parse_qs, urlparse

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from backend.security import Sessions, challenge, owner_key


def test_pkce_rfc7636_vector():
    assert challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_rotation_encrypts_refresh_and_invalidates_old_session():
    store = Sessions(Fernet.generate_key())
    sid = store.create({"sub": "alice", "exp": time.time() + 120},
                       {"access_token": "access", "refresh_token": "secret", "expires_in": 120})
    assert store.get(sid).refresh != b"secret"
    assert store.cipher.decrypt(store.get(sid).refresh) == b"secret"
    replacement = store.rotate(sid)
    assert replacement != sid
    assert store.get(sid) is None
    assert store.get(replacement).access == "access"


def test_absolute_session_expiry_is_not_extended_by_rotation():
    clock = [0]
    store = Sessions(Fernet.generate_key(), ttl=3600, clock=lambda: clock[0])
    sid = store.create({"sub": "alice", "exp": 120},
                       {"access_token": "access", "refresh_token": "secret", "expires_in": 120})
    clock[0] = 3500
    sid = store.rotate(sid)
    clock[0] = 3600
    assert store.get(sid) is None


def test_owner_namespace_includes_issuer():
    assert owner_key("ru", "alice") != owner_key("eu", "alice")
    assert owner_key("ru", "alice") != owner_key("ru", "bob")


def test_login_uses_pkce_cookie_state_nonce_and_no_tokens():
    from backend import auth
    client = TestClient(auth.app, base_url="https://localhost:8443")
    result = client.get("/auth/login", follow_redirects=False)
    params = parse_qs(urlparse(result.headers["location"]).query)
    assert params["code_challenge_method"] == ["S256"]
    assert params["state"] and params["nonce"]
    assert "code_verifier" not in params
    assert "HttpOnly" in result.headers["set-cookie"]
    assert "Secure" in result.headers["set-cookie"]
    assert "access_token" not in result.text
    assert client.get("/auth/callback?state=wrong&code=foo").status_code == 400


def test_unauthenticated_reports_and_download_rejected():
    from backend import auth
    client = TestClient(auth.app, base_url="https://localhost:8443")
    assert client.get("/api/reports?start=2026-01-01&end=2026-01-02").status_code == 401
    assert client.get("/auth/download", headers={"x-original-uri": "/cdn/anything"}).status_code == 401


def test_wrong_owner_and_logout_csrf_rejected():
    from backend import auth
    sid = auth.sessions.create({"sub": "alice", "exp": time.time() + 120,
                               "realm_access": {"roles": ["prothetic_user"]}},
                              {"access_token": "access", "refresh_token": "secret", "expires_in": 120})
    client = TestClient(auth.app, base_url="https://localhost:8443")
    client.cookies.set(auth.COOKIE, sid)
    assert client.get("/auth/download", headers={"x-original-uri": "/cdn/bob/file.json"}).status_code == 403
    assert client.post("/auth/logout").status_code == 403
    assert auth.sessions.get(sid) is not None


def test_valid_download_rotates_cookie_and_replay_fails():
    from backend import auth
    sid = auth.sessions.create({"sub": "alice", "exp": time.time() + 120,
                               "realm_access": {"roles": ["prothetic_user"]}},
                              {"access_token": "access", "refresh_token": "secret", "expires_in": 120})
    client = TestClient(auth.app, base_url="https://localhost:8443")
    client.cookies.set(auth.COOKIE, sid)
    uri = "/cdn/" + owner_key(auth.ISSUER, "alice") + "/reports_cdc-1/2026-01-01_2026-01-02.json"
    result = client.get("/auth/download", headers={"x-original-uri": uri})
    assert result.status_code == 204
    assert "HttpOnly" in result.headers["set-cookie"]
    assert auth.sessions.get(sid) is None


def test_expired_access_is_refreshed_server_side(monkeypatch):
    from backend import auth
    sid = auth.sessions.create({"sub": "alice", "exp": time.time() - 1},
                              {"access_token": "old", "refresh_token": "secret", "expires_in": 0})
    seen = []
    async def refresh(data):
        seen.append(data)
        return {"access_token": "new", "refresh_token": "rotated", "expires_in": 120}
    async def validate(token, audience):
        return {"sub": "alice", "exp": time.time() + 120}
    monkeypatch.setattr(auth, "tokens", refresh)
    monkeypatch.setattr(auth, "validate", validate)
    client = TestClient(auth.app, base_url="https://localhost:8443")
    result = client.get("/api/session", headers={"cookie": auth.COOKIE + "=" + sid})
    assert result.status_code == 200
    assert seen == [{"grant_type": "refresh_token", "refresh_token": "secret"}]
    assert "new" not in result.text and "rotated" not in result.text
    assert auth.sessions.get(sid) is None
