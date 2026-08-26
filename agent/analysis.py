"""Diff -> symbols -> referencing doc sections.

Purely local: no network, no cloud, no model. This is the context
reduction step from docs/CONCEPT.md ("Context selection decides the
cost") -- only the symbols and doc sections found here later go to the
agent, not the whole repo.

Known limit: extract_changed_symbols() only ever sees the diff text, not
the full repo. If a changed line sits far from the function signature --
outside the diff context -- the symbol won't be recognized. That's a
limit of the input, not a regex gap.
"""

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str  # "function" | "parameter" | "config_key" | "cli_flag" | "env_var"
    file_path: str
    line: int


@dataclass(frozen=True)
class DocSection:
    doc_path: str
    heading: str
    content: str
    symbol: Symbol
    line: int


# --- Diff walk -------------------------------------------------------------

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _strip_prefix(path: str) -> str:
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def _iter_changed_lines(diff: str):
    """Yields (file_path, line_no, sign, content) for every +/- line."""
    current_file = None
    old_line = new_line = 0

    for raw in diff.splitlines():
        if raw.startswith("diff --git "):
            current_file = None
        elif raw.startswith("+++ "):
            path = raw[4:].strip()
            current_file = None if path == "/dev/null" else _strip_prefix(path)
        elif raw.startswith("--- "):
            continue
        elif (m := _HUNK_RE.match(raw)) is not None:
            old_line, new_line = int(m.group(1)), int(m.group(2))
        elif current_file is None:
            continue
        elif raw.startswith("+"):
            yield current_file, new_line, "+", raw[1:]
            new_line += 1
        elif raw.startswith("-"):
            yield current_file, old_line, "-", raw[1:]
            old_line += 1
        else:
            old_line += 1
            new_line += 1


# --- Symbol patterns ---------------------------------------------------------

