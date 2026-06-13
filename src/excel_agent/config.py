"""Конфигурация: модель через OpenRouter, пути проекта."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

SKILLS_DIR = ROOT / "skills"
DATA_SOURCE = ROOT / "data" / "source"

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


def _make(model_name: str):
    from langchain_openai import ChatOpenAI

    if not API_KEY:
        raise RuntimeError(
            "Не задан ключ модели. Впишите API_KEY (или OPENROUTER_API_KEY/OPENAI_API_KEY) "
            "в .env вместе с OPENAI_BASE_URL и MODEL_NAME."
        )
    return ChatOpenAI(
        model=model_name,
        api_key=API_KEY,
        base_url=BASE_URL,
        temperature=TEMPERATURE,
        max_retries=MAX_RETRIES,
        timeout=REQUEST_TIMEOUT,
    )


def make_model():
    """Основная модель агента."""
    return _make(MODEL_NAME)


def make_map_model():
    """Модель для построчной обработки ячеек (можно дешевле основной)."""
    return _make(MAP_MODEL_NAME)
