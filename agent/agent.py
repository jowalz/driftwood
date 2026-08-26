"""ADK-Agent-Definition und Tools. Wird als Cloud Run Job ausgeführt."""

import json
import os

from google.adk.agents import Agent

from analysis import extract_changed_symbols, find_related_doc_sections
from actions import open_doc_update_pr, open_escalation_issue
from state import already_processed, fingerprint, mark_processed

MODEL = os.environ.get("AGENT_MODEL", "gemini-2.0-flash")

agent = Agent(
    name="driftwood_agent",
    model=MODEL,
    instruction=(
        "Du analysierst Code-Diffs, findest betroffene Dokumentation und hältst sie "
        "synchron. Erstelle PRs für einfache Updates, eskaliere unklare Fälle als Issue."
    ),
    tools=[extract_changed_symbols, find_related_doc_sections, open_doc_update_pr, open_escalation_issue],
)


def handle_event(message: dict) -> None:
    """Einstiegspunkt: nimmt eine Pub/Sub-Nachricht entgegen und stößt den Agent-Lauf an."""
    fp = fingerprint(message.get("event", ""), json.dumps(message.get("payload", {}), sort_keys=True))
    if already_processed(fp):
        return

    agent.run(message)
    mark_processed(fp, {"event": message.get("event")})


def main() -> None:
    raw = os.environ["PUBSUB_MESSAGE"]
    handle_event(json.loads(raw))


if __name__ == "__main__":
    main()
