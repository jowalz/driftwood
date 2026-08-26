"""ADK-Klassifikator: stuft einen Drift-Fund in FIX/ASK/ESCALATE ein.

Reine Klassifikation, keine Tools, keine Seiteneffekte -- output_schema
schaltet Tool-Aufrufe bei den meisten Modellen ohnehin ab. Welche Aktion
(actions.py) auf eine Route folgt, ist ein separater, deterministischer
Schritt und nicht Teil dieses Moduls (siehe Plan: "Nicht Teil dieses
Schritts").
"""

import json
import os
from typing import Literal

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel, Field

from analysis import DocSection, Symbol, extract_changed_symbols, find_referencing_docs
from state import already_delivered, content_hash, mark_delivered

MODEL = os.environ.get("AGENT_MODEL", "gemini-3.5-flash")

_APP_NAME = "driftwood"
_USER_ID = "driftwood"


class DriftAssessment(BaseModel):
    route: Literal["FIX", "ASK", "ESCALATE"] = Field(description="Genau eine der drei Routen.")
    reasoning: str = Field(description="Kurze Begruendung, ein bis drei Saetze.")
    symbol: str = Field(description="Name des betroffenen Symbols.")
    doc_location: str = Field(description="Datei und Abschnitt der betroffenen Doku-Stelle.")
    proposed_change: str = Field(
        default="",
        description="FIX: der vollstaendig korrigierte Text. ASK: eine einzige konkrete Frage. ESCALATE: leer.",
    )


INSTRUCTION = """\
Du klassifizierst GENAU EINEN Drift-Fund zwischen Code und Dokumentation.
Du bekommst: das geaenderte Symbol, den Diff-Ausschnitt, die Doku-Stelle
und ihren aktuellen Inhalt. Du gibst eine einzige strukturierte
Einschaetzung zurueck. Keine Aktion, keine Tool-Aufrufe, keine Prosa
ausserhalb der Felder.

Deine Standardannahme ist ASK. Nicht FIX.

Ein FIX ist die Ausnahme, nicht der Normalfall. Waehle FIX NUR, wenn ALLE
drei Bedingungen erfuellt sind:
1. Die Doku ist nachweislich falsch -- nicht nur unvollstaendig oder
   ungenau formuliert.
2. Es gibt GENAU EINE Korrektur, die direkt aus dem Diff folgt. Keine
   zweite plausible Formulierung ist denkbar.
3. Die Korrektur ist eine mechanische Textaenderung (umbenannter Name,
   geaenderter Zahlenwert, entfernte Option) -- keine Neuformulierung,
   keine Interpretation deinerseits.

Ist auch nur EINE dieser Bedingungen unsicher, ist es kein FIX. Sind zwei
Formulierungen plausibel, oder ist unklar, ob die Aenderung absichtlich
war? Dann ASK, gefolgt von einer einzigen konkreten Frage.

ESCALATE nur, wenn die Doku ein Feature beschreibt, das im Diff komplett
entfernt wurde -- du kannst dann nicht einfach Text korrigieren, sondern
jemand muss entscheiden, ob die Doku-Stelle geloescht, ersetzt oder als
veraltet markiert wird.

Vor jeder Entscheidung fuer FIX: ueberlege dir aktiv eine zweite, ebenfalls
plausible Korrektur. Faellt dir eine ein -- auch nur ansatzweise -- route
stattdessen nach ASK. Eine falsche ASK kostet den Maintainer dreissig
Sekunden. Eine falsche FIX kostet Vertrauen in das ganze Tool. Route im
Zweifel immer nach unten: ESCALATE vor ASK vor FIX.
"""

_classifier = LlmAgent(
    name="driftwood_classifier",
    model=MODEL,
    instruction=INSTRUCTION,
    output_schema=DriftAssessment,
)

_session_service = InMemorySessionService()
_runner = Runner(agent=_classifier, app_name=_APP_NAME, session_service=_session_service)


def _prompt_for(symbol: Symbol, doc_section: DocSection, diff_hunk: str) -> str:
    location = f'{doc_section.doc_path} -- "{doc_section.heading}"' if doc_section.heading else doc_section.doc_path
    return (
        f"Symbol: {symbol.name} ({symbol.kind}), Fundstelle {symbol.file_path}:{symbol.line}\n\n"
        f"Diff:\n{diff_hunk}\n\n"
        f"Doku-Stelle: {location}\n"
        f"Aktueller Doku-Inhalt:\n{doc_section.content}\n"
    )


def assess_drift(symbol: Symbol, doc_section: DocSection, diff_hunk: str) -> DriftAssessment:
    """Klassifiziert genau einen Drift-Fund. Keine Tools, keine Seiteneffekte."""
    session_id = content_hash(symbol.name, doc_section.doc_path, doc_section.heading)
    _session_service.create_session(app_name=_APP_NAME, user_id=_USER_ID, session_id=session_id)

    message = types.Content(role="user", parts=[types.Part(text=_prompt_for(symbol, doc_section, diff_hunk))])

    raw = None
    for event in _runner.run(user_id=_USER_ID, session_id=session_id, new_message=message):
        if event.is_final_response() and event.content:
            raw = event.content.parts[0].text

    if raw is None:
        raise RuntimeError(f"Klassifikator lieferte keine Antwort fuer {symbol.name}")
    return DriftAssessment.model_validate_json(raw)


def assess_findings(diff: str, repo_path: str) -> list[DriftAssessment]:
    """Verbindet analysis.py mit dem Klassifikator: eine Einschaetzung pro
    gefundenem Doku-Abschnitt. Der komplette Diff dient als Kontext, da
    analysis.py aktuell keine Hunk-Grenzen pro Symbol nach aussen gibt --
    fuer die Kontext-Reduktion aus CONCEPT.md reicht das fuer diesen
    Schritt, eine praezisere Hunk-Zuordnung ist ein moeglicher Folgeschritt."""
    symbols = extract_changed_symbols(diff)
    sections = find_referencing_docs(symbols, repo_path)
    return [assess_drift(section.symbol, section, diff) for section in sections]


def handle_event(message: dict) -> None:
    """Einstiegspunkt fuer den Cloud-Run-Job: Zustellungs-Dedup vor allem
    anderen. Die Verbindung von Webhook-Payload zu echtem Diff-Text (per
    GitHub-API) und die Aktion pro Route (actions.py) folgen in einem
    spaeteren Schritt."""
    delivery_id = message.get("delivery_id") or content_hash(
        message.get("event", ""), json.dumps(message.get("payload", {}), sort_keys=True)
    )
    if already_delivered(delivery_id):
        return

    mark_delivered(delivery_id)


def main() -> None:
    raw = os.environ["PUBSUB_MESSAGE"]
    handle_event(json.loads(raw))


if __name__ == "__main__":
    main()
