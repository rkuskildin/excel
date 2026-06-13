"""Раннер мини-бенча: гоняет агента по задачам в нескольких конфигурациях.

Запуск:
  python bench/run_bench.py                          # все профили, все задачи
  python bench/run_bench.py --profiles skill --tasks T01_dedup T04_merge_attendance

Результаты: bench/results/results.json + results.md (таблица для защиты).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "bench"))

from validators import VALIDATORS  # noqa: E402

from excel_agent.agent import build_agent, run_task  # noqa: E402
from excel_agent.config import DATA_SOURCE, MODEL_NAME  # noqa: E402

PROFILES = ["baseline", "skill", "skill_subagent"]


def run_one(profile: str, task: dict, runs_dir: Path) -> dict:
    workdir = runs_dir / profile / task["id"]
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)
    for name in task["inputs"]:
        shutil.copy2(DATA_SOURCE / name, workdir / name)

    rec = {"profile": profile, "task": task["id"], "passed": False,
           "tokens": 0, "seconds": 0.0, "backup_made": False, "note": ""}
    t0 = time.time()
    try:
        agent = build_agent(profile, workdir)
        res = run_task(agent, task["prompt"])
        rec["tokens"] = res["tokens"]
        ok, msg = VALIDATORS[task["id"]](workdir)
        rec["passed"], rec["note"] = ok, msg
    except Exception as e:  # noqa: BLE001
        rec["note"] = f"ошибка запуска: {e}"
        traceback.print_exc()
    rec["seconds"] = round(time.time() - t0, 1)
    rec["backup_made"] = any(workdir.glob("*.bak"))
    return rec


def to_markdown(records: list[dict]) -> str:
    lines = [
        f"# Результаты мини-бенча (модель: {MODEL_NAME})", "",
        "| Профиль | Задача | Пройдена | Токены | Время, с | Бэкап .bak | Комментарий |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in records:
        lines.append(
            f"| {r['profile']} | {r['task']} | {'PASS' if r['passed'] else 'FAIL'} "
            f"| {r['tokens']} | {r['seconds']} | {'да' if r['backup_made'] else '—'} "
            f"| {r['note']} |")
    lines.append("")
    for p in sorted({r["profile"] for r in records}):
        sub = [r for r in records if r["profile"] == p]
        passed = sum(r["passed"] for r in sub)
        tokens = sum(r["tokens"] for r in sub)
        lines.append(f"**{p}**: {passed}/{len(sub)} задач, {tokens} токенов суммарно")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profiles", nargs="+", default=PROFILES, choices=PROFILES)
    ap.add_argument("--tasks", nargs="+", default=None)
    args = ap.parse_args()

    tasks = json.loads((ROOT / "bench" / "tasks.json").read_text(encoding="utf-8"))
    if args.tasks:
        tasks = [t for t in tasks if t["id"] in args.tasks]

    runs_dir = ROOT / "runs" / "bench"
    results_dir = ROOT / "bench" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict] = []
    for profile in args.profiles:
        for task in tasks:
            print(f"=== {profile} / {task['id']} ...", flush=True)
            rec = run_one(profile, task, runs_dir)
            print(f"    {'PASS' if rec['passed'] else 'FAIL'} | {rec['note']} "
                  f"| {rec['tokens']} ток. | {rec['seconds']} c", flush=True)
            records.append(rec)

    (results_dir / "results.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    (results_dir / "results.md").write_text(to_markdown(records), encoding="utf-8")
    print(f"\nИтоги: {results_dir / 'results.md'}")


if __name__ == "__main__":
    main()
