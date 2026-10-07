import os
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
values = dotenv_values(root / '.env')
secrets = [value for key, value in values.items() if value and key not in {'PUBLIC_URL', 'PIPELINE_MODE', 'S3_ACCESS_KEY'}]
engine = os.environ.get('COMPOSE_COMMAND', str(root / '.venv/bin/podman-compose'))
environment = {**os.environ, 'PATH': '/opt/podman/bin:' + os.environ['PATH']}
process = subprocess.Popen([engine, *sys.argv[1:]], cwd=root, env=environment,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
try:
    for line in process.stdout:
        for secret in secrets:
            line = line.replace(secret, '[redacted]')
        print(line, end='', flush=True)
except KeyboardInterrupt:
    process.terminate()
raise SystemExit(process.wait())
