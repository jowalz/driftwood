# An agent that fixes your docs — or asks, when fixing would be a guess

I renamed a config parameter. Tests passed, CI was green, the pull request got approved in four minutes. Three weeks later someone new joined, followed the README exactly, and spent an afternoon debugging a setup that could not possibly work.

Nothing was broken. The documentation had simply stopped being true, and there is no test for that.

Linters check code against itself. CI checks behaviour against expectations. Nothing checks prose against reality — because the answer is almost never a clean yes or no. Sometimes the fix is obvious. Sometimes you genuinely need to ask the person who wrote it. That ambiguity is why this has stayed a human chore, and why it turned out to be a good problem for an agent that is allowed to be uncertain.

So I built one. It's called Driftwood.

## What it does

It watches a repository. When a commit lands, it reads the diff, finds the documentation that talks about the symbols that changed, and decides how confident it is:

| Route | When | What it does |
| --- | --- | --- |
| **FIX** | The docs are demonstrably wrong and the correction follows directly from the code — a renamed parameter, a changed default, a removed flag. | Opens a pull request with the corrected text. |
| **ASK** | Something no longer matches, but several corrections are plausible and the agent would have to guess intent. | Opens an issue asking the maintainer one specific question. |
| **ESCALATE** | The docs describe a feature that no longer exists. Deleting it is a product decision, not a text fix. | Notifies a human. Changes nothing. |

The routing is the whole point. An agent that always opens a pull request is a nuisance within a week, because the false positives train you to ignore it. An agent that knows the difference between "I can fix this" and "I should ask" is something you actually leave switched on.

## Does it actually distinguish?

That was the question I cared about. Here are two commits against the same repository. Both change a single numeric default in the same function signature — structurally identical from the outside.

```
[FIX] shorten_url @ README.md#shorten_url(long_url: str) -> str
      The default max_length parameter in shorten_url was changed from 10
      to 12, making the documentation's claim of 'between 6 and 10
      characters' incorrect. The number 10 should be mechanically updated.

[ASK] shorten_url @ README.md#Example
      The documentation is outdated regarding both the code length and the
      expiration time. A simple mechanical fix is not sufficient as we need
      to clarify if both values should be corrected.
```

One became a pull request, merged without edits. The other became a question — because the paragraph it touched *also* claimed a wrong expiry time, and correcting one number while leaving the other wrong would have been worse than not touching it.

That is context sensitivity rather than pattern matching, and it is the behaviour the entire design is aimed at.

## How it's built

```
GitHub webhook
  → Cloud Run service (receiver): verify HMAC, publish, return 200
  → Pub/Sub
  → Eventarc → Workflow
  → Cloud Run job (agent): Google ADK + Gemini 3.5 Flash via Vertex AI
  → pull request / issue / escalation
```

Firestore holds per-repository state: a fingerprint of every drift already reported and the pull request or issue it produced.

The receiver does almost nothing on purpose. GitHub expects an answer in seconds and the analysis takes closer to a minute, so the two are decoupled from the start. In the deployed system that round trip is **59 milliseconds**.

## Three things I only found by deploying

**Eventarc cannot invoke a Cloud Run job.** There is no `--destination-run-job`. Only *services* expose an HTTP endpoint Eventarc can push to; jobs are started through the Admin API, which Eventarc will not call for you. The fix is a Workflow sitting between the two, invoking `jobs.run`. It's a small piece of YAML and it is load-bearing — without it the pipeline doesn't run at all.

**The agent triggered itself.** Every FIX opens a branch and a commit on the target repository — which fires two more push webhooks at the very agent that caused them. The branch-creation event arrives with `before: 0000000000000000000000000000000000000000`, which `repo.compare()` can't resolve, so the job crashed. Cloud Run retried three times by default, so one successful fix produced eight crashed containers.

My first instinct was to catch the 404. Wrong layer: that stops the crash but still runs the whole pipeline twice per FIX for nothing. The real fix is four lines at the top of the handler — skip any push to `refs/heads/driftwood/*` before the diff is ever fetched.

An agent that acts on the world generates its own inputs. Nothing warns you about that, and it doesn't show up in the architecture diagram.

**The model wanted to be helpful.** Asked plainly to classify, Gemini returned "confidently fixable" for nearly everything, because that's the answer that sounds most useful. Honest uncertainty only became reliable when I inverted the burden of proof in the prompt: ASK is the baseline, and FIX has to be justified against the code.

The asymmetry is deliberate. A wrong ASK costs a maintainer thirty seconds. A wrong FIX costs trust in the whole tool.

## What it costs

Symbol extraction and documentation search run locally, in plain Python. Gemini is only called once a changed symbol actually appears somewhere in the docs — so the cost scales with *findings*, not with commits. The overwhelming majority of pushes touch nothing that is documented and cost exactly nothing.

When there is something to judge:

| | Tokens | Cost |
| --- | ---: | ---: |
| Input | 2,614 | $0.004 |
| Output, of which the verdicts are 479 | 2,583 | $0.023 |
| **One push, three classifications** | **5,197** | **$0.027** |

Under a cent per finding, against the afternoon it exists to prevent. One prevented incident pays for roughly 33,000 findings.

But the cheap part isn't the interesting part. The expensive failure mode was never the bill — it's an agent that opens enough wrong pull requests that people stop reading them. That failure costs nothing on the invoice and everything in practice: a muted agent has a hit rate of zero no matter how accurate it was.

Which is exactly where the money goes. Of the tokens billed as output, only 479 are the actual verdicts. The remaining ~2,100 are thinking tokens — four fifths of the bill is the model working out whether it's sure.

**The cent buys the hesitation, and the hesitation is the product.**

## What I'd take from this

The interesting part of an agent is not what it does. It's what it declines to do. The version that opened a pull request for everything was easier to build and would have been switched off in a week. Most of the work went into teaching it to stop, and that turned out to be the whole thing.

The confidence routing generalises, too. Anywhere an agent has to admit it isn't sure, the same shape applies: a confident path, an asking path, and a path where the right move is to change nothing and tell a human.

---

**Code:** [github.com/jowalz/driftwood](https://github.com/jowalz/driftwood)

**The testbed**, with the pull requests and issues the agent opened itself: [github.com/jowalz/driftwood-testbed](https://github.com/jowalz/driftwood-testbed)

*I created this piece of content for the purposes of entering the [All Things Agentic Hackathon](https://allthingsagentichackathon.devpost.com/).*
