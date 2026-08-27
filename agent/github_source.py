"""Fetches diff text and doc-tree content from GitHub for a push event.

Bridges the webhook payload (which carries neither) to analysis.py's
inputs: a unified-diff string and a local directory of Markdown files.
"""

import os
from pathlib import Path

from github import Github


def _repo(full_name: str):
    gh = Github(os.environ["GITHUB_TOKEN"])
    return gh.get_repo(full_name)


def fetch_diff(full_name: str, before: str, after: str) -> str:
    """Reconstructs a unified diff between before and after via the Compare API.

    The Compare API returns each file's hunk body in `.patch` but not the
    `diff --git` / `---` / `+++` headers analysis.py's diff walk expects,
    so those are rebuilt here.
    """
    repo = _repo(full_name)
    comparison = repo.compare(before, after)

    parts = []
    for f in comparison.files:
        if not f.patch:
            continue
        parts.append(f"diff --git a/{f.filename} b/{f.filename}")
        parts.append(f"--- a/{f.filename}")
        parts.append(f"+++ b/{f.filename}")
        parts.append(f.patch)

    return "\n".join(parts)


def checkout_docs(full_name: str, ref: str, dest_dir: str) -> None:
    """Writes every Markdown file at ref into dest_dir, mirroring its repo-relative path."""
    repo = _repo(full_name)
    tree = repo.get_git_tree(ref, recursive=True)

    for entry in tree.tree:
        if entry.type != "blob" or not entry.path.endswith(".md"):
            continue
        content_file = repo.get_contents(entry.path, ref=ref)
        target = Path(dest_dir) / entry.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content_file.decoded_content)
