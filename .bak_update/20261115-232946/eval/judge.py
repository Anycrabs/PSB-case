"""LLM-судья по методике организаторов (Технический регламент, раздел 8).

Каждый вопрос оценивается дважды: раунд 1 — A=baseline, B=наш ответ; раунд 2 — A=наш, B=baseline.
Шкала для ответа команды относительно baseline: 1.00 / 0.75 / 0.50 / 0.25 / 0.00, ориентир — эталон (golden).
Выдуманный факт в ответе команды обнуляет вопрос. Итог: Score = 0.7 * MatchRate + 0.3 * WinRate.

Локально (нужна любая OpenAI-совместимая LLM в LLM_BASE_URL / LLM_API_KEY / LLM_MODEL):
  python eval/judge.py --questions data/demo/demo_questions.csv --golden data/demo/demo_output.csv \
                       --ours submission.csv --baseline baseline.csv
На платформе тот же код вызывается из зонда solution.py (модуль lc_judge.py в ZIP).
Оговорка: судья организаторов, вероятно, сильнее модели решения; здесь судит та же модель — это ориентир, а не точный балл.
"""

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from legal_core.llm import chat_json  # noqa: E402

JUDGE_SYSTEM = """Ты — строгий судья ответов юридического ассистента. Ассистент обязан отвечать только по базе
документов компании и законов; эталонный ответ (golden) утверждён юристами и считается верным.
Сравни ответы A и B на вопрос, ориентируясь на эталон: точность фактов (суммы, сроки, ставки, пункты, документы),
полнота относительно эталона, поправка ошибочных предпосылок вопроса, честный отказ, если эталон говорит, что
ответа в базе нет. Длина и стиль вторичны. Факт, которого нет в эталоне и который противоречит ему или выглядит
выдуманным (несуществующий пункт, сумма, документ), — тяжёлая ошибка.
Верни только JSON:
{"verdict": "B_much_better | B_better | equal | B_worse | B_much_worse",
 "a_fabricated": true или false — есть ли в ответе A выдуманный или противоречащий эталону факт,
 "b_fabricated": true или false — то же для ответа B,
 "reason": "одно короткое предложение"}"""

SCALE = {"B_much_better": 1.0, "B_better": 0.75, "equal": 0.5, "B_worse": 0.25, "B_much_worse": 0.0}


def judge_round(question, golden, answer_a, answer_b, settings):
    user = (f"<question>\n{question}\n</question>\n<golden>\n{golden}\n</golden>\n"
            f"<answer_A>\n{answer_a}\n</answer_A>\n<answer_B>\n{answer_b}\n</answer_B>")
    result = chat_json([{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": user}],
                       settings, max_tokens=600, default={}) or {}
    return result


def judge_pair(question, golden, ours, baseline, settings):
    """Балл нашего ответа s_i в [0, 1] по двум раундам; None — судья не смог оценить."""
    r1 = judge_round(question, golden, baseline, ours, settings)   # B = наш
    r2 = judge_round(question, golden, ours, baseline, settings)   # B = baseline
    if r1.get("verdict") not in SCALE or r2.get("verdict") not in SCALE:
        return None, "судья не ответил по схеме"
    # выдуманный факт в нашем ответе (B в раунде 1, A в раунде 2) обнуляет весь вопрос
    if r1.get("b_fabricated") is True or r2.get("a_fabricated") is True:
        return 0.0, f"выдуманный факт в нашем ответе: {r1.get('reason', '')} / {r2.get('reason', '')}"
    s1 = SCALE[r1["verdict"]]
    s2 = 1.0 - SCALE[r2["verdict"]]      # в раунде 2 B — baseline; переворачиваем шкалу
    return (s1 + s2) / 2, f"{r1.get('reason', '')} / {r2.get('reason', '')}"


def summarize(scores):
    scores = [s for s in scores if s is not None]
    if not scores:
        return {"n": 0, "match_rate": 0.0, "win_rate": 0.5, "score": 0.0}
    wins = sum(s > 0.5 for s in scores)
    losses = sum(s < 0.5 for s in scores)
    match_rate = sum(scores) / len(scores)
    win_rate = wins / (wins + losses) if wins + losses else 0.5
    return {"n": len(scores), "match_rate": match_rate, "win_rate": win_rate,
            "score": 0.7 * match_rate + 0.3 * win_rate, "wins": wins, "losses": losses}


def read_map(path, column):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return {row["id"]: row[column] for row in csv.DictReader(fh)}


def main():
    from legal_core.config import get_settings

    parser = argparse.ArgumentParser()
    parser.add_argument("--questions", required=True)
    parser.add_argument("--golden", required=True, help="CSV id,answer с эталоном (или bench.csv с колонкой golden)")
    parser.add_argument("--ours", required=True)
    parser.add_argument("--baseline", required=True)
    args = parser.parse_args()
    settings = get_settings()
    questions = read_map(args.questions, "question")
    with open(args.golden, encoding="utf-8-sig") as fh:
        golden_col = "golden" if "golden" in (csv.DictReader(fh).fieldnames or []) else "answer"
    golden = read_map(args.golden, golden_col)
    ours, baseline = read_map(args.ours, "answer"), read_map(args.baseline, "answer")
    scores = []
    for qid, question in questions.items():
        score, reason = judge_pair(question, golden.get(qid, ""), ours.get(qid, ""), baseline.get(qid, ""), settings)
        scores.append(score)
        print(f"{qid:>4}  {'—' if score is None else f'{score:.2f}'}  {reason}")
    total = summarize(scores)
    print(f"\nВопросов оценено: {total['n']}; MatchRate {total['match_rate']:.3f}; WinRate {total['win_rate']:.3f}; "
          f"Score {total['score']:.3f} (x100 = {total['score'] * 100:.1f})")


if __name__ == "__main__":
    main()
