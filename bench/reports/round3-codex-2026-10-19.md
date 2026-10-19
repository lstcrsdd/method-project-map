# Method Project Map benchmark

Runs: 60 valid, 0 excluded (harness or provider errors), 0 flagged for looking outside the workspace (kept, see below).
Values are medians over repeats. Success = hidden tests and regression suite pass.

| task | mode | n | success | input tok | output tok | cost $ | tools | turns | time s | pmap calls |
|---|---|---|---|---|---|---|---|---|---|---|
| allocation-reserved-stock | baseline | 5 | 5/5 | 214k | 967 | – | 8 | – | 28 | 0 |
| allocation-reserved-stock | map-context | 5 | 5/5 | 225k | 897 | – | 7 | – | 25 | 0 |
| api-tax-exempt | baseline | 5 | 5/5 | 217k | 1k | – | 8 | – | 26 | 0 |
| api-tax-exempt | map-context | 5 | 5/5 | 189k | 816 | – | 6 | – | 25 | 0 |
| coupon-loyalty-stacking | baseline | 5 | 5/5 | 174k | 898 | – | 6 | – | 28 | 0 |
| coupon-loyalty-stacking | map-context | 5 | 5/5 | 184k | 859 | – | 7 | – | 26 | 0 |
| jpy-tax-rounding | baseline | 5 | 5/5 | 218k | 1k | – | 7 | – | 30 | 0 |
| jpy-tax-rounding | map-context | 5 | 5/5 | 187k | 764 | – | 7 | – | 22 | 0 |
| refund-store-credit-first | baseline | 5 | 5/5 | 258k | 1k | – | 8 | – | 31 | 0 |
| refund-store-credit-first | map-context | 5 | 5/5 | 230k | 1k | – | 8 | – | 32 | 0 |
| shipping-tax | baseline | 5 | 5/5 | 211k | 787 | – | 7 | – | 25 | 0 |
| shipping-tax | map-context | 5 | 5/5 | 187k | 705 | – | 7 | – | 24 | 0 |

## map-context vs baseline (ratio of medians; < 1 means the map-context run used less)

| task | input tok | output tok | cost $ | tools | turns | time s | success base → map |
|---|---|---|---|---|---|---|---|
| allocation-reserved-stock | 1.05 | 0.93 | – | 0.88 | – | 0.87 | 5/5 → 5/5 |
| api-tax-exempt | 0.87 | 0.73 | – | 0.75 | – | 0.96 | 5/5 → 5/5 |
| coupon-loyalty-stacking | 1.05 | 0.96 | – | 1.17 | – | 0.93 | 5/5 → 5/5 |
| jpy-tax-rounding | 0.86 | 0.65 | – | 1.00 | – | 0.75 | 5/5 → 5/5 |
| refund-store-credit-first | 0.89 | 1.01 | – | 1.00 | – | 1.03 | 5/5 → 5/5 |
| shipping-tax | 0.88 | 0.90 | – | 1.00 | – | 1.00 | 5/5 → 5/5 |
| **geometric mean** | 0.93 | 0.85 | – | 0.96 | – | 0.92 | |

## Statistics (5+ repeats)

Per task: median [interquartile range] of input tokens.

| task | baseline | map-context |
|---|---|---|
| allocation-reserved-stock | 214k [213–214k] | 225k [144–225k] |
| api-tax-exempt | 217k [210–223k] | 189k [185–224k] |
| coupon-loyalty-stacking | 174k [171–209k] | 184k [183–221k] |
| jpy-tax-rounding | 218k [217–221k] | 187k [184–188k] |
| refund-store-credit-first | 258k [258–261k] | 230k [230–234k] |
| shipping-tax | 211k [177–212k] | 187k [185–189k] |

**map-context vs baseline**: geometric mean over tasks of the median ratio, with a bootstrap 95% interval (an interval that excludes 1.00 is a real effect at this sample size).

| metric | ratio | 95% interval |
|---|---|---|
| input tok | 0.93 | 0.83 – 1.02 |
| output tok | 0.85 | 0.71 – 0.98 |
| tools | 0.96 | 0.85 – 1.04 |
| time s | 0.92 | 0.71 – 1.12 |

## Navigation metrics (median per task, then geometric mean of map-context / baseline, bootstrap 95%)

| metric | ratio | 95% interval | meaning |
|---|---|---|---|
| code pulled into context by commands (chars of command output) | **0.55** | **0.49 – 0.59** | the agent reads 45% less code |
| files read | 0.88 | 0.77 – 1.01 | |
| commands until the buggy file is first opened | 1.07 | 0.89 – 1.31 | it is the 2nd command in both modes |
| uncached input tokens | 1.00 | 0.80 – 1.14 | |
| cached input tokens | 0.92 | 0.81 – 1.04 | |

Generated domain files opened: 0 in all 60 runs (both modes). Bug reverted exactly: 59/60; only the target file changed: 60/60.

## Выводы решающего раунда (Codex `gpt-6.1-sol`, reasoning high, 6 задач × 2 режима × 5 повторов)

**Конфигурация.**
- Корпус `tallyhub-large`: 28,9 тысячи строк, 424 модуля. Это ядро `tallyhub` плюс 32 сгенерированных домена со схожим словарём и 160 своими тестами.
- Режим `map-context`: обзор карты на 24 тысячи символов (около 6 тысяч токенов) в промпте. Обзор содержит все 48 пакетов и около 190 модулей, ядро идёт первым.

1. **Качество не меняется: 60 из 60 успешны.** В 59 прогонах ошибка исправлена точным откатом, везде изменён только целевой файл.
2. **Карта в промпте заметно сокращает чтение кода.** Объём кода, который агент вытягивает командами, — 0,55 от базы, интервал 0,49–0,59. Эффект устойчивый, проявился во всех шести задачах. Выходные токены — 0,85, интервал 0,71–0,98: тоже значимо.
3. **Общая экономия токенов не доказана: 0,93, интервал 0,83–1,02.** Карта сама стоит около 6 тысяч токенов на каждый вызов модели, то есть 35–45 тысяч за прогон. Это почти полностью съедает экономию на чтении. Некэшированные входные токены одинаковы (1,00), а именно они определяют основную стоимость.
4. **Навигация не была узким местом даже на 29 тысячах строк.** В обоих режимах агент открывает нужный файл второй командой, а в сгенерированные домены не заходит ни разу. Причина в двух вещах:
   - симптомы содержат конкретные идентификаторы (`c_alice`, `tax_exempt_id`, `DESK-LAMP`), и `rg` сразу находит ядро;
   - домены лежат в отдельных пакетах, и их легко отсечь.

   Реальные баг-репорты обычно расплывчатее.

**Итог по гипотезе 1.** Карта не ухудшает работу агента и почти вдвое сокращает объём прочитанного кода. Чистая экономия токенов в этой постановке не подтверждена: стоимость самой карты в промпте сопоставима со сэкономленным. Направления, где экономия вероятна:
- компактная карта (пакеты и модули ядра, порядка 6–8 тысяч символов вместо 24 тысяч);
- расплывчатые симптомы без идентификаторов;
- агенты, которые перечитывают файлы целиком, или модели без кэширования промпта.
