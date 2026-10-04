import asyncio
import json
import os
import secrets
import time
from urllib.parse import urlencode

import httpx
import jwt
import psycopg
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from security import Sessions, challenge, owner_key

PUBLIC = os.getenv("PUBLIC_URL", "https://localhost:8443")
ISSUER = PUBLIC + "/realms/reports-realm"
INTERNAL = os.getenv("KEYCLOAK_URL", "http://keycloak:8080") + "/realms/reports-realm"
CLIENT = "bionicpro-auth"
SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")
API = os.getenv("REPORTS_API", "http://bionicpro-api:8001")
COOKIE = "__Host-bionicpro"
FLOW_COOKIE = "__Host-login"
sessions = Sessions(os.getenv("SESSION_KEY", "").encode() or Fernet.generate_key())
flows: dict[str, dict] = {}
lock = asyncio.Lock()
app = FastAPI()


def cookie(response: Response, sid: str, ttl=3600, name=COOKIE):
    response.set_cookie(name, sid, max_age=ttl, secure=True, httponly=True, samesite="lax", path="/")
    response.headers["Cache-Control"] = "no-store"


async def validate(token: str, audience: str):
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(INTERNAL + "/protocol/openid-connect/certs")
        response.raise_for_status()
    kid = jwt.get_unverified_header(token).get("kid")
    key = next((key for key in response.json()["keys"] if key["kid"] == kid), None)
    if key is None:
        raise ValueError("Unknown signing key")
    return jwt.decode(token, jwt.PyJWK.from_dict(key).key, algorithms=["RS256"],
                      audience=audience, issuer=ISSUER, options={"require": ["exp", "iat", "sub"]})


async def tokens(data: dict):
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(INTERNAL + "/protocol/openid-connect/token", data={
            **data, "client_id": CLIENT, "client_secret": SECRET,
        })
        response.raise_for_status()
        return response.json()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/auth/login")
def login(provider: str = "local"):
    if provider not in {"local", "yandex"}:
        raise HTTPException(400, "Неизвестный провайдер")
    now = time.time()
    for state in [key for key, value in flows.items() if value["until"] < now]:
        del flows[state]
    state, binding, verifier, nonce = (secrets.token_urlsafe(32) for _ in range(4))
    flows[state] = {"binding": binding, "verifier": verifier, "nonce": nonce, "until": now + 300}
    params = {"client_id": CLIENT, "response_type": "code", "scope": "openid profile email",
              "redirect_uri": PUBLIC + "/auth/callback", "state": state, "nonce": nonce,
              "code_challenge": challenge(verifier), "code_challenge_method": "S256"}
    if provider == "yandex":
        params["kc_idp_hint"] = "yandex"
    response = RedirectResponse(ISSUER + "/protocol/openid-connect/auth?" + urlencode(params))
    cookie(response, binding, 300, FLOW_COOKIE)
    return response


