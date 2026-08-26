"""GitHub-Aktionen: PR erstellen, Issue anlegen, Eskalation."""

import os

from github import Github

REPO_NAME = os.environ.get("GITHUB_REPO")


def _repo():
    gh = Github(os.environ["GITHUB_TOKEN"])
    return gh.get_repo(REPO_NAME)


def open_doc_update_pr(branch: str, base: str, title: str, body: str, files: dict[str, str]) -> str:
    """Erstellt einen Branch mit Doku-Updates und öffnet einen PR. Gibt die PR-URL zurück."""
    repo = _repo()
    base_ref = repo.get_git_ref(f"heads/{base}")
    repo.create_git_ref(ref=f"refs/heads/{branch}", sha=base_ref.object.sha)

    for path, content in files.items():
        try:
            existing = repo.get_contents(path, ref=branch)
            repo.update_file(path, f"docs: update {path}", content, existing.sha, branch=branch)
        except Exception:
            repo.create_file(path, f"docs: add {path}", content, branch=branch)

    pr = repo.create_pull(title=title, body=body, head=branch, base=base)
    return pr.html_url


def open_escalation_issue(title: str, body: str, labels: list[str] | None = None) -> str:
    """Legt ein Issue an, wenn eine Änderung nicht automatisch behoben werden kann."""
    repo = _repo()
    issue = repo.create_issue(title=title, body=body, labels=labels or ["driftwood"])
    return issue.html_url
