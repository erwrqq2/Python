# Лаба 4 — Типи, тести, пакування і CLI

[← до README](../../README.md)

Тепер findex — пакет, що ставиться однією командою, з командою `findex`,
перевіркою типів pyright у режимі strict, тестами з Hypothesis і CI.

### Встановлення

```bash
uv tool install .                       # з клону репозиторію: findex з'являється в PATH
uv tool install https://github.com/erwrqq2/Python/releases/download/v0.4.0/findex-0.4.0-py3-none-any.whl
uvx --from dist/findex-0.4.0-py3-none-any.whl findex --help   # без встановлення
```

`[project.scripts] findex = "findex.cli:app"` — при встановленні wheel створюється
виконуваний файл `findex` (на Windows `findex.exe`) у теці `bin`/`Scripts`
оточення; він імпортує `findex.cli` і викликає `app()`. Перевірено: wheel з
`uv build` ставиться в ізольоване оточення (`uv tool run --isolated --from …whl`)
і `findex --help` працює з будь-якої теки, без репозиторію.

### CLI: `findex index | search | stats`

typer — команди й опції з анотацій типів; rich — таблиці, підсвітка, смуга
прогресу.

```bash
findex index data/ --out index.fidx --positions        # смуга прогресу rich (stderr)
findex search index.fidx 'locomotive AND (steam OR engine) NOT "civil war"' -k 3
findex search index.fidx telegraph --json | jq .title  # JSON lines у stdout
findex stats index.fidx --top 5                        # або: findex stats data/
findex -vv search ...                                  # -v = INFO, -vv = DEBUG
```

```
locomotive AND (steam OR engine) NOT "civil war"  (6 of 143 match, bm25)
┌───┬───────┬───────────────────────────────────────────────────────────────────────────┐
│ # │ score │ document                                                                  │
├───┼───────┼───────────────────────────────────────────────────────────────────────────┤
│ 1 │ 8.216 │ The Boy Scouts Book of Campfire Stories [33]                              │
│   │       │ …d been well roofed with foliage. Here the travelers got their first view │
│   │       │ of a steam engine. The latter stood puffing and smoking near the village… │
│ 2 │ 7.734 │ Brooks's Readers, Third Year [59]                                         │
│   │       │ …reason mischief pasture couch bitter exclaimed sloping steam The old     │
│   │       │ apple tree had stood in the corner of the pasture for so many ye…         │
└───┴───────┴───────────────────────────────────────────────────────────────────────────┘
```

(у терміналі терми запиту в сніпетах підсвічені жовтим)

```
$ findex search index.fidx telegraph -k 2 --json --no-snippets
{"rank": 1, "score": 3.0913, "doc_id": 102, "title": "The American Electro Magnetic Telegraph …", "path": "data\\gutenberg\\61533.txt", "snippet": ""}
{"rank": 2, "score": 2.7413, "doc_id": 120, "title": "Military Reminiscences of the Civil War, Volume 2 …", "path": "data\\gutenberg\\6962.txt", "snippet": ""}
```

- **stdout — лише дані** (таблиця або JSON lines); **stderr — уся діагностика**:
  логи, смуга прогресу, помилки. Тому `--json` можна перенаправляти в `jq` чи
  файл, навіть з `-vv`.
- **Очікувані помилки** — один рядок і код виходу 1, без стека викликів:

  ```
  $ findex search index.fidx 'whale AND'
  error: bad query: query ends where a term was expected
  $ findex search nope.fidx whale
  error: file not found: nope.fidx
  ```

  Обробляються: немає файлу, зламаний запит, фраза на індексі без позицій, файл —
  не індекс (`store.IndexFormatError`). Неочікувані винятки (баги) **не**
  ховаються: їх видно з повним стеком.
- `findex --version`, автодоповнення в shell (`--install-completion`) — від typer.
- Старі команди з README лаб 1–3 (`python -m findex.index`, `python -m findex.search`)
  працюють; `python -m findex.search` тепер просто викликає `findex search`.

### Logging замість print

