# findex

[![CI](https://github.com/erwrqq2/Python/actions/workflows/ci.yml/badge.svg)](https://github.com/erwrqq2/Python/actions/workflows/ci.yml)

Невелика повнотекстова пошукова система, яку крок за кроком будую протягом
8 лабораторних курсу Python. Кожна лаба додає одну особливість мови й одну
частину рушія.

| Лаба | Тема | Тег |
|------|------|-----|
| 1 | Ітератори, генератори та корпус | `lab-01` |
| 2 | Інвертований індекс: словники, хешування та пам'ять | `lab-02` |
| 3 | Рейтинг і модель об'єктів: dunder-методи, протоколи, декоратори | `lab-03` |
| 4 | Типи, тести, пакування і CLI | `lab-04`, `v0.4.0` |

## Запуск

Потрібні [uv](https://docs.astral.sh/uv/) і Python 3.13

```bash
uv sync
uv run python scripts/download_gutenberg.py --count 150   # корпус -> data/
uv run python -m findex.stats data/
uv run findex index data/ --out index.fidx --positions
uv run findex search index.fidx 'locomotive AND (steam OR engine) NOT "civil war"'
uv run findex stats index.fidx
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

---

## Лаба 1 — Ітератори, генератори та корпус

### Структура

```
src/findex/
  corpus.py     # iter_documents(root) -> Iterator[Document]
  tokenize.py   # tokenize(text) -> Iterator[str]
  stats.py      # конвеєр + `python -m findex.stats`
tests/
  test_tokenize.py
  test_corpus.py
scripts/
  download_gutenberg.py
```

### `iter_documents(root)`

Генератор: сам виклик не робить жодного вводу-виводу, кожен `next()` відкриває
один файл, повертає `Document(doc_id, path, text)` (`NamedTuple`) і йде далі.
Джерела: дерево каталогів із `*.txt` (через `Path.rglob`) або файли `*.jsonl`
(по одному JSON-об'єкту з полем `"text"` у рядку). Некоректний UTF-8
записується в лог як попередження й декодується із символами заміни; файли,
які не вдалося прочитати, і биті JSON-рядки логуються й пропускаються — один
поганий документ ніколи не зупиняє обробку.

Список *шляхів* сортується (кілька КБ), тож `doc_id` стабільні між запусками;
самі тексти ніколи не збираються в пам'ять разом.

Демонстрація лінивості — повертається миттєво, читається лише перший файл:

```python
>>> from pathlib import Path
>>> from findex.corpus import iter_documents
>>> next(iter_documents(Path("data"))).path
WindowsPath('data/gutenberg/10072.txt')     # 0.001 с на корпусі 61 МБ
```

### `tokenize(text)` — правила

Порядок: **NFC для всього тексту → `re.finditer` → для кожного токена:
уніфікація апострофів + `casefold()`**. У лабі пропонується робити casefold
усього тексту до пошуку токенів; я роблю casefold кожного знайденого токена,
бо `casefold()` для цілого великого документа коштує ~52 МіБ тимчасового буфера
Шаблон приймає літери в будь-якому регістрі, тож
токени ті самі — перевірено на всьому корпусі: однакова кількість токенів
(10 487 819) і розмір словника (146 829) в обох варіантах.

| Правило | Приклад |
|---------|---------|
| Спершу NFC: розкладені й складені літери рівні | `cafe` + U+0301 → `café` |
| `casefold()`, а не `lower()` | `Straße` → `strasse` |
| Токен = максимальна послідовність Unicode-літер/цифр (`[^\W_]`), будь-яка писемність | `Київ` → `київ` |
| `_` — роздільник (у Gutenberg `_курсив_`) | `_italic_` → `italic` |
| Апостроф усередині слова зберігається; `’ ‘ ʼ` `` ` `` зводяться до `'` | `don't`, `П’ять` → `п'ять` |
| Апостроф на початку/в кінці слова — це лапка, відкидається | `'Tis` → `tis`, `students'` → `students` |
| Дефіс розділяє слова (кращий recall: запит "known" знайде "well-known"; у Gutenberg багато тире `--`) | `well-known` → `well`, `known` |
| Цифри — звичайні символи токена; розділові знаки всередині числа розбивають його | `1984`, `mp3`, `19th`; `3.14` → `3`, `14` |

Регулярний вираз (`A` = `(?:[^\W_][\u0300-\u036f]*)+` — послідовність
літер/цифр разом із комбінованими діакритичними знаками): `A(?:['’‘ʼ`]A)*`.

Тести: `tests/test_tokenize.py` (10 перевірок: генератор, порожній рядок і
лише пунктуація, змішаний регістр + casefold, пунктуація, кирилиця,
комбінований наголос, апострофи, апострофи-лапки, дефіси, цифри) і
`tests/test_corpus.py` (лінивість, порядок, JSON-lines, погане кодування).

Обидві функції — генератори, це перевіряється в тестах через
`inspect.isgeneratorfunction`.

### Статистика

```bash
uv run python -m findex.stats data/              # увесь корпус, лінива версія
uv run python -m findex.stats data/ --limit 50   # перші 50 документів (itertools.islice)
uv run python -m findex.stats data/ --eager      # версія на списках
```

Вивід на всьому корпусі:

```
mode:        lazy (generators)
documents:   143
tokens:      10,487,819
vocabulary:  146,829
top 50 terms:
    1. the                  667,169
    2. of                   365,524
    3. and                  325,893
    4. to                   270,761
  ...
   50. would                 24,666
elapsed:     22.76 s
peak memory: 26.3 MiB (tracemalloc)
```

Усі топ-50 — службові слова (the, of, and, to, a, in, that, he, was, i, ...) —
закон Ципфа; стоп-слова матимуть значення для ранжування в лабі 3.

### Жадна vs лінива версія

`compute_stats_eager` читає всі документи в список, токенізує кожен у список
токенів і лише потім рахує. `compute_stats` працює потоково: один документ →
токени → `Counter`. Той самий корпус, той самий токенізатор, кожен запуск в
окремому процесі (`python -m findex.stats data/ [--eager] [--limit N]`).
Windows 11, Python 3.13.18, 32 ГБ RAM; жадна версія витримує весь корпус, тому
основна таблиця — на ньому.

| Version | Documents | Peak memory | Elapsed |
|---------|-----------|-------------|---------|
| eager (lists) | 143 | 627.3 MiB | 23.22 s |
| lazy (generators) | 143 | 26.3 MiB | 22.76 s |

Як це масштабується з розміром корпусу:

| Документів | Токенів | пік eager | пік lazy | час eager | час lazy |
|-----------|--------|-----------|----------|-----------|----------|
| 25  | 2 127 932  | 125.1 МіБ | 8.9 МіБ  | 4.44 с  | 4.29 с  |
| 75  | 5 721 742  | 334.7 МіБ | 11.3 МіБ | 11.82 с | 12.07 с |
| 143 | 10 487 819 | 627.3 МіБ | 26.3 МіБ | 23.22 с | 22.76 с |

Час виміряно з увімкненим tracemalloc, який сповільнює код із великою
кількістю алокацій; обидві версії платять однакові накладні витрати.

### Куди пішла пам'ять

**Жадна версія (627 МіБ)** — окремо виміряно через tracemalloc:

- **82 МіБ — усі тексти одночасно** в `list[Document]`. 61 МБ на диску в пам'яті
  займають більше: `str` зберігає кожен символ із шириною найширшого символу
  рядка, тож один `’` чи `☆` робить усю книгу по 2 байти на символ.
- **540 МіБ — список списків токенів.** 10,5 млн токенів × ~54 байти: кожен
  токен — окремий об'єкт `str` (`sys.getsizeof("the")` = 44 байти: заголовок,
  хеш, довжина, дані) плюс 8-байтовий вказівник у списку (лише об'єкти списків —
  85 МіБ). Слово "the" трапляється 667 тис. разів і зберігається 667 тис. разів —
  список тримає кожне входження, а `Counter` — один ключ на кожен окремий терм.
- Підсумковий `Counter` (~11 МіБ) — єдина частина, яка насправді потрібна.

Пам'ять росте лінійно з розміром корпусу (125 → 335 → 627 МіБ). Час однаковий,
бо обидві версії виконують ту саму роботу; жадна просто тримає проміжні
результати. Це перестає бути безкоштовним, щойно дані не вміщаються в RAM.

**Лінива версія (26 МіБ) — чому не нуль:** пік — це «найбільший окремий
документ + `Counter`», і він не залежить від кількості документів:

- `Counter` зі 146 829 термами: 10,6 МіБ наприкінці;
- найбільший документ (*Motion pictures 1950–1959*, каталог реєстрації
  авторських прав, 4,3 млн символів, містить `☆` → 8,2 МіБ як `str`), плюс сирі
  байти (4,3 МіБ), які коротко існують під час декодування;
- кадри генераторів, один `Document`, один об'єкт збігу `re` — байти.

Пік лінивої версії зменшився з **85 МіБ до 26 МіБ** після одного виправлення.
Покрокове профілювання найбільшого документа показало: NFC безкоштовний (текст
уже в NFC, `unicodedata.normalize` повертає той самий об'єкт); `translate`
коштує одну копію (8,5 МіБ); але **`casefold()` для всього документа дав пік
59,6 МіБ** — CPython виділяє UCS-4-буфер із запасом на 3 вихідні символи на
кожен вхідний (один символ може перетворитися на три, напр. `ﬃ` → `ffi`):
4,3 млн × 3 × 4 Б ≈ 52 МіБ. Casefold кожного токена після пошуку прибирає і цей
сплеск, і копію всього тексту; швидкий шлях для ASCII (`str.isascii()` — O(1))
пропускає `translate` для більшості токенів, тож код ще й прискорився
(4,73 с → 4,29 с на 25 документах).

### Чого я навчився

- Генераторна функція не виконує жодного коду до першого `next()`; помилки
  з'являються там, де генератор споживається (у `Counter.update`), а не там, де
  він визначений.
- Пам'ять лінивого конвеєра обмежена *найбільшим елементом*, а не *кількістю
  елементів* — тож пік визначає один величезний документ.
- Спершу виміряй, потім вгадуй: прихована ціна була в `casefold()`, а не в
  регулярному виразі.

---

## Лаба 2 — Інвертований індекс: словники, хешування та пам'ять

### Структура

```
src/findex/
  index.py      # Posting, DocMeta, Index, build_index() + `python -m findex.index`
  search.py     # Boolean-пошук, рушії merge/set + `python -m findex.search`
  store.py      # save()/load(): pickle і власний бінарний формат
  memstudy.py   # три варіанти зберігання постингів під tracemalloc
  bench.py      # бенчмарк merge vs set
tests/
  test_index.py  test_search.py  test_store.py  test_cli.py
```

### Індекс

- `postings: dict[str, list[Posting]]` — терм → постинги, відсортовані за `doc_id`;
- `doc_lengths: dict[int, int]` — кількість токенів у документі (для BM25 у лабі 3);
- `doc_meta: dict[int, DocMeta]` — шлях і назва для показу результатів.

`Posting(doc_id, tf, positions=())` і `DocMeta(doc_id, path, title)` —
`@dataclass(frozen=True, slots=True)`: незмінні, хешовані (можна класти в `set`),
без `__dict__` в кожному екземплярі.

Індекс будується **одним проходом** по лінивому конвеєру з лаби 1: для кожного
документа токени рахуються в маленькому `Counter`, потім по одному `Posting` на
кожен терм додається в `defaultdict(list)`. Корпус у пам'ять не завантажується —
росте лише індекс. Постинги відсортовані без жодного `sort()`: `iter_documents`
видає `doc_id` 0, 1, 2, … за зростанням, а ми лише дописуємо в кінець.

Прапорець `--positions` додає до кожного постингу позиції токенів (для фразового
пошуку в лабі 3).

```bash
uv run python -m findex.index data/ --out index.bin                                # pickle
uv run python -m findex.index data/ --out index.fidx --format binary --positions   # свій формат
```

```
documents:   143
terms:       146,829
postings:    916,902
positions:   no
build:       24.49 s
save:        6.94 s (pickle)
file:        index.bin (15.1 MiB)
peak memory: 230.5 MiB (tracemalloc)
```

### Пошук

```bash
uv run python -m findex.search index.bin "telegraph OR telephone NOT war"
uv run python -m findex.search index.fidx "whale harpoon" --engine set
```

```
query:       'telegraph OR telephone NOT war' (engine=merge)
6 of 143 documents match
  [  11] Mount Music  (data\gutenberg\14025.txt)
  [  28] The Great Potlatch Riots  (data\gutenberg\24864.txt)
  ...
load:        6.081 s
search:      0.201 ms
peak memory: 230.2 MiB (tracemalloc)
```

(`load` тут повільний через tracemalloc; чисті цифри — у таблиці збереження нижче.)

Мова запитів обчислюється **строго зліва направо**, без пріоритетів і дужок
(справжній парсер — у лабі 3):

| Запит | Значення |
|-------|----------|
| `a b`, `a AND b` | документи з обома термами |
| `a OR b` | з будь-яким із них |
| `a NOT b` | з `a`, але без `b` |
| `NOT a` | усі документи без `a` |
| `a OR NOT b` | з `a` або без `b` |
| `a OR b NOT c` | `(a OR b) NOT c` |

Слова запиту проходять той самий токенізатор, що й документи (`Whale` знайде
`whale`); слово, яке розпадається на кілька токенів (`well-known`), означає AND усіх.

Два рушії з однаковим інтерфейсом (`--engine {merge,set}`):

- **merge** — два вказівники по відсортованих списках `doc_id`: `intersect`,
  `union`, `difference` за O(len(a) + len(b)), без хешування;
- **set** — `set(a) & set(b)`, `|`, `-`, а потім `sorted()`.

Тести перевіряють, що обидва рушії дають однаковий результат на всіх запитах, а
merge-операції збігаються з set-операціями на 200 випадкових парах списків.

### Збереження: pickle і власний бінарний формат

`store.save(index, path, fmt)` / `store.load(path)`; формат `load` визначає за
першими байтами файлу.

**pickle** — один виклик в кожен бік. **Ніколи не викликайте `load()` для чужого
pickle-файлу.** Розпаковування pickle — це не «читання даних»: файл є програмою
для віртуальної машини pickle, і її опкод `REDUCE` викликає будь-яку функцію, яку
можна імпортувати, з аргументами з файлу — наприклад `os.system("...")`.
Підроблений `index.bin`, завантажений з інтернету, виконає довільний код у момент
`load()`. Pickle годиться лише для власних кеш-файлів (коментар про це є і в
`store.py`).

**binary (`.fidx`)** — власний формат на `array('I')`, у ньому лише числа й
рядки, тож завантаження ніколи не виконує код:

```
b"FIDX\x01"        магічні байти + версія
uint32 LE          довжина JSON-заголовка
JSON-заголовок     {"positions": bool,
                    "docs":  [[doc_id, path, title, length], ...],
                    "terms": [[term, offset, count, n_positions], ...]}
дані               для кожного терму: doc_ids (count × uint32), tfs (count × uint32),
                   positions (n_positions × uint32, діляться по постингах за tf)
```

Зсуви в заголовку дозволяють у майбутньому читати постинги одного терму без
розбору всього файлу (stretch: mmap).

Виміри без tracemalloc, найкраще з 3; завантаження — в окремому процесі:

| Формат | Позиції | Розмір файлу | save | load |
|--------|---------|--------------|------|------|
| pickle | ні  | 15.1 МіБ | 3.07 с | 1.15 с |
| binary | ні  | 10.4 МіБ | 0.44 с | 0.57 с |
| pickle | так | 54.1 МіБ | 4.68 с | 1.59 с |
| binary | так | 50.6 МіБ | 1.90 с | 1.21 с |

- Бінарний формат на 31% менший і вантажиться вдвічі швидше: замість опису кожного
  об'єкта (клас + стан) — суцільні масиви по 4 байти на число.
- Під час `save` pickle тримає memo-словник з посиланням на кожен записаний
  об'єкт: пік збереження 230 МіБ проти 114 МіБ для бінарного формату.
- `load` тимчасово вимикає GC: завантаження створює ~1 млн об'єктів і жодного
  сміття, а кожен автоматичний прохід GC знову обходить їх усі. Це прискорило
  завантаження приблизно на 25–35% (pickle 1.47 → 1.15 с, binary 0.73 → 0.57 с).

**Ціна позицій.** Файл більшає в 3,6× (pickle) / 4,9× (binary), пік побудови й
збереження — 114 → 549 МіБ. Позицій 10,5 млн (по одній на кожен токен корпусу),
у середньому 11,4 на постинг: 11,4 × 4 Б ≈ 46 Б позицій проти 8 Б на `(doc_id, tf)`.
У пам'яті ще гірше: кортеж позицій — це об'єкт, а кожне число > 256 — окремий
`int` (28–32 Б).

**Пастка `__main__`.** Перша версія `python -m findex.index` зберігала
нечитабельний pickle: під `-m` модуль виконується як `__main__`, тож класи
записувалися як `__main__.Index` / `__main__.Posting`, і `findex.search` падав з
`Can't get attribute 'Posting'`. Тепер точка входу викликає `main()` з
імпортованого модуля `findex.index`; `tests/test_cli.py` будує індекс в одному
процесі й шукає в іншому, щоб помилка не повернулася.

### Пам'ять: три способи зберігати постинги

`uv run python -m findex.memstudy data/` — той самий корпус, той самий лінивий
потік, без позицій; кожен варіант будується під tracemalloc, зберігається через
pickle і завантажується назад.

| Postings representation | Peak memory (build) | Index file size | Load time |
|---|---|---|---|
| `list[Posting]`, plain `@dataclass` | 116.9 MiB | 20.3 MiB | 0.64 s |
| `list[Posting]`, `slots=True` | 81.9 MiB | 15.0 MiB | 1.90 s |
| `array('I')` pairs (doc_ids, tfs) | 56.5 MiB | 13.2 MiB | 0.19 s |

Скільки залишається в пам'яті після побудови: 112.7 / 77.7 / 51.0 МіБ.

**Куди пішли байти.** У корпусі 916 902 постинги і 146 829 термів.

- **Звичайний dataclass:** один екземпляр займає 96 Б (заголовок об'єкта 48 Б +
  значення атрибутів) плюс 8 Б вказівника в списку: 917 тис. × 104 Б ≈ 91 МіБ.
  У Python 3.13 атрибути зберігаються прямо в об'єкті, а справжній `__dict__`
  (ще +64 Б, виміряно) створюється лише при зверненні до `p.__dict__`. Тому
  різниця зі slots — ~40 Б, а не ~100 Б, як у старіших версіях.
- **`slots=True`:** `sys.getsizeof(Posting(...))` = 56 Б (заголовок + 3 слоти по
  8 Б), разом із вказівником — 64 Б × 917 тис. ≈ 56 МіБ. Різниця з першим рядком,
  35 МіБ, — це якраз 917 тис. × ~40 Б.
- **`array('I')`:** постинг займає лише 8 Б (4 Б `doc_id` + 4 Б `tf`, без
  об'єктів): 917 тис. × 8 Б ≈ 7 МіБ. Решта ~45 МіБ — це накладні витрати на
  терм, однакові для всіх рядків: словник на 147 тис. ключів, рядки термів
  (~50 Б кожен) і контейнери. У варіанті з масивами на кожен терм припадає кортеж
  (56 Б) і два об'єкти `array` (по 80 Б + запас). **64% термів трапляються лише в
  одному документі**, у середньому 6,2 постинги на терм, тож ці контейнери коштують
  більше, ніж самі дані.

Якщо рахувати лише постинги, `array` в 8 разів компактніший за slots (64 Б → 8 Б).
Загалом виграш лише в 1,45 раза: його з'їдають накладні витрати на терм. Числа
`doc_id ≤ 142` і більшість `tf` — малі цілі (≤ 256), CPython тримає їх у кеші,
тому в об'єктних варіантах вони нічого не коштують. На корпусі з тисячами
документів кожен `doc_id` став би окремим `int` на 28–32 Б, і різниця зросла б.

**Load time** показує інше: slots завантажується *повільніше* за звичайний
dataclass (1.90 с проти 0.64 с). Для frozen-dataclass зі slots `dataclasses`
генерує `__getstate__`/`__setstate__` на Python, який для кожного з 917 тис.
об'єктів викликає `object.__setattr__` на кожне поле. Звичайний екземпляр pickle
відновлює одним оновленням атрибутів на C. `array` серіалізується як суцільний
блок байтів, тому вантажиться найшвидше. (У цьому замірі GC під час
завантаження увімкнений.)

### Бенчмарк: merge vs set

`uv run python -m findex.bench index.bin` — найкращий із 5 повторів `timeit`,
мікросекунди на весь `evaluate()` (розбір запиту + отримання списків + операція).

| Терми (df) | Запит | merge, мкс | set, мкс | Збігів |
|---|---|---|---|---|
| 2 найчастіші: the (143), of (143) | `the of` | 17.0 | 12.9 | 143 |
| | `the OR of` | 17.1 | 12.5 | 143 |
| | `the NOT of` | 15.3 | 11.2 | 0 |
| 2 найрідкісніші: mehring (1), niebauer (1) | `mehring niebauer` | 3.9 | 4.0 | 1 |
| | `mehring OR niebauer` | 4.0 | 4.0 | 1 |
| | `mehring NOT niebauer` | 3.9 | 4.1 | 0 |
| частий + рідкісний: the (143), mehring (1) | `the mehring` | 10.8 | 7.3 | 1 |
| | `the OR mehring` | 12.6 | 8.9 | 143 |
| | `the NOT mehring` | 12.9 | 8.8 | 142 |

У корпусі зі 143 документів найдовший список постингів — 143 id, тому той самий
бенчмарк повторено на синтетичних списках (AND, випадкові id):

| Розміри списків | merge, мкс | set, мкс | set / merge |
|---|---|---|---|
| 1 000 & 1 000 | 109 | 37 | 0.34 |
| 10 000 & 10 000 | 1 171 | 760 | 0.65 |
| 100 000 & 100 000 | 13 511 | 13 107 | 0.97 |
| 1 000 000 & 1 000 000 | 137 584 | 163 257 | 1.19 |
| 1 000 000 & 10 | 92 335 | 57 343 | 0.62 |
| 1 000 000 & 1 000 | 103 754 | 63 433 | 0.61 |

**Хто де виграє і чому.** Асимптотика однакова, O(n + m), але константи різні:

- **Короткі списки — виграє set** (у 1,3–3 рази). Його цикли написані на C, а
  merge виконує байт-код Python на кожен крок. На рідкісних термах (по 1 id)
  різниці немає: час іде на розбір запиту й токенізацію.
- **Від ~100 тис. елементів — виграє merge** (1,19× на мільйоні). `set` мусить
  побудувати дві хеш-таблиці на мільйон записів (десятки МіБ, промахи кешу,
  алокації) і ще відсортувати результат. Merge лише послідовно читає два масиви.
- **Дуже різні розміри (1 000 000 & 10)** — погано обом: обидва лінійні за
  великим списком. Правильна відповідь — бінарний пошук / galloping (O(m log n)),
  ідея на майбутнє.

### Чого я навчився

- Хешованість — це контракт: `frozen=True` дає `__hash__` з полів, тож рівні
  `Posting` мають рівні хеші й поводяться як один ключ у `set`.
- `slots` економить пам'ять, але не все: у Python 3.13 звичайні екземпляри вже
  компактні, а pickle для slots-dataclass повільніший.
- Коли більшість термів трапляється один раз, пам'ять визначають накладні витрати
  на терм, а не постинги.
- pickle прив'язує дані до *імені модуля* класу — і до безпеки: це код, а не дані.

---

## Лаба 3 — Рейтинг і модель об'єктів: dunder-методи, протоколи, декоратори

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

---

## Лаба 4 — Типи, тести, пакування і CLI

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
