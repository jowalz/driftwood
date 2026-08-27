"""ADK classifier: sorts a drift finding into FIX/ASK/ESCALATE, and the
deterministic dispatch from a classification to a GitHub/Slack action.

Classification (LlmAgent, output_schema) has no tools and no side
effects -- output_schema disables tool calls on most models anyway.
Which actions.py call follows a route is separate, ordinary Python: the
classification decides, dispatch() just executes that decision.
"""

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel, Field

from actions import escalate_to_slack, open_ask_issue, open_fix_pr
from analysis import DocSection, Symbol, extract_changed_symbols, find_referencing_docs
from github_source import checkout_docs, fetch_diff
from state import already_delivered, content_hash, finding_fingerprint, mark_delivered

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


def _log_token_usage(symbol_name: str, usage, doc_sections_in_context: int) -> None:
    """A single JSON line on stdout -- Cloud Logging parses one-line JSON on
    stdout into structured jsonPayload automatically, no text parsing needed
    on the reading end. `severity`/`message` are the reserved keys it looks
    for; everything else becomes a queryable field."""
    print(
        json.dumps(
            {
                "severity": "INFO",
                "message": "token_usage",
                "symbol": symbol_name,
                "prompt_token_count": getattr(usage, "prompt_token_count", None),
                "candidates_token_count": getattr(usage, "candidates_token_count", None),
                "total_token_count": getattr(usage, "total_token_count", None),
                "doc_sections_in_context": doc_sections_in_context,
            }
        )
    )


def assess_drift(symbol: Symbol, doc_section: DocSection, diff_hunk: str) -> DriftAssessment:
    """Classifies exactly one drift finding. No tools, no side effects."""
    session_id = content_hash(symbol.name, doc_section.doc_path, doc_section.heading)
    asyncio.run(_session_service.create_session(app_name=_APP_NAME, user_id=_USER_ID, session_id=session_id))

    message = types.Content(role="user", parts=[types.Part(text=_prompt_for(symbol, doc_section, diff_hunk))])

    raw = None
    usage = None
    for event in _runner.run(user_id=_USER_ID, session_id=session_id, new_message=message):
        if event.is_final_response() and event.content:
            raw = event.content.parts[0].text
            usage = event.usage_metadata

    # Always 1 for now: each call carries exactly one doc_section (see
    # _prompt_for). Logged as a real field, not hardcoded in the message,
    # so it stays correct if a call ever batches more than one section.
    _log_token_usage(symbol.name, usage, doc_sections_in_context=1)

    if raw is None:
        raise RuntimeError(f"classifier returned no response for {symbol.name}")
    return DriftAssessment.model_validate_json(raw)


@dataclass(frozen=True)
class Finding:
    symbol: Symbol
    doc_section: DocSection
    assessment: DriftAssessment


def assess_findings(diff: str, repo_path: str) -> list[Finding]:
    """Connects analysis.py to the classifier: one Finding per doc section
    found. The full diff serves as context, since analysis.py currently
    doesn't expose per-symbol hunk boundaries -- good enough for the
    context reduction from CONCEPT.md at this step; a more precise hunk
    mapping is a possible follow-up."""
    symbols = extract_changed_symbols(diff)
    sections = find_referencing_docs(symbols, repo_path)
    return [
        Finding(symbol=section.symbol, doc_section=section, assessment=assess_drift(section.symbol, section, diff))
        for section in sections
    ]


def _splice_correction(repo_docs_dir: str, doc_section: DocSection, proposed_change: str) -> str:
    """Replaces doc_section's line range within its file with proposed_change
    and returns the full corrected file text -- open_fix_pr writes whole
    files, so the surrounding document must stay intact."""
    path = Path(repo_docs_dir) / doc_section.doc_path
    lines = path.read_text(encoding="utf-8").splitlines()
    start = doc_section.line - 1
    length = len(doc_section.content.splitlines())
    new_lines = lines[:start] + proposed_change.splitlines() + lines[start + length :]
    return "\n".join(new_lines) + "\n"


def dispatch(finding: Finding, repo_docs_dir: str, base_branch: str) -> str:
    """Route -> action. The classification already decided; this just carries
    it out. No further judgment happens here."""
    route = finding.assessment.route
    symbol, doc = finding.symbol, finding.doc_section

    if route == "FIX":
        corrected = _splice_correction(repo_docs_dir, doc, finding.assessment.proposed_change)
        fp = finding_fingerprint(symbol.name, doc.doc_path, doc.heading)
        branch = f"driftwood/fix-{fp[:12]}"
        return open_fix_pr(
            symbol.name,
            doc.doc_path,
            doc.heading,
            branch,
            base_branch,
            title=f"docs: fix {symbol.name}",
            body=finding.assessment.reasoning,
            files={doc.doc_path: corrected},
        )

    if route == "ASK":
        return open_ask_issue(
            symbol.name,
            doc.doc_path,
            doc.heading,
            title=f"Docs question: {symbol.name}",
            question=finding.assessment.proposed_change,
        )

    return escalate_to_slack(symbol.name, doc.doc_path, doc.heading, finding.assessment.reasoning)


def handle_event(message: dict) -> None:
    """Entry point for the Cloud Run job: delivery dedup, then fetch the real
    diff and doc tree from GitHub, classify each finding, log the route and
    reasoning, and dispatch the resulting action."""
    delivery_id = message.get("delivery_id") or content_hash(
        message.get("event", ""), json.dumps(message.get("payload", {}), sort_keys=True)
    )
    if already_delivered(delivery_id):
        return

    payload = message.get("payload", {})
    before = payload.get("before")
    after = payload.get("after")
    repo_full_name = payload.get("repository", {}).get("full_name")
    ref = payload.get("ref", "")

    # Not a diffable push (e.g. a ping event, or a branch deletion where
    # `after` is all zeros) -- the receiver deliberately doesn't filter by
    # event type, so this has to.
    if not (before and after and repo_full_name and ref) or set(after) == {"0"}:
        mark_delivered(delivery_id)
        return

    base_branch = ref.removeprefix("refs/heads/")
    diff = fetch_diff(repo_full_name, before, after)

    with tempfile.TemporaryDirectory() as tmp:
        checkout_docs(repo_full_name, after, tmp)
        for finding in assess_findings(diff, tmp):
            print(
                f"[{finding.assessment.route}] {finding.symbol.name} @ "
                f"{finding.doc_section.doc_path}#{finding.doc_section.heading}: "
                f"{finding.assessment.reasoning}"
            )
            dispatch(finding, tmp, base_branch)

    mark_delivered(delivery_id)


def main() -> None:
    raw = os.environ["PUBSUB_MESSAGE"]
    handle_event(json.loads(raw))


if __name__ == "__main__":
    main()
