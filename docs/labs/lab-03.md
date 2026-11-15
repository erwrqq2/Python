# Лаба 3 — Рейтинг і модель об'єктів: dunder-методи, протоколи, декоратори

[← до README](../../README.md)

### Структура

```
src/findex/
  index.py      # Index тепер клас: Mapping[str, list[Posting]]
  store.py      # + open_index() — context manager
  engines.py    # merge/set-операції з лаби 2 (колишній search.py)
  query.py      # парсер рекурсивним спуском, вузли Term/Phrase/And/Or/Not
  rank.py       # Scorer, TfIdf, BM25, SearchResult, Searcher (+ lru_cache)
  snippet.py    # сніпети ±80 символів з підсвіткою
  timing.py     # @timed
  search.py     # CLI ранжованого пошуку
  sanity.py     # три sanity-check рейтингу на корпусі
  evaluate.py   # P@5 для TF-IDF vs BM25
evaluation/queries.json   # 10 запитів із ручною розміткою
tests/  test_query.py  test_rank.py  test_snippet.py  (+ оновлені test_index, test_store, ...)
```

Індекс для лаби 3 будується з позиціями (для фраз):

```bash
uv run python -m findex.index data/ --out index.fidx --format binary --positions
```

Індекси, збережені pickle-ом у лабі 2, треба перебудувати: клас `Index` змінився, а
pickle зберігає посилання на клас, а не дані «самі по собі».

### `Index` як об'єкт Python

`Index` успадковує `collections.abc.Mapping[str, list[Posting]]` і реалізує лише
абстрактні методи — `get`, `keys`, `items`, `values` дістаються безкоштовно:

```python
>>> with open_index("index.fidx") as ix:
...     ix
...     len(ix), "locomotive" in ix, ix["locomotive"][0], ix.df("locomotive")
...     ix.num_docs, round(ix.avg_doc_length)
Index(terms=146_829, docs=143, positions)
(146829, True, Posting(doc_id=10, tf=1, positions=(47038,)), 16)
(143, 73341)
```

| Синтаксис | Метод |
|-----------|-------|
| `len(ix)` | `__len__` — кількість термів |
| `"x" in ix` | `__contains__` — пряма перевірка в dict (швидше за дефолтну з `Mapping`, яка робить `ix[x]` у try/except) |
| `ix["x"]` | `__getitem__` — постинги; `KeyError`, якщо терму немає |
| `for t in ix` | `__iter__` — терми |
| `repr(ix)` | `__repr__` — `Index(terms=146_829, docs=143, positions)` |

- `num_docs` — `@property`; `avg_doc_length` — `@functools.cached_property`:
  BM25 потрібне це число для кожного постингу, тож воно рахується один раз і
  зберігається в `__dict__` екземпляра (тому `Index` — не `slots`-клас).
- `doc_length(doc_id)`, `df(term)`.
- **Рівність — за ідентичністю.** `Mapping` порівнює вміст, але порівнювати два
  індекси по 147 тис. списків постингів ніколи не потрібно: індекс — це ресурс,
  а не значення. Тому `__eq__ = object.__eq__` і `__hash__ = object.__hash__`.

`open_index(path)` — `@contextmanager`: усе до `yield` — це `__enter__`, блок
`finally` — це `__exit__`, який виконується і при нормальному виході, і при
`return`, і при винятку. Виняток не ковтається: він виникає в точці `yield`, і
після `close()` летить далі. `close()` звільняє дані індексу. Тести перевіряють
обидва випадки.

### Мова запитів: парсер рекурсивним спуском

Граматика (від найнижчого пріоритету):

```
or_expr  := and_expr ( "OR" and_expr )*
and_expr := not_expr ( ["AND"] not_expr )*     # AND можна пропускати
not_expr := "NOT" not_expr | atom
atom     := СЛОВО | "фраза в лапках" | "(" or_expr ")"
```

Кожне правило — окремий метод `_Parser`, який викликає методи вищого пріоритету;
дужки повертають у `or_expr` — звідси й «рекурсивний спуск». Лексер (`_lex`)
спершу розбиває рядок на дужки, оператори й готові вузли `Term`/`Phrase`.

- `a OR b c` → `Or(a, And(b, c))`: AND зв'язує сильніше за OR, тому `b c`
  складається в `And` всередині правої частини OR.
- `a NOT b` → `And(a, Not(b))`; `NOT a OR b` → `Or(Not(a), b)`.
- Оператори — лише ВЕЛИКИМИ літерами; `and`/`or`/`not` — звичайні слова.
- Слова проходять токенізатор документів; слово з дефісом (`well-known`) стає
  фразою.

