import os
import time
from urllib.parse import urlsplit, urlunsplit

import httpx
import psycopg

dsn = urlsplit(os.environ['PROFILE_DSN'])
crm = urlunsplit(dsn._replace(path='/crm'))
fixture_id = 990001


def state():
    result = httpx.post('http://clickhouse:8123', auth=('reports', os.environ['CLICKHOUSE_PASSWORD']),
                        content=f'SELECT model, deleted FROM bionicpro.crm_cdc FINAL WHERE id={fixture_id} FORMAT TabSeparated', timeout=10)
    result.raise_for_status()
    return result.text.strip()


def await_state(expected):
    for attempt in range(30):
        if state() == expected:
            return
        time.sleep(1)
    raise AssertionError(f'CDC state did not become {expected!r}')


with psycopg.connect(crm, autocommit=True) as connection:
    if connection.execute('SELECT 1 FROM prostheses WHERE id=%s', (fixture_id,)).fetchone():
        raise RuntimeError('Test fixture ID already exists; it was not changed')
    try:
        connection.execute('INSERT INTO prostheses VALUES (%s, %s, %s)', (fixture_id, 'synthetic-cdc-test', 'Before'))
        await_state('Before\t0')
        print('PASS: CRM INSERT -> Debezium -> Kafka -> ClickHouse')
        connection.execute('UPDATE prostheses SET model=%s WHERE id=%s', ('After', fixture_id))
        await_state('After\t0')
        print('PASS: CRM UPDATE replaces old state')
    finally:
        connection.execute('DELETE FROM prostheses WHERE id=%s', (fixture_id,))
    await_state('After\t1')
    print('PASS: CRM DELETE preserved as tombstone')
