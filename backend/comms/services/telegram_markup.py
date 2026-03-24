"""Telegram HTML rendering helpers for mirrored chat text.

The mirror path uses Telegram HTML because it is more predictable than
MarkdownV2 for generated text. The current mapping is intentionally small:
- short headings become bold
- lists are flattened into plain bullet lists
- task lists become simple completed/pending bullet lines
- tables are flattened into readable key/value lines
- blockquotes become distinct quoted blocks
- fenced code blocks become isolated <pre><code> blocks
- simple inline emphasis and links are preserved when safe
- images are converted into readable plain text notes
"""

from __future__ import annotations

import html
import re

_HEADING_RE = re.compile(r"^\s{0,3}(#{1,3})\s+(.*)$")
_BULLET_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+\.\s+)(.*)$")
_TASK_RE = re.compile(r"^\s*-\s+\[(x|X| )\]\s+(.*)$")
_TABLE_ROW_RE = re.compile(r"^\s*\|?.*\|.*\|?\s*$")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_BLOCKQUOTE_RE = re.compile(r"^\s*>\s?(.*)$")
_CODE_FENCE_RE = re.compile(r"^\s*```(?:[a-zA-Z0-9_+-]+)?\s*$")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)")
_UNDERLINE_RE = re.compile(r"__(.+?)__")
_STRIKE_RE = re.compile(r"~(.+?)~")
_SPOILER_RE = re.compile(r"\|\|(.+?)\|\|")


def render_mirror_telegram_html(*, author_label: str, body: str) -> str:
    author = html.escape(str(author_label or "").strip())
    rendered_body = _render_markdownish_body(str(body or ""))
    if not rendered_body:
        return author
    if author:
        return f"<b>{author}</b>\n{rendered_body}"
    return rendered_body


