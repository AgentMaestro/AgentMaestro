from __future__ import annotations

from dataclasses import dataclass


class QueryLanguageError(ValueError):
    def __init__(self, message: str, position: int | None = None):
        super().__init__(message)
        self.position = position


class QueryNode:
    def to_dict(self) -> dict[str, object]:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class QueryEmpty(QueryNode):
    def to_dict(self) -> dict[str, object]:
        return {"type": "empty"}


@dataclass(frozen=True, slots=True)
class QueryTerm(QueryNode):
    value: str

    def to_dict(self) -> dict[str, object]:
        return {"type": "term", "value": self.value}


@dataclass(frozen=True, slots=True)
class QueryField(QueryNode):
    name: str
    value: QueryNode

    def to_dict(self) -> dict[str, object]:
        return {"type": "field", "name": self.name, "value": self.value.to_dict()}


@dataclass(frozen=True, slots=True)
class QueryNot(QueryNode):
    item: QueryNode

    def to_dict(self) -> dict[str, object]:
        return {"type": "not", "item": self.item.to_dict()}


@dataclass(frozen=True, slots=True)
class QueryAnd(QueryNode):
    items: tuple[QueryNode, ...]

    def to_dict(self) -> dict[str, object]:
        return {"type": "and", "items": [item.to_dict() for item in self.items]}


@dataclass(frozen=True, slots=True)
class QueryOr(QueryNode):
    items: tuple[QueryNode, ...]

    def to_dict(self) -> dict[str, object]:
        return {"type": "or", "items": [item.to_dict() for item in self.items]}


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str
    value: str
    position: int


_BOOLEAN_OPERATORS = {"AND", "OR", "NOT"}


def parse_query(query: str | None) -> QueryNode:
    text = str(query or "").strip()
    if not text:
        return QueryEmpty()
    parser = _QueryParser(_tokenize(text))
    node = parser.parse_expression()
    parser.expect_end()
    return node


def _tokenize(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    index = 0
    while index < len(text):
        ch = text[index]
        if ch.isspace():
            index += 1
            continue
        if ch == "(":
            tokens.append(_Token("LPAREN", ch, index))
            index += 1
            continue
        if ch == ")":
            tokens.append(_Token("RPAREN", ch, index))
            index += 1
            continue
        if ch == ":":
            tokens.append(_Token("COLON", ch, index))
            index += 1
            continue
        if ch == '"':
            start = index
            index += 1
            value_chars: list[str] = []
            escaped = False
            while index < len(text):
                current = text[index]
                if escaped:
                    value_chars.append(current)
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    tokens.append(_Token("STRING", "".join(value_chars), start))
                    index += 1
                    break
                else:
                    value_chars.append(current)
                index += 1
            else:
                raise QueryLanguageError("Malformed query: unmatched quote.", start)
            continue

        start = index
        while index < len(text) and not text[index].isspace() and text[index] not in "():\"":
            index += 1
        value = text[start:index]
        if not value:
            raise QueryLanguageError("Malformed query: unexpected character.", start)
        tokens.append(_Token("WORD", value, start))
    tokens.append(_Token("EOF", "", len(text)))
    return tokens


class _QueryParser:
    def __init__(self, tokens: list[_Token]):
        self._tokens = tokens
        self._index = 0

    def parse_expression(self) -> QueryNode:
        return self._parse_or()

    def expect_end(self) -> None:
        token = self._peek()
        if token.kind != "EOF":
            raise QueryLanguageError(f"Malformed query: unexpected token '{token.value}'.", token.position)

    def _parse_or(self) -> QueryNode:
        items = [self._parse_and()]
        while self._match_operator("OR"):
            items.append(self._parse_and())
        if len(items) == 1:
            return items[0]
        return QueryOr(tuple(items))

    def _parse_and(self) -> QueryNode:
        items = [self._parse_not()]
        while True:
            if self._match_operator("AND"):
                items.append(self._parse_not())
                continue
            if self._starts_primary(self._peek()):
                items.append(self._parse_not())
                continue
            break
        if len(items) == 1:
            return items[0]
        return QueryAnd(tuple(items))

    def _parse_not(self) -> QueryNode:
        if self._match_operator("NOT"):
            return QueryNot(self._parse_not())
        return self._parse_primary()

    def _parse_primary(self) -> QueryNode:
        token = self._peek()
        if token.kind == "LPAREN":
            self._advance()
            node = self._parse_or()
            self._expect("RPAREN")
            return node
        if token.kind == "WORD" and self._peek_next().kind == "COLON":
            field_name = self._advance().value.strip().lower()
            self._advance()
            return QueryField(field_name, self._parse_field_value())
        if token.kind == "STRING":
            self._advance()
            return QueryTerm(token.value)
        if token.kind == "WORD":
            self._advance()
            return QueryTerm(token.value)
        raise QueryLanguageError(f"Malformed query: unexpected token '{token.value}'.", token.position)

    def _parse_field_value(self) -> QueryNode:
        token = self._peek()
        if token.kind == "LPAREN":
            self._advance()
            node = self._parse_or()
            self._expect("RPAREN")
            return node
        if token.kind == "STRING":
            self._advance()
            return QueryTerm(token.value)
        if token.kind == "WORD":
            self._advance()
            return QueryTerm(token.value)
        raise QueryLanguageError("Malformed query: expected a field value.", token.position)

    def _match_operator(self, operator: str) -> bool:
        token = self._peek()
        if token.kind != "WORD":
            return False
        if token.value.upper() != operator:
            return False
        if self._peek_next().kind == "COLON":
            return False
        self._advance()
        return True

    def _starts_primary(self, token: _Token) -> bool:
        if token.kind in {"STRING", "LPAREN"}:
            return True
        if token.kind != "WORD":
            return False
        if self._peek_next().kind == "COLON":
            return True
        return token.value.upper() not in _BOOLEAN_OPERATORS

    def _peek(self) -> _Token:
        return self._tokens[self._index]

    def _peek_next(self) -> _Token:
        next_index = min(self._index + 1, len(self._tokens) - 1)
        return self._tokens[next_index]

    def _advance(self) -> _Token:
        token = self._tokens[self._index]
        if token.kind != "EOF":
            self._index += 1
        return token

    def _expect(self, kind: str) -> _Token:
        token = self._peek()
        if token.kind != kind:
            raise QueryLanguageError(f"Malformed query: expected {kind.lower()}.", token.position)
        return self._advance()