@app.get("/auth/callback")
async def callback(request: Request, state: str = "", code: str = ""):
    flow = flows.get(state)
    if not flow or flow["until"] < time.time() or not secrets.compare_digest(
        flow["binding"], request.cookies.get(FLOW_COOKIE, "")
    ):
        raise HTTPException(400, "Недействительная попытка входа")
    del flows[state]
    try:
        result = await tokens({"grant_type": "authorization_code", "code": code,
                               "redirect_uri": PUBLIC + "/auth/callback", "code_verifier": flow["verifier"]})
        identity = await validate(result["id_token"], CLIENT)
        if not secrets.compare_digest(identity.get("nonce", ""), flow["nonce"]):
            raise ValueError("Invalid nonce")
        claims = await validate(result["access_token"], "reports-api")
        if claims["sub"] != identity["sub"]:
            raise ValueError("Subject mismatch")
        sid = sessions.create(claims, result)
    except (httpx.HTTPError, jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(401, "Не удалось подтвердить вход") from None
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(FLOW_COOKIE, path="/", secure=True, httponly=True)
    cookie(response, sid)
    return response


async def authenticate(request: Request):
    sid = request.cookies.get(COOKIE)
    session = sessions.get(sid)
    if session is None:
        raise HTTPException(401, "Нужно войти")
    if request.method not in {"GET", "HEAD"}:
        if request.headers.get("origin") != PUBLIC or not secrets.compare_digest(
            request.headers.get("x-csrf-token", ""), session.csrf
        ):
            raise HTTPException(403, "Недействительный запрос")
    if session.access_until <= time.time() + 10:
        try:
            result = await tokens({"grant_type": "refresh_token",
                                   "refresh_token": sessions.cipher.decrypt(session.refresh).decode()})
            claims = await validate(result["access_token"], "reports-api")
            if claims["sub"] != session.subject:
                raise ValueError("Subject mismatch")
            session.access = result["access_token"]
            session.refresh = sessions.cipher.encrypt(result["refresh_token"].encode())
            session.access_until = min(claims["exp"], time.time() + result["expires_in"])
            session.roles = claims.get("realm_access", {}).get("roles", [])
        except (httpx.HTTPError, jwt.PyJWTError, ValueError, KeyError):
            sessions.revoke(sid)
            raise HTTPException(401, "Войдите повторно") from None
    return sid, session


@app.get("/api/session")
async def current(request: Request):
    async with lock:
        sid, session = await authenticate(request)
        response = JSONResponse({"name": session.name, "csrf": session.csrf,
                                 "needs_consent": session.provider == "yandex" and not consented(session.subject)})
        cookie(response, sessions.rotate(sid))
        return response


def consented(subject: str) -> bool:
    with psycopg.connect(os.environ["PROFILE_DSN"]) as connection:
        return connection.execute("SELECT 1 FROM profiles WHERE subject = %s", (subject,)).fetchone() is not None


@app.post("/api/consent")
async def consent(request: Request):
    async with lock:
        sid, session = await authenticate(request)
        if session.provider != "yandex":
            raise HTTPException(400, "Для этого входа согласие не требуется")
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(INTERNAL + "/broker/yandex/token",
                                        headers={"Authorization": "Bearer " + session.access})
            response.raise_for_status()
            try:
                broker = response.json()
            except ValueError:
                broker = response.text
            if isinstance(broker, str):
                from urllib.parse import parse_qs
                broker = {key: value[0] for key, value in parse_qs(broker).items()}
            profile = await client.get("https://login.yandex.ru/info", params={"format": "json"},
                                       headers={"Authorization": "OAuth " + broker["access_token"]})
            profile.raise_for_status()
        data = profile.json()
        selected = {key: data.get(key) for key in ("id", "login", "display_name", "default_email")}
        with psycopg.connect(os.environ["PROFILE_DSN"]) as connection:
            connection.execute("INSERT INTO profiles(subject, profile) VALUES (%s, %s::jsonb) "
                               "ON CONFLICT(subject) DO UPDATE SET profile=EXCLUDED.profile, consent_at=now()",
                               (session.subject, json.dumps(selected)))
        response = JSONResponse({"saved": True})
        cookie(response, sessions.rotate(sid))
        return response


@app.post("/auth/logout")
async def logout(request: Request):
    async with lock:
        sid, session = await authenticate(request)
        sessions.revoke(sid)
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                await client.post(INTERNAL + "/protocol/openid-connect/logout", data={
                    "client_id": CLIENT, "client_secret": SECRET,
                    "refresh_token": sessions.cipher.decrypt(session.refresh).decode(),
                })
            except httpx.HTTPError:
                pass
    response = JSONResponse({"logged_out": True})
    response.delete_cookie(COOKIE, path="/", secure=True, httponly=True)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/reports")
async def reports(request: Request):
    async with lock:
        sid, session = await authenticate(request)
        if session.provider == "yandex" and not consented(session.subject):
            raise HTTPException(403, "Сначала подтвердите использование профиля")
        async with httpx.AsyncClient(timeout=30) as client:
            upstream = await client.get(API + "/reports", params=request.query_params,
                                       headers={"Authorization": "Bearer " + session.access})
        response = Response(upstream.content, status_code=upstream.status_code, media_type="application/json")
        cookie(response, sessions.rotate(sid))
        return response


@app.get("/auth/download")
async def authorize_download(request: Request):
    async with lock:
        sid, session = await authenticate(request)
        uri = request.headers.get("x-original-uri", "").split("?", 1)[0]
        prefix = "/cdn/" + owner_key(ISSUER, session.subject) + "/"
        if "prothetic_user" not in session.roles or not uri.startswith(prefix) or ".." in uri:
            raise HTTPException(403, "Чужой отчёт недоступен")
        if session.provider == "yandex" and not consented(session.subject):
            raise HTTPException(403, "Требуется согласие")
        response = Response(status_code=204)
        cookie(response, sessions.rotate(sid))
        return response
