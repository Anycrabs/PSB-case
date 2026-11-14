# Локальная проверка «как на платформе»

`eval/local_platform.py` повторяет автоматическую проверку из технического регламента:

| Этап | Платформа | Локально |
|---|---|---|
| Запуск решения | Docker, только корневые `.py` и `requirements.txt` из ZIP, 8 CPU, 16 ГБ, 20 минут | то же: `python:3.12-slim`, `--cpus 8 --memory 16g`, тайм-аут 20 минут |
| Входные данные | `DATA_DIR` с `questions.csv` и `knowledge-base.zip` | то же, папка `eval_runs/<время>/data` |
| Модели | один прокси: DeepSeek V4.1 Flash, bge-m3, Qwen3 Reranker 8B | прокси `platform-proxy` на :9100: LLM из `.env`, bge-m3 из `embedder`, реранкер — приближение через bge-m3 |
| Соперник | baseline организаторов | `eval/baseline_solution.py` (их минимальный сабмит) |
| Оценка | LLM-судья, 2 раунда, шкала 1/0.75/0.5/0.25/0, выдумка = 0, Score = 0.7·MatchRate + 0.3·WinRate | то же (`eval/judge.py`) |
| Вопросы | скрытый public-набор | 18 демо-вопросов + наши вопросы с эталоном из `data/eval/bench.csv` |

Балл — ориентир для сравнения версий между собой, а не точная копия лидерборда: другие вопросы,
другой судья (та же модель, что отвечает) и приближённый реранкер.

## Бесплатно: GigaChat Freemium

Сбер даёт физлицам бесплатный пакет токенов GigaChat API на 12 месяцев (условия — developers.sber.ru,
раздел «Тарифы для физлиц»). Генерация во Freemium идёт в один поток, поэтому прокси пропускает запросы
по одному, а проверка сама ставит `MAX_WORKERS=1`.

1. Ключ: developers.sber.ru → вход по Сбер ID → создать проект GigaChat API → «Получить ключ» → скопировать
   **ключ авторизации** (показывается один раз).
2. В `.env` (каждая строка — без комментария в конце):
   ```
   GIGACHAT_CREDENTIALS=<ключ авторизации>
   GIGACHAT_SCOPE=GIGACHAT_API_PERS
   GIGACHAT_MODEL=GigaChat-2-Max
   LLM_BASE_URL=http://platform-proxy:9100/v1
   LLM_API_KEY=local
   LLM_MODEL=GigaChat-2-Max
   ```
   Последние три строки нужны веб-интерфейсу: он тоже ходит в GigaChat через прокси.
   Модели: `GigaChat-2` (Lite, самый большой бесплатный объём), `GigaChat-2-Pro`, `GigaChat-2-Max`.
3. Запуск всего стенда вместе с прокси:
   ```powershell
   docker compose --profile eval up -d --build
   ```
   Проверка: http://localhost:9100/health должен показать `"upstream": "gigachat"`, `"concurrency": 1`.
4. Прогон: `python eval/local_platform.py --set demo` (18 вопросов; полный набор в один поток идёт заметно дольше).

Что делает прокси для GigaChat: получает токен доступа по ключу и обновляет его заранее (токен живёт 30 минут),
убирает `response_format` (код проекта и так просит JSON в промпте и умеет достать ответ из текста),
заменяет температуру 0 на 0,01, повторяет запрос при 429. Проверка TLS для GigaChat выключена, если не задан
`GIGACHAT_CA_BUNDLE` — путь к сертификату «Russian Trusted Root CA» внутри контейнера; для локальных тестов это
допустимо, ключ авторизации всё равно никуда, кроме серверов Сбера, не уходит.

Расход: полный прогон на 40 вопросах — около 200 вызовов, порядка миллиона токенов. Модель Max расходует
свой отдельный, меньший пакет; для частых прогонов разумно `GIGACHAT_MODEL=GigaChat-2-Pro` или `GigaChat-2`.

## Подготовка (один раз)

1. В `.env` — настоящая LLM (без комментариев в конце строк):
   ```
   LLM_BASE_URL=https://api.deepseek.com
   LLM_API_KEY=sk-...
   LLM_MODEL=deepseek-flash
   ```
   Или Ollama на этом же компьютере:
   ```
   LLM_BASE_URL=http://host.docker.internal:11434/v1
   LLM_API_KEY=ollama
   LLM_MODEL=qwen2.5:7b
   ```
   Проверить цепочку без ключа: `LLM_BASE_URL=http://fake-llm:9000/v1` и запуск с `--profile dev --profile eval`
   (судья-заглушка ставит всем «сопоставимо», балл будет 50).
2. Поднять прокси (вместе с ним поднимется `embedder` с bge-m3):
   ```powershell
   docker compose --profile eval up -d --build platform-proxy
   ```
3. Для скрипта на хосте: `pip install httpx`.

## Запуск (из корня проекта)

```powershell
python eval/local_platform.py                   # демо + наш набор (40 вопросов), сабмит из текущего кода
python eval/local_platform.py --set demo        # только 18 демо-вопросов
python eval/local_platform.py --zip old.zip     # оценить другой ZIP (например, прошлую версию)
python eval/local_platform.py --reuse-baseline  # взять ответы baseline из прошлого прогона (экономит токены)
python eval/local_platform.py --no-docker       # без контейнера: быстрее, но без изоляции и лимитов
```

Итог печатается в конце и сохраняется в `eval_runs/<время>/`:
- `summary.txt` — Score, MatchRate, WinRate, победы/ничьи/поражения, выдуманные факты, время, число вызовов LLM;
- `report.csv` — по каждому вопросу: балл, эталон, наш ответ, ответ baseline, причина судьи. **Худшие сверху** —
  с них и начинать правки промптов и поиска;
- `team.log`, `baseline.log` — полный вывод решений.

## Как пользоваться для улучшений

1. Прогон текущей версии → запомнить Score.
2. Одна правка (промпт, параметр поиска, режим `PIPELINE_MODE`) → прогон с `--reuse-baseline`.
3. Сравнить Score и строки `report.csv`, где балл изменился. Оставлять правку, только если она помогла.
4. Сабмит на платформу — когда локально стало заметно лучше: так 5 сабмитов в день тратятся на проверенные версии.

Стоимость одного полного прогона на 40 вопросах: около 80 вызовов LLM решением (режим `auto`),
40 — baseline и 80 — судьёй.
