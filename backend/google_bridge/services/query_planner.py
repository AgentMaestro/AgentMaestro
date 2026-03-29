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
    operator: str | None = None

    def to_dict(self) -> dict[str, object]:
        data = {
            "field_name": self.field_name,
            "value": self.value,
            "negated": self.negated,
        }
        if self.operator:
            data["operator"] = self.operator
        return data


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
        query_string = _render_clause(literals, resource_kind=capabilities.resource_kind)
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
    operator: str | None = None,
) -> tuple[tuple[QueryLiteral, ...], ...]:
    if isinstance(node, QueryEmpty):
        return (tuple(),)
    if isinstance(node, QueryTerm):
        return ((QueryLiteral(field_name, node.value, negated, operator),),)
    if isinstance(node, QueryField):
        normalized_name = _canonicalize_query_field_name(resource_kind, str(node.name or "").strip().lower())
        if normalized_name not in supported_fields:
            raise QueryPlannerError(
                f"Unsupported query field '{normalized_name}' for {resource_kind}."
            )
        normalized_operator = str(node.operator or "").strip().lower() or None
        if normalized_operator:
            if resource_kind != "drive" or not _is_supported_drive_operator(normalized_name, normalized_operator):
                raise QueryPlannerError(
                    f"Unsupported query operator '{normalized_operator}' for {resource_kind} field '{normalized_name}'."
                )
        if resource_kind == "calendar" and normalized_name == "q":
            return _build_clauses(
                node.value,
                resource_kind=resource_kind,
                supported_fields=supported_fields,
                supported_operators=supported_operators,
                field_name=None,
                negated=negated,
                operator=operator,
            )
        return _build_clauses(
            node.value,
            resource_kind=resource_kind,
            supported_fields=supported_fields,
            supported_operators=supported_operators,
            field_name=normalized_name,
            negated=negated,
            operator=normalized_operator or operator,
        )
    if isinstance(node, QueryNot):
        if "NOT" not in supported_operators:
            raise QueryPlannerError(f"NOT is not supported for {resource_kind} queries.")
        return _build_clauses(node.item, resource_kind=resource_kind, supported_fields=supported_fields, supported_operators=supported_operators, field_name=field_name, negated=not negated, operator=operator)
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
                        operator=operator,
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
                operator=operator,
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
                    operator=operator,
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
                    operator=operator,
                )
            )
        return tuple(_dedupe_clauses(clauses))
    raise QueryPlannerError(f"Unsupported query node '{type(node).__name__}'.")


def _canonicalize_query_field_name(resource_kind: str, field_name: str) -> str:
    normalized_field_name = str(field_name or "").strip().lower()
    if resource_kind == "drive" and normalized_field_name == "mimetype":
        return "mime_type"
    if resource_kind == "drive" and normalized_field_name in {"modifiedtime", "modified_time"}:
        return "modified_time"
    if resource_kind == "drive" and normalized_field_name in {"createdtime", "created_time"}:
        return "created_time"
    if resource_kind == "drive" and normalized_field_name == "trashed":
        return "trashed"
    return normalized_field_name


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


def _render_clause(literals: tuple[QueryLiteral, ...], *, resource_kind: str | None = None) -> str:
    if not literals:
        return ""
    if resource_kind == "drive":
        return " and ".join(_render_literal(literal, resource_kind=resource_kind) for literal in literals)
    return " ".join(_render_literal(literal, resource_kind=resource_kind) for literal in literals)


def _render_literal(literal: QueryLiteral, *, resource_kind: str | None = None) -> str:
    if resource_kind == "drive":
        prefix = "not " if literal.negated else ""
        field_name = _render_drive_field_name(str(literal.field_name or ""))
        operator = str(literal.operator or "").strip().lower()
        if operator == "contains":
            value = _quote_drive_contains_value(str(literal.value or ""))
            return f"{prefix}{field_name} contains {value}"
        if operator in {"<", "<=", "=", "!=", ">", ">="}:
            value = _quote_drive_value(field_name, operator, str(literal.value or ""))
            return f"{prefix}{field_name} {operator} {value}"
        value = _quote_drive_value(field_name, "=", str(literal.value or ""))
        if field_name:
            return f"{prefix}{field_name} = {value}"
        return f"{prefix}{value}"
    prefix = "not " if literal.negated and literal.operator == "contains" else ("-" if literal.negated else "")
    if literal.field_name:
        if literal.operator == "contains":
            value = _quote_drive_contains_value(str(literal.value or ""))
            return f"{prefix}{literal.field_name} contains {value}"
        value = _quote_query_value(str(literal.value or ""))
        return f"{prefix}{literal.field_name}:{value}"
    value = _quote_query_value(str(literal.value or ""))
    return f"{prefix}{value}"


def _quote_query_value(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return '""'
    if _needs_quotes(text):
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return text


def _quote_drive_contains_value(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "''"
    escaped = text.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def _quote_drive_value(field_name: str, operator: str, value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "''"
    if str(field_name or "").strip().lower() == "trashed" and text.lower() in {"true", "false"}:
        return text.lower()
    if text.lower() in {"true", "false"} and operator in {"=", "!="}:
        return text.lower()
    return _quote_drive_contains_value(text)


def _render_drive_field_name(field_name: str) -> str:
    normalized = str(field_name or "").strip().lower()
    if normalized in {"mime_type", "mimetype"}:
        return "mimeType"
    if normalized in {"modified_time", "modifiedtime"}:
        return "modifiedTime"
    if normalized in {"created_time", "createdtime"}:
        return "createdTime"
    return normalized


def _is_supported_drive_operator(field_name: str, operator: str) -> bool:
    normalized_field = str(field_name or "").strip().lower()
    normalized_operator = str(operator or "").strip().lower()
    if normalized_field == "name":
        return normalized_operator in {"contains", "=", "!="}
    if normalized_field == "mime_type":
        return normalized_operator in {"=", "!="}
    if normalized_field in {"modified_time", "created_time"}:
        return normalized_operator in {"<", "<=", "=", "!=", ">", ">="}
    if normalized_field == "trashed":
        return normalized_operator in {"=", "!="}
    if normalized_field == "q":
        return normalized_operator == "contains"
    return False


def _needs_quotes(text: str) -> bool:
    if re.search(r"\s", text):
        return True
    return any(ch in text for ch in '()":')
