---
description: Review, sync the release plan, then commit and push
model: claude-sonnet-5
argument-hint: [optional focus/message hint]
---

# /ship — review → doc-sync → commit → push

Run the steps below **in order**. Stop the moment a gate fails; do not skip ahead.
Optional argument (`$ARGUMENTS`) is a hint about what changed or a phrase to seed the
commit message — treat it as guidance, not the final message.

## 1. Snapshot the working tree

- Run `git status` and `git diff` (and `git diff --staged`) so you know exactly what changed.
- If there is nothing to commit, say so and stop — nothing to ship.

## 2. Code review (blocking gate)

- Invoke the **code-review** skill at **medium** effort on the current diff.
- Read the findings and classify them:
  - **Blocking** — correctness bugs, broken behavior, security issues, data loss, leaks.
  - **Safe cleanups** — mechanical, behavior-preserving simplification / style / efficiency
    fixes the review is confident about.
- **If there are any blocking findings: STOP.** Do not commit, do not push, do not apply any
  fixes. Report the blocking findings clearly (file:line + one-line fix each) and let the
  user fix them first.
- **If there are no blocking findings, auto-apply the safe cleanups** to the working tree.
  Apply only fixes that are mechanical and behavior-preserving — if a suggested fix changes
  behavior, is ambiguous, or you're not confident, **skip it and just report it** instead of
  guessing. Briefly list what you applied and what you skipped, then continue. The applied
  fixes are part of this commit.

## 3. Sync the release plan (local only)

`docs/releasing/release-plan.md` is a **living doc and is gitignored** — editing it will
**not** be part of the commit, so this step is safe to do before committing.

- Read the top of the file (the `_Last updated: …_` line near the top, and the relevant
  Phase section) and compare it against what you're about to commit.
- If the committed change advances or contradicts something in the plan (a phase item
  landing, a decision changing, new work added), **update it**:
  - Prepend a new dated entry to the `_Last updated: …_` line in the same style as the
    existing entries (newest first, keep the prior notes), using today's date.
  - Update the affected Phase item status (e.g. mark it done, note the branch/PR).
- If nothing in the plan is affected, leave it untouched and say so. **Do not rewrite the
  whole file** — make the smallest correct edit.

## 4. Stage the real changes

- Stage the actual source/content changes for this commit. **Exclude** temp/backup
  artifacts (e.g. `*.bak` files) and anything clearly not meant for the commit — ask if
  unsure rather than blindly `git add -A`.
- Never stage gitignored files; the release-plan edit stays local by design.

## 5. Commit and push

- Write a **concise** commit message: a single imperative subject line, prefixed with the
  current branch's ticket if the branch is named like `JRNL-30` (e.g.
  `JRNL-30 - fix seed banner overflow`). Add a short body only if the change genuinely
  needs it.
- End the commit message with:

  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  ```

- Commit, then `git push`. If the branch has no upstream, push with
  `git push -u origin <branch>`.
- Report the commit hash, the branch, whether the release plan was updated, and any
  non-blocking review findings the user may want to follow up on.
