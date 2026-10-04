import json
import time
from pathlib import Path

import httpx

config = json.loads(Path('/connector.json').read_text())
with httpx.Client(timeout=10) as client:
    for attempt in range(60):
        try:
            result = client.get('http://kafka-connect:8083/connectors')
            if result.status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(3)
    else:
        raise RuntimeError('Kafka Connect did not start')
    response = client.put('http://kafka-connect:8083/connectors/crm/config', json=config['config'])
    response.raise_for_status()
print('CRM connector configured')
