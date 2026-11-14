"""Streaming tokenizer.

Pipeline for one text:  NFC normalise the text -> ``re.finditer`` over
TOKEN_RE -> for each match: unify apostrophes + ``casefold()`` -> yield.

Why casefold per token and not on the whole text: ``str.casefold()`` on a
4.3M-character document temporarily allocates ~52 MiB in CPython (a UCS-4
buffer with room for 3 output characters per input character, because one
character may fold into up to three, e.g. "ﬃ" -> "ffi"). Casefolding each
short token gives the same tokens — the pattern matches letters of any case —
without that spike. See README, "Where the memory went".

Tokenisation rules (the "policy"):

1. **Unicode.** Text is normalised to NFC first, so ``"cafe" + U+0301`` and the
   precomposed ``"café"`` give the same token. Then ``str.casefold()`` (not
   ``lower()``): ``"Straße"`` -> ``"strasse"``, so it matches ``"STRASSE"``.
2. **What is a token.** A maximal run of letters and digits in any script
   (``[^\\W_]`` = Unicode ``\\w`` without ``_``). Combining marks left after
   NFC (letters with no precomposed form) stay inside the token.
   Underscore is a separator: Gutenberg uses ``_italic_`` markup.
3. **Apostrophes** are kept *inside* a word: ``don't``, ``o'clock``,
   ``п'ять`` (in Ukrainian the apostrophe is part of the spelling). The
   variants ``’ ‘ ʼ `` ` are unified to ``'``. A leading or trailing
   apostrophe is a quote mark and is dropped: ``'tis`` -> ``tis``,
   ``students'`` -> ``students``.
4. **Hyphens split**: ``well-known`` -> ``well``, ``known``. A search for
   "known" should find "well-known", and Gutenberg texts contain line-break
   hyphenation and ``--`` dashes, so joining on hyphens adds noise.
5. **Digits** are ordinary token characters: ``1984`` -> ``1984``,
   ``mp3`` -> ``mp3``, ``19th`` -> ``19th``. Punctuation inside numbers
   splits them: ``3.14`` -> ``3``, ``14``; ``1,000`` -> ``1``, ``000``.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator

APOSTROPHE_VARIANTS = "’‘ʼ`"
APOSTROPHES = str.maketrans(dict.fromkeys(APOSTROPHE_VARIANTS, "'"))

# One "letter-or-digit" possibly followed by combining marks (U+0300..U+036F).
_ALNUM = r"(?:[^\W_][̀-ͯ]*)+"
TOKEN_RE = re.compile(rf"{_ALNUM}(?:['{APOSTROPHE_VARIANTS}]{_ALNUM})*")


def normalize_token(token: str) -> str:
    """Unified apostrophes + casefold for one already-matched token."""
    if token.isascii() and "`" not in token:  # fast path: most tokens (isascii is O(1))
        return token.casefold()
    return token.translate(APOSTROPHES).casefold()


def tokenize(text: str) -> Iterator[str]:
    """Yield normalised tokens of `text` one at a time (see module docstring)."""
    for match in TOKEN_RE.finditer(unicodedata.normalize("NFC", text)):
        yield normalize_token(match.group())
