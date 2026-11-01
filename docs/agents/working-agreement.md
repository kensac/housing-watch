# Working agreement

## Your own worktree

Several sessions work on this repository at once. A shared checkout means one
session's branch switch lands in another's working tree. Give each task its
own worktree from a fresh `origin/main`:

```
git fetch origin
git worktree add ../hw-<task> -b <branch> origin/main
```

Read another branch by revision, not by path: `git show origin/main:<path>`.

Never `git stash`. It is shared across worktrees, so one session's stash can be
popped by another. Commit to a branch instead.

## Update before you start, rebase before you push

Merges are squashed, so a merged branch is not an ancestor of main. Rebase an
unmerged branch on `origin/main` before pushing. A branch whose PR was squash
merged wants `git reset --hard origin/main`, not a rebase.

## Pull requests

- Every change goes through a PR against `main` on `kensac/housing-watch`.
  The owner reviews and merges. Do not merge your own PR.
- One reviewable behavior per PR.
- Run the CI gates locally first (see [testing.md](testing.md)), then watch
  them: `gh pr checks <n> --watch`.

## Attribution

Nothing in this repository names an AI assistant: no model or tool name, no
"generated with", no `Co-Authored-By` trailer for one, no "as an AI" aside.
That covers commit messages, PR titles and bodies, code, comments and docs.

This needs checking, not remembering. A squash merge uses the PR body as the
commit message, so whatever is in the description lands in history. Read the
title and body before you open the PR.

## Documentation

- One copy of each rule. [AGENTS.md](../../AGENTS.md) points to these topic
  documents; [ARCHITECTURE.md](../ARCHITECTURE.md) is the design; the README
  is for a person meeting the project. When two of them say the same thing,
  delete one.
- Rules, not history. Measurements, plans and task reports go in the PR.
- A PR that adds or changes a rule updates the topic document in the same PR.
- Before writing a claim about what the code does, open the code.
