import base64
import hashlib
import secrets
import time
from dataclasses import dataclass

from cryptography.fernet import Fernet


def challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def owner_key(issuer: str, subject: str) -> str:
    return hashlib.sha256(f"{issuer}\n{subject}".encode()).hexdigest()


@dataclass
class Session:
    subject: str
    name: str
    access: str
    refresh: bytes
    access_until: float
    expires: float
    csrf: str
    roles: list[str]
    provider: str


class Sessions:
    def __init__(self, key: bytes, ttl: int = 3600, clock=time.time):
        self.cipher = Fernet(key)
        self.ttl = ttl
        self.clock = clock
        self.values: dict[str, Session] = {}

    def create(self, claims: dict, tokens: dict) -> str:
        sid = secrets.token_urlsafe(32)
        self.values[sid] = Session(
            subject=claims["sub"], name=claims.get("name", "Пользователь"),
            access=tokens["access_token"],
            refresh=self.cipher.encrypt(tokens["refresh_token"].encode()),
            access_until=min(claims["exp"], self.clock() + tokens["expires_in"]),
            expires=self.clock() + self.ttl, csrf=secrets.token_urlsafe(32),
            roles=claims.get("realm_access", {}).get("roles", []),
            provider=claims.get("identity_provider", "local"),
        )
        return sid

    def get(self, sid: str | None) -> Session | None:
        now = self.clock()
        for expired in [k for k, v in self.values.items() if v.expires <= now]:
            del self.values[expired]
        return self.values.get(sid)

    def rotate(self, sid: str) -> str:
        session = self.values.pop(sid)
        replacement = secrets.token_urlsafe(32)
        self.values[replacement] = session
        return replacement

    def revoke(self, sid: str):
        self.values.pop(sid, None)