Кожен модуль бере `log = logging.getLogger(__name__)`, а налаштовується логер
**один раз** — у колбеку typer-застосунку (`cli.py`, `RichHandler` у stderr).
Бібліотечний код логування не налаштовує ніколи. Формат — лінивий `%`:
`log.debug("%s took %.1f ms", name, ms)` форматує рядок, лише якщо рівень DEBUG
увімкнено (f-рядок форматувався б завжди). Діагностичні `print` у скрипті
завантаження корпусу та в `memstudy` замінено на logging; `print` лишився лише
там, де друкуються самі результати (таблиці бенчмарків).

### Типи: pyright strict

`[tool.pyright] typeCheckingMode = "strict"` для `src`, `tests` і `scripts`:
**0 помилок.**

- **`Scorer` — `typing.Protocol`**: `TfIdf`, `BM25` і навіть звичайна функція з
  потрібною сигнатурою підходять без наслідування — pyright перевіряє структуру
  (тест присвоює функцію змінній типу `Scorer`).
- **`Literal` замість рядків:** `EngineName = Literal["merge", "set"]`,
  `ScorerName = Literal["bm25", "tfidf"]`, `Format = Literal["pickle", "binary"]`.
  `save(index, path, "yaml")` — помилка ще до запуску. (typer хоче `Enum`, тож у
  CLI є `StrEnum` і словник-перехідник `Enum → Literal`.)
- **Дженерики PEP 695:** `Engine[C]` параметризований типом колекції —
  `MERGE: Engine[Sequence[int]]`, `SET: Engine[set[int]]`, а `Node.evaluate[C](…,
  engine: Engine[C]) -> C`. Раніше там був `Any`; тепер pyright знає, що merge
  працює зі списками, а set — з множинами. `@timed` типізований через
  `def timed[**P, R](func: Callable[P, R]) -> Callable[P, R]`, тож обгорнуті
  функції зберігають свої сигнатури.
- Генератори повертають `Iterator[...]`, приймають `Iterable[...]`; контекстні
  менеджери з `@contextmanager` — `Generator[...]` (анотація `Iterator` для них
  у нових стабах позначена як застаріла — pyright так і сказав).
- `TypedDict` для JSON: заголовок бінарного формату (`BinaryHeader`), рядок
  `--json` (`SearchHit`), розмічений запит (`LabelledQuery`). `Final` для констант.
- Звуження типів: `index: object = pickle.loads(...)` → `isinstance(index, Index)`.

Обхідні шляхи — усі з поясненням:

| Де | Що | Чому |
|----|----|------|
| `store.py`, `evaluate.py` | `cast(BinaryHeader, json.loads(...))` | `json.loads` повертає `Any`; формат файлу задокументований і пишеться нашим же кодом |
| `tests/test_index.py` | `# type: ignore[misc]` | тест навмисно присвоює полю frozen-dataclass, щоб перевірити `FrozenInstanceError` |
| `tests/test_store.py` | `# pyright: ignore[reportArgumentType]` | тест навмисно передає `"yaml"`, щоб перевірити перевірку під час виконання |

### Тести

`uv run pytest` — **138 тестів** (у `tests/`, спільний маленький корпус із 4
документів — у `conftest.py`):

- токенізатор (`parametrize`, включно з кирилицею, комбінованим наголосом і
  апострофами), корпус, збірка індексу, merge vs set, парсер (порівняння дерев),
  обидва скорери з трьома перевірками лаби 3, save/load, сніпети, статистика й
  P@5;
- **CLI через `typer.testing.CliRunner`**: таблиця, `--json` (кожен рядок stdout
  парситься як JSON, логи `-vv` — лише в stderr), усі очікувані помилки —
  код 1, рівно один рядок у stderr, без `Traceback`;
- `@pytest.mark.slow` — два наскрізні тести, що будують індекс в одному процесі
  й шукають в іншому (регресія пастки `__main__` з лаби 2), ~1 с кожен.
  Швидкий прогін: `uv run pytest -m "not slow"`; CI запускає всі.

