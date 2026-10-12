# Fork pull request promotion

## Summary

- Promotes an exact, reviewed fork commit into an immutable same-repository
  branch and pull request where credentialed Black Duck checks can run safely.
- Keeps the original fork pull request as the canonical discussion thread and
  closes it only after its matching internal pull request merges.
- Uses SHA pinning, App-authenticated lifecycle markers, and event-driven
  reconciliation to protect against stale revisions, spoofed comments, and
  concurrent updates.

## Process flow

- A contributor opens or updates a fork pull request. Its Black Duck check
  fails intentionally and directs reviewers to use the promoted internal pull
  request.
- The exact fork head SHA follows one of the promotion paths below.
- The workflow applies the promotion safeguards before creating an internal
  pull request.
- The workflow creates or reopens an immutable internal pull request and closes
  older open, unmerged promotions as superseded.
- The required Black Duck check runs on the promoted pull request.
- If changes are requested, the contributor pushes a new SHA to the original
  fork pull request and the authorization and promotion process repeats.
- After the internal pull request merges, finalization closes only the matching
  source revision; a newer revision remains or is restored to open.

## Promotion paths

- **Automatic:** When both the author and triggering user have `write`,
  `maintain`, or `admin` access, the exact SHA is promoted to
  `trusted-fork-<user>/pr-<number>-<sha>`.
- **Manual:** All other revisions require a maintainer to review and promote
  the exact SHA to `external-contributions/pr-<number>-<sha>`. This includes a
  trusted author's revision when the triggering user is untrusted.

## Promotion safeguards

- The SHA-addressed branch name is also used as the internal pull request
  title.
- Draft, closed, non-fork, stale, and non-`main` pull requests are rejected.
- Changes under `.github/workflows/`, including renames and deletions, must be
  recreated and reviewed on a trusted internal branch.
- Promotion fails closed at GitHub's 3,000-file listing limit and revalidates
  the source head after file inspection.
- Re-promoting the same SHA reopens a closed-unmerged internal pull request.
  A newer promotion closes older open, unmerged promotions as superseded;
  merged promotions remain closed.

## Black Duck scan

The required `CI Black Duck security scan / Black Duck security scan` check
fails before checkout on the original fork pull request. On the promoted pull
request, it verifies that the branch head still matches the SHA in its name
before exposing credentials. Do not use **Update branch** or manually modify a
promoted branch; either action invalidates this guard.

The scan checks out the exact pull request head and creates a source ZIP from
the raw contents of every tracked Git blob without running repository code.
The inline exporter runs Python in isolated mode (`python3 -I`) and reads Git
objects with `git ls-tree` and `git cat-file`; it cannot import modules or site
customizations from the contributor checkout. It does not use `git archive`,
checkout filters, or contributor-controlled `.gitattributes`, so
`export-ignore` and `export-subst` cannot omit or rewrite reviewed files.
Unsupported tree paths fail the job rather than producing a partial scan. The
scan job has read-only repository permissions and receives no token capable of
commenting on or changing the pull request.

`Main Push and Scheduled full source scan` is the separate full scan for `main`
pushes and scheduled runs; it is not a pull-request merge gate.

## Finalization and reconciliation

Finalization uses a close, re-read, and reopen protocol. A later
`pull_request_target` `synchronize` event also reopens a source pull request if
a new SHA arrives after the final read. The new revision then returns to the
normal authorization process.

Promotion also records the fork's immutable GitHub repository ID with the
source pull request number and reviewed SHA in the internal pull request. If the
contributor later deletes the fork and GitHub returns `head.repo: null`,
finalization uses that stored identity and the still-reported head SHA to close
and comment on the source pull request safely. Older promotions without the
stored repository ID continue to finalize while the fork exists, but fail
closed if its identity is no longer available.

## GitHub App configuration

Created and installed a dedicated GitHub App on `arm/mcp` with these repository
permissions:

- Contents: read and write
- Metadata: read
- Pull requests: read and write

Do not grant the App Workflows permission. The promotion helper rejects
workflow-file changes before creating an internal ref, and the missing
permission provides a second enforcement layer at the GitHub API boundary.

Configured the repository with:

- Variable `GH_APP_ID`: the App client ID
- Secret `GH_APP_TOKEN`: one of the App private keys

The App token is used so the internal PR triggers normal `pull_request`
workflows without the approval-required behavior applied to PRs created by the
built-in `GITHUB_TOKEN`. The workflow also passes the App slug reported by the
token action to the lifecycle helper. Promotion, finalization, and
reconciliation markers are authoritative only when GitHub reports the comment's
`performed_via_github_app.slug` as that configured App; contributor-authored
lookalike markers are ignored.

## Actions policy

Automatic promotion uses `pull_request_target`, but the workflow checks out
only `main` and treats the fork revision strictly as an opaque Git object. It
never checks out or executes fork content. The organization Actions policy must
allow `pull_request_target` for this workflow, including the `closed` event used
to finalize a merged internal pull request, and must allow `workflow_dispatch`
for explicit maintainer promotion.
