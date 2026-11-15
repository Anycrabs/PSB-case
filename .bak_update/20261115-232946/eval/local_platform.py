"""Локальная копия автоматической проверки платформы (Технический регламент, разделы 5–8).

Что делает, как платформа:
  1. собирает ZIP сабмита (submission/build_zip.py) или берёт готовый (--zip);
  2. запускает решение в чистом контейнере python:3.12-slim: только корневые .py и requirements.txt из ZIP,
     8 CPU, 16 ГБ RAM, тайм-аут 20 минут, входные файлы в /data, переменные LLM_BASE_URL / LLM_API_KEY /
     LLM_MODEL / EMB_MODEL / RERANKER_MODEL / MAX_WORKERS / TEMPERATURE / MAX_TOKENS указывают на локальный прокси;
  3. так же запускает baseline — минимальный сабмит организаторов (eval/baseline_solution.py);
  4. проверяет формат submission.csv (id, answer, все вопросы, без пустых ответов);
  5. LLM-судья сравнивает ответы с baseline по эталону: два раунда со сменой мест, шкала 1/0.75/0.5/0.25/0,
     выдуманный факт обнуляет вопрос; MatchRate, WinRate, Score = 0.7 * MatchRate + 0.3 * WinRate.

Отличия от платформы (поэтому балл — ориентир, а не точная копия лидерборда):
  - вопросы — демо-набор организаторов и наш набор с эталонами (data/eval/bench.csv), а не скрытый public-набор;
  - судья — та же модель, что отвечает (из .env), а не судья организаторов;
  - реранкер — приближение через bge-m3 вместо Qwen3 Reranker 8B.

Подготовка (один раз):
  - в .env задана настоящая LLM: LLM_BASE_URL / LLM_API_KEY / LLM_MODEL (DeepSeek или Ollama);
  - docker compose --profile eval up -d --build platform-proxy    (поднимет embedder и прокси на :9100)
  - pip install httpx

Запуск из корня проекта:
  python eval/local_platform.py                  # демо + наш набор, сабмит из текущего кода
  python eval/local_platform.py --set demo       # только 18 демо-вопросов (быстрее)
  python eval/local_platform.py --zip old.zip    # оценить другой сабмит, например прошлую версию
  python eval/local_platform.py --reuse-baseline # не перезапускать baseline, взять ответы прошлого прогона
  python eval/local_platform.py --no-docker      # без контейнера: решение запускается текущим Python (быстрее,
                                                 # но без изоляции и лимитов; нужен pip install httpx numpy)
Результаты: eval_runs/<время>/report.csv (вопрос, эталон, наш ответ, baseline, балл, причина) и summary.txt.
"""

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

PROXY_HOST = "http://localhost:9100/v1"          # с хоста (для судьи)
PROXY_IN_NET = "http://platform-proxy:9100/v1"   # из контейнера с решением
NETWORK = "legal-assistant_legal-net"            # сеть compose (name: legal-assistant, сеть legal-net)
IMAGE = "python:3.12-slim"
TIMEOUT_S = 20 * 60
PLATFORM_ENV = {                                  # что платформа передаёт решению (раздел 6 регламента)
    "LLM_BASE_URL": PROXY_IN_NET,
    "LLM_API_KEY": "local-platform",
    "LLM_MODEL": "deepseek/deepseek-v4.1-flash",
    "EMB_MODEL": "baai/bge-m3",
    "RERANKER_MODEL": "qwen/qwen3-reranker-8b",
    "DATA_DIR": "/data",
    "MAX_WORKERS": "4",
    "TEMPERATURE": "0",
    "MAX_TOKENS": "1024",
    "SKIP_PROBE": "1",
}


def log(message):
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(path, rows, fields):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)


