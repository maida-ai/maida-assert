"""Acceptance crosses a data-only boundary into a fresh trusted writer."""

import base64
import importlib.util
import json
from hashlib import sha256
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module():
    spec = importlib.util.spec_from_file_location(
        "accept_artifact", ROOT / "scripts/accept_artifact.py"
    )
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class API:
    def __init__(self):
        self.permission = "write"
        self.pull = {
            "state": "open",
            "head": {
                "sha": "a" * 40,
                "ref": "feature",
                "repo": {"full_name": "owner/repo"},
            },
            "base": {"sha": "b" * 40, "ref": "main"},
        }
        self.old = json.dumps(
            {
                "schema_version": "0.3.1",
                "source_run_id": "old",
                "summary": {},
                "final_status": "ok",
            }
        ).encode()
        self.policy = b"version: 2\n"
        self.files = {"baseline.json": self.old, ".maida/policy.yaml": self.policy}
        self.mutations = []
        self.mode = "100644"

    def __call__(self, method, path, **kwargs):
        if method != "GET":
            self.mutations.append((method, path, kwargs.get("payload")))
            return {"sha": "c" * 40}
        if path.endswith("/permission"):
            return {"permission": self.permission}
        if "/pulls/" in path:
            return self.pull
        if "/git/commits/" in path:
            return {"tree": {"sha": "d" * 40}}
        if "/git/trees/" in path:
            return {
                "truncated": False,
                "tree": [
                    {
                        "path": name,
                        "mode": self.mode,
                        "type": "blob",
                        "sha": sha256(data).hexdigest(),
                    }
                    for name, data in self.files.items()
                ],
            }
        if "/git/blobs/" in path:
            data = next(
                data
                for data in self.files.values()
                if path.endswith(sha256(data).hexdigest())
            )
            return {"encoding": "base64", "content": base64.b64encode(data).decode()}
        raise AssertionError(path)


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    m = module()
    api = API()
    monkeypatch.setattr(m, "_request_json", api)
    context = m.prepare_context(
        repository="owner/repo",
        pr_number=42,
        login="alice",
        reason="Expected retry",
        baseline="baseline.json",
        policy=".maida/policy.yaml",
        run_id="123",
        run_attempt="1",
        comment_id=7,
        api_url="https://api.test",
        token="read-token",
    )
    directory = tmp_path / "artifact"
    directory.mkdir()
    baseline = json.loads(api.old)
    baseline["source_run_id"] = "new"
    baseline["summary"] = {"tool_calls": 2}
    baseline["acceptance"] = {"accepted_by": "forged", "reason": "forged"}
    artifact = directory / "acceptance.json"
    artifact.write_text(json.dumps({"context": context, "baseline": baseline}))
    return m, api, context, directory


def write(prepared):
    m, api, context, directory = prepared
    return m.write_artifact(
        context=context,
        artifact_directory=directory,
        repository="owner/repo",
        run_id="123",
        run_attempt="1",
        api_url="https://api.test",
        token="write-token",
    )


def test_writer_changes_only_baseline_and_replaces_untrusted_provenance(prepared):
    m, api, context, directory = prepared
    result = write(prepared)
    assert result["head-sha"] == "c" * 40
    tree = next(
        payload for _, path, payload in api.mutations if path.endswith("/git/trees")
    )
    assert len(tree["tree"]) == 1
    assert tree["tree"][0]["path"] == "baseline.json"
    baseline = json.loads(tree["tree"][0]["content"])
    accepted = baseline["acceptance"]
    assert accepted["reason"] == "Expected retry"
    assert accepted["accepted_by"] == "alice"
    assert accepted["source"]["commit_sha"] == "a" * 40
    assert accepted["previous_baseline"]["sha256"] == sha256(api.old).hexdigest()
    assert accepted["policy"]["sha256"] == sha256(api.policy).hexdigest()
    commit = next(
        payload for _, path, payload in api.mutations if path.endswith("/git/commits")
    )
    assert commit["parents"] == ["a" * 40]
    assert api.mutations[-2][2] == {"sha": "c" * 40, "force": False}
    assert api.mutations[-1][2]["client_payload"]["sha"] == result["head-sha"]
    assert "fresh" in result["message"].lower()


@pytest.mark.parametrize(
    "field",
    [
        "repository",
        "pr_number",
        "head_sha",
        "base_sha",
        "policy_sha256",
        "baseline_sha256",
        "reason",
        "run_id",
        "run_attempt",
        "comment_id",
    ],
)
def test_artifact_cannot_select_its_own_authorization(prepared, field):
    m, api, context, directory = prepared
    artifact = directory / "acceptance.json"
    data = json.loads(artifact.read_text())
    data["context"][field] = "forged"
    artifact.write_text(json.dumps(data))
    with pytest.raises(m.CommandError, match="binding"):
        write(prepared)
    assert not api.mutations


