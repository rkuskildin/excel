"""Сборка deep-агента для работы с Excel.

Три конфигурации (для сравнения на бенче, как советовал ментор):
  baseline       — агент без скилла и субагентов;
  skill          — + Excel-скилл (skills/excel/SKILL.md);
  skill_subagent — + субагент data-cleaner с чистым контекстом.
"""
from __future__ import annotations

from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends import LocalShellBackend

from .config import RECURSION_LIMIT, SKILLS_DIR, make_model
from .tools import CUSTOM_TOOLS

SYSTEM_PROMPT = """Ты — агент-ассистент консультанта по данным. Твоя специализация —
обработка Excel-файлов. Основные функции:
1. Стандартная работа с файлом: формулы, добавление столбцов, цвета и шрифты,
   изменение содержимого и вида ячеек, форматы чисел.
2. Анализ табличных данных через pandas и другие библиотеки (включая текстовые столбцы).
3. Причесывание данных: типы ячеек, битые значения и ошибки Excel, оформление
   пунктов/подпунктов, выравнивание ячеек по размерам.
4. Графики ВНУТРЕННИМИ средствами Excel (openpyxl.chart — нативные диаграммы,
   не картинки matplotlib).
5. Построчная обработка текстовых ячеек: для задач «обработай каждую строку»
   (тональность отзывов, классификация) используй тул llm_process_column —
   он циклом прогоняет каждую ячейку через LLM, не забивая твой контекст.

Правила работы:
- Работай ТОЛЬКО внутри рабочей папки; пути используй абсолютные.
- С таблицами работай программно (pandas/openpyxl через execute), не загружай их
  целиком в контекст: разведка через inspect_excel, дальше точечные скрипты.
- Перед первым изменением существующего файла делай backup_file (исходник
  пользователя незаметно не менять).
- Поверх оформленного файла пиши только через openpyxl (to_excel ломает стили).
- После выполнения проверь результат программно (перечитай файл, сверь 2-3 значения)
  и только потом отвечай.
- Отвечай кратко: что сделано, какие файлы изменены/созданы, контрольные цифры.
"""

CLEANER_SUBAGENT = {
    "name": "data-cleaner",
    "description": (
        "Субагент для грязной построчной чистки данных: битые числовые столбцы "
        "(смешанные форматы '1 234,50 руб.'), ошибки Excel (#DIV/0!, #N/A), пропуски, "
        "кривые типы. Передавай ему абсолютный путь к файлу, имя листа/столбца "
        "и критерий чистоты."
    ),
    "system_prompt": """Ты — субагент-чистильщик табличных данных. Тебе дают файл,
столбец и критерий чистоты. Действуй так:
1. inspect_excel для разведки; backup_file перед изменением.
2. Напиши python-функцию нормализации и пройди ЦИКЛОМ по строкам столбца;
   собери список ячеек, которые не распознались, и обработай их отдельно.
3. Записывай результат через openpyxl поверх существующей книги (не to_excel),
   чтобы не сломать форматирование.
4. Проверь результат программно и верни короткий отчёт: сколько строк обработано,
   сколько было проблемных, контрольная сумма столбца.
Не делай ничего за пределами поставленной задачи чистки.""",
}


def build_agent(profile: str = "skill_subagent", workdir: str | Path = "."):
    """Создаёт агента заданного профиля, работающего в папке workdir."""
    if profile not in ("baseline", "skill", "skill_subagent"):
        raise ValueError(f"Неизвестный профиль: {profile}")
    workdir = Path(workdir).resolve()
    model = make_model()
    # virtual_mode=False: агент работает с реальными абсолютными путями.
    # Это удобнее (см. лекцию про относительные/абсолютные пути), но НЕ даёт
    # песочницы — запускайте на доверенных данных или заверните в Docker.
    backend = LocalShellBackend(root_dir=workdir, virtual_mode=False)

    kwargs: dict = {
        "model": model,
        "tools": CUSTOM_TOOLS,
        "system_prompt": SYSTEM_PROMPT + f"\nРабочая папка: {workdir}",
        "backend": backend,
    }
    if profile in ("skill", "skill_subagent"):
        kwargs["skills"] = [str(SKILLS_DIR)]
    if profile == "skill_subagent":
        kwargs["subagents"] = [CLEANER_SUBAGENT]

    return create_deep_agent(**kwargs)


def run_task(agent, task: str) -> dict:
    """Запускает задачу, возвращает {'answer': str, 'tokens': int, 'steps': int}."""
    result = agent.invoke(
        {"messages": [{"role": "user", "content": task}]},
        config={"recursion_limit": RECURSION_LIMIT},
    )
    messages = result["messages"]
    tokens = 0
    for m in messages:
        usage = getattr(m, "usage_metadata", None)
        if usage:
            tokens += usage.get("total_tokens", 0)
    return {
        "answer": messages[-1].content if messages else "",
        "tokens": tokens,
        "steps": len(messages),
    }