_PY_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+(\w+)\s*\(")
_JS_FUNC_RE = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(\w+)\s*\(")
_GO_FUNC_RE = re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(\w+)\s*\(")

_CLI_FLAG_PATTERNS = [
    re.compile(r"add_argument\(\s*[\"'](--[\w-]+)[\"']"),
    re.compile(r"@click\.option\(\s*[\"'](--[\w-]+)[\"']"),
]

_ENV_VAR_CALL_PATTERNS = [
    re.compile(r"os\.environ\[[\"'](\w+)[\"']\]"),
    re.compile(r"os\.environ\.get\(\s*[\"'](\w+)[\"']"),
    re.compile(r"os\.getenv\(\s*[\"'](\w+)[\"']"),
    re.compile(r"process\.env\.(\w+)"),
]
_ENV_FILE_ASSIGN_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=")

_CONFIG_KEY_RE = re.compile(r"^\s*[\"']?([\w.-]+)[\"']?\s*:\s*\S")
_CONFIG_FILE_EXTS = (".yml", ".yaml", ".json", ".toml", ".ini", ".cfg")


def _is_env_file(file_path: str) -> bool:
    name = file_path.rsplit("/", 1)[-1]
    return name == ".env" or name.startswith(".env.")


def _python_params(def_line: str) -> list[str]:
    """Parses a single, fully visible Python def line via ast, to split
    parameters correctly even with nested type annotations."""
    stripped = def_line.strip()
    if not stripped.endswith(":"):
        stripped += ":"
    try:
        tree = ast.parse(f"{stripped}\n    pass")
    except SyntaxError:
        return []

    func = tree.body[0] if tree.body else None
    if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return []

    args = func.args
    names = [a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
    if args.vararg:
        names.append(args.vararg.arg)
    if args.kwarg:
        names.append(args.kwarg.arg)
    return names


def _paren_span(line: str, open_index: int) -> str | None:
    depth = 0
    for i in range(open_index, len(line)):
        if line[i] == "(":
            depth += 1
        elif line[i] == ")":
            depth -= 1
            if depth == 0:
                return line[open_index + 1 : i]
    return None


def _split_top_level(s: str) -> list[str]:
    parts, depth, current = [], 0, []
    for ch in s:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _naive_param_names(paren_content: str) -> list[str]:
    names = []
    for token in _split_top_level(paren_content):
        m = re.match(r"[\w$]+", token.lstrip("*&"))
        if m:
            names.append(m.group(0))
    return names


def _match_functions(file_path: str, line_no: int, content: str, add) -> None:
    if (m := _PY_DEF_RE.match(content)) is not None:
        add(file_path, "function", m.group(1), line_no)
        for param in _python_params(content):
            add(file_path, "parameter", param, line_no)
        return

    if (m := _JS_FUNC_RE.match(content)) is not None:
        add(file_path, "function", m.group(1), line_no)
        paren = _paren_span(content, m.end() - 1)
        if paren is not None:
            for param in _naive_param_names(paren):
                add(file_path, "parameter", param, line_no)
        return

    if (m := _GO_FUNC_RE.match(content)) is not None:
        add(file_path, "function", m.group(1), line_no)
        open_index = content.index("(", m.end(1))
        paren = _paren_span(content, open_index)
        if paren is not None:
            for param in _naive_param_names(paren):
                add(file_path, "parameter", param, line_no)
        return


def _match_cli_flags(file_path: str, line_no: int, content: str, add) -> None:
    for pattern in _CLI_FLAG_PATTERNS:
        if (m := pattern.search(content)) is not None:
            add(file_path, "cli_flag", m.group(1), line_no)


def _match_env_vars(file_path: str, line_no: int, content: str, add) -> None:
    for pattern in _ENV_VAR_CALL_PATTERNS:
        for m in pattern.finditer(content):
            add(file_path, "env_var", m.group(1), line_no)

    if _is_env_file(file_path):
        if (m := _ENV_FILE_ASSIGN_RE.match(content)) is not None:
            add(file_path, "env_var", m.group(1), line_no)


def _match_config_keys(file_path: str, line_no: int, content: str, add) -> None:
    if not file_path.lower().endswith(_CONFIG_FILE_EXTS):
        return
    if (m := _CONFIG_KEY_RE.match(content)) is not None:
        add(file_path, "config_key", m.group(1), line_no)


def extract_changed_symbols(diff: str) -> list[Symbol]:
    """Pulls changed identifiers out of a unified diff: function names,
    parameters, config keys, CLI flags, env vars."""
    seen: dict[tuple[str, str, str], Symbol] = {}

    def add(file_path: str, kind: str, name: str, line: int) -> None:
        key = (file_path, kind, name)
        if key not in seen:
            seen[key] = Symbol(name=name, kind=kind, file_path=file_path, line=line)

    for file_path, line_no, _sign, content in _iter_changed_lines(diff):
        _match_functions(file_path, line_no, content, add)
        _match_cli_flags(file_path, line_no, content, add)
        _match_env_vars(file_path, line_no, content, add)
        _match_config_keys(file_path, line_no, content, add)

    return list(seen.values())


# --- Doc search --------------------------------------------------------------

_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__"}
_FENCE_RE = re.compile(r"^\s*```")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)")


def _scan_markdown(text: str, doc_path: str, patterns: list[tuple[Symbol, re.Pattern]]) -> list[DocSection]:
    lines = text.splitlines()
    sections: list[DocSection] = []
    heading = ""
    i, n = 0, len(lines)

    while i < n:
        line = lines[i]

        if (m := _HEADING_RE.match(line)) is not None:
            heading = m.group(2).strip()
            i += 1
            continue

        if not line.strip():
            i += 1
            continue

        block_start = i
        block_lines = []
        if _FENCE_RE.match(line):
            block_lines.append(line)
            i += 1
            while i < n and not _FENCE_RE.match(lines[i]):
                block_lines.append(lines[i])
                i += 1
            if i < n:
                block_lines.append(lines[i])
                i += 1
        else:
            while i < n and lines[i].strip() and not _HEADING_RE.match(lines[i]):
                block_lines.append(lines[i])
                i += 1

        content = "\n".join(block_lines).strip()
        haystack = f"{heading}\n{content}"
        for symbol, pattern in patterns:
            if pattern.search(haystack):
                sections.append(
                    DocSection(
                        doc_path=doc_path,
                        heading=heading,
                        content=content,
                        symbol=symbol,
                        line=block_start + 1,
                    )
                )

    return sections


def find_referencing_docs(symbols: list[Symbol], repo_path: str) -> list[DocSection]:
    """Searches all Markdown files in the repo for the symbols and returns,
    per match, the surrounding section (heading + paragraph/code block)."""
    if not symbols:
        return []

    patterns = [(s, re.compile(rf"(?<!\w){re.escape(s.name)}(?!\w)")) for s in symbols]

    root = Path(repo_path)
    sections: list[DocSection] = []
    for md_path in sorted(root.rglob("*.md")):
        if any(part in _SKIP_DIRS for part in md_path.parts):
            continue
        text = md_path.read_text(encoding="utf-8", errors="ignore")
        rel_path = str(md_path.relative_to(root)).replace("\\", "/")
        sections.extend(_scan_markdown(text, rel_path, patterns))

    return sections


# --- CLI -----------------------------------------------------------------


def _format_report(symbols: list[Symbol], sections: list[DocSection]) -> str:
    if not symbols:
        return "No symbols found in the diff.\n"

    by_kind: dict[str, list[Symbol]] = {}
    for s in symbols:
        by_kind.setdefault(s.kind, []).append(s)

    sections_by_symbol: dict[tuple[str, str, str], list[DocSection]] = {}
    for sec in sections:
        key = (sec.symbol.file_path, sec.symbol.kind, sec.symbol.name)
        sections_by_symbol.setdefault(key, []).append(sec)

    lines = [f"{len(symbols)} symbol(s) found:\n"]
    for kind in sorted(by_kind):
        lines.append(f"[{kind}]")
        for s in by_kind[kind]:
            lines.append(f"  {s.name}  ({s.file_path}:{s.line})")
            for sec in sections_by_symbol.get((s.file_path, s.kind, s.name), []):
                heading = sec.heading or "(no heading)"
                lines.append(f'    -> {sec.doc_path}:{sec.line}  "{heading}"')
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Driftwood: diff -> symbols -> referencing doc sections"
    )
    parser.add_argument("--repo", required=True, help="Path to the repository to search for docs")
    parser.add_argument("--diff", required=True, help="Path to a file with unified diff text")
    args = parser.parse_args()

    diff_text = Path(args.diff).read_text(encoding="utf-8", errors="ignore")
    symbols = extract_changed_symbols(diff_text)
    sections = find_referencing_docs(symbols, args.repo)

    sys.stdout.write(_format_report(symbols, sections))


if __name__ == "__main__":
    main()
