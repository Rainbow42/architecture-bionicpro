import json
import os
import time
from datetime import datetime, timedelta, timezone

import boto3
import psycopg2
import requests
from airflow import DAG
from airflow.operators.python import PythonOperator


def clickhouse(query, params=None):
    result = requests.post("http://clickhouse:8123", params={"database": "bionicpro", **(params or {})},
                           data=query.encode(), auth=("reports", os.environ["CLICKHOUSE_PASSWORD"]), timeout=120)
    result.raise_for_status()
    return result.text


def load_telemetry():
    # The teaching fixture is seven days; production ingestion needs a durable source watermark.
    with psycopg2.connect(os.environ["TELEMETRY_DSN"]) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id, prosthesis_id, measured_at, signal FROM signals WHERE measured_at < current_date")
        rows = [{"id": row[0], "prosthesis_id": row[1], "measured_at": row[2].astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                 "signal": row[3], "version": int(time.time())} for row in cursor.fetchall()]
    if rows:
        clickhouse("INSERT INTO telemetry FORMAT JSONEachRow\n" + "\n".join(map(json.dumps, rows)))


def load_crm():
    with psycopg2.connect(os.environ["CRM_DSN"]) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id, subject, model FROM prostheses")
        rows = [dict(zip(("id", "subject", "model"), row)) for row in cursor.fetchall()]
    clickhouse("TRUNCATE TABLE crm_batch")
    if rows:
        clickhouse("INSERT INTO crm_batch FORMAT JSONEachRow\n" + "\n".join(map(json.dumps, rows)))


def materialize(mode):
    if mode == "cdc":
        status = requests.get("http://kafka-connect:8083/connectors/crm/status", timeout=10)
        status.raise_for_status()
        state = status.json()
        if state["connector"]["state"] != "RUNNING" or not state["tasks"] or any(t["state"] != "RUNNING" for t in state["tasks"]):
            raise RuntimeError("CDC connector is not running")
        clickhouse("SYSTEM REFRESH VIEW reports_refresh")
        clickhouse("SYSTEM WAIT VIEW reports_refresh")
        version = int(clickhouse("SELECT max(generation) FROM reports_cdc"))
    else:
        version = int(time.time())
        clickhouse(f"""INSERT INTO reports_batch
            SELECT {version}, c.subject, toDate(t.measured_at), t.prosthesis_id, c.model,
                   count(), avg(t.signal), today()
            FROM (SELECT * FROM telemetry FINAL) t INNER JOIN crm_batch c ON c.id=t.prosthesis_id
            WHERE t.measured_at < toStartOfDay(now())
            GROUP BY c.subject, toDate(t.measured_at), t.prosthesis_id, c.model
            UNION ALL SELECT {version}, '', toDate('1970-01-01'), 0, '', 0, 0, today()""")
    return {"generation": version, "table": "reports_" + mode}


def publish(ti):
    manifest = ti.xcom_pull(task_ids="materialize")
    bounds = json.loads(clickhouse(
        f"SELECT minIf(day, subject != '') AS available_from, min(complete_until) AS complete_until "
        f"FROM {manifest['table']} WHERE generation = {int(manifest['generation'])} FORMAT JSONEachRow"))
    if bounds["available_from"] == "1970-01-01":
        raise RuntimeError("Telemetry source is empty")
    manifest.update(bounds)
    s3 = boto3.client("s3", endpoint_url="http://minio:9000", region_name="us-east-1",
                      aws_access_key_id=os.environ["S3_ACCESS_KEY"], aws_secret_access_key=os.environ["S3_SECRET_KEY"])
    s3.put_object(Bucket="reports", Key="manifests/current.json", Body=json.dumps(manifest).encode(),
                  ContentType="application/json")


mode = os.getenv("PIPELINE_MODE", "cdc")
if mode not in {"batch", "cdc"}:
    raise ValueError("PIPELINE_MODE must be batch or cdc")

with DAG("reports_" + mode, schedule="*/5 * * * *", start_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
         catchup=False, max_active_runs=1, default_args={"retries": 2, "retry_delay": timedelta(seconds=30)}) as dag:
    telemetry = PythonOperator(task_id="load_telemetry", python_callable=load_telemetry)
    prepare = PythonOperator(task_id="materialize", python_callable=materialize, op_kwargs={"mode": mode})
    ready = PythonOperator(task_id="publish", python_callable=publish)
    telemetry >> prepare >> ready
    if mode == "batch":
        crm = PythonOperator(task_id="load_crm", python_callable=load_crm)
        crm >> prepare
