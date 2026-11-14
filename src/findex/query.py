"""Query language: recursive-descent parser -> tree of nodes -> matching doc_ids.

    python AND (async OR await) NOT java "event loop"

Grammar (lowest precedence first)::

    or_expr  := and_expr ( "OR" and_expr )*
    and_expr := not_expr ( ["AND"] not_expr )*      # AND may be omitted
    not_expr := "NOT" not_expr | atom
    atom     := WORD | "quoted phrase" | "(" or_expr ")"

So ``a OR b c`` is ``Or(a, And(b, c))`` and ``a NOT b`` is ``And(a, Not(b))``.
Operators must be UPPERCASE; ``and`` / ``or`` / ``not`` are ordinary words.
Words go through the document tokenizer: ``Whale`` -> ``whale``; a word that
splits into several tokens (``well-known``) becomes a phrase.

Nodes are frozen dataclasses, so trees compare with ``==`` (tests compare trees,
not strings) and are hashable (the search cache is keyed by the tree). They can
also be built with operators::

    Term("a") | Term("b") & ~Term("c")  ==  Or(Term("a"), And(Term("b"), Not(Term("c"))))

``node.evaluate(index, engine)`` returns the Boolean match set; ranking
(`findex.rank`) then scores only those documents.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Final

from findex.engines import MERGE, SET, Engine, EngineName, intersect
from findex.index import Index
from findex.tokenize import tokenize

OPERATORS: Final = frozenset({"AND", "OR", "NOT"})


class QueryError(ValueError):
    pass


# --- nodes --------------------------------------------------------------------------


class Node:
    """Base class: operator overloading and the evaluation interface."""

    def __and__(self, other: Node) -> And:
        return And(self, other)

    def __or__(self, other: Node) -> Or:
        return Or(self, other)

    def __invert__(self) -> Not:
        return Not(self)

    def evaluate[C](self, index: Index, engine: Engine[C]) -> C:
        """Matching doc_ids in the engine's representation (set for SET, list for MERGE)."""
        raise NotImplementedError

    def terms(self) -> Iterator[str]:
        """Terms that should contribute to the score (everything not under NOT)."""
        raise NotImplementedError


@dataclass(frozen=True)
class Term(Node):
    word: str

    def evaluate[C](self, index: Index, engine: Engine[C]) -> C:
        return engine.make(index.doc_ids(self.word))

    def terms(self) -> Iterator[str]:
        yield self.word

    def __str__(self) -> str:
        return self.word


@dataclass(frozen=True)
class Phrase(Node):
    words: tuple[str, ...]

    def evaluate[C](self, index: Index, engine: Engine[C]) -> C:
        return engine.make(phrase_doc_ids(index, self.words))

    def terms(self) -> Iterator[str]:
        yield from self.words

    def __str__(self) -> str:
        return '"' + " ".join(self.words) + '"'


@dataclass(frozen=True)
class And(Node):
    left: Node
    right: Node

    def evaluate[C](self, index: Index, engine: Engine[C]) -> C:
        # a AND NOT b == a minus b: no need to build the complement of b.
        if isinstance(self.right, Not):
            return engine.not_(
                self.left.evaluate(index, engine), self.right.child.evaluate(index, engine)
            )
        if isinstance(self.left, Not):
            return engine.not_(
                self.right.evaluate(index, engine), self.left.child.evaluate(index, engine)
            )
        return engine.and_(self.left.evaluate(index, engine), self.right.evaluate(index, engine))

    def terms(self) -> Iterator[str]:
        yield from self.left.terms()
        yield from self.right.terms()

    def __str__(self) -> str:
        return f"({self.left} AND {self.right})"


@dataclass(frozen=True)
class Or(Node):
    left: Node
    right: Node

    def evaluate[C](self, index: Index, engine: Engine[C]) -> C:
        return engine.or_(self.left.evaluate(index, engine), self.right.evaluate(index, engine))

    def terms(self) -> Iterator[str]:
        yield from self.left.terms()
        yield from self.right.terms()

    def __str__(self) -> str:
        return f"({self.left} OR {self.right})"


@dataclass(frozen=True)
class Not(Node):
    child: Node

    def evaluate[C](self, index: Index, engine: Engine[C]) -> C:
        everything = engine.make(index.all_doc_ids())
        return engine.not_(everything, self.child.evaluate(index, engine))

    def terms(self) -> Iterator[str]:
        return iter(())  # excluded terms never add to a score

    def __str__(self) -> str:
        return f"NOT {self.child}"


