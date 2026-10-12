# Copyright © 2026, Arm Limited and Contributors. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


REPOSITORY = Path(__file__).resolve().parents[2]
SCRIPT = REPOSITORY / ".github/scripts/promote-fork-pr.py"
SPEC = importlib.util.spec_from_file_location("promote_fork_pr", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
PROMOTION = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PROMOTION
SPEC.loader.exec_module(PROMOTION)


SHA = "a" * 40
APP_SLUG = "mcp-promotion"


def app_comment(body: str) -> dict:
    return {
        "body": body,
        "performed_via_github_app": {"slug": APP_SLUG},
    }


def source_pr(*, sha: str = SHA, draft: bool = False) -> dict:
    return {
        "number": 185,
        "state": "open",
        "draft": draft,
        "title": "A fork contribution",
        "html_url": "https://github.com/arm/mcp/pull/185",
        "user": {"login": "trusted-user"},
        "base": {"ref": "main", "repo": {"full_name": "arm/mcp"}},
        "head": {
            "sha": sha,
            "repo": {
                "id": 1234,
                "fork": True,
                "full_name": "trusted-user/mcp",
                "owner": {"login": "trusted-user", "type": "User"},
            },
        },
    }


def internal_pr(
    *,
    number: int = 186,
    source_number: int = 185,
    sha: str = SHA,
    branch: str | None = None,
    state: str = "open",
    merged: bool = False,
    source_repository_id: int | None = 1234,
) -> dict:
    branch = branch or f"external-contributions/pr-{source_number}-{sha}"
    if source_repository_id is None:
        marker = f"<!-- fork-promotion: source-pr={source_number} -->"
    else:
        marker = (
            "<!-- fork-promotion: "
            f"source-pr={source_number} source-sha={sha} "
            f"source-repo-id={source_repository_id} -->"
        )
    return {
        "number": number,
        "state": state,
        "merged": merged,
        "html_url": f"https://github.com/arm/mcp/pull/{number}",
        "body": marker,
        "base": {"ref": "main", "repo": {"full_name": "arm/mcp"}},
        "head": {
            "ref": branch,
            "sha": sha,
            "repo": {"full_name": "arm/mcp"},
        },
    }


def test_promotion_branch_names_match_trust_classification() -> None:
    assert PROMOTION.promotion_branch("manual", "ignored", 185, SHA) == (
        f"external-contributions/pr-185-{SHA}"
    )
    assert PROMOTION.promotion_branch("auto", "trusted-user", 185, SHA) == (
        f"trusted-fork-trusted-user/pr-185-{SHA}"
    )
    assert PROMOTION.parse_promotion_branch(
        f"external-contributions/pr-185-{SHA}"
    ) == (185, SHA)
    assert PROMOTION.parse_promotion_branch(
        f"trusted-fork-trusted-user/pr-185-{SHA}"
    ) == (185, SHA)
    assert PROMOTION.parse_promotion_branch(f"feature/pr-185-{SHA}") is None


def test_internal_pr_uses_the_promotion_name_for_its_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingApi:
        def __init__(self) -> None:
            self.payload: dict | None = None

        def post(self, _path: str, payload: dict) -> dict:
            self.payload = payload
            return {"number": 186, "html_url": "https://github.com/arm/mcp/pull/186"}

    api = RecordingApi()
    monkeypatch.setattr(PROMOTION, "find_internal_pr", lambda *_args: None)
    branch = f"external-contributions/pr-185-{SHA}"
    PROMOTION.create_internal_pr(
        api,
        "arm/mcp",
        branch,
        {
            "head_repository": "external-user/mcp",
            "head_repository_id": "1234",
            "html_url": "https://github.com/arm/mcp/pull/185",
            "sha": SHA,
        },
        185,
        "manual",
        "maintainer",
    )

    assert api.payload is not None
    assert api.payload["head"] == branch
    assert api.payload["title"] == branch
    assert f"source-sha={SHA} source-repo-id=1234" in api.payload["body"]


def test_new_internal_pr_requires_a_stored_source_repository_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(PROMOTION, "find_internal_pr", lambda *_args: None)

    with pytest.raises(ValueError, match="no valid stored repository ID"):
        PROMOTION.create_internal_pr(
            SimpleNamespace(),
            "arm/mcp",
            f"external-contributions/pr-185-{SHA}",
            {
                "head_repository": "external-user/mcp",
                "html_url": "https://github.com/arm/mcp/pull/185",
                "sha": SHA,
            },
            185,
            "manual",
            "maintainer",
        )


def test_closed_unmerged_internal_pr_is_reopened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingApi:
        def __init__(self) -> None:
            self.patch_request: tuple[str, dict] | None = None

        def get(self, path: str) -> dict:
            assert path == "/repos/arm/mcp/pulls/186"
            return {"number": 186, "state": "closed", "merged": False}

        def patch(self, path: str, payload: dict) -> dict:
            self.patch_request = (path, payload)
            return {
                "number": 186,
                "state": "open",
                "html_url": "https://github.com/arm/mcp/pull/186",
            }

    api = RecordingApi()
    monkeypatch.setattr(
        PROMOTION,
        "find_internal_pr",
        lambda *_args: {"number": 186, "state": "closed"},
    )

    internal = PROMOTION.create_internal_pr(
        api,
        "arm/mcp",
        f"external-contributions/pr-185-{SHA}",
        {
            "head_repository": "external-user/mcp",
            "head_repository_id": "1234",
            "html_url": "https://github.com/arm/mcp/pull/185",
            "sha": SHA,
        },
        185,
        "manual",
        "maintainer",
    )

    assert api.patch_request == (
        "/repos/arm/mcp/pulls/186",
        {"state": "open"},
    )
    assert internal["state"] == "open"


def test_merged_internal_pr_is_not_reopened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MergedApi:
        def get(self, _path: str) -> dict:
            return {
                "number": 186,
                "state": "closed",
                "merged": True,
                "html_url": "https://github.com/arm/mcp/pull/186",
            }

        def patch(self, _path: str, _payload: dict) -> None:
            pytest.fail("a merged internal pull request must not be reopened")

    monkeypatch.setattr(
        PROMOTION,
        "find_internal_pr",
        lambda *_args: {"number": 186, "state": "closed"},
    )

    internal = PROMOTION.create_internal_pr(
        MergedApi(),
        "arm/mcp",
        f"external-contributions/pr-185-{SHA}",
        {
            "head_repository": "external-user/mcp",
            "head_repository_id": "1234",
            "html_url": "https://github.com/arm/mcp/pull/185",
            "sha": SHA,
        },
        185,
        "manual",
        "maintainer",
    )

    assert internal["merged"] is True


def test_write_level_permissions_are_trusted() -> None:
    for permission in ("write", "maintain", "admin"):
        assert PROMOTION.is_trusted_permission(permission)
    for permission in ("none", "read", "triage"):
        assert not PROMOTION.is_trusted_permission(permission)


def test_missing_collaborator_is_treated_as_external() -> None:
    class MissingCollaboratorApi:
        def get(self, _path: str) -> None:
            raise PROMOTION.ApiError(404, "GET", "test", "Not Found")

    assert (
        PROMOTION.collaborator_permission(
            MissingCollaboratorApi(), "arm/mcp", "external-user"
        )
        == "none"
    )


def test_comment_idempotency_ignores_contributor_marker_spoofing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "<!-- predictable-marker -->"

    class CommentApi:
        def __init__(self) -> None:
            self.created: list[dict] = []

        def get(self, _path: str, _query: dict) -> list[dict]:
            return [{"body": marker, "performed_via_github_app": None}]

        def post(self, _path: str, payload: dict) -> dict:
            self.created.append(payload)
            return app_comment(payload["body"])

    monkeypatch.setenv("GH_APP_SLUG", APP_SLUG)
    api = CommentApi()

    PROMOTION.post_comment_once(api, "arm/mcp", 185, marker, "Canonical body")

    assert api.created == [{"body": f"{marker}\nCanonical body"}]


def test_comment_idempotency_trusts_only_the_configured_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "<!-- canonical-marker -->"

    class CommentApi:
        def get(self, _path: str, _query: dict) -> list[dict]:
            return [app_comment(f"{marker}\nCanonical body")]

        def post(self, _path: str, _payload: dict) -> None:
            pytest.fail("the configured App's marker must be idempotent")

    monkeypatch.setenv("GH_APP_SLUG", APP_SLUG)

    PROMOTION.post_comment_once(
        CommentApi(), "arm/mcp", 185, marker, "Canonical body"
    )


def test_comment_creation_requires_configured_app_attribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CommentApi:
        def get(self, _path: str, _query: dict) -> list[dict]:
            return []

        def post(self, _path: str, payload: dict) -> dict:
            return {"body": payload["body"], "performed_via_github_app": None}

    monkeypatch.setenv("GH_APP_SLUG", APP_SLUG)

    with pytest.raises(RuntimeError, match="attribute the comment"):
        PROMOTION.post_comment_once(
            CommentApi(),
            "arm/mcp",
            185,
            "<!-- canonical-marker -->",
            "Canonical body",
        )


def test_replacement_internal_pr_closes_the_superseded_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = internal_pr()
    current_sha = "b" * 40
    current_branch = f"external-contributions/pr-185-{current_sha}"
    current_url = "https://github.com/arm/mcp/pull/187"

    class RecordingApi:
        def __init__(self) -> None:
            self.requests: list[tuple[str, dict]] = []

        def get(self, path: str) -> dict:
            assert path == "/repos/arm/mcp/pulls/186"
            return old

        def patch(self, path: str, payload: dict) -> None:
            self.requests.append((path, payload))

    api = RecordingApi()
    monkeypatch.setattr(
        PROMOTION,
        "paginated_get",
        lambda *_args: [
            old,
            internal_pr(
                number=187,
                sha=current_sha,
                branch=current_branch,
            ),
            internal_pr(number=188, source_number=999),
        ],
    )
    comments: list[tuple[int, str]] = []
    monkeypatch.setattr(
        PROMOTION,
        "post_comment_once",
        lambda _api, _repository, number, _marker, body: comments.append(
            (number, body)
        ),
    )

    superseded = PROMOTION.supersede_internal_prs(
        api, "arm/mcp", 185, current_branch, current_url
    )

    assert superseded == ["https://github.com/arm/mcp/pull/186"]
    assert api.requests == [
        ("/repos/arm/mcp/pulls/186", {"state": "closed"})
    ]
    assert comments == [
        (186, f"Superseded by {current_url}. Do not merge this older revision.")
    ]


def test_closing_source_pr_uses_the_pull_request_endpoint() -> None:
    class RecordingApi:
        def __init__(self) -> None:
            self.requests: list[tuple[str, dict]] = []
            self.closed = False

        def get(self, path: str) -> dict:
            assert path == "/repos/arm/mcp/pulls/185"
            pull = source_pr()
            pull["state"] = "closed" if self.closed else "open"
            return pull

        def patch(self, path: str, payload: dict) -> None:
            self.requests.append((path, payload))
            self.closed = payload["state"] == "closed"

    api = RecordingApi()
    assert PROMOTION.close_source_pr(api, "arm/mcp", 185, SHA)

    assert api.requests == [
        ("/repos/arm/mcp/pulls/185", {"state": "closed"})
    ]


def test_source_pr_remains_open_when_a_new_revision_arrives() -> None:
    class UpdatedSourceApi:
        def get(self, _path: str) -> dict:
            return source_pr(sha="b" * 40)

        def patch(self, _path: str, _payload: dict) -> None:
            pytest.fail("an updated source pull request must not be closed")

    assert not PROMOTION.close_source_pr(UpdatedSourceApi(), "arm/mcp", 185, SHA)


def test_source_pr_is_reopened_when_revision_races_the_close() -> None:
    class RacingSourceApi:
        def __init__(self) -> None:
            self.get_count = 0
            self.requests: list[tuple[str, dict]] = []

        def get(self, path: str) -> dict:
            assert path == "/repos/arm/mcp/pulls/185"
            self.get_count += 1
            if self.get_count == 1:
                return source_pr()
            pull = source_pr(sha="b" * 40)
            pull["state"] = "closed"
            return pull

        def patch(self, path: str, payload: dict) -> None:
            self.requests.append((path, payload))

    api = RacingSourceApi()
    assert not PROMOTION.close_source_pr(api, "arm/mcp", 185, SHA)
    assert api.requests == [
        ("/repos/arm/mcp/pulls/185", {"state": "closed"}),
        ("/repos/arm/mcp/pulls/185", {"state": "open"}),
    ]


def test_merged_internal_pr_closes_its_matching_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FinalizationApi:
        def __init__(self) -> None:
            self.source_state = "open"
            self.requests: list[tuple[str, dict]] = []

        def get(self, path: str) -> dict:
            if path == "/repos/arm/mcp/pulls/186":
                return internal_pr(state="closed", merged=True)
            assert path == "/repos/arm/mcp/pulls/185"
            pull = source_pr()
            pull["state"] = self.source_state
            return pull

        def patch(self, path: str, payload: dict) -> None:
            self.requests.append((path, payload))
            events.append(f"patch:{payload['state']}")
            self.source_state = payload["state"]

    comments: list[str] = []

    def record_comment(
        _api: object,
        _repository: str,
        _number: int,
        marker: str,
        body: str,
    ) -> None:
        events.append(marker)
        comments.append(body)

    monkeypatch.setattr(
        PROMOTION,
        "post_comment_once",
        record_comment,
    )
    api = FinalizationApi()

    assert PROMOTION.finalize_internal_pr(api, "arm/mcp", 186)
    assert api.requests == [
        ("/repos/arm/mcp/pulls/185", {"state": "closed"})
    ]
    assert events[0].startswith("<!-- fork-promotion-merged:")
    assert events.index("patch:closed") > 0
    assert any("source pull request is now closed" in comment for comment in comments)


def test_merged_internal_pr_finalizes_after_the_source_fork_is_deleted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DeletedForkApi:
        def __init__(self) -> None:
            self.source_state = "open"
            self.requests: list[tuple[str, dict]] = []

        def get(self, path: str) -> dict:
            if path == "/repos/arm/mcp/pulls/186":
                return internal_pr(state="closed", merged=True)
            assert path == "/repos/arm/mcp/pulls/185"
            pull = source_pr()
            pull["state"] = self.source_state
            pull["head"]["repo"] = None
            return pull

        def patch(self, path: str, payload: dict) -> None:
            self.requests.append((path, payload))
            self.source_state = payload["state"]

    comments: list[str] = []
    monkeypatch.setattr(
        PROMOTION,
        "post_comment_once",
        lambda _api, _repository, _number, _marker, body: comments.append(body),
    )
    api = DeletedForkApi()

    assert PROMOTION.finalize_internal_pr(api, "arm/mcp", 186)
    assert api.requests == [
        ("/repos/arm/mcp/pulls/185", {"state": "closed"})
    ]
    assert any("source pull request is now closed" in comment for comment in comments)


def test_deleted_fork_finalization_requires_stored_repository_identity() -> None:
    class LegacyPromotionApi:
        def get(self, path: str) -> dict:
            if path == "/repos/arm/mcp/pulls/186":
                return internal_pr(
                    state="closed",
                    merged=True,
                    source_repository_id=None,
                )
            assert path == "/repos/arm/mcp/pulls/185"
            pull = source_pr()
            pull["head"]["repo"] = None
            return pull

        def patch(self, _path: str, _payload: dict) -> None:
            pytest.fail("missing source identity must fail closed")

    with pytest.raises(ValueError, match="no live fork or stored repository ID"):
        PROMOTION.finalize_internal_pr(LegacyPromotionApi(), "arm/mcp", 186)


def test_finalization_rejects_a_changed_source_repository_identity() -> None:
    with pytest.raises(ValueError, match="repository identity changed"):
        PROMOTION.validate_source_pr_for_finalization(
            source_pr(), "arm/mcp", 185, 9999
        )


def test_merged_internal_pr_leaves_a_newer_source_revision_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UpdatedSourceApi:
        def get(self, path: str) -> dict:
            if path == "/repos/arm/mcp/pulls/186":
                return internal_pr(state="closed", merged=True)
            assert path == "/repos/arm/mcp/pulls/185"
            return source_pr(sha="b" * 40)

        def patch(self, _path: str, _payload: dict) -> None:
            pytest.fail("a newer source revision must remain open")

    comments: list[str] = []
    monkeypatch.setattr(
        PROMOTION,
        "post_comment_once",
        lambda _api, _repository, _number, _marker, body: comments.append(body),
    )

    assert not PROMOTION.finalize_internal_pr(
        UpdatedSourceApi(), "arm/mcp", 186
    )
    assert any(
        "requires separate authorization and promotion" in comment
        for comment in comments
    )


def test_finalization_comment_is_state_neutral_for_a_newer_closed_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ClosedUpdatedSourceApi:
        def get(self, path: str) -> dict:
            if path == "/repos/arm/mcp/pulls/186":
                return internal_pr(state="closed", merged=True)
            assert path == "/repos/arm/mcp/pulls/185"
            pull = source_pr(sha="b" * 40)
            pull["state"] = "closed"
            return pull

        def patch(self, _path: str, _payload: dict) -> None:
            pytest.fail("finalization must not change a newer source revision")

    comments: list[str] = []
    monkeypatch.setattr(
        PROMOTION,
        "post_comment_once",
        lambda _api, _repository, _number, _marker, body: comments.append(body),
    )

    assert not PROMOTION.finalize_internal_pr(
        ClosedUpdatedSourceApi(), "arm/mcp", 186
    )
    result = next(comment for comment in comments if "now points to" in comment)
    assert f"now points to `{'b' * 40}`" in result
    assert "requires separate authorization and promotion" in result
    assert "remains open" not in result


def test_finalization_reopens_a_source_updated_just_after_closing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LateUpdateApi:
        def __init__(self) -> None:
            self.source_reads = 0
            self.requests: list[tuple[str, dict]] = []

        def get(self, path: str) -> dict:
            if path == "/repos/arm/mcp/pulls/186":
                return internal_pr(state="closed", merged=True)
            assert path == "/repos/arm/mcp/pulls/185"
            self.source_reads += 1
            if self.source_reads == 1:
                return source_pr()
            pull = source_pr(sha="b" * 40)
            pull["state"] = "closed" if self.source_reads == 2 else "open"
            return pull

        def patch(self, path: str, payload: dict) -> None:
            self.requests.append((path, payload))

    monkeypatch.setattr(PROMOTION, "close_source_pr", lambda *_args: True)
    monkeypatch.setattr(PROMOTION, "post_comment_once", lambda *_args: None)
    api = LateUpdateApi()

    assert not PROMOTION.finalize_internal_pr(api, "arm/mcp", 186)
    assert api.requests == [
        ("/repos/arm/mcp/pulls/185", {"state": "open"})
    ]


def test_synchronize_reconciliation_reopens_a_late_source_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updated_sha = "b" * 40

    class ReconciliationApi:
        def __init__(self) -> None:
            self.source_state = "closed"
            self.requests: list[tuple[str, dict]] = []

        def get(self, path: str) -> dict:
            if path == "/repos/arm/mcp/pulls/186":
                pull = internal_pr(state="closed", merged=True)
                pull["merged_at"] = "2026-10-12T12:00:00Z"
                return pull
            assert path == "/repos/arm/mcp/pulls/185"
            pull = source_pr(sha=updated_sha)
            pull["state"] = self.source_state
            return pull

        def patch(self, path: str, payload: dict) -> None:
            self.requests.append((path, payload))
            self.source_state = payload["state"]

    monkeypatch.setattr(
        PROMOTION,
        "paginated_get",
        lambda _api, path, _query: [
            app_comment(
                "<!-- fork-promotion-merged: "
                f"internal-pr=186 source-sha={SHA} -->"
            )
        ]
        if path.endswith("/issues/185/comments")
        else [],
    )
    comments: list[str] = []
    monkeypatch.setattr(
        PROMOTION,
        "post_comment_once",
        lambda _api, _repository, _number, _marker, body: comments.append(body),
    )
    monkeypatch.setenv("GH_APP_SLUG", APP_SLUG)
    api = ReconciliationApi()

    assert PROMOTION.reconcile_source_pr(api, "arm/mcp", 185, updated_sha)
    assert api.requests == [
        ("/repos/arm/mcp/pulls/185", {"state": "open"})
    ]
    assert "normal authorization and promotion process" in comments[0]


def test_synchronize_reconciliation_keeps_the_merged_revision_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ReconciliationApi:
        def get(self, path: str) -> dict:
            if path == "/repos/arm/mcp/pulls/186":
                return internal_pr(state="closed", merged=True)
            assert path == "/repos/arm/mcp/pulls/185"
            pull = source_pr()
            pull["state"] = "closed"
            return pull

        def patch(self, _path: str, _payload: dict) -> None:
            pytest.fail("the merged source revision must remain closed")

    monkeypatch.setattr(
        PROMOTION,
        "paginated_get",
        lambda *_args: [
            app_comment(
                "<!-- fork-promotion-merged: "
                f"internal-pr=186 source-sha={SHA} -->"
            )
        ],
    )
    monkeypatch.setenv("GH_APP_SLUG", APP_SLUG)

    assert not PROMOTION.reconcile_source_pr(
        ReconciliationApi(), "arm/mcp", 185, SHA
    )


def test_synchronize_reconciliation_rejects_a_stale_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_sha = "c" * 40

    class ReconciliationApi:
        def get(self, path: str) -> dict:
            if path == "/repos/arm/mcp/pulls/186":
                return internal_pr(state="closed", merged=True)
            assert path == "/repos/arm/mcp/pulls/185"
            pull = source_pr(sha=current_sha)
            pull["state"] = "closed"
            return pull

        def patch(self, _path: str, _payload: dict) -> None:
            pytest.fail("a stale synchronize event must not reopen the source")

    monkeypatch.setattr(
        PROMOTION,
        "paginated_get",
        lambda *_args: [
            app_comment(
                "<!-- fork-promotion-merged: "
                f"internal-pr=186 source-sha={SHA} -->"
            )
        ],
    )
    monkeypatch.setenv("GH_APP_SLUG", APP_SLUG)

    with pytest.raises(ValueError, match="head changed"):
        PROMOTION.reconcile_source_pr(
            ReconciliationApi(), "arm/mcp", 185, "b" * 40
        )


def test_synchronize_reconciliation_ignores_an_unmerged_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ReconciliationApi:
        def get(self, path: str) -> dict:
            if path == "/repos/arm/mcp/pulls/186":
                return internal_pr(state="open", merged=False)
            assert path == "/repos/arm/mcp/pulls/185"
            pull = source_pr(sha="b" * 40)
            pull["state"] = "closed"
            return pull

        def patch(self, _path: str, _payload: dict) -> None:
            pytest.fail("an unverified merge marker must not reopen the source")

    monkeypatch.setattr(
        PROMOTION,
        "paginated_get",
        lambda *_args: [
            app_comment(
                "<!-- fork-promotion-merged: "
                f"internal-pr=186 source-sha={SHA} -->"
            )
        ],
    )
    monkeypatch.setenv("GH_APP_SLUG", APP_SLUG)

    assert not PROMOTION.reconcile_source_pr(
        ReconciliationApi(), "arm/mcp", 185, "b" * 40
    )


def test_synchronize_reconciliation_ignores_a_contributor_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ReconciliationApi:
        def get(self, path: str) -> dict:
            assert path == "/repos/arm/mcp/pulls/185"
            pull = source_pr(sha="b" * 40)
            pull["state"] = "closed"
            return pull

        def patch(self, _path: str, _payload: dict) -> None:
            pytest.fail("a contributor marker must not reopen the source")

    monkeypatch.setattr(
        PROMOTION,
        "paginated_get",
        lambda *_args: [
            {
                "body": (
                    "<!-- fork-promotion-merged: "
                    f"internal-pr=186 source-sha={SHA} -->"
                ),
                "performed_via_github_app": None,
            }
        ],
    )
    monkeypatch.setenv("GH_APP_SLUG", APP_SLUG)

    assert not PROMOTION.reconcile_source_pr(
        ReconciliationApi(), "arm/mcp", 185, "b" * 40
    )


@pytest.mark.parametrize(
    "file",
    [
        {"filename": ".github/workflows/untrusted.yml"},
        {
            "filename": "docs/renamed-workflow.yml",
            "previous_filename": ".github/workflows/untrusted.yml",
        },
    ],
)
def test_workflow_file_changes_are_rejected(file: dict) -> None:
    class FilesApi:
        def get(self, path: str, query: dict) -> list[dict]:
            assert path == "/repos/arm/mcp/pulls/185/files"
            assert query == {"per_page": 100, "page": 1}
            return [file]

    with pytest.raises(RuntimeError, match="privileged workflow files"):
        PROMOTION.ensure_no_workflow_file_changes(FilesApi(), "arm/mcp", 185)


def test_non_workflow_file_changes_can_be_promoted() -> None:
    class FilesApi:
        def get(self, _path: str, _query: dict) -> list[dict]:
            return [{"filename": "src/example.py"}]

    PROMOTION.ensure_no_workflow_file_changes(FilesApi(), "arm/mcp", 185)


def test_truncated_pull_request_file_list_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        PROMOTION,
        "paginated_get",
        lambda *_args: [
            {"filename": f"src/generated-{index}.py"}
            for index in range(PROMOTION.MAX_PULL_REQUEST_FILES)
        ],
    )

    with pytest.raises(RuntimeError, match="truncated the file list"):
        PROMOTION.ensure_no_workflow_file_changes(object(), "arm/mcp", 185)


