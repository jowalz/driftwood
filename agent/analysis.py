"""Diff → Symbole → relevante Doku-Abschnitte."""

from dataclasses import dataclass


@dataclass
class ChangedSymbol:
    name: str
    kind: str  # "function" | "class" | "module" | ...
    file_path: str


@dataclass
class DocMatch:
    doc_path: str
    section: str
    symbol: ChangedSymbol


def extract_changed_symbols(diff: str) -> list[ChangedSymbol]:
    """Parst einen Unified-Diff und extrahiert geänderte Funktionen/Klassen."""
    raise NotImplementedError


def find_related_doc_sections(symbols: list[ChangedSymbol]) -> list[DocMatch]:
    """Sucht Doku-Abschnitte, die die geänderten Symbole referenzieren."""
    raise NotImplementedError
