"""ADK classifier: sorts a drift finding into FIX/ASK/ESCALATE.

Pure classification, no tools, no side effects -- output_schema disables
tool calls on most models anyway. Which action (actions.py) follows a
route is a separate, deterministic step and not part of this module (see
the plan: "Not part of this step").
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
    route: Literal["FIX", "ASK", "ESCALATE"] = Field(description="Exactly one of the three routes.")
    reasoning: str = Field(description="Short justification, one to three sentences.")
    symbol: str = Field(description="Name of the affected symbol.")
    doc_location: str = Field(description="File and section of the affected doc location.")
    proposed_change: str = Field(
        default="",
        description="FIX: the fully corrected text. ASK: a single concrete question. ESCALATE: empty.",
    )


INSTRUCTION = """\
You classify EXACTLY ONE drift finding between code and documentation.
You get: the changed symbol, the diff excerpt, the doc location, and its
current content. You return a single structured assessment. No action,
no tool calls, no prose outside the fields.

Your default assumption is ASK. Not FIX.

A FIX is the exception, not the norm. Choose FIX ONLY if ALL three
conditions hold:
1. The docs are demonstrably wrong -- not just incomplete or imprecisely
   worded.
2. There is EXACTLY ONE correction that follows directly from the diff.
   No second plausible wording is conceivable.
3. The correction is a mechanical text change (renamed identifier,
   changed number, removed option) -- not a rewording, not an
   interpretation on your part.

If even ONE of these conditions is uncertain, it is not a FIX. Are two
wordings plausible, or is it unclear whether the change was intentional?
Then ASK, followed by a single concrete question.

ESCALATE only if the docs describe a feature that was completely removed
in the diff -- you can't just correct text then, someone has to decide
whether the doc location gets deleted, replaced, or marked deprecated.

Before every FIX decision: actively think of a second, equally plausible
correction. If one comes to mind -- even tentatively -- route to ASK
instead. A wrong ASK costs the maintainer thirty seconds. A wrong FIX
costs trust in the whole tool. When in doubt, always route downward:
ESCALATE before ASK before FIX.
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
        f"Symbol: {symbol.name} ({symbol.kind}), location {symbol.file_path}:{symbol.line}\n\n"
        f"Diff:\n{diff_hunk}\n\n"
        f"Doc location: {location}\n"
        f"Current doc content:\n{doc_section.content}\n"
    )


def assess_drift(symbol: Symbol, doc_section: DocSection, diff_hunk: str) -> DriftAssessment:
    """Classifies exactly one drift finding. No tools, no side effects."""
    session_id = content_hash(symbol.name, doc_section.doc_path, doc_section.heading)
    _session_service.create_session(app_name=_APP_NAME, user_id=_USER_ID, session_id=session_id)

    message = types.Content(role="user", parts=[types.Part(text=_prompt_for(symbol, doc_section, diff_hunk))])

    raw = None
    for event in _runner.run(user_id=_USER_ID, session_id=session_id, new_message=message):
        if event.is_final_response() and event.content:
            raw = event.content.parts[0].text

    if raw is None:
        raise RuntimeError(f"classifier returned no response for {symbol.name}")
    return DriftAssessment.model_validate_json(raw)


def assess_findings(diff: str, repo_path: str) -> list[DriftAssessment]:
    """Connects analysis.py to the classifier: one assessment per doc section
    found. The full diff serves as context, since analysis.py currently
    doesn't expose per-symbol hunk boundaries -- good enough for the
    context reduction from CONCEPT.md at this step; a more precise hunk
    mapping is a possible follow-up."""
    symbols = extract_changed_symbols(diff)
    sections = find_referencing_docs(symbols, repo_path)
    return [assess_drift(section.symbol, section, diff) for section in sections]


def handle_event(message: dict) -> None:
    """Entry point for the Cloud Run job: delivery dedup before anything
    else. Connecting the webhook payload to actual diff text (via the
    GitHub API) and the action per route (actions.py) follow in a later
    step."""
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