def question_set(name):
    """Вопросы и эталоны: demo — 18 демо-вопросов организаторов, bench — наши с эталоном, all — оба."""
    items = []
    if name in ("demo", "all"):
        golden = {r["id"]: r["answer"] for r in read_csv(ROOT / "data" / "demo" / "demo_output.csv")}
        items += [(f"d{r['id']}", r["question"], golden.get(r["id"], ""))
                  for r in read_csv(ROOT / "data" / "demo" / "demo_questions.csv")]
    if name in ("bench", "all"):
        items += [(r["id"], r["question"], r["golden"]) for r in read_csv(ROOT / "data" / "eval" / "bench.csv")
                  if r["golden"]]
    return items


def unpack_like_platform(zip_path, target):
    """Платформа переносит только корневые .py и requirements.txt."""
    target.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if "/" not in name and (name.endswith(".py") or name == "requirements.txt"):
                (target / name).write_bytes(zf.read(name))
    if not (target / "solution.py").exists():
        raise SystemExit(f"В корне {zip_path} нет solution.py — платформа такой сабмит не запустит")


def run_local(name, workdir, datadir, run_dir):
    """--no-docker: решение запускается текущим Python в своей папке, с теми же переменными окружения."""
    env = {**os.environ, **PLATFORM_ENV, "LLM_BASE_URL": PROXY_HOST, "DATA_DIR": str(datadir.resolve())}
    log(f"{name}: запуск без контейнера (тайм-аут {TIMEOUT_S // 60} мин)")
    started = time.time()
    with open(run_dir / f"{name}.log", "w", encoding="utf-8") as out:
        try:
            code = subprocess.run([sys.executable, "-I", "solution.py"], cwd=workdir, env=env, stdout=out,
                                  stderr=subprocess.STDOUT, timeout=TIMEOUT_S).returncode
        except subprocess.TimeoutExpired:
            code = -1
            out.write("\n[local-platform] ТАЙМ-АУТ 20 минут\n")
    seconds = time.time() - started
    log(f"{name}: {'ТАЙМ-АУТ' if code == -1 else f'код {code}'}, {seconds:.0f} с, лог: {name}.log")
    return seconds, code


def run_container(name, workdir, datadir, run_dir):
    """Запуск решения в изолированном контейнере с лимитами платформы. Возвращает (секунды, код выхода)."""
    container = f"local-platform-{name}-{int(time.time())}"
    env_args = [arg for key, value in PLATFORM_ENV.items() for arg in ("-e", f"{key}={value}")]
    script = ("pip install -q --no-cache-dir --disable-pip-version-check -r requirements.txt "
              "&& echo __START__ && python solution.py")
    cmd = ["docker", "run", "--rm", "--name", container, "--network", NETWORK, "--cpus", "8", "--memory", "16g",
           "-v", f"{workdir.resolve()}:/app", "-v", f"{datadir.resolve()}:/data", "-w", "/app",
           *env_args, IMAGE, "sh", "-c", script]
    log_path = run_dir / f"{name}.log"
    log(f"{name}: запуск в контейнере ({IMAGE}, 8 CPU, 16 ГБ, тайм-аут {TIMEOUT_S // 60} мин)")
    started, solve_started, code = time.time(), None, 0
    with open(log_path, "w", encoding="utf-8") as out:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace")
        try:
            for line in proc.stdout:
                out.write(line)
                if line.strip() == "__START__":
                    solve_started = time.time()   # время установки зависимостей не считаем
                elif solve_started and time.time() - solve_started > TIMEOUT_S:
                    raise subprocess.TimeoutExpired(cmd, TIMEOUT_S)
            code = proc.wait()
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "kill", container], capture_output=True)
            proc.wait()
            code = -1
            out.write("\n[local-platform] ТАЙМ-АУТ 20 минут\n")
    seconds = time.time() - (solve_started or started)
    log(f"{name}: {'ТАЙМ-АУТ' if code == -1 else f'код {code}'}, {seconds:.0f} с, лог: {log_path.name}")
    return seconds, code


