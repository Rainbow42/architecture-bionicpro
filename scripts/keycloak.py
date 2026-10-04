import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
values = dotenv_values(root / '.env')
environment = {**os.environ, 'PATH': '/opt/podman/bin:' + os.environ['PATH']}
compose = ['docker', 'compose'] if shutil.which('docker') else [str(root / '.venv/bin/podman-compose')]


def execute(args, data=None):
    result = subprocess.run([*compose, 'exec', '-T', 'keycloak', *args], input=data, capture_output=True,
                            text=True, cwd=root, env=environment)
    if result.returncode:
        message = result.stderr
        for value in values.values():
            if value:
                message = message.replace(value, '[redacted]')
        raise RuntimeError(message)
    return result.stdout


execute(['sh', '-c', '/opt/keycloak/bin/kcadm.sh config credentials --server http://localhost:8080 '
         '--realm master --user admin --password "$KC_BOOTSTRAP_ADMIN_PASSWORD"'])
if len(sys.argv) > 1 and sys.argv[1] == 'yandex':
    if not values.get('YANDEX_CLIENT_ID') or not values.get('YANDEX_CLIENT_SECRET'):
        raise SystemExit('Set YANDEX_CLIENT_ID and YANDEX_CLIENT_SECRET in local .env; do not share them')
    payload = json.loads(execute(['/opt/keycloak/bin/kcadm.sh', 'get', 'identity-provider/instances/yandex', '-r', 'reports-realm']))
    payload['enabled'] = True
    payload['config'].update(clientId=values['YANDEX_CLIENT_ID'], clientSecret=values['YANDEX_CLIENT_SECRET'])
    execute(['/opt/keycloak/bin/kcadm.sh', 'update', 'identity-provider/instances/yandex', '-r', 'reports-realm', '-f', '-'], json.dumps(payload))
    print('Yandex provider enabled; verify real login and profile consent in the UI')
elif len(sys.argv) > 1 and sys.argv[1] == 'export':
    data = execute(['/opt/keycloak/bin/kcadm.sh', 'create',
                    'partial-export?exportClients=true&exportGroupsAndRoles=true', '-r', 'reports-realm', '-o'])
    realm = json.loads(data)
    realm.pop('users', None)
    for client in realm.get('clients', []):
        if client.get('clientId') == 'bionicpro-auth':
            client['secret'] = '${OIDC_CLIENT_SECRET}'
        elif 'secret' in client:
            client['secret'] = '${UNUSED_CLIENT_SECRET}'
    for provider in realm.get('identityProviders', []):
        if provider.get('alias') == 'yandex':
            provider['config']['clientId'] = '${YANDEX_CLIENT_ID}'
            provider['config']['clientSecret'] = '${YANDEX_CLIENT_SECRET}'
    for component in realm.get('components', {}).get('org.keycloak.storage.UserStorageProvider', []):
        if component.get('providerId') == 'ldap':
            component['config']['bindCredential'] = ['${LDAP_PASSWORD}']
    output = json.dumps(realm, ensure_ascii=False, indent=2) + '\n'
    for key, value in values.items():
        if key not in {'PUBLIC_URL', 'PIPELINE_MODE', 'S3_ACCESS_KEY'} and value and len(value) > 20 and value in output:
            raise RuntimeError(f'Refusing to export local value: {key}')
    (root / 'keycloak/keycloak-results-export.json').write_text(output)
    print('Exported live realm; user credentials and local secrets are excluded')
elif len(sys.argv) > 1 and sys.argv[1] == 'check':
    for username, can_report in [('john.doe', True), ('jane.smith', False)]:
        users = json.loads(execute(['/opt/keycloak/bin/kcadm.sh', 'get', 'users', '-r', 'reports-realm',
                                    '-q', 'username=' + username, '--fields', 'id,username']))
        user = next(user for user in users if user['username'] == username)
        roles = json.loads(execute(['/opt/keycloak/bin/kcadm.sh', 'get',
                                   f"users/{user['id']}/role-mappings/realm/composite", '-r', 'reports-realm']))
        assert ('prothetic_user' in {role['name'] for role in roles}) == can_report
        print('PASS: LDAP role mapping for ' + username)
    executions = json.loads(execute(['/opt/keycloak/bin/kcadm.sh', 'get',
                                     'authentication/flows/forms-mfa/executions', '-r', 'reports-realm']))
    assert any(item.get('providerId') == 'auth-otp-form' and item['requirement'] == 'REQUIRED' for item in executions)
    print('PASS: OTP is REQUIRED in live password flow')
else:
    raise SystemExit('Usage: python scripts/keycloak.py export|yandex|check')
