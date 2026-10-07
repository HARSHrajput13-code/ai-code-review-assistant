# Contributing

This summarises the implementation workflow in the **Concrete Implementation Specification (CIS)**, §22.3–22.6 and decision D-96. If this file and the CIS ever disagree, the CIS wins. The specifications in `docs/` are the only source of truth for *what* to build; this file covers *how* changes reach `main`.

## Branches and pull requests

- `main` is the **protected integration branch**. Nothing is pushed to it directly.
- Every implementation task happens on a **dedicated task branch** and ends in **exactly one pull request** to `main`.
- Branch names use one of these prefixes plus a lowercase kebab-case task name: `feat/`, `fix/`, `refactor/`, `test/`, `docs/`, `ci/`, `build/`, `chore/`.
- Names such as `new`, `testing`, `final`, `changes`, `temp`, `experiment` or personal names are not allowed.
- The planned PRs, PR-00 to PR-10, use exactly the branch names in CIS §22.5.

## Scope: one PR = one bounded implementation purpose

- **Map to the CIS.** Every PR maps to its CIS §22.5 plan and states its CIS traceability: the PR number, milestone, CIS sections and decision IDs.
- **No later work.** Later-milestone or later-PR work must not be added to an earlier PR.
- **No unrelated refactoring.** Do not refactor unrelated code or introduce unplanned dependencies.
- **No retrofitting the CIS.** Do not edit the CIS to justify code that was already written.
- **Record, don't expand.** A change that belongs to another PR is recorded as a dependency or blocker in the PR description. It is not silently added.
- **Declare tests and exclusions.** Every PR lists its tests and acceptance criteria, and states what is **not** implemented.
- **Q7 code is not a baseline.** The Q7 experiment's code is not production code and must never be restored, copied or adapted (CIS §22.2).

## Commits: Conventional Commits 1.0.0

```text
<type>[optional scope][!]: <description>
```

- **Types:** `feat`, `fix`, `docs`, `test`, `build`, `ci`, `refactor`, `perf`, `style`, `chore`.
- **Scope:** lowercase kebab-case, for example `analysis`, `review`, `api`, `ui`, `ai`, `eval`.
- **Description:** imperative and concise. It starts lowercase, has no trailing period, and is at most 72 characters.
- **One change per commit.** Each commit is one coherent logical change.
- **No vague messages,** such as `changes`, `updates`, `final changes`, `fix stuff`, `misc` or `implemented m1`.
- **Breaking changes** use `!` plus a `BREAKING CHANGE:` footer, and must be justified in the PR.

Examples:

```text
feat(analysis): add isolated static tool runner
test(review): add deterministic scoring vectors
fix(ai): reject malformed structured output
docs(cis): clarify implementation lifecycle
ci(github): enforce pull request checks
```

The CI `commits` check enforces this pattern on the PR title and on every non-merge commit (`.github/workflows/ci.yml`).

## CI and merging

- **Required checks.** `commits` (from PR-00), then `backend` and `frontend` (added by PR-01). Each check becomes required once it exists.
- **Required checks are never silently waived.** A failing test is fixed or explicitly escalated. It is never skipped, weakened, or worked around by changing a threshold, a dependency version or a CI check.
- **Merge commits only.** Squash and rebase merging are disabled, so the merge preserves the PR's Conventional Commit history. The merge commit message is the PR title.
- **Merge conditions.** A PR is mergeable only when all of these hold:
  - its scope matches its plan;
  - its commits follow the convention;
  - the required checks and tests pass;
  - no unrelated files changed and no later milestone was implemented;
  - CIS traceability is present;
  - review comments are resolved;
  - the branch is up to date with `main`;
  - no dependency or version changed silently.
- **Approvals.** At least one approving review is required when a second reviewer is available. In solo development the author completes the PR template's self-review checklist instead.
