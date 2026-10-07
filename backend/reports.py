import asyncio
import json
import os
import re
import secrets
from datetime import date

import boto3
import httpx
import jwt
from botocore.exceptions import ClientError
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from auth import validate
from security import owner_key

BUCKET = os.getenv("REPORT_BUCKET", "reports")
ISSUER = os.getenv("PUBLIC_URL", "https://localhost:8443") + "/realms/reports-realm"
S3 = boto3.client("s3", endpoint_url=os.getenv("S3_ENDPOINT", "http://minio:9000"),
                  aws_access_key_id=os.getenv("S3_ACCESS_KEY", ""),
                  aws_secret_access_key=os.getenv("S3_SECRET_KEY", ""), region_name="us-east-1")
app = FastAPI()
generation_lock = asyncio.Lock()


def object_bytes(key: str) -> bytes | None:
    try:
        result = S3.get_object(Bucket=BUCKET, Key=key)
        return result["Body"].read()
    except ClientError as error:
        if error.response["Error"]["Code"] in {"NoSuchKey", "404"}:
            return None
        raise


def period(start: str, end: str, manifest: dict):
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        raise HTTPException(400, "Период задаётся в формате YYYY-MM-DD") from None
    if first >= last or (last - first).days > 366:
        raise HTTPException(400, "Нужен непустой период не длиннее года; конец не включается")
    if first < date.fromisoformat(manifest["available_from"]) or last > date.fromisoformat(manifest["complete_until"]):
        raise HTTPException(409, {"message": "Этот период ещё не подготовлен",
                                  "available_from": manifest["available_from"],
                                  "complete_until": manifest["complete_until"]})
    return first, last


async def principal(request: Request):
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(401, "Нужно войти")
    try:
        claims = await validate(header[7:], "reports-api")
    except (ValueError, KeyError, jwt.PyJWTError, httpx.HTTPError):
        raise HTTPException(401, "Недействительный токен") from None
    if "prothetic_user" not in claims.get("realm_access", {}).get("roles", []):
        raise HTTPException(403, "Роль не даёт доступа к отчётам")
    return claims


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/reports")
async def reports(request: Request, start: str, end: str):
    claims = await principal(request)
    if set(request.query_params) != {"start", "end"}:
        raise HTTPException(400, "Допустимы только start и end; пользователь берётся из сессии")
    raw_manifest = object_bytes("manifests/current.json")
    if raw_manifest is None:
        raise HTTPException(503, "Дождитесь первой обработки данных")
    manifest = json.loads(raw_manifest)
    first, last = period(start, end, manifest)
    version = int(manifest["generation"])
    table = manifest["table"]
    if table not in {"reports_batch", "reports_cdc"}:
        raise HTTPException(503, "Некорректная версия витрины")
    key = f"{owner_key(ISSUER, claims['sub'])}/{table}-{version}/{first}_{last}.json"
    async with generation_lock:
        cached = object_bytes(key)
        if cached is None:
            query = f"SELECT day, prosthesis_id, model, samples, avg_signal FROM {table} " \
                    "WHERE subject = {subject:String} AND generation = {generation:UInt64} " \
                    "AND day >= {start:Date} AND day < {end:Date} ORDER BY day, prosthesis_id FORMAT JSONEachRow"
            async with httpx.AsyncClient(timeout=25) as client:
                result = await client.post(os.getenv("CLICKHOUSE_URL", "http://clickhouse:8123"),
                                           auth=("reports", os.environ["CLICKHOUSE_PASSWORD"]),
                                           params={"database": "bionicpro", "param_subject": claims["sub"],
                                                   "param_generation": version, "param_start": str(first),
                                                   "param_end": str(last)}, content=query)
                result.raise_for_status()
            rows = [json.loads(line) for line in result.text.splitlines() if line]
            cached = json.dumps({"from": str(first), "until": str(last), "generation": version,
                                 "rows": rows}, ensure_ascii=False).encode()
            S3.put_object(Bucket=BUCKET, Key=key, Body=cached, ContentType="application/json")
    return {"url": "/cdn/" + key, "generation": version, "complete_until": manifest["complete_until"]}


@app.get("/objects/{key:path}")
def download(key: str, request: Request):
    expected = os.environ["ORIGIN_KEY"]
    if not expected or not secrets.compare_digest(request.headers.get("x-origin-key", ""), expected):
        raise HTTPException(403, "Недоступно напрямую")
    if not re.fullmatch(r"[a-f0-9]{64}/reports_(?:batch|cdc)-\d+/\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}\.json", key):
        raise HTTPException(404)
    data = object_bytes(key)
    if data is None:
        raise HTTPException(404)
    return Response(data, media_type="application/json", headers={"Cache-Control": "public, max-age=300"})
