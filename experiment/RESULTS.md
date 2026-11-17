# seen-before: results of the test run (2026-11-16)

Every number below follows `PROTOCOL.md` (criteria committed before any score; amendments 1 to 6 dated, all
before the first Claude call on a question; 7 and 8 record what was found after the run). Losses are reported with wins. Records: `runs/test/<arm>/*.json`,
scores: `runs/test/score-test.json`, counts: `runs/simplicity.json`, `runs/freshness.json`.

**Set-up.** 124 test questions: GitHub issues from PowerToys, Windows Terminal and WSL filed between 2026-07-01
and 2026-11-15 that maintainers closed as duplicates. They come from 101 different duplicate clusters, with at
most 2 per cluster. 30 more questions are bugs closed as fixed, never marked duplicate. The archive holds the
66,747 issues filed before 2026-07-01, with their comments up to that date. Every arm answers with Claude Sonnet
5.5 in a sterile headless session (leak-checked before the pilot and again before the test: 0 canaries). The arms
were interleaved per question. 770 sessions; no infrastructure retries; 0 citations from memory.

## The question: is training a model the right way to do it?

The approach seen at the convention (A: a model trained on the tickets retrieves the closest ones, and the AI
answers once from them) against an AI agent that searches the tickets itself with ripgrep (B). Three more arms
mix the two (below). Each row is one measure; neither approach wins them all.