def test_source_head_is_revalidated_after_file_inspection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UpdatingSourceApi:
        def __init__(self) -> None:
            self.get_count = 0

        def get(self, path: str) -> dict:
            assert path == "/repos/arm/mcp/pulls/185"
            self.get_count += 1
            return source_pr(sha=SHA if self.get_count == 1 else "b" * 40)

    monkeypatch.setattr(
        PROMOTION,
        "parse_args",
        lambda: SimpleNamespace(
            command="promote",
            mode="auto",
            pull_number=185,
            expected_sha=SHA,
            actor="trusted-user",
        ),
    )
    monkeypatch.setattr(PROMOTION, "GitHubApi", lambda *_args: UpdatingSourceApi())
    monkeypatch.setattr(PROMOTION, "collaborator_permission", lambda *_args: "write")
    monkeypatch.setattr(
        PROMOTION, "ensure_no_workflow_file_changes", lambda *_args: None
    )
    monkeypatch.setattr(
        PROMOTION,
        "ensure_ref",
        lambda *_args: pytest.fail("a changed source head must not create a ref"),
    )
    monkeypatch.setenv("GITHUB_REPOSITORY", "arm/mcp")
    monkeypatch.setenv("GH_TOKEN", "test-token")

    with pytest.raises(ValueError, match="head changed"):
        PROMOTION.main()


