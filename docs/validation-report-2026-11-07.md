# Отчёт о валидации — 7 ноября 2026

## Что измерено

| Контур | Результат | Что это доказывает |
| --- | --- | --- |
| Offline scenario eval | 15 / 15 сценариев, прохождение жёстких ограничений 100% | Детерминированная логика состояний, ограничений и отказов не регрессировала. Это не оценка качества внешней модели. |
| Live E2E, синтетический каталог | 5 / 5 функциональных сценариев | API, PostgreSQL, гибридный поиск, rerank, объяснение на русском и выборочный переплан прошли вместе. Предложения в каталоге синтетические, их нельзя бронировать. |
| Live parse eval, успешный запуск до этого изменения | 37 / 37 обязательных полей; 53 / 55 assertions; 0 fallback | Для `deepseek-v4.1-flash-1017` на 20 размеченных русских репликах field accuracy 100%, assertion accuracy 96,36%, exact match 90%. Оценочная стоимость 1,07814 ₽ по пользовательским тарифам. |
| Live parse eval, повтор после изменения | 0 успешных ответов модели из 20 | Все вызовы закончились `ConnectTimeout` примерно за 12 секунд. Это показатель доступности провайдера в данном запуске, а не нулевая accuracy модели; результат не используется для сравнения качества. |

Последнее изменение сохраняет дословное мягкое предпочтение пользователя, если модель не вернула
`preference_quotes`; оно не создаёт факт или жёсткое ограничение. Для фразы «в ноябре» eval теперь
явно допускает только `inferred` значение с `needs_confirmation=true`, а не скрытую дату.
Повторный live parse-eval надо провести после восстановления доступа к провайдеру — только тогда
можно считать это изменение подтверждённым на модели.

## Последний успешный E2E

`artifacts/async-e2e.json` содержит полный трассировочный JSON. В нём 5 сценариев:
обычный hybrid+rerank, изменение только рейсов, уточнение origin, невозможный бюджет и
неподдерживаемое направление. Все проверки прошли.

| Slot / model | Calls | p50 | p95 | HTTP errors |
| --- | ---: | ---: | ---: | ---: |
| Parse / deepseek-v4.1-flash-1017 | 5 | 3 091 ms | 3 868 ms | 0 |
| Embedding / qwen3-embedding-8b | 1 | 1 839 ms | 1 839 ms | 0 |
| Rerank / qwen3-reranker-8b | 3 | 1 721 ms | 1 805 ms | 0 |
| Explain / qwen3.7-plus | 3 | 4 838 ms | 4 960 ms | 0 |
| Questions / deepseek-v4.1-flash-1017 | 1 | 1 866 ms | 1 866 ms | 0 |

Это маленькая acceptance-выборка: p95 здесь выборочный, а не production SLO.

## Где лежат данные

- `artifacts/async-e2e.json` — последний live E2E; старые E2E архивируются рядом с timestamp.
- `artifacts/parse-accuracy-*.json` — полный per-case parse-eval, включая spans, fallback и цену.
- PostgreSQL `evaluation_runs` — неизменяемые live parse-eval результаты; API:
  `GET /v1/monitoring/accuracy`.
- `GET /v1/monitoring/models` — накопленные HTTP spans, latency, usage, оценочная стоимость
  и coverage по цене. Это не invoice и не accuracy без labelled dataset.
- [Модельные метрики](evidence/README.md) — определения метрик и сырые артефакты прогонов.
- [Архитектура](ARCHITECTURE.md) — схема, trade-offs и честные границы прототипа.

## Как воспроизвести

```bash
make eval                                      # offline, без сети
RUN_LIVE_EVALS=1 make eval-async-live          # платный 5-case E2E
RUN_LIVE_EVALS=1 make eval-accuracy            # платный 20-case parse-eval + DB persistence
curl -fsS http://127.0.0.1:8010/v1/monitoring/models
curl -fsS http://127.0.0.1:8010/v1/monitoring/accuracy
```

Live-команды намеренно требуют `RUN_LIVE_EVALS=1`: ключ в `.env` сам по себе не запускает
оплачиваемый трафик.
