CREATE DATABASE crm;
CREATE DATABASE telemetry;
CREATE DATABASE profiles;
CREATE DATABASE airflow;
CREATE DATABASE keycloak;
\connect crm
CREATE TABLE prostheses (
    id integer PRIMARY KEY,
    subject text NOT NULL,
    model text NOT NULL
);
INSERT INTO prostheses VALUES
    (1, '11111111-1111-4111-8111-111111111111', 'Hand A'),
    (2, '22222222-2222-4222-8222-222222222222', 'Hand B');
ALTER TABLE prostheses REPLICA IDENTITY FULL;
CREATE PUBLICATION bionicpro_crm FOR TABLE prostheses;
\connect telemetry
CREATE TABLE signals (
    id bigint PRIMARY KEY,
    prosthesis_id integer NOT NULL,
    measured_at timestamptz NOT NULL,
    signal double precision NOT NULL
);
INSERT INTO signals
SELECT n * 1000 + p, p, current_date - (n || ' days')::interval + interval '12 hours', 0.4 + n * 0.01
FROM generate_series(1, 7) n CROSS JOIN generate_series(1, 2) p;
\connect profiles
CREATE TABLE profiles (
    subject text PRIMARY KEY,
    profile jsonb NOT NULL,
    consent_at timestamptz NOT NULL DEFAULT now()
);
