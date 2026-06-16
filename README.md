# Excel Deep-Agent — агент-ассистент консультанта по данным

Учебный проект по мотивам консультации (см. `transcription (2) — нормализация и саммари.md`)
и ТЗ: deep-агент на открытом харнессе **DeepAgents** (LangChain), заточенный под обработку
Excel-файлов, с собственным скиллом, субагентом-чистильщиком и мини-бенчем
с детерминированными валидаторами.

## Основные функции

1. **Стандартная работа с файлом** — формулы, добавление столбцов, цвета и шрифты,
   изменение содержимого и вида ячеек, форматы чисел.
2. **Анализ табличных данных** — pandas и другие библиотеки, включая текстовые столбцы.
3. **Причесывание данных** — типы ячеек, битые значения и ошибки Excel, оформление
   пунктов/подпунктов, выравнивание ячеек по размерам.
4. **Графики средствами Excel** — нативные диаграммы через `openpyxl.chart`
   (Bar/Line/Pie/Scatter), не картинки matplotlib.
5. **Построчная LLM-обработка ячеек** — тул `llm_process_column` циклом пакетами
   прогоняет каждую строку текстового столбца через LLM (например, тональность
   каждого отзыва) и пишет результат в новый столбец, не забивая контекст агента.

## Состав

- **Агент** (`src/excel_agent/agent.py`) — DeepAgents + LocalShellBackend: файловые тулы,
  shell, todo-планирование. Таблицы — только программно, не в контекст.
- **Кастомные тулы** (`src/excel_agent/tools.py`) — `inspect_excel` (разведка),
  `backup_file` (бэкап `.bak`), `llm_process_column` (построчная LLM-обработка).
- **Excel-скилл** (`skills/excel/SKILL.md`, MIT) — оригинальный скилл по общеотраслевым
  стандартам: формулы вместо хардкода, ноль формульных ошибок, сохранение форматирования,
  чистка битых чисел, нативные графики, финмодельные цветовые/числовые конвенции,
  пересчёт формул через headless-LibreOffice. (Не содержит текста чужих лицензированных скиллов.)
- **Субагент `data-cleaner`** — чистый контекст, построчная чистка грязных столбцов.
- **Три профиля** для сравнения: `baseline` / `skill` / `skill_subagent`.
- **Мини-бенч** (`bench/`) — 8 задач с детерминированной проверкой.
- **Демо-данные** (`data/`) — «грязный» кейс консалтинга: продажи с битой выручкой
  и дублями, посещаемость двумя файлами, клиенты с пропусками, отзывы для классификации.

## Установка

Требуется **Python 3.11+**.

```bash
cd excel_agent
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # и впишите доступ к модели (см. ниже)
```

### Выбор модели (любой OpenAI-совместимый провайдер)

Провайдер задаётся через `.env` тремя переменными — менять код не нужно:

```ini
OPENAI_BASE_URL=...    # адрес API
API_KEY=...            # ключ
MODEL_NAME=...         # имя модели
TEMPERATURE=0
MAX_RETRIES=8          # авто-повтор при 429 (важно для бесплатных тарифов)
```

Готовые наборы:

| Провайдер | `OPENAI_BASE_URL` | `MODEL_NAME` | Где взять ключ |
|---|---|---|---|
| OpenRouter (по умолч.) | `https://openrouter.ai/api/v1` | `qwen/qwen3-coder` | openrouter.ai |
| Google Gemini (free) | `https://generativelanguage.googleapis.com/v1beta/openai/` | `gemini-2.5-flash` | aistudio.google.com |
| Groq (free) | `https://api.groq.com/openai/v1` | `llama-3.3-70b-versatile` | console.groq.com |
| DeepSeek | `https://api.deepseek.com` | `deepseek-chat` | platform.deepseek.com |

Совместимость: для обратной совместимости понимаются и `OPENROUTER_API_KEY`/`OPENAI_API_KEY`.
Для построчной обработки ячеек можно задать отдельную дешёвую модель — `MAP_MODEL_NAME`.

> ⚠️ Бесплатные тарифы жёстко лимитируют запросы (Gemini free — порядка 20/сутки на модель).
> Для надёжного прохождения бенча используйте платный ключ. `MAX_RETRIES` сглаживает
> кратковременные лимиты, но не дневной потолок.

### Fallback-цепочка провайдеров (необязательно)

Чтобы при лимите основного провайдера задача автоматически повторялась на резервном,
задайте `MODEL_FALLBACKS` — JSON-список провайдеров (поля `base_url`, `model` и
`api_key` либо `api_key_env` с именем переменной окружения):

```ini
MODEL_FALLBACKS=[{"base_url":"https://api.groq.com/openai/v1","api_key_env":"GROQ_API_KEY","model":"llama-3.3-70b-versatile"}]
```

Ротация — на уровне задачи (`agent.run_with_fallback`): если основной провайдер вернул
429/quota, агент пересобирается и повторяет задачу на следующем. Использует и веб-морда, и бенч.

