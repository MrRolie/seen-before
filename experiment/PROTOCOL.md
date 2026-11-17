# seen-before: experiment protocol

Status: **committed before any score was computed** (dev or test, AI or retrieval-only). A change after the first
run is a dated amendment at the bottom, with its reason; nothing above it is rewritten.

> Public edition: the wording about the author's own approvals is made neutral. Every measure, rule, number and date is as committed.

## The question

A support desk keeps every past ticket. A new one arrives. Can an AI answer it from the past ones: has this been
seen before, which past ticket is it, and what should the customer do?

Two ways to build that AI are compared, both ending in the same model writing the answer:

- **A, the pipeline** (the approach seen at an AI convention): a model trained on the past tickets, embedding
  retrieval, a re-ranker, and the language model answering from the top results.
- **B, the agent with deterministic tools** (the alternative under test): the language model is given ripgrep-style
  search and a reader over the same tickets, and searches for itself. Nothing is trained, nothing is embedded.

The question has three parts, reported separately: is B **simpler**, **perhaps more accurate**, and
**fast**?

## Corpus

- GitHub issues of three Windows PC software projects, all public and updated daily: `microsoft/PowerToys`,
  `microsoft/terminal`, `microsoft/WSL`. Issue text belongs to its authors and is not redistributed;
  `corpus/fetch.py` fetches it.
- **Cutoff `2026-07-01T00:00Z`.** The searchable corpus is every issue created before the cutoff, with its title,
  body and the comments posted before the cutoff. Labels and open/closed state are removed (they are later
  knowledge). Every question is created on or after the cutoff, so every question is answered only from items
  that existed before it.
- The test window is chosen to sit after the answering models' training data (Claude Opus 5.5 states a June 2026
  knowledge cutoff; no published cutoff was found for Sonnet 5.5, so this is not verified for it). Old answer
  issues may be memorised either way; the citation rule below keeps a memorised number from scoring.
- Known leak, accepted and measured: an issue body edited after the cutoff is seen in its edited form. The build
  reports how many answer issues were edited after the cutoff (`lastEditedAt`), and the result is also reported
  with those questions removed.

## Ground truth

- A **duplicate link** is a maintainer's comment (`OWNER`/`MEMBER`/`COLLABORATOR`, human account) of the form
  `/dup #N`, `/duplicate #N`, `duplicate of #N` or `dupe of #N` (or the issue URL, across the three repos) on an
  issue that carries the repository's duplicate label. The last such comment wins. Self-links, links to a missing
  issue and links to a later issue are dropped and counted.
- Links are joined into **clusters**. A question's answer set is every member of its cluster that is in the
  corpus. Citing any of them counts.