def check_submission(path, ids):
    """Проверки формата, как при приёме сабмита. Возвращает (ответы по id, список ошибок)."""
    if not path.exists():
        return {}, ["нет submission.csv"]
    rows = read_csv(path)
    errors = []
    if rows and list(rows[0].keys()) != ["id", "answer"]:
        errors.append(f"заголовок {list(rows[0].keys())}, нужен ['id', 'answer']")
    answers = {r.get("id", ""): (r.get("answer") or "").strip() for r in rows}
    missing = [i for i in ids if i not in answers]
    empty = [i for i in ids if i in answers and not answers[i]]
    if missing:
        errors.append(f"нет ответов на {len(missing)} вопрос(ов): {', '.join(missing[:5])}")
    if empty:
        errors.append(f"пустые ответы: {', '.join(empty[:5])}")
    return answers, errors


def proxy_stats(reset=False):
    import httpx

    url = PROXY_HOST.rsplit("/v1", 1)[0] + ("/stats/reset" if reset else "/stats")
    try:
        return (httpx.post if reset else httpx.get)(url, timeout=10).json()
    except httpx.HTTPError:
        return {}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--set", choices=["demo", "bench", "all"], default="all")
    parser.add_argument("--zip", help="готовый ZIP сабмита; по умолчанию собирается из текущего кода")
    parser.add_argument("--reuse-baseline", action="store_true", help="взять ответы baseline из прошлого прогона")
    parser.add_argument("--judge-workers", type=int, default=None,
                        help="параллельных вызовов судьи; по умолчанию как у прокси (GigaChat Freemium — 1)")
    parser.add_argument("--workers", type=int, default=None,
                        help="MAX_WORKERS для решения; по умолчанию 4, а при прокси в один поток — 1")
    parser.add_argument("--no-docker", action="store_true", help="запускать решения без контейнера")
    args = parser.parse_args()

    import httpx

    try:
        health = httpx.get(PROXY_HOST.rsplit("/v1", 1)[0] + "/health", timeout=5)
        health.raise_for_status()
        health = health.json()
    except httpx.HTTPError:
        raise SystemExit("Прокси не отвечает на :9100. Запустите: docker compose --profile eval up -d --build platform-proxy")
    single = int(health.get("concurrency") or 8) == 1
    workers = args.workers or (1 if single else 4)
    judge_workers = args.judge_workers or (1 if single else 4)
    PLATFORM_ENV["MAX_WORKERS"] = str(workers)
    log(f"LLM: {health.get('upstream')} ({health.get('model')}); параллельно: решение {workers}, судья {judge_workers}")
    if single:
        log("Прокси работает в один поток (GigaChat Freemium): прогон будет медленнее; начните с --set demo")

    items = question_set(args.set)
    ids = [qid for qid, _, _ in items]
    run_dir = ROOT / "eval_runs" / time.strftime("%Y%m%d-%H%M%S")
    data_dir = run_dir / "data"
    data_dir.mkdir(parents=True)
    write_csv(data_dir / "questions.csv", [{"id": q, "question": text} for q, text, _ in items], ["id", "question"])
    shutil.copy(ROOT / "data" / "knowledge-base.zip", data_dir / "knowledge-base.zip")
    log(f"Вопросов: {len(items)} (набор {args.set}); папка прогона: {run_dir.relative_to(ROOT)}")

    # 1. сабмит команды
    zip_path = Path(args.zip) if args.zip else ROOT / "dist" / "submission.zip"
    if not args.zip:
        subprocess.run([sys.executable, str(ROOT / "submission" / "build_zip.py"), "--out", str(zip_path)], check=True)
    unpack_like_platform(zip_path, run_dir / "team")
    proxy_stats(reset=True)
    run = run_local if args.no_docker else run_container
    team_seconds, team_code = run("team", run_dir / "team", data_dir, run_dir)
    team_usage = proxy_stats()
    team, team_errors = check_submission(run_dir / "team" / "submission.csv", ids)

    # 2. baseline организаторов
    previous = sorted((ROOT / "eval_runs").glob("*/baseline/submission.csv"))
    previous = [p for p in previous if p.parent.parent != run_dir]
    if args.reuse_baseline and previous:
        baseline, _ = check_submission(previous[-1], ids)
        log(f"baseline: ответы из {previous[-1].parent.parent.name}")
    else:
        base_dir = run_dir / "baseline"
        base_dir.mkdir()
        shutil.copy(ROOT / "eval" / "baseline_solution.py", base_dir / "solution.py")
        (base_dir / "requirements.txt").write_text("httpx==0.28.1\n", encoding="utf-8")
        run("baseline", base_dir, data_dir, run_dir)
        baseline, base_errors = check_submission(base_dir / "submission.csv", ids)
        if base_errors:
            log(f"baseline: {'; '.join(base_errors)}")

    # 3. судья
    if team_errors:
        log("Сабмит не прошёл бы проверку формата: " + "; ".join(team_errors))
    os.environ.update({"LLM_BASE_URL": PROXY_HOST, "LLM_API_KEY": "local-platform"})
    from legal_core.config import get_settings

    from judge import judge_pair, summarize

    settings = get_settings()
    log(f"Судья: {len(items)} вопросов × 2 раунда")

    def judge(item):
        qid, question, golden = item
        ours, base = team.get(qid, ""), baseline.get(qid, "")
        if not ours:
            return qid, 0.0, "нет ответа команды (ошибка или тайм-аут)"
        try:
            return (qid, *judge_pair(question, golden, ours, base, settings))
        except Exception as exc:  # noqa: BLE001 — техническая ошибка судьи: вопрос исключается, как на платформе
            return qid, None, f"ошибка судьи: {type(exc).__name__}"

    with ThreadPoolExecutor(max_workers=judge_workers) as pool:
        verdicts = {qid: (score, reason) for qid, score, reason in pool.map(judge, items)}

    rows = []
    for qid, question, golden in items:
        score, reason = verdicts[qid]
        rows.append({"id": qid, "score": "" if score is None else f"{score:.2f}", "question": question,
                     "golden": golden, "ours": team.get(qid, ""), "baseline": baseline.get(qid, ""), "reason": reason})
    rows.sort(key=lambda r: float(r["score"]) if r["score"] else 9)   # худшие сверху: с них и начинать разбор
    write_csv(run_dir / "report.csv", rows, ["id", "score", "question", "golden", "ours", "baseline", "reason"])

    total = summarize([verdicts[q][0] for q in ids])
    fabricated = sum(1 for s, r in verdicts.values() if s == 0 and str(r).startswith("выдуманный факт"))
    ties = total["n"] - total.get("wins", 0) - total.get("losses", 0)
    lines = [
        f"Набор: {args.set}, вопросов {len(items)}, оценено судьёй {total['n']}",
        f"Score x100 = {total['score'] * 100:.1f}   (0.7 × MatchRate {total['match_rate']:.3f} + 0.3 × WinRate {total['win_rate']:.3f})",
        f"Побед {total.get('wins', 0)}, ничьих {ties}, поражений {total.get('losses', 0)}; выдуманных фактов: {fabricated}",
        f"Время решения: {team_seconds:.0f} с из {TIMEOUT_S} (код выхода {team_code})",
        f"Вызовов LLM решением: {team_usage.get('chat', '?')}, токенов: "
        f"{team_usage.get('prompt_tokens', 0)} + {team_usage.get('completion_tokens', 0)}",
        "Формат submission.csv: " + ("ок" if not team_errors else "; ".join(team_errors)),
        f"Подробно (худшие вопросы сверху): {(run_dir / 'report.csv').relative_to(ROOT)}",
    ]
    (run_dir / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (run_dir / "summary.json").write_text(json.dumps({**total, "fabricated": fabricated, "seconds": team_seconds},
                                                     ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
