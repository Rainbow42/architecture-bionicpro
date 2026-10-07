import base64
import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / '.env'
if path.exists():
    raise SystemExit('.env already exists; left unchanged')
values = {'PUBLIC_URL': 'http://localhost:8088', 'PIPELINE_MODE': 'cdc',
          'SESSION_KEY': base64.urlsafe_b64encode(os.urandom(32)).decode(),
          'S3_ACCESS_KEY': 'bionicpro-local'}
for key in ('OIDC_CLIENT_SECRET', 'ORIGIN_KEY', 'POSTGRES_PASSWORD', 'CLICKHOUSE_PASSWORD',
            'S3_SECRET_KEY', 'ADMIN_PASSWORD', 'DEMO_PASSWORD', 'LDAP_PASSWORD'):
    values[key] = secrets.token_hex(24)
with open(path, 'x', opener=lambda path, flags: os.open(path, flags, 0o600)) as stream:
    stream.write(''.join(f'{key}={value}\n' for key, value in values.items()))
print('Created local .env; credentials are not printed')