- Labels made by maintainers, not by a similarity bot, so the labels do not favour either approach. (Checked by
  sampling: the canonical is named by a person's `/dup` command.)

## Questions

- **Seen** questions: issues created in `[2026-07-01, 2026-11-16)` with a valid link whose cluster has a member in
  the corpus. The AI sees the title and body only, as the customer wrote them; never the comments (they hold the
  answer).
- Dropped and counted: a question whose own text names any member of its answer set.
- **At most 2 questions per cluster.** Before the cap, one crash cluster (`PowerToys#31226`, PowerToys Run
  `COMException 0x80263001`) holds most of the window's duplicates; uncapped, a constant guess would look like
  skill. The build reports the uncapped top-cluster share.
- **New** questions (secondary): bug issues from the same window closed as completed, never labelled or linked
  as duplicates. The right answer is "not seen before". Their labels are noisier (an unlinked duplicate looks
  new); they are reported apart and never folded into the primary number.
- **Split:** 20 seen questions are dev (prompts and settings are tuned only on dev), the rest test. Dev and test
  share no cluster. New questions: 10 dev, 30 test. Seed 7. The test set is scored once per arm.
- **Strata, fixed now:** a question is `exact_token` if its text carries an HRESULT-style hex code, a .NET
  exception type, a stack frame, a WSL error path, a COM error name or a long numeric error code (regex in
  `corpus/build.py`); otherwise `plain_language`. Every accuracy number is reported per stratum as well as
  overall, because exact tokens are where literal search is strongest.

## The arms

Both answering arms use the same model (**Claude Sonnet 5.5**, pinned by id in the run record), the same system
prompt core, the same output format, and the same view of a ticket (the rendered markdown, truncated at the same
length). Each runs in a headless Claude Code session with no user settings, no CLAUDE.md, no other tools.

- **A, pipeline.** `bge-base-en-v1.5` fine-tuned on the training pairs (contrastive loss, in-batch and mined hard
  negatives, bf16 on the local GPU); top 50 by cosine similarity; re-ranked by `bge-reranker-v2-m3`; the top 5
  given to the model in one prompt. The model answers in one turn, with no tools.
- **B, agent.** The model gets two tools over the same corpus as one markdown file per issue: `search` (ripgrep,
  literal or regex, case-insensitive by default; returns matching issues with their titles and matching lines)
  and `read` (one issue's markdown). At most 20 turns.
- **C, control** (only if approved on the run card): the agent of B, with a semantic `search` backed by
  A's trained retriever and re-ranker in place of ripgrep. It separates "the agent loop" from "the tool", the
  first question a skeptic asks of B's result.
- **Training pairs** for A: links whose two issues and whose `/dup` comment all predate the cutoff. 5% of them are
  held out to choose training settings. A is built "well enough" when its trained retriever beats the untrained
  base on the dev questions' recall@10; if it does not, training is fixed on dev before any test run.

## Output and the citation rule

Each arm returns JSON: `{"seen_before": true|false, "matches": ["Repo#N", ...] (ranked, at most 3),
"answer": "what the customer should do"}`. A cited issue counts only if it appeared in that run's tool results
(B, C) or supplied context (A). A citation from memory scores as a miss and is counted.

## Measures

**Accurate** (primary, deterministic, no judge):
- **hit@1**: the first match is in the answer set. **hit@3**: any of the matches is.
- Reported overall and per stratum, with Wilson 95% intervals.
- B against A: paired difference in hit@1 with a 95% paired bootstrap interval (10,000 resamples, seed 7).
  **More accurate HOLDS** if the interval's lower end is above 0; **FAILS** if its upper end is below 0;
  otherwise **NOT SHOWN** (the data cannot tell them apart).
- Secondary: on new questions, the false-match rate (said seen before when the label says new).
- Retrieval-only diagnostics, no AI, same questions: recall@1/5/10 of BM25, the untrained base embedding, the
  trained embedding, and trained + re-ranker. They show what training and re-ranking each buy.
- Constant-guess baseline: always cite the largest cluster in the training pairs.

**Fast**, each share on its own:
- **Per answer**: wall time end to end; median and 90th percentile, per arm.
- **Retrieval share**: time spent inside retrieval (A: query embedding, similarity search and re-ranking; B and
  C: time inside the tool calls, timed by the tool server itself). **Model share**: the rest. Turns and tool calls
  per answer.
- **Build**: wall time from fetched data to first answerable question (A: training, embedding the corpus,
  loading the re-ranker; B: writing the files).
- **Freshness**: time from a new issue arriving to it being searchable (A: embed and add one document; B: write
  one file). Plus whether A's trained model ages: hit@1 by month of the test window, both arms.
- **"Fast" is not given one combined verdict**: each share above is reported, the faster arm named per
  share.

**Simpler** (counts, no judgement): lines of the arm's own code (non-blank, non-comment); direct third-party
dependencies; model files and their size on disk; steps from fetched data to first answer; whether a GPU is
needed; what must happen when a new issue arrives. **Simpler HOLDS** if B is at most A on every count; otherwise
each count is reported and no verdict is given.

**Cost**: input and output tokens per answer, per arm, and the dollar figure Claude Code reports for them.

## Run discipline

- Arms run one after another, never at the same time, so their timings do not contend.
- The size of each run (questions, arms, model, turn cap) is written on a card and runs start only once that size is approved: first a
  pilot on dev, then the test run.
- Heavy jobs (training, embedding, re-ranking) run under a memory cap (`prlimit`).
- Every run writes a record: model id, Claude Code version, git commit, start and end times, every prompt, every
  tool call with its timing, every output.
- Losses are published with wins. Every number this protocol names is published, whichever way it falls.

## Amendments

### 1. 2026-11-16, before any score: more arms, run order, failures

Added after the protocol was set, at the author's request: "you can also design potentially superior hybrid methods or methods
inspired by solutions that already exist". No number of any kind existed when this was written.

**Arms** (letters fixed; the section "The arms" above still defines A, B and C):
- **A** pipeline: trained embedding, re-ranker, top 5, one turn. Unchanged.
- **F** hybrid pipeline: BM25 keyword candidates (SQLite FTS5) and trained-embedding candidates fused by
  reciprocal rank fusion (k = 60), the fused top 50 re-ranked, top 5, one turn. This is the shape the existing
  ticket-search connectors use (BM25 + vectors).
- **B** agent with ripgrep `search` and `read`. Unchanged.
- **C** agent with semantic `search` (A's trained retriever and re-ranker) and `read`. No longer optional: without
  it neither B's nor D's result can be read (is it the loop or the tool?).
- **D** hybrid agent: ripgrep `search`, semantic `similar`, and `read`; the model picks per step.
- The semantic tools and the one-shot arms call one retrieval service that loads the models once
  (`experiment/pipeline/serve.py`), so no answer pays for loading a model.

**Comparisons, named now.** Primary, unchanged: **B minus A** on hit@1, with the verdict rule above; it alone
answers "more accurate". Secondary, each with its paired interval, no verdict word: D − B, D − A, F − A,
C − A (the loop, same retriever), C − B (the tool, same loop).

**Which approach the connector carries** (the form is chosen separately). The arm with the highest
test hit@1, unless a simpler arm's hit@1 is within its paired 95% interval of it (the difference is not shown),
in which case the simplest such arm by the simplicity counts. The best of five arms on one test set is an
optimistic number, so the chosen arm is measured once more on a **confirmation set**: issues created after
2026-11-16, built by the same rules before release, answered only by the chosen arm and by A. That number is the
one the connector's page states.

**Run order.** Arms are interleaved per question (seeded random order per question), one call at a time, never
concurrent, so time of day and load fall on every arm alike. (Replaces "one after another".)

**Failures.** A model failure (no JSON answer, the turn cap) is recorded and scores as a miss. An infrastructure
failure (usage limit, overload, a crashed CLI, a timeout) is never recorded as an answer: retried, and if it
persists the run stops and resumes later.

**Edit leak, comments.** A comment posted before the cutoff but edited after it is dropped from the corpus and
counted.

### 2. 2026-11-16, before any score: the duplicate-link rule, from a per-repo sample

The rule above was checked only on PowerToys. A sample of the 20 latest duplicate-labelled issues per repo
(2025-06 to 2026-11) showed: WSL maintainers write `/dupe #N`; Terminal maintainers write `/duplicate of #N` and
`a /dup of #N`; and one Terminal team member's `/dup` command, acted on by the repository's bot, carries the
association `NONE`. So: the forms `/dupe`, `/dup of` and `/duplicate of` are added; and where the repository's
bot posted its duplicate confirmation ("identified this issue as a duplicate"), the link is the last human `/dup`
command before its first confirmation, whatever the author's association (the bot acts only on authorised
users). Without a bot confirmation, a maintainer association is still required. Every person naming a target in
the sample was a maintainer or the bot-confirmed team member; no similarity bot names targets in any of the three
repos. `build_report.json` gives the yield per repo.

### 3. 2026-11-16, before any score: GitHub's own duplicate links; question counts; dev size

Reading the issues the rule still missed showed three things, all fixed before any score:
- GitHub's "Close as duplicate" stores the original as `duplicateOf` (GraphQL), often with no comment and
  sometimes with no duplicate label. A duplicate is now an issue with a duplicate label **or** closed as
  duplicate; GitHub's own link is used first (`corpus/fetch_dups.py`), the comment rule otherwise. Links where
  the closer is the issue's own author are kept and counted (`native_self`).
- Staff often carry the association `CONTRIBUTOR`; it now counts as a maintainer on a duplicate-labelled issue.
  Freer wording ("Closing as dupe of <url>", "closing this as a duplicate. See: <url>") is accepted, with a
  negation guard ("not a dup of #5").
- A link to a *newer* issue (761 of them) still says "same problem": it now joins the cluster. A question's
  answer set remains its cluster's members created before the cutoff.

Inside the question window the link yield is now PowerToys 267 of 285, Terminal 31 of 31, WSL 20 of 24; the rest
are genuinely ambiguous ("sounds a lot like #48586 or #47652").

Resulting sets (build_report.json): **124 test seen questions from 101 clusters** (11 exact_token, 113
plain_language), 30 test new; 20 dev seen, 10 dev new; 16,090 training pairs; 66,747 tickets in the corpus.
The dev set stays at 20 seen + 10 new: prompts and settings are fixed in code, and dev is used to catch plumbing
and format failures, not to tune five arms. The exact_token stratum is thin (11): its numbers are reported with
their intervals and read as indicative only. With 124 paired questions, a difference in hit@1 smaller than about
10 points will likely land NOT SHOWN.

### 4. 2026-11-16, after the dev diagnostics, before any test score or any Claude run: arm A's re-ranker

The retrieval-only diagnostics on the 20 dev questions (the first scores of any kind; test untouched) showed the
off-the-shelf re-ranker (`bge-reranker-v2-m3`) pushing the duplicate out: trained embedding recall@1 0.55,
after re-ranking 0.00. Reading its choices: it ranks tickets on the same topic above the ticket with the same
problem ("Reverse mouse scroll direction" above the original of "ZoomIt: reverse arrow direction"). It is a
relevance model, not a duplicate detector. Keeping it would make arm A a strawman, which the floor rule forbids;
the approach as described at the convention ("train a model on those issues and use embedding re-rankers") trains on the
tickets, so the re-ranker is trained too:
- `experiment/pipeline/train_reranker.py`: a cross-encoder (`bge-reranker-base`) trained with binary
  cross-entropy on the training pairs (pairs touching held-out pairs excluded), 3 hard negatives per pair from the
  trained embedding's top 30 outside the pair's cluster.
- **The re-ranking stage is chosen on the 560 held-out training pairs** (pre-cutoff, never trained on), not on
  dev: the best recall@1 among no re-ranker, off-the-shelf, trained. The choice is recorded in
  `data/models/reranker_choice.json` and applies to every arm that re-ranks (A, F, and the semantic tools of C
  and D). The dev diagnostics are then re-run and reported, both versions.

### 5. 2026-11-16, before any test score or Claude run: the re-ranker, second and last attempt

Amendment 4's first attempt failed and the comparison it used was tilted. On the 560 held-out pairs:
untrained base embedding recall@1 0.295 (recall@5 0.555); trained embedding 0.366 (0.661), so training clearly
helps and arm A passes its "built well enough" gate on 560 pairs, not only on 20 dev questions. Off-the-shelf
re-ranker over the trained top 50: 0.200 (0.459) on the raw ticket, 0.261 (0.507) on titles, 0.234 (0.520) on
cleaned text. The trained re-ranker's loss stayed at the base rate (about 0.5 for 1 positive to 3 negatives):
it learned nothing. The efficient-attention flag was checked and changes nothing (embedding cosine 0.99998,
re-ranker scores equal to three decimals).

Second attempt, fixed now:
- the same held-out pairs as the embedding (the exact 560), not every pair touching their tickets;
- hard negatives from the trained embedding's ranks 6 to 30, not 1 to 30 (unlinked true duplicates sit at the
  top), the same rule the embedding used;
- input is the title and the body with issue-form boilerplate removed (`experiment/pipeline/clean.py`), for the
  re-ranker only (the embedding stays as trained);
- a check that can fail: the final training loss must be below 0.45; if not, the attempt counts as failed.

The choice is again the best recall@1 on the 560 held-out pairs among: no re-ranker, off-the-shelf (raw, title,
cleaned), trained. **If no re-ranker beats the trained embedding alone, arm A runs without one.** That is a
stated deviation from the approach as described at the convention ("embedding re-rankers"), shown on the run card with these
numbers, and the re-ranker is not attempted a third time.

Outcome of amendment 5 (2026-11-16): the second training attempt failed its check (final loss 0.506 against the
0.45 bar; base rate about 0.56). Inputs were checked and reach the model whole (both tickets, separators in
place); the cause was not found. Held-out recall@1: no re-ranker 0.366; off-the-shelf 0.200 raw, 0.261 titles,
0.234 cleaned. **Arm A runs without a re-ranker** (trained embedding, top 5), and so do F (fused keyword and
trained embedding, top 5) and the semantic tools of C and D (trained embedding, top 20). This is a stated
deviation from "embedding re-rankers".

### 6. 2026-11-16, before any Claude call on a question: sterile subject calls, leak-checked

Before the pilot, at the author's request: nothing of the local context of the person running the experiment
may reach the model. Every Claude call in every arm now goes through `experiment/sterile.py`: a fresh config home
holding only a copy of the account credentials, an account record with the person's email, names and
organisation blanked (Claude Code otherwise attaches the account email to every conversation and rewrites it from
its profile fetch on each run, so the blank record is written before every call), HOME pointing there, an
environment reduced to PATH, LANG and the config dir (no parent-session variables), a working directory under
/var/tmp with no CLAUDE.md, .claude or .git in it or above it, and the MCP server named neutrally ("archive").
Each call's init event must list exactly the arm's tools and servers, no non-builtin plugin, no skill, no slash
command; otherwise the run stops (exit 3), never retried.

Before the pilot, `experiment/leakcheck.py` (canary words kept outside this repo): plain default `claude -p`
showed 7 canaries (the detector is sensitive); the sterile path showed 0 in 3 runs, each init passing.
**PASS**, `runs/leakcheck.json`. The check is re-run before the test run.

### 7. 2026-11-16, after the test run: one defect found in arm C and D's timing

The semantic tool imported the retrieval service's module to call it, which loaded PyTorch on the first semantic
call of every session: 3.8 s median on that call, 0.014 s on later calls. Accuracy is unaffected; the recorded
times stand, reported beside a corrected estimate in RESULTS.md. Fixed for later runs with a standard-library
client (`experiment/pipeline/client.py`). Nothing was re-run.

### 8. 2026-11-16, after the test run: a fresh recount; amendment 2's bot statement was wrong

A fresh reviewer recounted every score from the raw records (no disagreement) and audited the answer key. It found
that PowerToys has an AI triage bot (`github-actions[bot]`, "Possible duplicates", first seen 2026-08-15, after
the cutoff, so it is absent from the corpus) that named an answer ticket before the maintainer linked the
question on 30 of the 124 test questions. Amendment 2's "no similarity bot names targets" is false for PowerToys
in the question window; its sample did not reach that period. Maintainers still made every link. Nothing is
re-scored: the pre-registered verdict stands, and RESULTS.md adds the line without those 30 questions
(`experiment/sensitivity.py`, line 5) beside it.

### 9. 2026-11-17, after the results: presentation only

The results and the showcase are presented as a comparison of approaches around one question, "is this the right
way to do it?", rather than as a claim with verdicts. No measure, rule, number or comparison changes. The primary
comparison (B against A on hit@1, with its paired interval) and the connector rule are reported as fixed above;
the words HOLDS / FAILS / NOT SHOWN are kept only where this protocol's sections use them.

### 10. 2026-11-17, at the public release: two statements above that the release does not meet

- The confirmation set (amendment 1) has not been run: issues filed after 2026-11-16 need weeks to be marked as
  duplicates. The release goes out with the test-set numbers only and says the confirmation run is pending.
- "Run discipline" says every run records every prompt. The records hold each answer's output, every tool call
  with its timing and results, the tickets shown, the model and CLI version and the commit, but not the prompt
  text itself. The prompt is rebuilt exactly from the code at the recorded commit and the question
  (`experiment/run.py`, `experiment/common.py`).
