"""Конфигурация: модель (любой OpenAI-совместимый провайдер + fallback-цепочка), пути."""
from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

SKILLS_DIR = ROOT / "skills"
DATA_SOURCE = ROOT / "data" / "source"

def skills_dirs() -> list[str]:
    """Каталоги скиллов для агента: встроенный + (если есть) пользовательский.

    EXTRA_SKILLS_DIR читается динамически (его может выставить веб-морда после импорта).
    """
    dirs = [str(SKILLS_DIR)]
    extra = os.getenv("EXTRA_SKILLS_DIR", "").strip()
    if extra and Path(extra).is_dir():
        dirs.append(extra)
    return dirs

# Любой OpenAI-совместимый провайдер: задаётся через .env (base_url + ключ + модель).
# По умолчанию — OpenRouter, для обратной совместимости.
BASE_URL = (os.getenv("OPENAI_BASE_URL")
            or os.getenv("OPENROUTER_BASE_URL")
            or "https://openrouter.ai/api/v1")
API_KEY = (os.getenv("OPENROUTER_API_KEY")
           or os.getenv("OPENAI_API_KEY")
           or os.getenv("API_KEY")
           or "")
# Модель — открытая по умолчанию; для Gemini/Groq и т.п. задаётся в .env (MODEL_NAME)
MODEL_NAME = os.getenv("MODEL_NAME", "qwen/qwen3-coder")
# Дешёвая модель для построчной обработки ячеек (llm_process_column)
MAP_MODEL_NAME = os.getenv("MAP_MODEL_NAME", MODEL_NAME)
TEMPERATURE = float(os.getenv("TEMPERATURE", "0"))
RECURSION_LIMIT = int(os.getenv("RECURSION_LIMIT", "80"))
# Бесплатные тарифы жёстко лимитируют RPM (например, Gemini free = 5–15/мин).
# Клиент сам ждёт по Retry-After и повторяет — это спасает агентный цикл от 429.
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "8"))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "120"))


# Fallback-цепочка: при ошибке основного провайдера (например, 429/исчерпана квота)
# запрос автоматически уходит к следующему. Задаётся в .env как JSON-список:
#   MODEL_FALLBACKS=[{"base_url":"https://api.groq.com/openai/v1",
#                     "api_key_env":"GROQ_API_KEY","model":"llama-3.3-70b-versatile"}]
# Поля: base_url, model и (api_key | api_key_env). Пропущенные берутся из основного.
def _provider_specs() -> list[dict]:
    specs = [{"base_url": BASE_URL, "api_key": API_KEY, "model": MODEL_NAME}]
    raw = os.getenv("MODEL_FALLBACKS", "").strip()
    if raw:
        try:
            for item in json.loads(raw):
                key = item.get("api_key") or (
                    os.getenv(item["api_key_env"]) if item.get("api_key_env") else None)
                specs.append({
                    "base_url": item.get("base_url", BASE_URL),
                    "api_key": key or API_KEY,
                    "model": item.get("model", MODEL_NAME),
                })
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            print(f"[config] MODEL_FALLBACKS проигнорирован (плохой JSON): {e}")
    return specs


def _chat(spec: dict, model_name: str | None = None):
    from langchain_openai import ChatOpenAI

    if not spec["api_key"]:
        raise RuntimeError(
            "Не задан ключ модели. Впишите API_KEY (или OPENROUTER_API_KEY/OPENAI_API_KEY) "
            "в .env вместе с OPENAI_BASE_URL и MODEL_NAME."
        )
    return ChatOpenAI(
        model=model_name or spec["model"],
        api_key=spec["api_key"],
        base_url=spec["base_url"],
        temperature=TEMPERATURE,
        max_retries=MAX_RETRIES,
        timeout=REQUEST_TIMEOUT,
    )


def provider_specs() -> list[dict]:
    """Список провайдеров: [основной, ...фолбэки]. Публичная обёртка над _provider_specs."""
    return _provider_specs()


def n_providers() -> int:
    """Сколько провайдеров доступно (основной + фолбэки)."""
    return len(_provider_specs())


# Прозрачный with_fallbacks несовместим с deepagents.resolve_model (он ждёт BaseChatModel
# или строку), поэтому fallback реализован как РОТАЦИЯ на уровне задачи (см. agent.run_with_fallback):
# make_model(index) отдаёт обычный ChatOpenAI выбранного провайдера.
def make_model(index: int = 0):
    """Модель агента от провайдера №index (0 — основной)."""
    specs = _provider_specs()
    return _chat(specs[index % len(specs)])


def make_map_model(index: int = 0):
    """Модель для построчной обработки ячеек (дешёвая map-модель — на выбранном провайдере)."""
    specs = _provider_specs()
    spec = specs[index % len(specs)]
    # MAP_MODEL_NAME переопределяет модель только для основного провайдера
    override = MAP_MODEL_NAME if (index == 0 and MAP_MODEL_NAME != MODEL_NAME) else None
    return _chat(spec, override)
