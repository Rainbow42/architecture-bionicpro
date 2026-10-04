import json

from fastapi.testclient import TestClient

import reports

# This checks live storage, not an end-to-end login: identity is supplied by the harness.
subject = '11111111-1111-4111-8111-111111111111'


async def identity(request):
    return {'sub': subject}


reports.principal = identity
manifest = json.loads(reports.object_bytes('manifests/current.json'))
client = TestClient(reports.app)
params = {'start': manifest['available_from'], 'end': manifest['complete_until']}
first = client.get('/reports', params=params)
assert first.status_code == 200, first.text
url = first.json()['url']
payload = json.loads(reports.object_bytes(url.removeprefix('/cdn/')))
assert payload['rows'] and {row['prosthesis_id'] for row in payload['rows']} == {1}
print('PASS: published CDC snapshot -> own rows from ClickHouse -> private S3 report')

subject = '22222222-2222-4222-8222-222222222222'
second = client.get('/reports', params=params)
assert second.status_code == 200, second.text
assert second.json()['url'] != url
payload2 = json.loads(reports.object_bytes(second.json()['url'].removeprefix('/cdn/')))
assert payload2['rows'] and {row['prosthesis_id'] for row in payload2['rows']} == {2}
print('PASS: separate subject selects a separate set of prostheses and S3 namespace')


class NoDatabase:
    def __init__(self, *args, **kwargs):
        raise AssertionError('Cache hit must not access ClickHouse')


reports.httpx.AsyncClient = NoDatabase
repeat = client.get('/reports', params=params)
assert repeat.status_code == 200 and repeat.json()['url'] == second.json()['url']
print('PASS: repeated request returns S3 report without a database call')
print('NOTE: real OIDC/MFA login and CDN authorization must be checked separately')
