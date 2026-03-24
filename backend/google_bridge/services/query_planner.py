from __future__ import annotations

from dataclasses import dataclass
import re

from google_bridge.services.query_capabilities import get_google_query_capabilities, get_query_clause_limit
from google_bridge.services.query_language import (
    QueryAnd,
    QueryEmpty,
    QueryField,
    QueryLanguageError,
    QueryNode,
    QueryNot,
    QueryOr,
    QueryTerm,
    parse_query,
)


class QueryPlannerError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class QueryLiteral:
    field_name: str | None
    value: str
    negated: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "field_name": self.field_name,
            "value": self.value,
            "negated": self.negated,
        }


@dataclass(frozen=True, slots=True)
class QueryClause:
    index: int
    query: str
    literals: tuple[QueryLiteral, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "query": self.query,
            "literals": [literal.to_dict() for literal in self.literals],
        }


@dataclass(frozen=True, slots=True)
class QueryPlan:
    resource_kind: str
    action_kind: str
    operation: str
    original_query: str
    query_ast: dict[str, object]
    capabilities: dict[str, object]
    execution_mode: str
    normalized_query: str
    clauses: tuple[QueryClause, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "resource_kind": self.resource_kind,
            "action_kind": self.action_kind,
            "operation": self.operation,
            "original_query": self.original_query,
            "query_ast": self.query_ast,
            "capabilities": self.capabilities,
            "execution_mode": self.execution_mode,
            "normalized_query": self.normalized_query,
            "call_count": len(self.clauses),
            "calls": [clause.to_dict() for clause in self.clauses],
        }

    @property
    def query_strings(self) -> tuple[str, ...]:
        return tuple(clause.query for clause in self.clauses)


def plan_google_query(
    query: str | None,
    *,
    resource_kind: str,
    action_kind: str,
    operation: str,
) -> QueryPlan:
    text = str(query or "").strip()
    capabilities = get_google_query_capabilities(
        resource_kind=resource_kind,
        action_kind=action_kind,
        operation=operation,
    )
    if text and not capabilities.query_enabled:
        raise QueryPlannerError(
            f"Google query planning is not enabled for {capabilities.resource_kind} {action_kind} {operation}."
        )

    if not text:
        empty_clause = QueryClause(index=1, query="", literals=tuple())
        return QueryPlan(
            resource_kind=capabilities.resource_kind,
            action_kind=str(action_kind or "").strip().lower(),
            operation=str(operation or "").strip().lower(),
            original_query="",
            query_ast=QueryEmpty().to_dict(),
            capabilities=capabilities.to_dict(),
            execution_mode="single",
            normalized_query="",
            clauses=(empty_clause,),
        )

    parsed = parse_query(text)
    clause_limit = get_query_clause_limit()
    clause_literals = _build_clauses(
        parsed,
        resource_kind=capabilities.resource_kind,
        supported_fields=capabilities.supported_fields,
        supported_operators=capabilities.supported_operators,
    )
    if len(clause_literals) > clause_limit:
        raise QueryPlannerError(
            f"Google query planning expanded to {len(clause_literals)} clauses, which exceeds the limit of {clause_limit}."
        )

    rendered_clauses: list[QueryClause] = []
    seen_clause_strings: set[str] = set()
    for literals in clause_literals:
        query_string = _render_clause(literals)
        if query_string in seen_clause_strings:
            continue
        seen_clause_strings.add(query_string)
        rendered_clauses.append(
            QueryClause(
                index=len(rendered_clauses) + 1,
                query=query_string,
                literals=literals,
            )
        )

    normalized_query = " OR ".join(clause.query for clause in rendered_clauses if clause.query)
    if not normalized_query and rendered_clauses:
        normalized_query = ""

    return QueryPlan(
        resource_kind=capabilities.resource_kind,
        action_kind=str(action_kind or "").strip().lower(),
        operation=str(operation or "").strip().lower(),
        original_query=text,
        query_ast=parsed.to_dict(),
        capabilities=capabilities.to_dict(),
        execution_mode="single" if len(rendered_clauses) == 1 else "fanout",
        normalized_query=normalized_query,
        clauses=tuple(rendered_clauses or (QueryClause(index=1, query="", literals=tuple()),)),
    )


