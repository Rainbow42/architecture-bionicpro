import json
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from backend import auth


@pytest.mark.parametrize('filename', ['realm-template.json', 'keycloak-results-export.json'])
def test_keycloak_client_includes_subject_scope(filename):
    realm = json.loads((Path(__file__).resolve().parents[1] / 'keycloak' / filename).read_text())
    client = next(client for client in realm['clients'] if client['clientId'] == auth.CLIENT)
    assert 'basic' in client['defaultClientScopes']


@pytest.mark.parametrize('failure', [None, 'missing_sub', 'wrong_audience', 'wrong_issuer',
                                   'wrong_nonce', 'wrong_subject', 'expired'])
def test_callback_validates_signed_tokens(monkeypatch, failure):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk['kid'] = 'test-key'
    real_client = httpx.AsyncClient

    def certificates(request):
        assert str(request.url) == auth.INTERNAL + '/protocol/openid-connect/certs'
        return httpx.Response(200, json={'keys': [jwk]})

    monkeypatch.setattr(auth.httpx, 'AsyncClient',
                        lambda **kwargs: real_client(transport=httpx.MockTransport(certificates), **kwargs))
    client = TestClient(auth.app, base_url='https://localhost:8443')
    login = client.get('/auth/login', follow_redirects=False)
    params = parse_qs(urlparse(login.headers['location']).query)
    state = params['state'][0]
    nonce = params['nonce'][0]
    now = int(time.time())
    common = {'iss': auth.ISSUER, 'sub': 'test-user', 'iat': now, 'exp': now + 120}
    identity = {**common, 'aud': auth.CLIENT, 'nonce': nonce}
    access = {**common, 'aud': 'reports-api'}
    if failure == 'missing_sub':
        del access['sub']
    elif failure == 'wrong_audience':
        access['aud'] = 'other-api'
    elif failure == 'wrong_issuer':
        access['iss'] = 'https://other.example.invalid'
    elif failure == 'wrong_nonce':
        identity['nonce'] = 'other-nonce'
    elif failure == 'wrong_subject':
        access['sub'] = 'other-user'
    elif failure == 'expired':
        access['exp'] = now - 10

    async def tokens(data):
        assert data['grant_type'] == 'authorization_code'
        assert auth.challenge(data['code_verifier']) == params['code_challenge'][0]
        return {'id_token': jwt.encode(identity, key, algorithm='RS256', headers={'kid': jwk['kid']}),
                'access_token': jwt.encode(access, key, algorithm='RS256', headers={'kid': jwk['kid']}),
                'refresh_token': 'test-refresh', 'expires_in': 120}

    monkeypatch.setattr(auth, 'tokens', tokens)
    try:
        response = client.get('/auth/callback', params={'state': state, 'code': 'test-code'},
                              follow_redirects=False)
        assert response.status_code == (401 if failure else 303)
        assert state not in auth.flows
        if failure:
            assert auth.COOKIE not in response.cookies
        else:
            sid = response.cookies[auth.COOKIE]
            assert auth.sessions.get(sid).subject == 'test-user'
            auth.sessions.revoke(sid)
        assert client.get('/auth/callback', params={'state': state, 'code': 'test-code'}).status_code == 400
    finally:
        auth.flows.pop(state, None)
