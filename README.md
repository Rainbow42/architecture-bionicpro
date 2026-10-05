# Девятый спринт

BionicPRO: вход через серверные сессии, отчётность в ClickHouse, приватный S3-кеш и CDC из CRM.

Работа ещё не готова к сдаче: остаются настоящий вход через Яндекс ID и завершение сквозных проверок. OTP на локальном стенде настроен, вход и скачивание отчёта из браузера работают. Это не подтверждение прохождения ревью.

## Запуск

Нужны Docker Compose и Python 3.12+.

```sh
python3 scripts/init.py
docker compose up --build -d
```

`scripts/init.py` создаёт непубликуемый `.env` с отдельными локальными паролями. Повторный запуск не перезаписывает его. Пароль тестовых пользователей `prothetic1` и `prothetic2` находится в `DEMO_PASSWORD`. Пароль Keycloak admin — в `ADMIN_PASSWORD`. Не добавляйте `.env` в Git.

Открыть `http://localhost:8088`. При первом входе нужно настроить OTP. Отчёт готовится за полные дни; данные за текущий день вернут 409. После первого успешного DAG появится манифест готовой витрины. Интерфейс позволяет скачать только собственный отчёт.

Если стенд был создан до добавления scope `basic`, выполните `.venv/bin/python scripts/keycloak.py configure`. Эта команда обновляет только scopes клиента и сохраняет пользователей, OTP и роли. Повторный запуск контейнера с `--import-realm` существующий realm не обновляет. После исправления начните вход с главной страницы, не обновляйте старый `/auth/callback`: код входа одноразовый.

По умолчанию используется CDC. Для промежуточного варианта второго задания задайте `PIPELINE_MODE=batch` и пересоздайте только Airflow. Не запускайте оба DAG одновременно с одним manifest.

## Файлы

- `backend/auth.py`, `backend/security.py` — bionicpro-auth, PKCE, обновление токенов и ротация сессии.
- `keycloak/realm-template.json`, `ldap/config.ldif` — начальная конфигурация Keycloak, обязательный OTP, каталог и роли.
- `keycloak/keycloak-results-export.json` — экспорт работающего realm без секретов и пользовательских credentials.
- `backend/reports.py` — API отчётности и приватный origin для кеша.
- `airflow/dags/reports.py` — расписание, batch/CDC и публикация завершённой версии.
- `clickhouse/init.sql`, `debezium/connector.json` — Kafka Engine, две Materialized View, обработка изменений CRM.
- `nginx/default.conf.template` — проверка владельца перед выдачей из CDN-кеша.
- `docs/architecture.md` — решения, ограничения и размещение данных по странам.
- `docs/bionicpro.drawio` — исходный контур, региональная аутентификация и отчётность.
- `minio/Dockerfile` — сборка официального MinIO из исходников закреплённого релиза: старые готовые образы недоступны.

Исходный `keycloak/realm-export.json` — файл шаблона курса; стенд его не использует.

Просмотр схем без редактора: [существующий контур](docs/existing.svg), [вход](docs/authentication.svg), [отчёты и CDC](docs/reporting.svg).

## Проверки

```sh
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt pytest
.venv/bin/pytest -q
cd frontend
npm ci
npm run build
npm audit
```

Модульные тесты проверяют PKCE, шифрование refresh token, ротацию и срок сессии, обновление access token, CSRF, запрет чужих отчётов, неподготовленный период и отсутствие обращения в ClickHouse при попадании в S3. Эти тесты не заменяют проверку живого Keycloak, LDAP, MFA, CDC и Яндекс ID.

Результаты стенда — в `docs/checks`. Оставшиеся шаги с аккаунтом — в [docs/manual-checks.md](docs/manual-checks.md).
