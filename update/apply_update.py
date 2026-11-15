"""Обновление проекта legal-assistant: простой режим сабмита, лог в stderr, исправленный локальный судья.

Запуск из КОРНЯ проекта (там, где compose.yaml):
    python update\apply_update.py

Что делает:
  * заменяет целиком 4 файла, которые трогали только мы: submission/solution.py, eval/judge.py,
    eval/local_platform.py, tests/test_fixes.py;
  * в legal_core/pipeline.py и legal_core/prompts.py НИЧЕГО не заменяет (там есть правки коллеги),
    а только дописывает в конец новые функции, если их ещё нет;
  * перед любым изменением кладёт копии старых файлов в папку .bak_update/<время>/.
"""

import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent / "files"
ROOT = Path.cwd()
REPLACE = ["submission/solution.py", "eval/judge.py", "eval/local_platform.py", "tests/test_fixes.py"]
APPEND = {
    "legal_core/pipeline.py": ("pipeline_append.py", "def answer_question_lean("),
    "legal_core/prompts.py": ("prompts_append.py", "LEAN_SYSTEM ="),
}


def main():
    if not (ROOT / "compose.yaml").exists() or not (ROOT / "legal_core").is_dir():
        sys.exit("Запустите из корня проекта legal-assistant (там, где compose.yaml): python update\\apply_update.py")
    backup = ROOT / ".bak_update" / time.strftime("%Y%m%d-%H%M%S")
    for rel in REPLACE + list(APPEND):
        if (ROOT / rel).exists():
            (backup / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / rel, backup / rel)
    print(f"Копии старых файлов: {backup.relative_to(ROOT)}")

    for rel in REPLACE:
        (ROOT / rel).parent.mkdir(parents=True, exist_ok=True)
        source = HERE / rel if (HERE / rel).exists() else HERE / (rel + ".txt")  # тест лежит как .txt, чтобы pytest не нашёл копию
        shutil.copy2(source, ROOT / rel)
        print(f"  заменён   {rel}")

    for rel, (block_name, marker) in APPEND.items():
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        needed = {"legal_core/pipeline.py": ["def extractive_answer(", "def add_missing_caveats", "extract_event_period"],
                  "legal_core/prompts.py": ["def date_line(", "def format_context("]}[rel]
        missing = [n for n in needed if n not in text and not (n == "def add_missing_caveats" and "add_missing_caveats" in text)]
        if missing:
            sys.exit(f"В {rel} нет {missing}: похоже, прошлое обновление (fixes-time-editions-docker) не применено. "
                     f"Пришлите этот файл — разберёмся.")
        if marker in text:
            print(f"  уже есть  {rel} (простой режим добавлен раньше)")
            continue
        path.write_text(text.rstrip("\n") + "\n" + (HERE / block_name).read_text(encoding="utf-8"), encoding="utf-8")
        print(f"  дописан   {rel}")

    print("\nГотово. Папку update теперь можно удалить. Дальше: python -m pytest -q tests")


if __name__ == "__main__":
    main()