def match(tree: Node, index: Index, engine: EngineName = "set") -> list[int]:
    """Sorted doc_ids matching the query tree, computed with the named engine."""
    if engine == "merge":
        return MERGE.finish(tree.evaluate(index, MERGE))
    return SET.finish(tree.evaluate(index, SET))


# --- phrases ------------------------------------------------------------------------


def _followed_by(starts: Sequence[int], positions: Sequence[int], offset: int) -> list[int]:
    """Starts s for which s + offset is in `positions` (two-pointer merge, both sorted)."""
    return intersect(starts, [p - offset for p in positions])


def phrase_doc_ids(index: Index, words: Sequence[str]) -> list[int]:
    """Docs where `words` occur consecutively. Needs an index built with positions."""
    if not index.has_positions:
        raise QueryError("phrase search needs an index built with --positions")
    candidates = MERGE.make(index.doc_ids(words[0]))
    for word in words[1:]:
        candidates = intersect(candidates, index.doc_ids(word))
    if not candidates:
        return []
    by_doc = [{p.doc_id: p.positions for p in index.get(word, ())} for word in words]
    hits: list[int] = []
    for doc_id in candidates:
        starts: Sequence[int] = by_doc[0][doc_id]
        for offset, word_positions in enumerate(by_doc[1:], start=1):
            starts = _followed_by(starts, word_positions[doc_id], offset)
            if not starts:
                break
        else:
            hits.append(doc_id)
    return hits


# --- lexer --------------------------------------------------------------------------

_LEX_RE = re.compile(r'\s*(?:(?P<paren>[()])|"(?P<phrase>[^"]*)"|(?P<quote>")|(?P<word>[^\s()"]+))')


def _lex(query: str) -> list[str | Node]:
    """Tokens: "(", ")", "AND"/"OR"/"NOT", or ready Term/Phrase nodes."""
    out: list[str | Node] = []
    pos = 0
    query = query.rstrip()
    while pos < len(query):
        m = _LEX_RE.match(query, pos)
        if m is None:  # pragma: no cover - the pattern matches any non-space char
            raise QueryError(f"cannot read query at {pos}")
        pos = m.end()
        if m["paren"]:
            out.append(m["paren"])
        elif m["quote"]:
            raise QueryError("unterminated quote")
        elif m["word"] in OPERATORS:
            out.append(m["word"])
        else:
            text = m["phrase"] if m["phrase"] is not None else m["word"]
            words = tuple(tokenize(text))
            if len(words) == 1:
                out.append(Term(words[0]))
            elif words:
                out.append(Phrase(words))
            elif m["phrase"] is not None:
                raise QueryError("empty phrase")
            # a bare word with no tokens (pure punctuation) is ignored
    return out


# --- parser -------------------------------------------------------------------------


class _Parser:
    def __init__(self, tokens: list[str | Node]) -> None:
        self.tokens = tokens
        self.i = 0

    def peek(self) -> str | Node | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self) -> str | Node | None:
        tok = self.peek()
        self.i += 1
        return tok

    def or_expr(self) -> Node:
        node = self.and_expr()
        while self.peek() == "OR":
            self.take()
            node = Or(node, self.and_expr())
        return node

    def and_expr(self) -> Node:
        node = self.not_expr()
        while True:
            tok = self.peek()
            if tok == "AND":
                self.take()
            elif tok is None or tok in ("OR", ")"):
                return node
            node = And(node, self.not_expr())  # explicit or implicit AND

    def not_expr(self) -> Node:
        if self.peek() == "NOT":
            self.take()
            return Not(self.not_expr())
        return self.atom()

    def atom(self) -> Node:
        tok = self.take()
        if isinstance(tok, Node):
            return tok
        if tok == "(":
            node = self.or_expr()
            if self.take() != ")":
                raise QueryError("missing ')'")
            return node
        if tok is None:
            raise QueryError("query ends where a term was expected")
        raise QueryError(f"unexpected {tok!r}")


def parse(query: str) -> Node:
    """Parse a query string into a tree of Term/Phrase/And/Or/Not nodes."""
    tokens = _lex(query)
    if not tokens:
        raise QueryError("query has no searchable terms")
    parser = _Parser(tokens)
    tree = parser.or_expr()
    if parser.peek() is not None:
        raise QueryError(f"unexpected {parser.peek()!r}")
    return tree
