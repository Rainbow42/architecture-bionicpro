import json
from datetime import date

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import reports


def test_period_rejects_unprocessed_and_unbounded_dates():
    manifest = {"available_from": "2026-01-01", "complete_until": "2026-02-01"}
    assert reports.period("2026-01-01", "2026-01-03", manifest) == (date(2026, 1, 1), date(2026, 1, 3))
    for start, end, code in [("bad", "2026-01-03", 400), ("2026-01-03", "2026-01-01", 400),
                             ("2026-01-01", "2026-02-02", 409), ("2025-12-31", "2026-01-02", 409)]:
        with pytest.raises(HTTPException) as error:
            reports.period(start, end, manifest)
        assert error.value.status_code == code


def test_api_requires_authentication():
    client = TestClient(reports.app)
    assert client.get("/reports?start=2026-01-01&end=2026-01-02").status_code == 401


def test_cached_report_does_not_query_clickhouse(monkeypatch):
    async def identity(_request):
        return {"sub": "alice"}
    monkeypatch.setattr(reports, "principal", identity)
    manifest = {"available_from": "2026-01-01", "complete_until": "2026-02-01",
                "generation": 1, "table": "reports_cdc"}
    requested = []
    def read(key):
        requested.append(key)
        return json.dumps(manifest).encode() if key == "manifests/current.json" else b'{"rows":[]}'
    monkeypatch.setattr(reports, "object_bytes", read)
    def no_clickhouse(*args, **kwargs):
        raise AssertionError("ClickHouse must not be queried on an S3 hit")
    monkeypatch.setattr(reports.httpx, "AsyncClient", no_clickhouse)
    client = TestClient(reports.app)
    response = client.get("/reports?start=2026-01-01&end=2026-01-02")
    assert response.status_code == 200
    assert "/reports_cdc-1/" in response.json()["url"]
    assert len(requested) == 2
    manifest["generation"] = 2
    response2 = client.get("/reports?start=2026-01-01&end=2026-01-02")
    assert response2.json()["url"] != response.json()["url"]


def test_user_cannot_override_report_owner(monkeypatch):
    async def identity(_request):
        return {"sub": "alice"}
    monkeypatch.setattr(reports, "principal", identity)
    client = TestClient(reports.app)
    assert client.get("/reports?start=2026-01-01&end=2026-01-02&user_id=bob").status_code == 400


def test_private_origin_rejects_public_request(monkeypatch):
    monkeypatch.setenv("ORIGIN_KEY", "private-test-origin")
    assert TestClient(reports.app).get("/objects/anything").status_code == 403


def test_missing_snapshot_is_not_reported_as_empty_success(monkeypatch):
    async def identity(_request):
        return {"sub": "alice"}
    monkeypatch.setattr(reports, "principal", identity)
    monkeypatch.setattr(reports, "object_bytes", lambda key: None)
    assert TestClient(reports.app).get("/reports?start=2026-01-01&end=2026-01-02").status_code == 503
