"""CLI: одна задача или интерактивный чат с Excel-агентом.

Примеры:
  python main.py --workdir runs/demo "Очисти столбец выручки в sales_2025.xlsx"
  python main.py --profile baseline --workdir runs/demo   # интерактивный режим
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from excel_agent.agent import build_agent, run_task  # noqa: E402
from excel_agent.config import DATA_SOURCE  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Excel deep-agent (DeepAgents + OpenRouter)")
    ap.add_argument("task", nargs="?", help="текст задачи; без него — интерактивный чат")
    ap.add_argument("--profile", default="skill_subagent",
                    choices=["baseline", "skill", "skill_subagent"])
    ap.add_argument("--workdir", default="runs/demo", help="рабочая папка агента")
    ap.add_argument("--fresh", action="store_true",
                    help="скопировать свежие демо-данные из data/source в workdir")
    args = ap.parse_args()

    workdir = Path(args.workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    if args.fresh:
        for f in DATA_SOURCE.glob("*.xlsx"):
            shutil.copy2(f, workdir / f.name)
        print(f"Демо-данные скопированы в {workdir}")

    agent = build_agent(args.profile, workdir)
    print(f"Профиль: {args.profile} | Рабочая папка: {workdir}")

    if args.task:
        res = run_task(agent, args.task)
        print(f"\n{res['answer']}\n\n[токены: {res['tokens']}, сообщений: {res['steps']}]")
        return

    print("Интерактивный режим. 'exit' — выход.")
    while True:
        try:
            q = input("\nВы: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q or q.lower() in ("exit", "quit", "выход"):
            break
        res = run_task(agent, q)
        print(f"\nАгент: {res['answer']}\n[токены: {res['tokens']}]")


if __name__ == "__main__":
    main()