def _build_clauses(
    node: QueryNode,
    *,
    resource_kind: str,
    supported_fields: frozenset[str],
    supported_operators: frozenset[str],
    field_name: str | None = None,
    negated: bool = False,
) -> tuple[tuple[QueryLiteral, ...], ...]:
    if isinstance(node, QueryEmpty):
        return (tuple(),)
    if isinstance(node, QueryTerm):
        return ((QueryLiteral(field_name, node.value, negated),),)
    if isinstance(node, QueryField):
        normalized_name = str(node.name or "").strip().lower()
        if normalized_name not in supported_fields:
            raise QueryPlannerError(
                f"Unsupported query field '{normalized_name}' for {resource_kind}."
            )
        if resource_kind == "calendar" and normalized_name == "q":
            return _build_clauses(
                node.value,
                resource_kind=resource_kind,
                supported_fields=supported_fields,
                supported_operators=supported_operators,
                field_name=None,
                negated=negated,
            )
        return _build_clauses(
            node.value,
            resource_kind=resource_kind,
            supported_fields=supported_fields,
            supported_operators=supported_operators,
            field_name=normalized_name,
            negated=negated,
        )
    if isinstance(node, QueryNot):
        if "NOT" not in supported_operators:
            raise QueryPlannerError(f"NOT is not supported for {resource_kind} queries.")
        return _build_clauses(node.item, field_name=field_name, negated=not negated)
    if isinstance(node, QueryAnd):
        if "AND" not in supported_operators:
            raise QueryPlannerError(f"AND is not supported for {resource_kind} queries.")
        if negated:
            clauses: list[tuple[QueryLiteral, ...]] = []
            for item in node.items:
                clauses.extend(
                    _build_clauses(
                        item,
                        resource_kind=resource_kind,
                        supported_fields=supported_fields,
                        supported_operators=supported_operators,
                        field_name=field_name,
                        negated=True,
                    )
                )
            return tuple(_dedupe_clauses(clauses))
        clauses = [tuple()]
        for item in node.items:
            item_clauses = _build_clauses(
                item,
                resource_kind=resource_kind,
                supported_fields=supported_fields,
                supported_operators=supported_operators,
                field_name=field_name,
                negated=False,
            )
            clauses = _combine_and(clauses, item_clauses)
        return tuple(_dedupe_clauses(clauses))
    if isinstance(node, QueryOr):
        if "OR" not in supported_operators:
            raise QueryPlannerError(f"OR is not supported for {resource_kind} queries.")
        if negated:
            clauses = [tuple()]
            for item in node.items:
                item_clauses = _build_clauses(
                    item,
                    resource_kind=resource_kind,
                    supported_fields=supported_fields,
                    supported_operators=supported_operators,
                    field_name=field_name,
                    negated=True,
                )
                clauses = _combine_and(clauses, item_clauses)
            return tuple(_dedupe_clauses(clauses))
        clauses = []
        for item in node.items:
            clauses.extend(
                _build_clauses(
                    item,
                    resource_kind=resource_kind,
                    supported_fields=supported_fields,
                    supported_operators=supported_operators,
                    field_name=field_name,
                    negated=False,
                )
            )
        return tuple(_dedupe_clauses(clauses))
    raise QueryPlannerError(f"Unsupported query node '{type(node).__name__}'.")


def _combine_and(
    left: list[tuple[QueryLiteral, ...]],
    right: tuple[tuple[QueryLiteral, ...], ...],
) -> list[tuple[QueryLiteral, ...]]:
    if not left:
        return list(right)
    if not right:
        return list(left)
    combined: list[tuple[QueryLiteral, ...]] = []
    for left_clause in left:
        for right_clause in right:
            combined.append(left_clause + right_clause)
    return combined


def _dedupe_clauses(clauses: list[tuple[QueryLiteral, ...]]) -> list[tuple[QueryLiteral, ...]]:
    seen: set[tuple[QueryLiteral, ...]] = set()
    unique: list[tuple[QueryLiteral, ...]] = []
    for clause in clauses:
        if clause in seen:
            continue
        seen.add(clause)
        unique.append(clause)
    return unique


def _render_clause(literals: tuple[QueryLiteral, ...]) -> str:
    if not literals:
        return ""
    return " ".join(_render_literal(literal) for literal in literals)


def _render_literal(literal: QueryLiteral) -> str:
    prefix = "-" if literal.negated else ""
    value = _quote_query_value(str(literal.value or ""))
    if literal.field_name:
        return f"{prefix}{literal.field_name}:{value}"
    return f"{prefix}{value}"


def _quote_query_value(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return '""'
    if _needs_quotes(text):
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return text


def _needs_quotes(text: str) -> bool:
    if re.search(r"\s", text):
        return True
    return any(ch in text for ch in '()":')