| Measure | Which is ahead | Evidence |
|---|---|---|
| **Right ticket named first** (the comparison the protocol fixed in advance) | **B, narrowly** | hit@1, B − A = **+9.7 points**, 95% paired interval [+1.6, +18.5]: B right where A was wrong on 21 questions, A right where B was wrong on 9. The lower end is one or two questions above zero. Without the 6 questions whose answer ticket was edited after the cutoff: +9.3 [+0.8, +17.8]. Without the 30 questions an AI triage bot had suggested duplicates for (below): +8.5 [−1.1, +18.1], a difference not shown. |
| **Things to build and keep** | **B** (fewer on every count) | Own code 66 lines against 218. Dependencies 2 against 6. Model and index files 0 MB against 614 MB. Build steps 0 against 3 (about 23 min on a GPU). No GPU against GPU. |
| **Time per answer** | **A** | Median end to end 4.2 s for A against 12.2 s for B (90th percentile 6.1 s against 18.5 s). B takes 8 turns and 7 tool calls at the median; A takes one turn. Both include the Claude Code CLI's start-up, so the 3x ratio understates the gap between the bare calls. |
| **Retrieval alone** | **A per question; both negligible** | A retrieves once in 0.02 s. B's searches add up to 0.28 s per question (median 0.07 s per ripgrep call over 66,747 files). In both arms nearly all the time is the model's. |
| **Time to build** | **B** | B 0 s: the ticket files are the index (writing them all takes 2.7 s). A: 17 min training plus 6 min embedding on the GPU. |
| **A new ticket becomes findable** | **B** | After 0.02 ms in B (write one file). In A it takes 12 ms with the model loaded (embed and append), and the model itself learns the ticket only at the next 17-minute retrain. |
| **Cost** | **A** | $4.13 against $9.90 for the 124 + 30 questions (Claude Code's reported figure). Median input 5.0k tokens against 41.8k. |

**The short answer.** On these tickets, training a model was not the step that mattered. Letting the AI search more
than once was: the same trained model, given to an agent as its search tool (C), named the right ticket first 74
times against 59 as a one-shot pipeline (A). An agent with plain ripgrep (B) got 71, a difference from C that these
124 questions cannot tell from zero, with nothing to train, no GPU and no index to keep up to date. The one-shot
pipeline is the fastest and cheapest per answer.

## All arms

| Arm | hit@1 (95% CI) | hit@3 | exact-token hit@1 | plain hit@1 | answer was shown | new: said "seen" | median s | retrieval s | cost |
|---|---|---|---|---|---|---|---|---|---|
| A pipeline | 59/124 (0.39–0.56) | 77 | 8/11 | 51/113 | 81 | 7/30 | 4.2 | 0.02 | $4.13 |
| F hybrid pipeline | 67/124 (0.45–0.63) | 80 | 11/11 | 56/113 | 86 | 6/30 | 4.7 | 0.53 | $4.91 |
| **B ripgrep agent** | 71/124 (0.49–0.66) | **89** | 10/11 | 61/113 | **106** | 7/30 | 12.2 | 0.28 | $9.90 |
| C semantic agent | **74/124** (0.51–0.68) | 85 | 11/11 | 63/113 | 101 | 8/30 | 14.8 (≈11.0*) | 3.85 (≈0.05*) | $7.52 |
| D hybrid agent | **74/124** (0.51–0.68) | 86 | 11/11 | 63/113 | 102 | 9/30 | 15.5 (≈11.7*) | 3.95 (≈0.1*) | $8.47 |

\* A defect in my semantic tool, not search cost. The first semantic call of each session took 3.8 s because the
tool loaded PyTorch on import; later calls took 0.014 s. Recorded times are shown, with the corrected estimate in
brackets. Accuracy is unaffected. The import is fixed (`experiment/pipeline/client.py`) for any later run.

"Answer was shown" counts the questions where an answer ticket appeared anywhere in what the arm retrieved or
read. B saw the right ticket in 106 of 124 questions, but cited it first in only 71.

**Secondary comparisons** (hit@1, paired, 95% interval; no verdict words, per amendment 1):
D − B +2.4 [−4.0, +8.9]; D − A +12.1 [+4.8, +20.2]; F − A +6.5 [−0.8, +14.5]; C − A +12.1 [+4.0, +20.2];
C − B +2.4 [−4.0, +8.9]. Also needed by the connector rule: B − F +3.2 [−4.0, +10.5]; C − F +5.6 [−1.6, +12.9].

**What the comparisons say.** The agent loop is what beats the one-shot pipeline. With the same trained
retriever, the loop gains 12 points (C − A). Inside the loop, swapping ripgrep for the trained retriever moves
hit@1 by 2.4 points, which these 124 questions cannot tell from zero (C − B). Giving the agent both tools (D)
adds nothing measurable over either alone. Fusing keyword search into the one-shot pipeline (F) narrows the gap
to the agents without closing it.

**Exact tokens.** On the 11 questions that carry an error code, exception or stack frame, every arm except A
finds nearly all of them (F, C and D 11/11, B 10/11, A 8/11). The stratum is too small to rank the arms (see
amendment 3).

**Ageing.** hit@1 by month of the question (July to November) shows no drift for the trained model against the
others at these sizes (A: 11/26, 20/43, 13/19, 7/19, 8/17).

**New questions.** Every arm claimed a match on 6 to 9 of the 30 bugs labelled new. The labels are noisy (an
unlinked duplicate looks new; the protocol says so in advance), so this number bounds the false-alarm rate from
above rather than measuring it.

**Failures.** B returned no parseable answer twice; both are scored as misses. No arm hit the turn cap.

## Sensitivity of the primary result (`experiment/sensitivity.py`, `runs/test/sensitivity.json`)

The verdict above is the pre-registered one. These lines sit beside it. One crosses zero (the bot line, the last
one).
- **Questions changed by someone other than their author** (retitled 4, body edited 3, 6 questions, 4 of them
  seen): the questions were fetched as they are now, not as first filed. Without them, B − A = +10.8
  [+2.5, +19.2].
- **Resampling by cluster** instead of by question (101 clusters): [+1.6, +18.3].
- **Narrower answer set** (the question's linked original and that original's direct duplicates, instead of the
  whole cluster; the largest cluster has 1,241 tickets): B − A = +9.7 [+1.6, +18.5]; arm counts unchanged
  except F 66 instead of 67.
- **Citations the parser could have dropped** (ids not in the strict `Repo#N` form): none in any arm. B's two
  parse failures are answers in prose with no JSON, after 11 and 15 turns: model failures, scored as misses.

- **Questions an AI bot had suggested duplicates for** (found by the fresh recount, amendment 8). PowerToys runs
  an AI triage bot that posts "Possible duplicates" (from 2026-08-15). On 30 of the 124 questions it named an
  answer ticket before the maintainer linked the question; in 20 it named the very ticket the maintainer chose.
  The maintainer still made every link, but the answer key may lean towards what a similarity bot finds. Without
  those 30: B − A = +8.5 [−1.1, +18.1] (94 questions, B alone right on 15, A alone on 7), **not shown**. On the 30
  dropped questions B was right on 17 and A on 13, so the lean, if any, did not favour B; the interval widens
  mostly because the set is smaller. Arm counts without them: A 46, F 52, B 54, C 60, D 59 of 94.

## Independent recount

A fresh reviewer, without my scorer or this write-up, recomputed every number from the raw answers and records.
All 770 rows and every count above match. Its own bootstrap (another random generator) gives B − A
[+0.8, +18.5]; over 200 seeds the lower end stays between +0.8 and +1.6, never at or below zero. Sign test on
the 21 against 9 questions: p = 0.043. Its other answer-key findings, none changing a verdict:
- PowerToys#49083 ("0.100.2 breaks the PuTTY system menu") has an unlinked twin, PowerToys#46992. All five arms
  cite the twin first and all score a miss. That link and PowerToys#49452's were made by the issue's own author,
  not a maintainer (the second accepting a bot's suggestion).
- terminal#20478: the maintainer wrote `/dup` twice; the last-link rule kept #14185 and dropped #10119, which
  A cites third (A's hit@3 loses one).
- PowerToys#23059 (text expansion) sits in a context-menu cluster through a maintainer's `/dup #33`. No arm
  cited it.
- Long link chains do not inflate scores: one hit@1 in any arm (F) uses a ticket three or more links away.
- Leakage: no comment in the corpus dated on or after the cutoff (300 sampled files and all 2,406 answer files);
  no test question in the corpus; the one corpus mention of a test question is the known edit leak.

## Retrieval alone, no AI, test questions (`runs/diagnostics-test.json`)

| stage | recall@1 | @5 | @10 | @50 |
|---|---|---|---|---|
| constant guess (largest training cluster) | 0.016 | 0.016 | 0.016 | 0.016 |
| BM25 keyword (FTS5) | 0.298 | 0.556 | 0.621 | 0.758 |
| untrained embedding | 0.371 | 0.589 | 0.669 | 0.839 |
| trained embedding (A's retrieval) | 0.387 | 0.653 | 0.758 | 0.879 |
| keyword + trained, fused (F's retrieval) | 0.411 | 0.694 | 0.790 | 0.887 |

The cap per cluster works: the constant guess scores 1.6%. Training adds 1.6 points at recall@1 and 6.4 at
recall@5 over the untrained model. Claude, given A's top 5, picks the right one first more often than the
retriever ranks it first (hit@1 0.476 against recall@1 0.387).

## The pilot and the test disagree on one point

On the 20 dev questions, the ripgrep agent beat the semantic agent (C − B −20 points). On the 124 test questions
the order reversed, to a difference too small to tell from zero (C − B +2.4 [−4.0, +8.9]). The test is the
result; the dev set was for plumbing.

## Deviations, stated

- **A has no re-ranker.** The approach as seen at the convention has one. The off-the-shelf re-ranker lowered held-out
  recall@1 from 0.366 to 0.200, and a re-ranker trained on the tickets did not learn in two attempts
  (amendments 4 and 5). The trained embedding itself works: held-out recall@1 0.366 against 0.295 for the
  untrained one.
- **C and D's timings carry the import defect** described above.
- **Dev set of 20** for plumbing only; nothing was tuned per arm.
- **Knowledge cutoff.** The protocol says Sonnet 5.5's cutoff was not verified. The CLI's own environment block,
  seen in the leak probe, states June 2026 for it, before the 2026-07-01 question window.

## Which approach the connector carries (amendment 1's rule)

C and D share the highest hit@1 (74/124). B is within their paired intervals (C − B and D − B: +2.4
[−4.0, +8.9], the difference not shown), and B is the simplest arm by every count. **The rule picks B, the agent
with ripgrep.** The protocol also calls for B and A to be measured once more on a confirmation set: issues
filed after 2026-11-16, built by the same rules. **That run is pending**: those issues need time to be marked as
duplicates. Until it is done, the numbers above are the only ones, and the connector states none of its own.
