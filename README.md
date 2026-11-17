# seen-before

**Is this the right way to do it?**

At an AI convention, trained-model and embedding pipelines were being pitched for one job: an AI that answers
support questions from a company's past tickets. You train a model on the tickets, use it to find the ones
closest to the new question, and let the AI answer from those. We wanted to know whether that is the right way
to do it. So we built it, built the alternatives, and compared them on real GitHub issues.

**[Watch the comparison](https://mrrolie.github.io/seen-before/showcase/)** (or open `showcase/index.html`): an
animation over the real archive. Every dot is a past issue,
and every number comes from the run records.

## What we compared

The archive: 66,747 GitHub issues from Microsoft PowerToys, Windows Terminal and WSL, all filed before
2026-07-01. The questions: 124 later issues that the projects' maintainers closed as duplicates of earlier ones,
plus 30 bugs never marked as duplicates. Each approach had to name the earlier issue. All five used the same model
to write the answer (Claude Sonnet 5.5), in clean headless sessions.

| Approach | Right first (of 124) | Time per answer (median) | Cost (154 questions) | To build |
|---|---|---|---|---|
| **A.** Trained model: embeddings fine-tuned on the tickets, top 5, one answer | 59 | 4.2 s | $4.13 | training and indexing, 614 MB, GPU |
| **F.** Trained model + keyword search (BM25), fused, top 5, one answer | 67 | 4.7 s | $4.91 | the same plus a keyword index |
| **B.** Agent with ripgrep: searches the ticket files itself, as often as it likes | 71 | 12.2 s | $9.90 | nothing |
| **C.** Agent with the trained model as its search tool | 74 | 14.8 s | $7.52 | training and indexing, GPU |
| **D.** Agent with both tools | 74 | 15.5 s | $8.47 | training and indexing, GPU |

**What it says**, on these issues, with one model and 124 questions: training a model was not the step that
mattered. Letting the AI search more than once was:
the same trained model, used as an agent's search tool (C), named the right issue first 74 times instead of 59
(A). An agent with plain ripgrep (B) got 71. These 124 questions cannot tell 71 from 74, and B needs nothing
trained, no GPU and no index to keep up to date.

**Where the trained model wins, and the caveats:**
- The one-shot pipelines answer about three times faster and cost about half as much.
- B against A was fixed in advance as the main comparison: +9.7 points, 95% interval +1.6 to +18.5. That is
  narrow.
- On 30 of the questions, an AI triage bot had suggested duplicates before a maintainer linked them. Without
  those 30, the gap is +8.5, with an interval from −1.1 to +18.1: too close to call.
- A ran without a re-ranker. An off-the-shelf one made it worse, and one trained on the tickets did not learn
  in two attempts.

Everything else, including the losses, is in [`experiment/RESULTS.md`](experiment/RESULTS.md). The rules were
written down before the first run in [`experiment/PROTOCOL.md`](experiment/PROTOCOL.md), with every later change
dated.

## The connector

The approach we shipped is B, because it was the simplest one within reach of the best. It lives in
[`connector/`](connector/README.md) and comes in two forms:
- **A GitHub Action.** When an issue is opened, the agent searches the repository's past issues with ripgrep.
  If it finds an earlier report, it comments with links.
- **A Claude Code plugin.** It gives you a `/seen-before` command, an MCP server with `search` and `read`, and a
  background refresh of the issue mirror.

## Repository

| Path | What it holds |
|---|---|
| `corpus/` | Fetches issues and comments from the GitHub API, and builds the archive, the duplicate links and the questions. The issue text is not redistributed; you fetch it with your own token. |
| `experiment/` | The five approaches, the runner, the scorer, the protocol and the results. |
| `experiment/pipeline/` | The trained-model approach: training, indexing and the retrieval service. |
| `runs/` | Every recorded answer: the tickets shown, each tool call with its timing and results, and the model's output, plus the scores computed from them. The prompts are not stored; they are rebuilt exactly from the code and the questions. |
| `connector/` | The shipped connector. |
| `showcase/` | The animation: a static page (`index.html`, `data.js`), and `build_data.py`, which builds its data from the run records. |
| `tests/` | Tests for the link rules, the tools, the clean sessions and the connector's parity with approach B. |

## Reproducing it

You need Python 3.12, `ripgrep`, Claude Code (`claude`), a GitHub token, and a GPU for training (ROCm or CUDA).

    python -m venv .venv && .venv/bin/pip install -r requirements.txt
    export GITHUB_TOKEN=...
    for r in microsoft/PowerToys microsoft/terminal microsoft/WSL; do
      python corpus/fetch.py $r data/raw && python corpus/fetch_dups.py $r data/raw; done
    python corpus/build.py data/raw data/built
    python corpus/edits.py data/built && python corpus/question_edits.py data/built
    .venv/bin/python -m experiment.pipeline.train
    .venv/bin/python -m experiment.pipeline.retrieve index --model trained
    .venv/bin/python -m experiment.keyword
    .venv/bin/python -m experiment.pipeline.serve &
    .venv/bin/python -m experiment.run --arms A,F,B,C,D --split test --out runs/test
    .venv/bin/python -m experiment.score runs/test --split test

Your numbers will differ somewhat:
- Issues are fetched as they are today, not as they were.
- The model's answers vary from run to run.
- Comments are kept by the date they were written, with their text as it is today.

`experiment/leakcheck.py` checks that your own Claude Code setup does not reach the model. It needs a file of
"canary" words that appear only in your own setup.

## Licence

The code is under the MIT licence (`LICENSE`). The issues belong to their authors and are not included. The
showcase data holds the map positions, which are derived, and the titles of the 18 issues it shows. In `runs/`,
GitHub usernames that the model's answers and searches mention are replaced with `[user]`.