**Це заміна мови з лаби 2**, де запит читався строго зліва направо. Тепер
`whale OR text NOT ship` означає `whale OR (text AND NOT ship)`, а не
`(whale OR text) NOT ship`.

Вузли — `@dataclass(frozen=True)`, тому дерева порівнюються через `==` (тести
парсера порівнюють дерева, а не рядки) і є хешованими. Їх можна будувати
операторами (`__and__`, `__or__`, `__invert__`):

```python
>>> parse('python AND (async OR await) NOT java "event loop"')   # перенесено для читабельності
And(left=And(left=And(left=Term(word='python'),
                      right=Or(left=Term(word='async'), right=Term(word='await'))),
             right=Not(child=Term(word='java'))),
    right=Phrase(words=('event', 'loop')))
>>> Term("a") | Term("b") & ~Term("c") == parse("a OR b NOT c")
True
```

`node.evaluate(index, engine)` повертає булеву множину збігів будь-яким із двох
рушіїв лаби 2 (`--engine set|merge`). `And(x, Not(y))` обчислюється як `x − y`,
без побудови доповнення `y`. **Фраза** вимагає позицій: документи-кандидати —
перетин списків постингів, далі для кожного документа двовказівниковий merge:
позиція `p` першого слова лишається, якщо `p + 1` є в позиціях другого, `p + 2` —
у третього, і так далі.

### Ранжування: протокол `Scorer`, TF-IDF і BM25

```python
class Scorer(Protocol):
    def __call__(self, term: str, posting: Posting, index: Index) -> float: ...
```

`TfIdf` і `BM25` — frozen-dataclass з `__call__`; від протоколу вони **не
успадковуються** (duck typing): підходить будь-який callable з такою сигнатурою,
навіть `lambda` (перевіряється в тестах через `@runtime_checkable`).

