# Деплой Excel-агента в Docker

Воспроизводимая конфигурация запуска веб-морды (то, что раньше жило в ручных
`docker run`-командах): переменные окружения, тома, порты, TLS, папка скиллов.

## Быстрый старт

```bash
cd deploy
cp .env.example .env            # вписать WEB_PASSWORD (логин по умолчанию admin)
cp ../.env.example ../.env      # вписать доступ к модели (см. таблицу провайдеров в ../README.md)
./gen-cert.sh <host-или-ip>     # самоподписанный TLS; либо положи свой в ./tls/{cert,key}.pem
docker compose up -d --build
```

Морда: `https://<host>:<WEB_PORT>` (по умолчанию порт **8600**), логин/пароль — из `deploy/.env`.

## Что куда монтируется

| Переменная / том | Назначение |
|---|---|
| `../.env` (env_file) | провайдер модели: `OPENAI_BASE_URL`, `API_KEY`, `MODEL_NAME`, `MODEL_FALLBACKS` |
| `deploy/.env` | деплой-переменные: `WEB_USER`, `WEB_PASSWORD`, `WEB_PORT`, `WEB_PROFILE` |
| `./runs` → `/app/runs` | результаты `.xlsx` (`runs/web`) и загруженные скиллы (`runs/skills`) — переживают пересоздание |
| `./tls` → `/tls` (ro) | TLS-сертификат; если файлов нет — морда поднимется по обычному HTTP |

## Управление

```bash
docker compose logs -f excel-web      # логи
docker compose restart excel-web      # перезапуск
docker compose down                   # остановить и удалить контейнер
docker compose up -d --build          # пересобрать образ и поднять
```

## CLI и бенч в том же образе (без морды)

```bash
# одна задача
docker run --rm --env-file ../.env -v "$PWD/runs:/app/runs" excel-agent:latest \
  --fresh --workdir runs/demo "Очисти столбец 'Выручка (грязная)' в sales_2025.xlsx и добавь столбец 'Выручка'"

# мини-бенч (3 профиля x 8 задач)
docker run --rm --env-file ../.env -v "$PWD/runs:/app/runs" \
  --entrypoint python excel-agent:latest bench/run_bench.py
```

## Боевой HTTPS (вместо самоподписанного)

Нужен домен, направленный на сервер. Получи сертификат Let's Encrypt (например, через
`certbot`) и положи `fullchain.pem`/`privkey.pem` в `deploy/tls/` как `cert.pem`/`key.pem`
(или поправь пути в `docker-compose.yml`), затем `docker compose up -d`.

> ⚠️ Секреты (`deploy/.env`, `../.env`, `deploy/tls/`, `deploy/runs/`) исключены из git
> через `.gitignore`. В репозиторий попадают только `*.example` и эта инструкция.