## Запуск

```bash
# свежие демо-данные в рабочую папку + одна задача
python main.py --fresh --workdir runs/demo "Очисти столбец 'Выручка (грязная)' в sales_2025.xlsx и добавь числовой столбец 'Выручка'"

# построчная обработка отзывов
python main.py --workdir runs/demo "Определи тональность каждого отзыва в reviews.xlsx, результат в столбец 'Тональность'"

# интерактивный чат / другой профиль
python main.py --workdir runs/demo
python main.py --profile baseline --workdir runs/demo "..."
```

## Docker

Рекомендуемый способ «боевого» запуска (агент изолирован от хоста; в образе есть
LibreOffice для пересчёта формул):

```bash
docker build -t excel-agent .
docker run --rm --env-file .env -v "$PWD/runs:/app/runs" excel-agent \
  --fresh --workdir runs/demo "Очисти столбец 'Выручка (грязная)' в sales_2025.xlsx и добавь столбец 'Выручка'"
```

Том `runs/` пробрасывается на хост — результаты `.xlsx` остаются после завершения контейнера.

## Веб-интерфейс

`webapp.py` — лёгкая морда (FastAPI) поверх агента: загрузка `.xlsx`, постановка задачи,
скачивание результата. Защита — HTTP Basic Auth; при указании сертификата работает по HTTPS.

```bash
docker run -d --name excel-web --env-file .env \
  -e WEB_USER=admin -e WEB_PASSWORD='ваш-пароль' -e WEB_PORT=8600 \
  -e WEB_SSL_CERT=/tls/cert.pem -e WEB_SSL_KEY=/tls/key.pem \
  -v "$PWD/runs:/app/runs" -v "$PWD/tls:/tls:ro" \
  -p 8600:8600 --entrypoint python excel-agent webapp.py
# затем: https://<host>:8600  (логин/пароль из WEB_USER/WEB_PASSWORD)
```

Переменные: `WEB_USER`, `WEB_PASSWORD` (обязателен), `WEB_PORT` (8600), `WEB_PROFILE`
(`skill`), `WEB_SSL_CERT`/`WEB_SSL_KEY` (без них — обычный HTTP). Самоподписанный сертификат:
`openssl req -x509 -newkey rsa:2048 -nodes -days 825 -keyout tls/key.pem -out tls/cert.pem -subj "/CN=localhost"`.

## Бенч

```bash
python bench/run_bench.py                      # 3 профиля x 8 задач
python bench/run_bench.py --profiles baseline skill --tasks T01_dedup T07_native_chart
```

Результаты — `bench/results/results.md` (PASS/FAIL, токены, время, наличие бэкапа)
и `results.json` — материал для защиты: сравнение конфигураций.

| ID | Задача | Проверка |
|---|---|---|
| T01_dedup | удалить дубли строк | 60 уникальных строк, состав совпадает |
| T02_clean_revenue | распознать битый столбец выручки | значения = Часы x Ставка |
| T03_region_summary | свод по регионам на новый лист | суммы сходятся, исходник цел |
| T04_merge_attendance | объединить 2 списка посещаемости | 12 человек без дублей |
| T05_fill_segment | заполнить пропуски по правилу | правило соблюдено, старое цело |
| T06_rename_keep_format | переименовать заголовок | текст новый, заливка/ширина целы |
| T07_native_chart | свод + нативная диаграмма Excel | объект диаграммы на листе 'Свод', свод сходится |
| T08_review_sentiment | тональность каждого отзыва | точность >= 90% против эталонной разметки |

Корректность валидаторов проверена «золотыми решениями» (8/8 PASS) и
нетронутыми данными (8/8 FAIL).

## Структура

```
excel_agent/
├── main.py                  # CLI: задача или чат
├── webapp.py                # веб-интерфейс (FastAPI, Basic Auth, TLS)
├── Dockerfile               # образ с LibreOffice для пересчёта формул
├── project_overview.html    # описание проекта со схемой архитектуры
├── src/excel_agent/         # config (сменный провайдер), tools, agent
├── skills/excel/SKILL.md    # Excel-скилл (MIT)
├── data/                    # generate_demo_data.py + source/*.xlsx
├── bench/                   # tasks.json, validators.py, run_bench.py
└── runs/                    # рабочие папки запусков (создаются сами, в .gitignore)
```

## Безопасность

`LocalShellBackend` выполняет команды агента на машине без песочницы
(`virtual_mode=False`, агент видит реальные пути). Запускайте на доверенных данных;
для «боевого» режима используйте **Docker** (раздел выше) — агент изолирован в контейнере.

Веб-интерфейс закрыт паролем (Basic Auth) и поддерживает TLS. По открытому HTTP пароль
передаётся в base64 без шифрования — для публичного доступа используйте HTTPS
(`WEB_SSL_CERT`/`WEB_SSL_KEY`) или обратный прокси. Секреты (`.env`, `tls/`, пароли)
исключены из репозитория через `.gitignore`.
