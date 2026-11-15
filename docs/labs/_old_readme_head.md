# findex

[![CI](https://github.com/erwrqq2/Python/actions/workflows/ci.yml/badge.svg)](https://github.com/erwrqq2/Python/actions/workflows/ci.yml)

**Живий пошук: <https://findex-f2zl.onrender.com>** · API: <https://findex-f2zl.onrender.com/docs>
(безкоштовний тариф Render: після 15 хв без запитів перше відкриття триває ~30–60 с)

Невелика повнотекстова пошукова система, яку крок за кроком будую протягом
8 лабораторних курсу Python. Кожна лаба додає одну особливість мови й одну
частину рушія.

| Лаба | Тема | Тег |
|------|------|-----|
| 1 | Ітератори, генератори та корпус | `lab-01` |
| 2 | Інвертований індекс: словники, хешування та пам'ять | `lab-02` |
| 3 | Рейтинг і модель об'єктів: dunder-методи, протоколи, декоратори | `lab-03` |
| 4 | Типи, тести, пакування і CLI | `lab-04`, `v0.4.0` |
| 5 | Конкурентність і GIL: потоки, процеси і паралельна індексація | `lab-05` |
| 6 | Асинхронний краулер: asyncio | `lab-06` |
| 7 | Пошук у вебі: FastAPI, Pydantic і публічна адреса | `lab-07` |

## Запуск

Потрібні [uv](https://docs.astral.sh/uv/) і Python 3.13

```bash
uv sync
uv run python scripts/download_gutenberg.py --count 150   # корпус -> data/
uv run python -m findex.stats data/
uv run findex index data/ --out index.fidx --positions
uv run findex search index.fidx 'locomotive AND (steam OR engine) NOT "civil war"'
uv run findex stats index.fidx
uv run findex serve --index crawl.fidx --corpus corpus/books-toscrape.jsonl   # http://127.0.0.1:8000
uv run python -m findex.evaluate index.fidx evaluation/queries.json
uv run pytest
uv run ruff check .
uv run pyright
```

## Корпус

**Project Gutenberg**, англомовні книги у форматі plain text.

- `scripts/download_gutenberg.py` завантажує офіційний каталог
  (`pg_catalog.csv`) з дзеркала `gutenberg.pglaf.org` (Project Gutenberg просить
  автоматичні клієнти користуватися дзеркалами), відбирає англомовні записи типу
  *Text* з фіксованим seed (`--seed 42`) і завантажує `--count` книг.
- Ліцензійна шапка й підвал Gutenberg (`*** START OF ...` / `*** END OF ...`)
  вирізаються; кожна книга зберігається як `data/gutenberg/<id>.txt`, перший
  рядок — назва.
- Скрипт можна перезапускати — вже завантажені книги пропускаються; між
  запитами пауза 1 с; після 5 мережевих помилок поспіль скрипт зупиняється.
- `data/` у `.gitignore` — корпус ніколи не комітиться.

Поточний розмір: **143 книги, 61 МБ** тексту в UTF-8 

