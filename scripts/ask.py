"""Задать вопрос из командной строки без запуска сервисов (индекс и модель — в этом процессе).

Пример:
  python scripts/ask.py "Какие документы нужно приложить к проекту договора?"
  python scripts/ask.py --json "…"   # полный ответ с трассировкой
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from legal_core.config import get_settings  # noqa: E402
from legal_core.index import load_index  # noqa: E402
from legal_core.pipeline import answer_question  # noqa: E402
from legal_core.runtime import make_local_deps  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    deps = make_local_deps(settings, load_index(settings["index_dir"]))
    result = answer_question(args.question, [], deps, settings)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        print(f"[{result['status']}] {result['latency_ms']} мс\n\n{result['answer_text']}")


if __name__ == "__main__":
    main()
