---
name: finish-chunk
description: Run the full review process at the end of each chunk of work, then propose a commit.
---
Run these steps in order and report the result of each:

1. Run fast tests: .venv/bin/python -m pytest tests/ -q -m "not integration"

2. Invoke the qa-tester agent. Wait for it to finish before continuing.
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

5. If analytics or metric definitions changed: invoke the stats-reviewer agent.
   Wait for it to finish before step 6. Give it only:
   - The list of changed source files
   - Instruction: run `git diff HEAD` yourself, read the relevant ADRs, and
     report any statistical validity concerns.
   Do NOT pre-filter or describe the changes yourself.

6. Invoke the code-reviewer agent. Wait for it to finish. Give it only:
   - The list of changed source files
   - Instruction: run `git diff HEAD` yourself and review for correctness bugs,
     logic errors, and meaningful simplifications.
   Do NOT include your own findings, suspicions, focus areas, or severity
   pre-classifications.

Steps 5 and 6 are always sequential. Never run them in parallel.

7. Summary table: step, result (PASS/FAIL/SKIPPED), issues found.

8. Commit proposal - ONLY if every step above is PASS or SKIPPED:
   - Show `git status` and `git diff --stat`.
   - Flag anything suspicious: data files, caches, .venv, secrets,
     files unrelated to this chunk, tests whose expected values were changed.
   - Propose a commit message in this format:
       <area>: <short summary of what was done>
       (blank line)
       - bullet list of the main changes
       - which reviews ran and their result
   - Ask me: "Commit with this message? (yes / edit / no)".
   - Commit ONLY after I answer yes. Stage specific files, never `git add .`
     blindly. Never push.

If any step FAILS: do not propose a commit. List what must be fixed.
Do not fix anything on your own during this skill.
