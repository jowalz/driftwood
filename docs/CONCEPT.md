# Driftwood — concept and architecture

Context document for anyone (human or agent) working on this repo.
Read this before writing code.

Submission for the All Things Agentic Hackathon, track **The Taskmaster**.

---

## The problem

Documentation stops being true silently. A parameter gets renamed, a
default changes, a flag is removed — tests still pass, CI is green, and
the README goes on describing a system that no longer exists.

Linters check code against itself. CI checks behaviour against
expectations. Nothing checks prose against reality, because the answer
is rarely a clean yes or no. Sometimes the correction is obvious.
Sometimes you genuinely have to ask the person who wrote it.

That ambiguity is the reason this has stayed a manual chore, and it is
the reason it suits an agent that is allowed to be uncertain.

## What Driftwood does

Driftwood watches a repository. When a commit lands, it reads the diff,
finds the documentation that references the symbols that changed, and
classifies its own confidence. The classification determines the action.

**This routing is the product.** An agent that always opens a pull
request becomes noise within a week — the false positives train the
maintainer to ignore it. An agent that knows the difference between "I
can fix this" and "I should ask" is one you leave switched on.

### The three routes

| Route | Condition | Action |
|---|---|---|
| `FIX` | Docs are demonstrably wrong and the correction follows directly from the code. Renamed parameter, changed default, removed flag. | Open a pull request with the corrected text. |
| `ASK` | Something no longer matches, but several corrections are plausible. The agent would have to guess intent. | Open an issue asking the maintainer one specific question. |
| `ESCALATE` | Docs describe a feature that no longer exists. Deleting it is a product decision, not a text fix. | Notify a human (Slack). Change nothing. |

Design rule: **when in doubt, route downward.** A wrong `ASK` costs a
maintainer thirty seconds. A wrong `FIX` costs trust in the whole tool.

---

## Architecture

```
GitHub push event
        │
        ▼
Cloud Run service — receiver
  verify signature, publish to Pub/Sub, return 200
        │
        ▼
      Pub/Sub
        │
        ▼
Cloud Run job — ADK agent (Gemini 3.5 Flash)  ────►  Firestore
  extract changed symbols                            per-repo state
  retrieve referencing doc sections                  drift fingerprints
  classify confidence                                open PR / issue refs
  execute the selected route
        │
        ├──► Pull request      (FIX)
        ├──► GitHub issue      (ASK)
        └──► Slack alert       (ESCALATE)
```

### Why the receiver does almost nothing

GitHub expects a webhook response within seconds. The analysis takes
longer than that. Splitting the receiver from the worker is not
decoration — it is what keeps the webhook honest and lets the analysis
take the time it needs. Pub/Sub is the seam.

The receiver verifies the HMAC signature, publishes the raw event, and
returns `200`. Nothing else. If it grows past ~50 lines, something has
leaked into the wrong component.

### Idempotency is a hard requirement

Five commits in five minutes must not produce five identical pull
requests.

The difficulty: "have I already reported this?" is not a string
comparison. The same underlying drift looks different after every
intervening commit. The fingerprint therefore keys on the *subject* of
the drift — the symbol and the documentation location — not on the
generated text.

Before acting, the agent checks Firestore. If a fingerprint has an open
PR or issue, it updates rather than duplicates.

### Context selection decides the cost

Do not send whole repositories to the model.

1. Take the commit diff.
2. Extract the symbols that changed (function names, parameters,
   config keys, CLI flags).
3. Search the docs for sections referencing those symbols.
4. Send only those sections plus the relevant diff hunks.

Documentation rarely sits next to the code it describes, so step 3 has
to actually search — the diff alone misses most drift.

---

## Stack

Fixed by the hackathon requirements plus our choices:

- **Model:** Gemini 3.5 Flash via Vertex AI
- **Agent framework:** Google ADK (Agent Development Kit)
- **Compute:** Cloud Run — one service (receiver), one job (agent)
- **Messaging:** Pub/Sub
- **State:** Firestore
- **Secrets:** Secret Manager (GitHub token, webhook secret)
- **External:** GitHub REST API, Slack webhook

## Repo layout

```
driftwood/
├── README.md              setup + reproducible testing instructions
├── requirements.txt
├── .env.example
├── receiver/              Cloud Run service
│   ├── main.py            webhook in, Pub/Sub out
│   └── Dockerfile
├── agent/                 Cloud Run job
│   ├── agent.py           ADK agent definition and tools
│   ├── analysis.py        diff → symbols → referencing doc sections
│   ├── state.py           Firestore fingerprints and idempotency
│   ├── actions.py         GitHub PR / issue, Slack escalation
│   └── Dockerfile
├── deploy/
│   └── deploy.sh          gcloud commands, reproducible
└── docs/
    ├── CONCEPT.md          this file
    └── architecture.png
```

## Non-goals

Deliberately out of scope for the hackathon build:

- Rewriting documentation for style, tone, or clarity. Driftwood only
  addresses statements that are *false*.
- Generating documentation that does not exist yet.
- Any action outside the three routes above. If the agent wants to do
  something else, that is a bug in the prompt, not a feature.

## Definition of done

The demo must show, on a repository the agent has not been prepared for:

1. A push triggering the agent, visible in Cloud Run logs.
2. Two different commits producing two *different* routes, with the
   agent's reasoning visible.
3. The resulting pull request or issue live on GitHub.

The contrast between the two runs is the demo. Not that it works — that
it **distinguishes**.
