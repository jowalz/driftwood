"""Calibration check for the classifier in agent.py.

Six hand-built cases, two per route, run live against Gemini 3.5 Flash
via Vertex AI (no Cloud Run deployment needed, but local gcloud
credentials + GOOGLE_CLOUD_PROJECT/GOOGLE_CLOUD_LOCATION from .env are
required). Cases 1/3 and 2/4 are deliberately structurally similar, to
probe exactly the FIX/ASK boundary where the model tends to
overcorrect per the task description.

Run: python -m agent.routing_examples
"""

import sys
from dataclasses import dataclass

from agent.agent import DriftAssessment, assess_drift
from agent.analysis import DocSection, Symbol


@dataclass(frozen=True)
class Example:
    name: str
    expected_route: str
    symbol: Symbol
    doc_section: DocSection
    diff_hunk: str


EXAMPLES = [
    Example(
        name="FIX-1: code_length default 6 -> 8, docs state the number literally",
        expected_route="FIX",
        symbol=Symbol(name="code_length", kind="parameter", file_path="shorturl.py", line=14),
        doc_section=DocSection(
            doc_path="README.md",
            heading="shorten_url(long_url: str) -> str",
            content="Generates a 6-character short code for `long_url`. Links expire after 1 hour.",
            symbol=Symbol(name="code_length", kind="parameter", file_path="shorturl.py", line=14),
            line=9,
        ),
        diff_hunk=(
            "-def shorten_url(long_url: str, code_length: int = 6) -> str:\n"
            "+def shorten_url(long_url: str, code_length: int = 8) -> str:"
        ),
    ),
    Example(
        name="FIX-2: CLI flag --verbose -> --debug, 1:1 rename",
        expected_route="FIX",
        symbol=Symbol(name="--debug", kind="cli_flag", file_path="cli.py", line=22),
        doc_section=DocSection(
            doc_path="README.md",
            heading="Options",
            content="Use `--verbose` to enable verbose logging.",
            symbol=Symbol(name="--debug", kind="cli_flag", file_path="cli.py", line=22),
            line=30,
        ),
        diff_hunk=(
            '-    parser.add_argument("--verbose", help="enables verbose logging")\n'
            '+    parser.add_argument("--debug", help="enables verbose logging")'
        ),
    ),
    Example(
        name="ASK-1: timeout split into connect_timeout/read_timeout",
        expected_route="ASK",
        symbol=Symbol(name="connect_timeout", kind="parameter", file_path="client.py", line=5),
        doc_section=DocSection(
            doc_path="README.md",
            heading="fetch(url: str, timeout: int) -> Response",
            content="The `timeout` parameter (seconds) limits how long to wait for a response.",
            symbol=Symbol(name="connect_timeout", kind="parameter", file_path="client.py", line=5),
            line=14,
        ),
        diff_hunk=(
            "-def fetch(url: str, timeout: int = 30):\n"
            "+def fetch(url: str, connect_timeout: int = 5, read_timeout: int = 30):"
        ),
    ),
    Example(
        name="ASK-2: limit-based search becomes cursor pagination",
        expected_route="ASK",
        symbol=Symbol(name="cursor", kind="parameter", file_path="search.py", line=8),
        doc_section=DocSection(
            doc_path="README.md",
            heading="search(query: str, limit: int) -> list[Result]",
            content="Returns the first `limit` results, sorted by relevance.",
            symbol=Symbol(name="cursor", kind="parameter", file_path="search.py", line=8),
            line=40,
        ),
        diff_hunk=(
            "-def search(query: str, limit: int = 20):\n"
            "+def search(query: str, cursor: str | None = None):"
        ),
    ),
    Example(
        name="ESCALATE-1: entire RateLimiter class removed",
        expected_route="ESCALATE",
        symbol=Symbol(name="RateLimiter", kind="function", file_path="middleware.py", line=1),
        doc_section=DocSection(
            doc_path="README.md",
            heading="Rate Limiting",
            content="All endpoints are limited to 100 requests per minute. "
            "Exceeding this returns `429 Too Many Requests`.",
            symbol=Symbol(name="RateLimiter", kind="function", file_path="middleware.py", line=1),
            line=55,
        ),
        diff_hunk=(
            "-class RateLimiter:\n"
            "-    def __init__(self, max_per_minute: int = 100):\n"
            "-        ...\n"
            "-    def enforce(self, request):\n"
            "-        ..."
        ),
    ),
    Example(
        name="ESCALATE-2: entire API-key middleware removed",
        expected_route="ESCALATE",
        symbol=Symbol(name="require_api_key", kind="function", file_path="middleware.py", line=40),
        doc_section=DocSection(
            doc_path="README.md",
            heading="Authentication",
            content="All requests require a valid API key in the `X-API-Key` header.",
            symbol=Symbol(name="require_api_key", kind="function", file_path="middleware.py", line=40),
            line=70,
        ),
        diff_hunk=(
            "-def require_api_key(handler):\n"
            "-    def wrapper(request):\n"
            "-        if request.headers.get('X-API-Key') != API_KEY:\n"
            "-            raise Unauthorized()\n"
            "-        return handler(request)\n"
            "-    return wrapper"
        ),
    ),
]


def _run(example: Example) -> DriftAssessment:
    return assess_drift(example.symbol, example.doc_section, example.diff_hunk)


def main() -> int:
    failures = 0
    for example in EXAMPLES:
        result = _run(example)
        ok = result.route == example.expected_route
        failures += 0 if ok else 1
        mark = "OK  " if ok else "FAIL"
        print(f"[{mark}] {example.name}")
        print(f"       expected={example.expected_route}  actual={result.route}")
        print(f"       reasoning: {result.reasoning}")
        if result.proposed_change:
            print(f"       proposed_change: {result.proposed_change}")
        print()

    total = len(EXAMPLES)
    print(f"{total - failures}/{total} cases routed as expected.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
