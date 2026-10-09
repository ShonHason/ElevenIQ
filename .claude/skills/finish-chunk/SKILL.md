---
name: finish-chunk
description: Run the tiered review process at the end of each chunk of work, then propose a commit.
---
Run these steps in order and report the result of each.

**The chunk diff** is `git diff HEAD` plus untracked files (`git status --short`): the
uncommitted work of this chunk. Every agent is scoped to it and reviews nothing else.

**Classify the diff first** (from `git diff --name-only HEAD` and the untracked files). This
decides which agents run:
- `src_logic`: a file under src/ or tools/ changed other than docs or comments (any .py
  logic, config constants, or a committed measurement file).
- `stats_scope`: a metric definition, cohort/percentile rule, measurement, or registry
  status changed (src/eleveniq/metrics/, pipeline aggregation or ratio code,
  analytics/, config thresholds those use, measurements/*.json, or an ADR rule they implement),
  or a statistical claim changed (a figure, finding or interpretation in docs/reviews/,
  an ADR or a page text that states a statistical result). A UI-only chunk (layout, styling,
  interaction, wording that states no statistical result) is never `stats_scope`.
  The milestone sign-off always runs the stats-reviewer (CLAUDE.md "Speed rules").
- `method_reviewed`: the stats-reviewer already reviewed this method before the code
  (a recorded verdict in the ADR or plan for this chunk).

1. Run fast tests: .venv/bin/python -m pytest tests/ -q -m "not integration"

2. qa-tester: **only if `src_logic`** (src/ or tools/). Skip it for docs/, spikes/, fixtures or tests-only
   changes. Wait for it to finish before continuing.
   Give it only:
   - The list of changed source files (from `git diff --name-only HEAD`)
   - Instruction: run `git diff HEAD` yourself, read the ADRs and docstrings in
     the changed files, write the missing unit tests to tests/ (do not fix
     implementation code), and report which test cases you added and why.
   Do NOT give the agent any pre-analysis, findings, focus areas, or lists of
   cases to skip.

3. Run tests with coverage:
   .venv/bin/python -m pytest tests/ -q -m "not integration" --cov=eleveniq --cov-report=term-missing
   Report files under 80% coverage and the uncovered lines.

4. If pipeline code changed: run the integration tests (-m integration).

5. stats-reviewer: **only if `stats_scope`**. Wait for it to finish before step 6.
   Give it only:
   - The list of changed source files
   - If `method_reviewed`: instruction to review the **produced output only** (the
     pipeline run, the spike's check output, the figures written to the ADR) against
     the reviewed method, and to report any statistical validity concerns. Do not
     re-review the method itself.
   - Otherwise: instruction to run `git diff HEAD` yourself, read the relevant ADRs, and
     report any statistical validity concerns.
   Do NOT pre-filter or describe the changes yourself.

6. code-reviewer: **always**, scoped to the chunk diff only. Wait for it to finish.
   Give it only:
   - The list of changed files (tracked and untracked)
   - Instruction: run `git diff HEAD` yourself (and read the untracked files), review
     only that diff for correctness bugs, logic errors, and meaningful
     simplifications, and do not review unchanged code except to understand the diff.
   Do NOT include your own findings, suspicions, focus areas, or severity
   pre-classifications.

Steps 5 and 6 are always sequential. Never run them in parallel.

7. Summary table: step, result (PASS/FAIL/SKIPPED), issues found. **For every agent,
   state that it ran, or why it was skipped** (e.g. "qa-tester: SKIPPED — no src/ logic
   changed, only docs/ and spikes/"; "stats-reviewer: output-only — method reviewed
   before code on <date>").

8. Commit proposal - ONLY if every step above is PASS or SKIPPED:
   - Show `git status` and `git diff --stat`.
   - Call out any changed golden file (tests/fixtures/**/golden_*) explicitly, with its
     `--update-golden` diff summary.
   - Flag anything suspicious: data files, caches, .venv, secrets,
     files unrelated to this chunk, tests whose expected values were changed.
   - Propose a commit message in this format:
       <area>: <short summary of what was done>
       (blank line)
       - bullet list of the main changes
       - which reviews ran and their result, and which were skipped and why
   - Ask me: "Commit with this message? (yes / edit / no)".
   - Commit ONLY after I answer yes. Stage specific files, never `git add .`
     blindly. Never push.

If any step FAILS: do not propose a commit. List what must be fixed.
Do not fix anything on your own during this skill.

**After review fixes** (applied in the main session after my approval):
- Before applying the fixes, stage the reviewed files (`git add <files>`), so that the
  unstaged `git diff` afterwards is exactly the fix delta.
- Run the fast tests (and the integration tests if pipeline code changed).
- Run the code-reviewer on the fix delta only (`git diff`, unstaged). No full re-run of
  the other agents.
- Then continue from step 7.
