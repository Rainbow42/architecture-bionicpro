CREATE DATABASE IF NOT EXISTS bionicpro;
USE bionicpro;

CREATE TABLE IF NOT EXISTS telemetry (
    id UInt64, prosthesis_id UInt32, measured_at DateTime('UTC'), signal Float64,
    version UInt64
) ENGINE = ReplacingMergeTree(version) ORDER BY id;

CREATE TABLE IF NOT EXISTS crm_batch (
    id UInt32, subject String, model String
) ENGINE = MergeTree ORDER BY id;

CREATE TABLE IF NOT EXISTS crm_cdc (
    id UInt32, subject String, model String, version UInt64, deleted UInt8
) ENGINE = ReplacingMergeTree(version) ORDER BY id;

CREATE TABLE IF NOT EXISTS crm_queue (raw String)
ENGINE = Kafka SETTINGS kafka_broker_list = 'kafka:9092',
    kafka_topic_list = 'crm.public.prostheses', kafka_group_name = 'clickhouse-crm',
    kafka_format = 'JSONAsString', kafka_num_consumers = 1;

CREATE MATERIALIZED VIEW IF NOT EXISTS crm_ingest TO crm_cdc AS
WITH JSONExtractString(raw, 'op') AS op,
     if(op = 'd', JSONExtractRaw(raw, 'before'), JSONExtractRaw(raw, 'after')) AS row
SELECT toUInt32(JSONExtractUInt(row, 'id')) AS id,
       JSONExtractString(row, 'subject') AS subject,
       JSONExtractString(row, 'model') AS model,
       JSONExtractUInt(raw, 'source', 'lsn') AS version,
       toUInt8(op = 'd') AS deleted
FROM crm_queue WHERE op IN ('r', 'c', 'u', 'd');

CREATE TABLE IF NOT EXISTS reports_batch (
    generation UInt64, subject String, day Date, prosthesis_id UInt32,
    model String, samples UInt64, avg_signal Float64, complete_until Date
) ENGINE = MergeTree ORDER BY (generation, subject, day, prosthesis_id);

CREATE TABLE IF NOT EXISTS reports_cdc AS reports_batch
ENGINE = MergeTree ORDER BY (generation, subject, day, prosthesis_id);

CREATE MATERIALIZED VIEW IF NOT EXISTS reports_refresh
REFRESH EVERY 1 DAY APPEND TO reports_cdc AS
SELECT toUInt64(toUnixTimestamp(now())) AS generation, c.subject AS subject,
       toDate(t.measured_at) AS day, t.prosthesis_id AS prosthesis_id, c.model AS model,
       count() AS samples, avg(t.signal) AS avg_signal, today() AS complete_until
FROM (SELECT * FROM telemetry FINAL) t
INNER JOIN (SELECT * FROM crm_cdc FINAL WHERE deleted = 0) c ON c.id = t.prosthesis_id
WHERE t.measured_at < toStartOfDay(now())
GROUP BY subject, day, prosthesis_id, model
UNION ALL
SELECT toUInt64(toUnixTimestamp(now())), '', toDate('1970-01-01'), 0, '', 0, 0, today();