- **TF-IDF:** `(1 + ln tf) · ln(N / df)`.
- **BM25** (варіант Lucene/Elasticsearch):
  `idf · tf·(k1+1) / (tf + k1·(1 − b + b·dl/avgdl))`, де
  `idf = ln(1 + (N − df + 0.5)/(df + 0.5))` (ніколи не від'ємний), `k1 = 1.5`,
  `b = 0.75`.
  - **k1** керує насиченням tf: зі зростанням tf бал прямує до `idf·(k1+1)`.
  - **b** керує нормалізацією за довжиною: при `b = 0` вона вимкнена, при `b = 1`
    повна.

`Searcher.search(query, scorer=BM25(), k=10)`:

1. розбирає запит у дерево;
2. отримує булеву множину збігів (кешовано, див. нижче);
3. для кожного «позитивного» терму запиту (не під `NOT`) проходить його постинги
   й додає `score[doc_id] += scorer(term, posting, index)`;
4. бере топ-k через `heapq.nlargest(k, …)` — O(n log k) замість повного
   сортування O(n log n).

`SearchResult(score, doc_id, title, snippet)` — `@dataclass(frozen=True,
order=True)`. Поля впорядковані як `(score, doc_id)`, а `title` і `snippet`
мають `compare=False`. Тому `sorted(results, reverse=True)` дає найкращі першими,
і два результати рівні, лише якщо рівні їхні бали й документ. (У завданні
порядок полів `(doc_id, score, title)`, але тоді `sorted()` сортував би за
`doc_id`.)

### Три sanity-check рейтингу

`uv run python -m findex.sanity index.fidx`

**1. Рідкісний терм вище частого.** Запит `river OR locomotive`:
df(river) = 107, df(locomotive) = 16 зі 143.

| BM25: місце | бал | документ | містить `locomotive` |
|---|---|---|---|
| 1 | 6.072 | The Life of George Stephenson… | так |
| 2 | 4.971 | The hand of Providence | так |
| … | | | так |
| 16 | 1.871 | Our War with Spain for Cuba's Freedom | так |
| 17 | 0.722 | The Americans in the Great War… | ні |
| 18 | 0.720 | With Lee in Virginia… | ні |

Усі 16 документів із `locomotive` стоять вище за будь-який документ лише з `river`
(і для TF-IDF теж). idf(river) у BM25 = 0.29, тож навіть дуже часта «river» дає
максимум 0.29 × 2.5 ≈ 0.73, а одне входження `locomotive` (idf 2.17) — більше.

**2. 20-те повторення майже нічого не додає.** Терм `locomotive`, документ
середньої довжини (73 258 токенів):

| tf | BM25 | внесок цього входження | TF-IDF | внесок цього входження |
|---|---|---|---|---|
| 1 | 2.168 | +2.168 | 2.190 | +2.190 |
| 2 | 3.096 | +0.929 | 3.708 | +1.518 |
| 3 | 3.612 | +0.516 | 4.596 | +0.888 |
| 5 | 4.167 | +0.227 | 5.715 | +0.489 |
| 10 | 4.710 | +0.067 | 7.234 | +0.231 |
| 19 | 5.020 | +0.020 | 8.639 | +0.118 |
| 20 | 5.039 | **+0.018** | 8.752 | +0.112 |

20-те входження додає в BM25 0.8% від першого, і бал уже на 93% межі
`idf·(k1+1) = 5.416`. У TF-IDF (з логарифмом) — 5%, і росте він без межі.

**3. Короткий документ з одним входженням вище дуже довгого.** Слово
`illustration`, по одному входженню:

| документ | довжина, токенів | tf | BM25 | TF-IDF |
|---|---|---|---|---|
| Jonah Christmas 1917 | 1 475 | 1 | **0.661** | 0.368 |
| Motion pictures, 1950-1959 : Catalog… | 584 444 | 1 | 0.089 | 0.368 |

BM25 ставить короткий документ у 7,4 раза вище; TF-IDF довжини не бачить і дає
однаковий бал.

### Декоратори та кеш: `@timed` і `lru_cache`

`@timed` (`timing.py`, з `functools.wraps`) стоїть на `build_index`, `load`,
`save` і `Searcher.search`. Він пише в лог `findex.timing` рядок
`"<ім'я> took <мс> ms"` у блоці `finally`, тож виклики, що впали з винятком,
теж вимірюються. Без `wraps` усі обгорнуті функції в логах і трасуваннях
називалися б `wrapper` і втратили б docstring.

Булевий збіг кешується через `functools.lru_cache(maxsize=256)`:

- **Ключ — дерево запиту, а не рядок.** Вузли — frozen-dataclass, отже хешовані,
  тож `Locomotive (steam OR engine) NOT "Civil War"` і
  `locomotive AND (steam OR engine) NOT "civil war"` — один запис у кеші.
- **Кеш живе в екземплярі `Searcher`** (`self._match_cached =
  lru_cache(maxsize=256)(self._match)`). Якщо повісити `@lru_cache` на метод
  класу, ключем став би й `self`, і кеш тримав би кожен `Searcher` (разом з
  індексом) до кінця процесу.

```bash
uv run python -m findex.search index.fidx 'locomotive AND (steam OR engine) NOT "civil war"' \
    'Locomotive (steam OR engine) NOT "Civil War"' -k 3 -v
```

```
  DEBUG findex.timing: load took 1414.0 ms
  DEBUG findex.timing: Searcher._match took 0.4 ms
  DEBUG findex.rank: match cache: CacheInfo(hits=0, misses=1, maxsize=256, currsize=1)
  DEBUG findex.timing: Searcher.search took 28.1 ms
  DEBUG findex.timing: Searcher._match took 0.0 ms
  DEBUG findex.rank: match cache: CacheInfo(hits=1, misses=1, maxsize=256, currsize=1)
  DEBUG findex.timing: Searcher.search took 28.2 ms
Index(terms=146_829, docs=143, positions) loaded in 1.41 s, scorer=BM25(k1=1.5, b=0.75)

query:  locomotive AND (steam OR engine) NOT "civil war"
parsed: ((locomotive AND (steam OR engine)) AND NOT "civil war")
6 matching documents, top 3 (28.2 ms):
  1.   8.216  [  33] The Boy Scouts Book of Campfire Stories
           …d been well roofed with foliage. Here the travelers got their first view of a **steam** **engine**. The latter stood puffing and smoking near the village of Utica, to th…
  2.   7.734  [  59] Brooks's Readers, Third Year
           …reason mischief pasture couch bitter exclaimed sloping **steam** The old apple tree had stood in the corner of the pasture for so many ye…
  3.   6.845  [  10] A Gunner Aboard the "Yankee" From the Diary of Number Five of the Aft…
           …eck and piled them five deep, and about the same number in height, around the **steam** steering **engine** under the forward wheel-house. This was to give added protect…
```

Другий запит — інший рядок, але те саме дерево: `_match` зайняв 0.0 мс,
`hits=1`. Більшу частину з 28 мс `search` займає читання файлів для сніпетів;
сам булевий збіг — 0.4 мс.

### Сніпети

Для кожного результату — вікно ±80 символів навколо «найкращого» входження,
терми запиту підсвічені `**…**`. Найкраще входження — те, у вікні якого
найбільше *різних* термів запиту (при рівності — найперше), тому вище
`**steam** **engine**` вибрано замість одиночного `steam`. Пошук входжень — один
скомпільований regex з усіх термів із межами слова та `IGNORECASE`, тож він
працює на C по сирому тексту, без повторної токенізації книги на Python.
Вікна шукаються ковзним вікном із двома вказівниками, за O(кількість входжень).

### Оцінка: precision@5, TF-IDF vs BM25

`uv run python -m findex.evaluate index.fidx evaluation/queries.json --details`

10 запитів, для кожного вручну позначено книги, які *мають* бути в топ-5
(`evaluation/queries.json`; розмітка — за назвою та тематикою книги, зроблена до
запуску пошуку). P@5 = релевантних у топ-5 / 5. Коли релевантних книг менше
п'яти, найкраще можливе P@5 менше 1 — це стовпець `max`.

| Запит | Релевантних | max | BM25 P@5 | TF-IDF P@5 |
|---|---|---|---|---|
| `fishing trout flies` | 2 | 0.4 | 0.4 | 0.4 |
| `insane asylum` | 3 | 0.6 | 0.4 | 0.4 |
| `bees honey hive` | 1 | 0.2 | 0.2 | 0.2 |
| `maya yucatan` | 2 | 0.4 | 0.4 | 0.4 |
| `telegraph` | 1 | 0.2 | 0.2 | 0.2 |
| `locomotive railway` | 1 | 0.2 | 0.2 | 0.2 |
| `"civil war" confederate` | 2 | 0.4 | 0.4 | 0.4 |
| `jesus disciples` | 2 | 0.4 | 0.4 | 0.2 |
| `slavery slaves` | 3 | 0.6 | 0.4 | 0.2 |
| `apes monkeys` | 1 | 0.2 | 0.2 | 0.2 |
| **середнє** | | **0.36** | **0.32** | **0.28** |

Що видно з `--details`:

- **TF-IDF тягне догори найдовший документ.** *Motion pictures, 1950–1959*
  (каталог на 584 тис. токенів, де трапляється майже будь-яке слово) потрапляє в
  топ-5 TF-IDF у 6 запитах з 10 (`fishing trout flies`, `bees honey hive`,
  `telegraph`, `locomotive railway`, `apes monkeys`, `insane asylum`). У BM25 —
  лише в двох і нижче. Це саме те, що лагодить нормалізація за довжиною (b).
- **`jesus disciples`:** TF-IDF ставить на 2–4 місця богословські трактати, а
  BM25 повертає в топ-5 Weymouth New Testament (короткий том передмов).
- **`slavery slaves`:** BM25 піднімає *A Child's Anti-Slavery Book* на 1-ше місце
  (у TF-IDF — 3-тє) і додає *The American Missionary*.
- **Чого не вистачає обом:**
  - *Narrative of Venture* (`slavery slaves`) збігається, але не в топ-5: у BM25 —
    7-ме місце (5.009 проти 5.169 на 5-му), у TF-IDF — 14-те. Це коротка книга
    (10 тис. токенів при середніх 73 тис.) з кількома згадками; нормалізація
    за довжиною піднімає її на 7 місць, але не вистачає.
  - *Colony Treatment of the Insane* (`insane asylum`) зовсім не збігається: у ній
    є лише «asylums» (1 раз), а неявний AND вимагає саме «asylum». Без стемінгу
    (asylum/asylums, slave/slaves) це різні слова.
  - Насичення tf у BM25 має і зворотний бік: у книзі про телеграф слово
    `telegraph` трапляється 261 раз, але це майже не відрізняється від 2–3
    входжень.

Вибірка мала й шумна (10 запитів, 1–3 релевантні книги на запит), але напрям
однозначний: BM25 ≥ TF-IDF у кожному запиті, середнє 0.32 проти 0.28 із
максимально можливих 0.36.

### Чого я навчився

- Синтаксис Python (`len`, `in`, `[]`, `for`, `with`, `&`, `|`, `~`) — це
  виклики dunder-методів. Реалізувавши кілька з них, отримуєш об'єкт, що
  поводиться як вбудований, а `Mapping` додає решту.
- Протокол важливіший за ієрархію: скорер — це просто callable.
- Хешованість знову знадобилася: frozen-дерево запиту — готовий ключ для
  `lru_cache`.
- Метрика на реальних даних показала те, що в теорії виглядає дрібницею:
  без нормалізації за довжиною один гігантський документ засмічує видачу.
