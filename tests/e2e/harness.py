"""Execute composite shell steps, mocking only GitHub and runner setup.

This is a deliberately small runner for these Actions, not an Actions emulator.
Unknown expressions, step types, API routes, and checkout targets fail loudly.
All Git mutations are confined to disposable local repositories.
"""

from __future__ import annotations

import base64
import copy
import json
import os
import re
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parents[2]
STICKY_SHA = "0ea0beb66eb9baf113663a64ec522f60e49231c0"


def read_commands(path):
    values = {}
    lines = iter(path.read_text().splitlines())
    for line in lines:
        if not line:
            continue
        if "<<" in line:
            name, delimiter = line.split("<<", 1)
            content = []
            for item in lines:
                if item == delimiter:
                    break
                content.append(item)
            else:
                raise AssertionError("Unterminated runner command")
            values[name] = "\n".join(content)
        else:
            name, value = line.split("=", 1)
            values[name] = value
    return values


class GitHubFixture:
    def __init__(self):
        self.comments = []
        self.checks = []
        self.dispatches = []
        self.requests = []
        self.errors = []
        self.permission = "write"
        self.head = ""
        self.reject_checks = False

    def request(self, method, path, body):
        self.requests.append((method, path, body))
        if (
            method == "GET"
            and path == "/repos/fixture/consumer/collaborators/reviewer/permission"
        ):
            return 200, {"permission": self.permission}
        if method == "GET" and path == "/repos/fixture/consumer/pulls/1":
            return 200, {
                "state": "open",
                "base": {"sha": self.consumer.base, "ref": "main"},
                "head": {
                    "sha": self.head,
                    "ref": "candidate",
                    "repo": {"full_name": "fixture/consumer"},
                },
            }
        if "/git/" in path:
            return self.git_request(method, path, body)
        if method == "POST" and path == "/repos/fixture/consumer/check-runs":
            if self.reject_checks:
                return 403, {"message": "Resource not accessible by integration"}
            self.checks.append(body)
            return 201, {"id": len(self.checks), **body}
        if method == "POST" and path == "/repos/fixture/consumer/dispatches":
            self.dispatches.append(body)
            return 204, None
        if method == "POST" and path == "/repos/fixture/consumer/issues/1/comments":
            comment = {
                "id": len(self.comments) + 1,
                "node_id": f"C{len(self.comments) + 1}",
                "body": body["body"],
            }
            self.comments.append(comment)
            return 201, comment
        if method == "POST" and path == "/graphql":
            query = body["query"]
            if "query(" in query and "pullRequest" in query:
                nodes = [
                    {
                        "id": c["node_id"],
                        "body": c["body"],
                        "isMinimized": False,
                        "author": {"login": "github-actions"},
                    }
                    for c in self.comments
                ]
                return 200, {
                    "data": {
                        "viewer": {"login": "github-actions[bot]"},
                        "repository": {
                            "pullRequest": {
                                "comments": {
                                    "nodes": nodes,
                                    "pageInfo": {
                                        "endCursor": None,
                                        "hasNextPage": False,
                                    },
                                }
                            }
                        },
                    }
                }
            if "updateIssueComment" in query:
                values = body["variables"]["input"]
                comment = next(c for c in self.comments if c["node_id"] == values["id"])
                comment["body"] = values["body"]
                return 200, {
                    "data": {
                        "updateIssueComment": {
                            "issueComment": {"id": values["id"], "body": values["body"]}
                        }
                    }
                }
        raise AssertionError(f"Unexpected GitHub request: {method} {path}")

    def git_request(self, method, path, body):
        c = self.consumer

        def git(*args, data=None, env=None):
            result = subprocess.run(
                ["git", "--git-dir", str(c.remote), *args],
                input=data,
                env=env or c.env,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, result.stderr
            return result.stdout.strip()

        if method == "GET" and "/git/commits/" in path:
            return 200, {
                "tree": {"sha": git("rev-parse", path.rsplit("/", 1)[-1] + "^{tree}")}
            }
        if method == "GET" and "/git/trees/" in path:
            entries = []
            for line in git("ls-tree", "-r", path.rsplit("/", 1)[-1]).splitlines():
                metadata, name = line.split("\t")
                mode, kind, sha = metadata.split()
                entries.append({"path": name, "mode": mode, "type": kind, "sha": sha})
            return 200, {"truncated": False, "tree": entries}
        if method == "GET" and "/git/blobs/" in path:
            data = subprocess.check_output(
                [
                    "git",
                    "--git-dir",
                    str(c.remote),
                    "cat-file",
                    "blob",
                    path.rsplit("/", 1)[-1],
                ]
            )
            return 200, {
                "encoding": "base64",
                "content": base64.b64encode(data).decode(),
            }
        if method == "POST" and path.endswith("/git/trees"):
            env = {**c.env, "GIT_INDEX_FILE": str(c.root.parent / "api-index")}
            git("read-tree", body["base_tree"], env=env)
            for entry in body["tree"]:
                sha = git("hash-object", "-w", "--stdin", data=entry["content"])
                git(
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    entry["mode"],
                    sha,
                    entry["path"],
                    env=env,
                )
            return 201, {"sha": git("write-tree", env=env)}
        if method == "POST" and path.endswith("/git/commits"):
            env = {
                **c.env,
                "GIT_AUTHOR_NAME": body["author"]["name"],
                "GIT_AUTHOR_EMAIL": body["author"]["email"],
                "GIT_COMMITTER_NAME": "Fixture",
                "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
            }
            return 201, {
                "sha": git(
                    "commit-tree",
                    body["tree"],
                    "-p",
                    body["parents"][0],
                    "-m",
                    body["message"],
                    env=env,
                )
            }
        if method == "PATCH" and "/git/refs/heads/" in path:
            assert body["force"] is False
            ref = "refs/heads/" + path.split("/git/refs/heads/", 1)[1]
            assert git("merge-base", self.head, body["sha"]) == self.head
            git("update-ref", ref, body["sha"], self.head)
            self.head = body["sha"]
            return 200, {"object": {"sha": self.head}}
        raise AssertionError((method, path, body))

    def __enter__(self):
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.handle_request()

            def do_POST(self):
                self.handle_request()

            def do_PATCH(self):
                self.handle_request()

            def handle_request(self):
                try:
                    size = int(self.headers.get("Content-Length", 0))
                    body = json.loads(self.rfile.read(size)) if size else None
                    status, result = fixture.request(
                        self.command, urlparse(self.path).path, body
                    )
                except (
                    AssertionError,
                    KeyError,
                    ValueError,
                    TypeError,
                    StopIteration,
                ) as exc:
                    fixture.errors.append(str(exc))
                    status, result = 500, {"message": str(exc)}
                data = json.dumps(result).encode() if result is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        assert not self.errors, self.errors


class Consumer:
    def __init__(self, tmp_path, api):
        self.root = tmp_path / "consumer"
        self.root.mkdir()
        self.remote = tmp_path / "remote.git"
        home = tmp_path / "home"
        home.mkdir()
        self.env = {
            "PATH": os.environ["PATH"],
            "HOME": str(home),
            "USER": "fixture-user",
            "LOGNAME": "fixture-user",
            "LANG": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "MAIDA_DATA_DIR": str(tmp_path / "runs"),
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_WORKSPACE": str(self.root),
            "GITHUB_REPOSITORY": "fixture/consumer",
            "GITHUB_API_URL": api.url,
            "GITHUB_GRAPHQL_URL": api.url + "/graphql",
            "GITHUB_SERVER_URL": "https://github.example",
            "GITHUB_RUN_ID": "100",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_TOKEN": "fixture-token",
        }
        self.api = api
        api.consumer = self
        self.run("git", "init", "--bare", str(self.remote))
        self.run("git", "init", "--initial-branch=main")
        self.run("git", "config", "user.name", "Fixture")
        self.run("git", "config", "user.email", "fixture@example.invalid")
        for name in ("agent.py", "policy.yaml"):
            shutil.copyfile(Path(__file__).parent / "fixtures" / name, self.root / name)
        (self.root / "state.txt").write_text("good\n")
        self.run("python", "agent.py")
        self.run("maida", "baseline", "--out", "baseline.json")
        self.run("git", "add", "agent.py", "state.txt", "policy.yaml", "baseline.json")
        self.run("git", "commit", "-m", "Known good fixture")
        self.run("git", "remote", "add", "origin", str(self.remote))
        self.run("git", "switch", "-c", "candidate")
        self.run("git", "push", "origin", "candidate")
        self.update_head()
        self.base = self.api.head
        self._install_gh(tmp_path)

    def run(self, *command, check=True, env=None):
        result = subprocess.run(
            command,
            check=False,
            cwd=self.root,
            env=env or self.env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if check:
            assert result.returncode == 0, (
                f"{command}:\n{result.stdout}\n{result.stderr}"
            )
        return result

    def update_head(self):
        self.api.head = self.run("git", "rev-parse", "HEAD").stdout.strip()
        self.env["GITHUB_SHA"] = self.api.head

    def regress(self):
        (self.root / "state.txt").write_text("regressed\n")
        self.run("git", "add", "state.txt")
        self.run("git", "commit", "-m", "Regressed tool behavior")
        self.run("git", "push", "origin", "candidate")
        self.update_head()

    def _install_gh(self, tmp_path):
        # Only the transport is replaced; action.yml builds the real payload.
        directory = tmp_path / "bin"
        directory.mkdir()
        gh = directory / "gh"
        gh.write_text("""#!/usr/bin/env python3
import os, pathlib, sys, urllib.request
args = sys.argv[1:]
assert args[:3] == ["api", "--method", "POST"], args
route = args[args.index("--input") - 1]
assert route == "repos/fixture/consumer/check-runs", route
data = pathlib.Path(args[args.index("--input") + 1]).read_bytes()
request = urllib.request.Request(os.environ["GITHUB_API_URL"] + "/" + route, data=data, headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(request, timeout=5) as response:
    print(response.read().decode())
""")
        gh.chmod(0o755)
        self.env["PATH"] = str(directory) + os.pathsep + self.env["PATH"]


class Composite:
    def __init__(self, consumer, action, inputs=None):
        self.consumer = consumer
        self.path = ROOT / action
        self.action = yaml.safe_load(self.path.read_text())
        self.inputs = {
            k: str(v.get("default", "")) for k, v in self.action["inputs"].items()
        }
        self.inputs.update(inputs or {})
        self.steps = {}
        self.executed = []
        self.logs = []
        self.failed = False
        self.context = {
            "github.token": "fixture-token",
            "github.action_path": str(self.path.parent),
            "github.event.pull_request.head.sha": consumer.api.head,
            "github.sha": consumer.api.head,
            "github.server_url": "https://github.example",
            "github.repository": "fixture/consumer",
            "github.run_id": "100",
            "github.event_name": "issue_comment"
            if action.startswith("accept-command")
            else "pull_request",
        }

    def value(self, expression):
        expression = expression.strip()
        if " || " in expression:
            return next(
                (self.value(x) for x in expression.split(" || ") if self.value(x)), ""
            )
        if expression.startswith("inputs."):
            return self.inputs[expression[7:]]
        if expression.startswith("steps."):
            _, identifier, field, *tail = expression.split(".")
            step = self.steps.get(identifier, {"outputs": {}, "outcome": "skipped"})
            return step[field].get(tail[0], "") if tail else step[field]
        return self.context[expression]

    def render(self, value):
        return re.sub(r"\$\{\{(.*?)\}\}", lambda m: self.value(m[1]), str(value))

    def condition(self, expression):
        expression = expression.strip().removeprefix("${{").removesuffix("}}").strip()
        clauses = expression.split(" && ")
        for clause in clauses:
            if clause == "always()":
                continue
            match = re.fullmatch(r"(.+?) == '([^']*)'", clause)
            assert match, f"Unsupported condition: {clause}"
            if self.value(match[1]) != match[2]:
                return False
        return True

    def run(self):
        c = self.consumer
        event = {
            "repository": {"full_name": "fixture/consumer"},
            "pull_request": {
                "number": 1,
                "head": {"sha": c.api.head},
                "base": {"sha": c.base, "repo": {"full_name": "fixture/consumer"}},
            },
            "issue": {"number": 1, "pull_request": {"url": "fixture"}},
            "comment": {
                "id": 7,
                "body": "/maida accept intentional retry",
                "user": {"login": "reviewer"},
            },
        }
        event_path = c.root.parent / "event.json"
        event_path.write_text(json.dumps(event))
        env = {
            **c.env,
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_EVENT_NAME": self.context["github.event_name"],
            "GITHUB_ACTION_PATH": str(self.path.parent),
        }
        for index, step in enumerate(self.action["runs"]["steps"]):
            condition = step.get("if", "")
            if self.failed and "always()" not in condition:
                continue
            if condition and not self.condition(condition):
                continue
            identifier = step.get("id", f"step-{index}")
            self.executed.append(step["name"])
            output_path = c.root.parent / f"{self.path.parent.name}-{index}-output"
            env_path = c.root.parent / f"{self.path.parent.name}-{index}-env"
            output_path.write_text("")
            env_path.write_text("")
            step_env = {
                **env,
                "GITHUB_OUTPUT": str(output_path),
                "GITHUB_ENV": str(env_path),
                **{k: self.render(v) for k, v in step.get("env", {}).items()},
            }
            if "run" in step:
                if step["name"] == "Install Maida":
                    # Installed once from requirements-e2e.txt before testing.
                    version = c.run(
                        "python",
                        "-c",
                        "import importlib.metadata; print(importlib.metadata.version('maida-ai'))",
                    ).stdout.strip()
                    assert self.inputs["maida-version"] == "v" + version
                    continue
                assert step["shell"] == "bash"
                result = c.run(
                    "bash",
                    "--noprofile",
                    "--norc",
                    "-eo",
                    "pipefail",
                    "-c",
                    self.render(step["run"]),
                    env=step_env,
                    check=False,
                )
            else:
                result = self.external(step, step_env)
            self.logs.append(result.stdout + result.stderr)
            self.steps[identifier] = {
                "outcome": "success" if result.returncode == 0 else "failure",
                "outputs": read_commands(output_path),
            }
            env.update(read_commands(env_path))
            if result.returncode and not step.get("continue-on-error"):
                self.failed = True
        return self

    def external(self, step, env):
        uses = step["uses"]
        c = self.consumer
        if uses.startswith("astral-sh/setup-uv@"):
            return c.run("uv", "--version", env=env)
        if uses.startswith("actions/setup-python@"):
            return c.run("python", "--version", env=env)
        if uses.startswith("actions/checkout@"):
            assert self.render(step["with"]["repository"]) == "fixture/consumer"
            assert (
                self.render(step["with"]["ref"])
                == c.run("git", "rev-parse", "HEAD").stdout.strip()
            )
            return c.run("git", "status", "--short", env=env)
        assert uses == f"marocchino/sticky-pull-request-comment@{STICKY_SHA}", uses
        sticky = Path(os.environ["MAIDA_E2E_STICKY_PATH"]).resolve()
        sha = c.run("git", "-C", str(sticky), "rev-parse", "HEAD").stdout.strip()
        assert sha == STICKY_SHA, "Use the pinned sticky-comment revision"
        metadata = yaml.safe_load((sticky / "action.yml").read_text())
        values = {
            k: self.render(v.get("default", "")) for k, v in metadata["inputs"].items()
        }
        values.update({k: self.render(v) for k, v in step.get("with", {}).items()})
        env = {**env, **{f"INPUT_{k.upper()}": str(v) for k, v in values.items()}}
        return c.run(
            "node", str(sticky / metadata["runs"]["main"]), env=env, check=False
        )


def acceptance(consumer, *, agent_script="agent.py"):
    """Exercise separate jobs; only artifact bytes cross from candidate to writer."""
    prepare = Composite(
        consumer,
        "accept-command/action.yml",
        {
            "stage": "authorize",
            "baseline": "baseline.json",
            "policy": "policy.yaml",
            "github-token": "fixture-token",
        },
    ).run()
    if (
        prepare.failed
        or prepare.steps["prepare"]["outputs"].get("authorized") != "true"
    ):
        return prepare
    context = prepare.steps["prepare"]["outputs"]["context"]
    # Candidate execution has a separate checkout, HOME and runner command files.
    candidate = copy.copy(consumer)
    candidate.root = consumer.root.parent / "capture"
    consumer.run(
        "git",
        "clone",
        "--branch",
        "candidate",
        str(consumer.remote),
        str(candidate.root),
    )
    candidate.env = {k: v for k, v in consumer.env.items() if k != "GITHUB_TOKEN"}
    capture_home = consumer.root.parent / "capture-home"
    capture_home.mkdir()
    candidate.env.update(HOME=str(capture_home), GITHUB_WORKSPACE=str(candidate.root))
    artifact = consumer.root.parent / "captured-artifact"
    capture = Composite(
        candidate,
        "capture-acceptance/action.yml",
        {
            "context": context,
            "agent-script": agent_script,
            "artifact-directory": str(artifact),
        },
    ).run()
    if capture.failed:
        return capture
    writer = copy.copy(consumer)
    writer.root = consumer.root.parent / "trusted-writer"
    writer.root.mkdir()
    writer_home = consumer.root.parent / "writer-home"
    writer_home.mkdir()
    writer.env = {
        **consumer.env,
        "HOME": str(writer_home),
        "GITHUB_WORKSPACE": str(writer.root),
    }
    downloaded = writer.root / "artifact"
    shutil.copytree(artifact, downloaded)
    result = Composite(
        writer,
        "write-back/action.yml",
        {
            "context": context,
            "artifact-directory": str(downloaded),
            "github-token": "fixture-token",
        },
    ).run()
    result.logs = prepare.logs + capture.logs + result.logs
    return result