**Hypothesis — 6 властивостей** (`tests/test_properties.py`), кожна — твердження
для *будь-якого* входу:

1. `load(save(index))` дає той самий індекс — для обох форматів, з позиціями й без;
2. постинги завжди відсортовані й унікальні, сума tf документа = його довжині,
   позиції відсортовані і їх рівно tf;
3. merge-операції збігаються з операціями над `set` (оракул);
4. `parse(str(tree)) == tree` для випадкових дерев запитів;
5. токенізатор не видає порожніх токенів і **ідемпотентний**: токенізація
   склеєних токенів дає ті самі токени;
6. BM25 не спадає з ростом tf і не зростає з ростом довжини документа.

**Hypothesis знайшов справжній баг.** Властивість 5 упала на вході з одного
символу U+02BC (MODIFIER LETTER APOSTROPHE) і стиснула контрприклад до нього.
За Unicode це *літера* (категорія Lm), тож `\w` її захоплював: виходив токен
`'` з одного апострофа, а з `ʼtis` — `'tis` з апострофом на початку, що порушує
власне правило токенізатора з лаби 1. Виправлено: U+02BC виключено з класу
«літера/цифра» (усередині слова він і далі працює як апостроф), додано
регресійний тест. Після виправлення властивість пройшла 20 000 прикладів.
На англійському корпусі цифри не змінилися (10 487 819 токенів, 146 829 термів).

### Покриття

`uv run pytest --cov` — **75%** (з гілками):

| Модуль | Покриття | |
|--------|----------|---|
| `cli.py` | 98% | |
| `store.py` | 98% | |
| `query.py`, `snippet.py` | 97% | |
| `rank.py`, `stats.py` | 95% | |
| `corpus.py` | 94% | |
| `index.py` | 77% | не покрито: `main()` для `python -m findex.index` |
| `evaluate.py` | 67% | не покрито: `main()`; сама логіка P@5 покрита |
| `tokenize.py`, `engines.py`, `timing.py`, `__init__.py` | 100% | |
| `bench.py`, `memstudy.py`, `sanity.py` | 0% | навмисно |
| `__main__.py`, `search.py` | 0% | навмисно |

Що навмисно лишилось поза покриттям і чому:

- **`bench.py`, `memstudy.py`, `sanity.py`** — скрипти вимірювань для README
  лаб 2–3: вони друкують таблиці з реальними числами на повному корпусі, а
  перевіряти їм нічого (вимірювані функції — merge, scorer-и, збірка індексу —
  покриті тестами).
- **`main()` у `index.py` / `evaluate.py`, `__main__.py`, `search.py`** — тонкі
  точки входу `python -m …`. `python -m findex.index` і `python -m findex search`
  виконуються в slow-тестах, але в окремому процесі, а coverage міряє лише
  поточний процес.

### CI

`.github/workflows/ci.yml` — на кожен push і pull request, на Ubuntu та
Windows: `uv sync --locked` → `ruff check` → `ruff format --check` → `pyright` →
`pytest --cov` (усі тести, разом зі slow) → `uv build` → встановлення
зібраного wheel у чисте оточення й `findex --help`. Wheel з Ubuntu
зберігається як артефакт. `uv.lock` закомічений, тож CI ставить рівно ті самі
версії залежностей.

### Реліз

Тег `v0.4.0` (версія в `pyproject.toml` — `0.4.0`), wheel
`findex-0.4.0-py3-none-any.whl` прикріплений до GitHub Release v0.4.0.

### Чого я навчився

- Анотації нічого не роблять під час виконання — їх читають pyright, typer
  (будує з них CLI) і `dataclasses`. Strict-режим знаходить не лише помилки, а й
  місця, де тип був «будь-який»: `Engine` з `Any` став дженериком.
- Властивість сильніша за приклад: 10 ручних тестів токенізатора не знайшли U+02BC,
  а Hypothesis знайшов і стиснув контрприклад до одного символу.
- Розділення stdout/stderr — це контракт CLI: без нього `--json | jq` ламається
  від першого рядка логу.