def _render_markdownish_body(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    rendered_lines: list[str] = []
    in_code_block = False
    code_lines: list[str] = []
    in_quote_block = False
    quote_lines: list[str] = []

    i = 0
    while i < len(lines):
        line = lines[i]

        if _CODE_FENCE_RE.match(line):
            if in_quote_block:
                rendered_lines.append(_render_blockquote_block(quote_lines))
                quote_lines = []
                in_quote_block = False
            if in_code_block:
                rendered_lines.append(_render_code_block(code_lines))
                code_lines = []
                in_code_block = False
            else:
                in_code_block = True
            i += 1
            continue

        if in_code_block:
            code_lines.append(line)
            i += 1
            continue

        if _looks_like_table_block(lines, i):
            if in_quote_block:
                rendered_lines.append(_render_blockquote_block(quote_lines))
                quote_lines = []
                in_quote_block = False
            table_lines, next_index = _consume_table_block(lines, i)
            rendered_lines.extend(table_lines)
            i = next_index
            continue

        quote_match = _BLOCKQUOTE_RE.match(line)
        if quote_match:
            if not in_quote_block:
                in_quote_block = True
            quote_lines.append(quote_match.group(1))
            i += 1
            continue

        if in_quote_block:
            rendered_lines.append(_render_blockquote_block(quote_lines))
            quote_lines = []
            in_quote_block = False

        rendered_lines.append(_render_text_line(line))
        i += 1

    if code_lines:
        rendered_lines.append(_render_code_block(code_lines))
    if quote_lines:
        rendered_lines.append(_render_blockquote_block(quote_lines))

    return "\n".join(rendered_lines).strip()


def _render_code_block(lines: list[str]) -> str:
    code = "\n".join(lines).rstrip("\n")
    return f"<pre><code>{html.escape(code)}</code></pre>"


def _render_text_line(line: str) -> str:
    raw = line.rstrip()
    if not raw.strip():
        return ""

    if raw.strip() == "---":
        return ""

    heading_match = _HEADING_RE.match(raw)
    if heading_match:
        return f"<b>{_render_inline_markup(heading_match.group(2).strip())}</b>"

    task_match = _TASK_RE.match(raw)
    if task_match:
        checked = task_match.group(1).strip().lower() == "x"
        label = _render_inline_markup(task_match.group(2).strip())
        return f"&#8226; {'Completed' if checked else 'Pending'} task: {label}"

    bullet_match = _BULLET_RE.match(raw)
    if bullet_match:
        return f"&#8226; {_render_inline_markup(bullet_match.group(1).strip())}"

    image_match = _IMAGE_RE.search(raw)
    if image_match:
        alt_text = image_match.group(1).strip() or "attachment"
        url = image_match.group(2).strip()
        return _render_inline_markup(_IMAGE_RE.sub(f"Image: {alt_text} ({url})", raw))

    return _render_inline_markup(raw)


def _looks_like_table_block(lines: list[str], start_index: int) -> bool:
    if start_index + 1 >= len(lines):
        return False
    header_line = lines[start_index]
    separator_line = lines[start_index + 1]
    if "|" not in header_line or "|" not in separator_line:
        return False
    return bool(_TABLE_ROW_RE.match(header_line) and _TABLE_SEPARATOR_RE.match(separator_line))


def _consume_table_block(lines: list[str], start_index: int) -> tuple[list[str], int]:
    header_line = lines[start_index]
    separator_index = start_index + 1
    end_index = separator_index + 1
    while end_index < len(lines):
        candidate = lines[end_index]
        if not candidate.strip() or not _TABLE_ROW_RE.match(candidate):
            break
        end_index += 1

    header_cells = _split_table_cells(header_line)
    rendered_lines = [f"<b>{html.escape(' / '.join(header_cells))}</b>"]
    for row_line in lines[separator_index + 1 : end_index]:
        row_cells = _split_table_cells(row_line)
        row_parts: list[str] = []
        for idx, cell in enumerate(row_cells):
            header = html.escape(header_cells[idx] if idx < len(header_cells) else f"Column {idx + 1}")
            value = _render_inline_markup(cell)
            row_parts.append(f"<b>{header}</b>: {value}")
        rendered_lines.append(" | ".join(row_parts))
    return rendered_lines, end_index


def _split_table_cells(line: str) -> list[str]:
    stripped = line.strip().strip("|")
    cells = [cell.strip() for cell in stripped.split("|")]
    return [cell for cell in cells if cell]


def _render_blockquote_block(lines: list[str]) -> str:
    rendered_lines = [_render_inline_markup(line.strip()) for line in lines if line.strip()]
    if not rendered_lines:
        return ""
    return f"<blockquote>{'<br>'.join(rendered_lines)}</blockquote>"


def _render_inline_markup(text: str) -> str:
    placeholders: list[tuple[str, str]] = []

    def stash(fragment: str) -> str:
        token = f"@@TG{len(placeholders)}@@"
        placeholders.append((token, fragment))
        return token

    working = _IMAGE_RE.sub(
        lambda match: stash(
            f"Image: {html.escape(match.group(1).strip() or 'attachment')} ({html.escape(match.group(2), quote=True)})"
        ),
        text,
    )
    working = _LINK_RE.sub(
        lambda match: stash(
            f'<a href="{html.escape(match.group(2), quote=True)}">{html.escape(match.group(1))}</a>'
        ),
        working,
    )
    working = _INLINE_CODE_RE.sub(
        lambda match: stash(f"<code>{html.escape(match.group(1))}</code>"),
        working,
    )
    working = html.escape(working)
    working = _BOLD_RE.sub(lambda match: f"<b>{match.group(1)}</b>", working)
    working = _UNDERLINE_RE.sub(lambda match: f"<u>{match.group(1)}</u>", working)
    working = _STRIKE_RE.sub(lambda match: f"<s>{match.group(1)}</s>", working)
    working = _SPOILER_RE.sub(lambda match: f"<tg-spoiler>{match.group(1)}</tg-spoiler>", working)
    working = _ITALIC_RE.sub(lambda match: f"<i>{match.group(1)}</i>", working)
    for token, fragment in placeholders:
        working = working.replace(token, fragment)
    return working