@pytest.mark.parametrize("mode", ["auto", "manual"])
def test_source_pr_stays_open_after_internal_pr_is_created(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    class SourceApi:
        def get(self, _path: str) -> dict:
            return source_pr()

    calls: list[str] = []
    monkeypatch.setattr(
        PROMOTION,
        "parse_args",
        lambda: SimpleNamespace(
            command="promote",
            mode=mode,
            pull_number=185,
            expected_sha=SHA,
            actor="trusted-user",
        ),
    )
    monkeypatch.setattr(PROMOTION, "GitHubApi", lambda *_args: SourceApi())
    monkeypatch.setattr(PROMOTION, "collaborator_permission", lambda *_args: "write")
    monkeypatch.setattr(
        PROMOTION, "ensure_no_workflow_file_changes", lambda *_args: None
    )
    monkeypatch.setattr(
        PROMOTION, "ensure_ref", lambda *_args: calls.append("ensure-ref")
    )

    def create_internal(*_args: object) -> dict:
        calls.append("create-internal-pr")
        return {"html_url": "https://github.com/arm/mcp/pull/186"}

    monkeypatch.setattr(PROMOTION, "create_internal_pr", create_internal)
    monkeypatch.setattr(
        PROMOTION,
        "comment_on_source_pr",
        lambda *_args: calls.append("comment-on-source-pr"),
    )
    monkeypatch.setattr(
        PROMOTION,
        "supersede_internal_prs",
        lambda *_args: calls.append("supersede-internal-prs") or [],
    )
    monkeypatch.setattr(
        PROMOTION,
        "close_source_pr",
        lambda *_args: pytest.fail("the source PR must remain open after promotion"),
    )
    monkeypatch.setenv("GITHUB_REPOSITORY", "arm/mcp")
    monkeypatch.setenv("GH_TOKEN", "test-token")

    assert PROMOTION.main() == 0
    assert calls == [
        "ensure-ref",
        "create-internal-pr",
        "comment-on-source-pr",
        "supersede-internal-prs",
    ]


def test_source_validation_pins_the_exact_reviewed_sha() -> None:
    validated = PROMOTION.validate_source_pr(source_pr(), "arm/mcp", 185, SHA)
    assert validated["author"] == "trusted-user"
    assert validated["sha"] == SHA
    assert validated["head_repository_id"] == "1234"

    with pytest.raises(ValueError, match="head changed"):
        PROMOTION.validate_source_pr(source_pr(), "arm/mcp", 185, "b" * 40)


def test_source_validation_rejects_draft_prs() -> None:
    with pytest.raises(ValueError, match="still a draft"):
        PROMOTION.validate_source_pr(
            source_pr(draft=True), "arm/mcp", 185, SHA
        )


def test_automatic_branch_rejects_unsafe_usernames() -> None:
    with pytest.raises(ValueError, match="unsafe GitHub username"):
        PROMOTION.promotion_branch("auto", "bad/user", 185, SHA)
