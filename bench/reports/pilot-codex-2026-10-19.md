# Method Project Map benchmark

Runs: 12 valid, 0 excluded (harness or provider errors), 0 flagged for looking outside the workspace (kept, see below).
Values are medians over repeats. Success = hidden tests and regression suite pass.

| task | mode | n | success | input tok | output tok | cost $ | tools | turns | time s | pmap calls |
|---|---|---|---|---|---|---|---|---|---|---|
| asyncio-timeout-expired | baseline | 1 | 1/1 | 208k | 1k | – | 7 | – | 29 | 0 |
| asyncio-timeout-expired | map | 1 | 1/1 | 208k | 1k | – | 7 | – | 30 | 0 |
| email-address-quotes | baseline | 1 | 1/1 | 194k | 1k | – | 6 | – | 30 | 0 |
| email-address-quotes | map | 1 | 1/1 | 226k | 1k | – | 8 | – | 31 | 0 |
| email-rfc2231-filename | baseline | 1 | 1/1 | 182k | 1k | – | 6 | – | 32 | 0 |
| email-rfc2231-filename | map | 1 | 1/1 | 220k | 1k | – | 10 | – | 29 | 0 |
| http-cookie-octal | baseline | 1 | 1/1 | 145k | 779 | – | 5 | – | 22 | 0 |
| http-cookie-octal | map | 1 | 1/1 | 208k | 1k | – | 7 | – | 31 | 0 |
| logging-config-child-propagate | baseline | 1 | 1/1 | 176k | 867 | – | 8 | – | 27 | 0 |
| logging-config-child-propagate | map | 1 | 1/1 | 153k | 1k | – | 6 | – | 25 | 0 |
| logging-rotating-backups | baseline | 1 | 1/1 | 209k | 1k | – | 6 | – | 31 | 0 |
| logging-rotating-backups | map | 1 | 1/1 | 180k | 2k | – | 7 | – | 35 | 0 |

## Map vs baseline (ratio of medians; < 1 means the map run used less)

| task | input tok | output tok | cost $ | tools | turns | time s | success base → map |
|---|---|---|---|---|---|---|---|
| asyncio-timeout-expired | 1.00 | 0.95 | – | 1.00 | – | 1.06 | 1/1 → 1/1 |
| email-address-quotes | 1.16 | 1.17 | – | 1.33 | – | 1.01 | 1/1 → 1/1 |
| email-rfc2231-filename | 1.21 | 1.18 | – | 1.67 | – | 0.93 | 1/1 → 1/1 |
| http-cookie-octal | 1.43 | 1.77 | – | 1.40 | – | 1.42 | 1/1 → 1/1 |
| logging-config-child-propagate | 0.87 | 1.18 | – | 0.75 | – | 0.92 | 1/1 → 1/1 |
| logging-rotating-backups | 0.86 | 1.14 | – | 1.17 | – | 1.11 | 1/1 → 1/1 |
| **geometric mean** | 1.07 | 1.21 | – | 1.18 | – | 1.06 | |

## Выводы пилота (Codex `gpt-6.1-sol`, reasoning high, 1 повтор)

**Конфигурация.** 6 задач × 2 режима. Корпус — копия `email`, `logging`, `http`, `asyncio` из Python 3.14. Два прогона сорвались из-за перегрузки модели («at capacity») и были перезапущены; в таблицу вошли повторные прогоны.

1. **Карта ни разу не использована.** В режиме `map` агент получил описание команд `pmap`, но во всех шести прогонах сразу искал код через `rg` и `sed`. Число вызовов `pmap` — 0 из 6. Гипотеза 1 в этой постановке не проверялась вовсе: различия между режимами — это шум между прогонами (n = 1), а не эффект карты.
2. **Задачи оказались слишком лёгкими для проверки навигации.**
   - Все 12 прогонов успешны.
   - Каждый раз ошибка исправлена ровно откатом внесённой строки (+1/−1, только целевой файл).
   - На прогон уходит 5–10 команд и около 30 секунд.
   - Симптом называет публичное API, `rg` находит место с первой попытки, а стандартную библиотеку модель знает из обучения.
3. **Потолок экономии мал.** Из 145–226 тысяч входных токенов на прогон около 55 тысяч — фиксированный системный контекст Codex. Даже идеальная навигация сэкономила бы здесь лишь часть оставшегося.
4. **Попыток подсмотреть установленную stdlib не было** (0 срабатываний детектора).

**Что это значит для гипотезы 1.** Пилот ничего не доказал ни в пользу карты, ни против неё. Зато он показал два условия, без которых проверка бессмысленна:
- агент должен реально получать содержимое карты;
- задачи должны упираться в навигацию: незнакомый код, симптом без названия API, изменения в нескольких файлах.
