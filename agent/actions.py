"""GitHub-Aktionen fuer FIX/ASK und Slack-Benachrichtigung fuer ESCALATE.

Jede Funktion ist eine Route aus dem Konzept (docs/CONCEPT.md). Die
Idempotenz-Pruefung sitzt hier, nicht beim Agenten: der Agent liefert nur
Symbol + Doku-Stelle, die Fingerprint-Logik und das "aktualisieren statt
duplizieren" laufen intern ueber state.py.
"""

import os

import requests
from github import Github

import state

REPO_NAME = os.environ.get("GITHUB_REPO")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")


def _repo():
    gh = Github(os.environ["GITHUB_TOKEN"])
    return gh.get_repo(REPO_NAME)


def open_fix_pr(
    symbol: str,
    doc_path: str,
    doc_section: str,
    branch: str,
    base: str,
    title: str,
    body: str,
    files: dict[str, str],
) -> str:
    """FIX-Route: korrigierte Doku direkt per PR. Bestehender PR fuer denselben
    Drift wird aktualisiert statt dupliziert."""
    fp = state.finding_fingerprint(symbol, doc_path, doc_section)
    existing = state.get_open_reference(fp)
    repo = _repo()

    if existing and existing.get("route") == "FIX":
        branch = existing["branch"]
        url = existing["url"]
    else:
        base_ref = repo.get_git_ref(f"heads/{base}")
        repo.create_git_ref(ref=f"refs/heads/{branch}", sha=base_ref.object.sha)
        url = None

    for path, content in files.items():
        try:
            current = repo.get_contents(path, ref=branch)
            repo.update_file(path, f"docs: update {path}", content, current.sha, branch=branch)
        except Exception:
            repo.create_file(path, f"docs: add {path}", content, branch=branch)

    if url is None:
        pr = repo.create_pull(title=title, body=body, head=branch, base=base)
        url = pr.html_url

    state.record_reference(fp, "FIX", url, branch=branch)
    return url


def open_ask_issue(symbol: str, doc_path: str, doc_section: str, title: str, question: str) -> str:
    """ASK-Route: eine konkrete Rueckfrage als Issue. Ein bestehendes Issue fuer
    denselben Drift bekommt einen Kommentar statt eines Duplikats."""
    fp = state.finding_fingerprint(symbol, doc_path, doc_section)
    existing = state.get_open_reference(fp)
    repo = _repo()

    if existing and existing.get("route") == "ASK":
        issue = repo.get_issue(existing["issue_number"])
        issue.create_comment(question)
        url, issue_number = existing["url"], existing["issue_number"]
    else:
        issue = repo.create_issue(title=title, body=question, labels=["driftwood", "question"])
        url, issue_number = issue.html_url, issue.number

    state.record_reference(fp, "ASK", url, issue_number=issue_number)
    return url


def escalate_to_slack(symbol: str, doc_path: str, doc_section: str, reasoning: str) -> str:
    """ESCALATE-Route: informiert einen Menschen per Slack, aendert nichts am
    Repo. Fuer denselben Drift wird nur einmal benachrichtigt."""
    fp = state.finding_fingerprint(symbol, doc_path, doc_section)
    existing = state.get_open_reference(fp)
    reference = f"{doc_path}#{doc_section}"

    if existing and existing.get("route") == "ESCALATE":
        return existing["url"]

    if SLACK_WEBHOOK_URL:
        requests.post(
            SLACK_WEBHOOK_URL,
            json={
                "text": (
                    f":warning: *Driftwood ESCALATE*\n"
                    f"Symbol: `{symbol}`\n"
                    f"Doku: `{reference}`\n"
                    f"{reasoning}"
                )
            },
            timeout=10,
        )

    state.record_reference(fp, "ESCALATE", reference)
    return reference
