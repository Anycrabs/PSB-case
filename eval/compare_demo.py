"""Ответы системы рядом с образцом организаторов (data/demo/demo_output.csv).

  DATA_DIR=data/demo python submission/solution.py   # создаёт submission.csv по демо-вопросам
  python eval/compare_demo.py                        # печатает вопрос, наш ответ и образец
"""

import csv
import sys
import textwrap


def read(path, key):
    with open(path, encoding="utf-8-sig") as fh:
        return {row["id"]: row[key] for row in csv.DictReader(fh)}


def main():
    ours = read(sys.argv[1] if len(sys.argv) > 1 else "submission.csv", "answer")
    gold = read("data/demo/demo_output.csv", "answer")
    questions = read("data/demo/questions.csv", "question")
    for qid in sorted(gold, key=int):
        print(f"\n=== {qid}. {questions.get(qid, '')}")
        print(textwrap.fill("НАШ:     " + ours.get(qid, "—"), 110, subsequent_indent=" " * 9))
        print(textwrap.fill("ОБРАЗЕЦ: " + gold[qid], 110, subsequent_indent=" " * 9))


if __name__ == "__main__":
    main()