@pytest.mark.parametrize(
    "change,match",
    [
        ("unauthorized", "write access"),
        ("fork", "Fork"),
        ("head", "head"),
        ("base", "base"),
        ("closed", "open"),
        ("policy", "policy"),
        ("baseline", "baseline"),
        ("extra", "only acceptance.json"),
        ("symlink", "regular"),
        ("oversized", "size"),
        ("running", "completed"),
    ],
)
def test_rejects_unsafe_write_without_mutation(prepared, change, match):
    m, api, context, directory = prepared
    if change == "unauthorized":
        api.permission = "read"
    elif change == "fork":
        api.pull["head"]["repo"]["full_name"] = "other/fork"
    elif change == "head":
        api.pull["head"]["sha"] = "e" * 40
    elif change == "base":
        api.pull["base"]["sha"] = "f" * 40
    elif change == "closed":
        api.pull["state"] = "closed"
    elif change == "policy":
        api.files[".maida/policy.yaml"] = b"changed"
    elif change == "baseline":
        api.files["baseline.json"] = b"changed"
    elif change == "extra":
        (directory / "agent.py").write_text("planted")
    elif change == "symlink":
        (directory / "acceptance.json").rename(directory.parent / "payload")
        (directory / "acceptance.json").symlink_to(directory.parent / "payload")
    elif change == "oversized":
        (directory / "acceptance.json").write_bytes(b" " * (m.MAX_BYTES + 1))
    elif change == "running":
        data = json.loads((directory / "acceptance.json").read_text())
        data["baseline"]["final_status"] = "running"
        (directory / "acceptance.json").write_text(json.dumps(data))
    with pytest.raises(m.CommandError, match=match):
        write(prepared)
    assert not api.mutations


def test_writer_uses_verified_bytes_even_if_artifact_changes_later(
    prepared, monkeypatch
):
    m, api, context, directory = prepared
    original = m.validate_context

    def mutate_after_read(*args, **kwargs):
        (directory / "acceptance.json").write_text('{"baseline":"planted"}')
        return original(*args, **kwargs)

    monkeypatch.setattr(m, "validate_context", mutate_after_read)
    write(prepared)
    tree = next(
        payload for _, path, payload in api.mutations if path.endswith("/git/trees")
    )
    assert json.loads(tree["tree"][0]["content"])["source_run_id"] == "new"


def test_branch_moves_during_commit_creation(prepared, monkeypatch):
    m, api, context, directory = prepared

    def request(method, path, **kwargs):
        result = api(method, path, **kwargs)
        if method == "POST" and path.endswith("/git/commits"):
            api.pull["head"]["sha"] = "e" * 40
        return result

    monkeypatch.setattr(m, "_request_json", request)
    with pytest.raises(m.CommandError, match="head"):
        write(prepared)
    assert not any(
        method == "PATCH" or path.endswith("/dispatches")
        for method, path, _ in api.mutations
    )


@pytest.mark.parametrize(
    "path",
    [
        "../baseline.json",
        "/baseline.json",
        ".github/workflows/gate.yml",
        "policy.yaml",
        "a/../baseline.json",
    ],
)
def test_rejects_unsafe_baseline_paths(prepared, path):
    m, api, context, directory = prepared
    with pytest.raises(m.CommandError, match="baseline"):
        m.prepare_context(
            repository="owner/repo",
            pr_number=42,
            login="alice",
            reason="Expected",
            baseline=path,
            policy="policy.yaml",
            run_id="123",
            run_attempt="1",
            comment_id=7,
            api_url="https://api.test",
            token="token",
        )


def test_unchanged_baseline_requests_fresh_gate_without_duplicate_commit(prepared):
    m, api, context, directory = prepared
    (directory / "acceptance.json").write_text(
        json.dumps({"context": context, "baseline": json.loads(api.old)})
    )
    result = write(prepared)
    assert result["changed"] == "false"
    assert result["commit-sha"] == ""
    assert len(api.mutations) == 1
    assert api.mutations[0][1].endswith("/dispatches")
    assert result["head-sha"] == context["head_sha"]


def test_dispatch_failure_reports_written_head(prepared, monkeypatch):
    m, api, context, directory = prepared

    def request(method, path, **kwargs):
        if path.endswith("/dispatches"):
            raise m.CommandError("HTTP 403")
        return api(method, path, **kwargs)

    monkeypatch.setattr(m, "_request_json", request)
    with pytest.raises(m.CommandError, match="fresh-gate dispatch failed"):
        write(prepared)
    assert api.mutations[-1][0] == "PATCH"


@pytest.mark.parametrize(
    "field,value",
    [("repository", "other/repo"), ("run_id", "999"), ("run_attempt", "2")],
)
def test_rejects_cross_repository_or_attempt_replay(prepared, field, value):
    m, api, context, directory = prepared
    context[field] = value
    with pytest.raises(
        m.CommandError, match="different repository or workflow attempt"
    ):
        write(prepared)
    assert not api.mutations


@pytest.mark.parametrize(
    "payload", ["[]", "null", "{", '{"baseline":null}', '{"context":{},"baseline":{}}']
)
def test_rejects_malformed_payload_without_mutation(prepared, payload):
    m, api, context, directory = prepared
    (directory / "acceptance.json").write_text(payload)
    with pytest.raises(m.CommandError):
        write(prepared)
    assert not api.mutations


def test_symlink_in_git_tree_is_rejected(prepared):
    m, api, context, directory = prepared
    api.mode = "120000"
    with pytest.raises(m.CommandError, match="tracked regular file"):
        write(prepared)
    assert not api.mutations


@pytest.mark.parametrize(
    "payload",
    ['{"context":{},"context":{}}', '{"baseline":NaN}', '{"baseline":Infinity}'],
)
def test_ambiguous_json_is_rejected(prepared, payload):
    m, api, context, directory = prepared
    with pytest.raises(m.CommandError, match="Invalid acceptance JSON"):
        m._json(payload)


def test_new_run_identity_alone_does_not_create_duplicate_commit(prepared):
    m, api, context, directory = prepared
    baseline = json.loads(api.old)
    baseline.update(
        source_run_id="another-run",
        source_run_ids=["another-run"],
        source_run_name="same-agent",
        created_at="later",
    )
    (directory / "acceptance.json").write_text(
        json.dumps({"context": context, "baseline": baseline})
    )
    assert write(prepared)["changed"] == "false"
