"""Execute composite shell steps, mocking only GitHub and runner setup.

This is a deliberately small runner for these Actions, not an Actions emulator.
Unknown expressions, step types, API routes, and checkout targets fail loudly.
All Git mutations are confined to disposable local repositories.
"""

import base64
import copy
import json
import os
import re
import runpy
import shutil
import subprocess
import tempfile
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
        self.reject_dispatches = False
        self.reject_statuses = False
        self.statuses = []

    def request(self, method, path, body):
        self.requests.append((method, path, body))
        if (
            method == "GET"
            and path == "/repos/fixture/consumer/collaborators/reviewer/permission"
        ):
            return 200, {"permission": self.permission}
        if method == "GET" and path == "/repos/fixture/consumer/pulls/1":
            return 200, {
                "number": 1, "state": "open",
                "base": {"sha": self.consumer.base, "ref": "main", "repo": {"full_name": "fixture/consumer"}},
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
        if method == "POST" and "/statuses/" in path:
            if self.reject_statuses:
                return 503, {"message": "Status publication unavailable"}
            self.statuses.append({"sha": path.rsplit("/", 1)[-1], **body})
            return 201, body
        if method == "POST" and path == "/repos/fixture/consumer/dispatches":
            if self.reject_dispatches:
                return 503, {"message": "Dispatch unavailable"}
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
                check=False,
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
    def __init__(self, consumer, action, inputs=None, *, event=None, event_name=None):
        self.consumer = consumer
        self.path = ROOT / action
        self.action = yaml.safe_load(self.path.read_text())
        self.inputs = {
            k: str(v.get("default", "")) for k, v in self.action.get("inputs", {}).items()
        }
        self.inputs.update(inputs or {})
        self.steps = {}
        self.executed = []
        self.logs = []
        self.failed = False
        self.event = event
        self.command_dir = Path(tempfile.mkdtemp(dir=consumer.root.parent))
        self.context = {
            "github.token": "fixture-token",
            "github.action_path": str(self.path.parent),
            "github.event.pull_request.head.sha": (
                event.get("pull_request", {}).get("head", {}).get("sha", "")
                if event is not None else consumer.api.head
            ),
            "github.sha": consumer.env["GITHUB_SHA"],
            "github.server_url": "https://github.example",
            "github.repository": "fixture/consumer",
            "github.run_id": "100",
            "github.event_name": "issue_comment"
            if action.startswith("accept-command")
            else "pull_request",
        }
        if event_name:
            self.context["github.event_name"] = event_name

    def value(self, expression):
        expression = expression.strip()
        if expression in {"true", "false"}:
            return expression
        if " != " in expression or " == " in expression:
            return str(self.condition(expression)).lower()
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
            if " || " in clause:
                if not any(self.condition(part) for part in clause.split(" || ")):
                    return False
                continue
            match = re.fullmatch(r"startsWith\((.+?), '([^']*)'\)", clause)
            if match:
                if not self.value(match[1]).startswith(match[2]):
                    return False
                continue
            match = re.fullmatch(r"(.+?) (==|!=) '([^']*)'", clause)
            if match:
                equal = self.value(match[1]) == match[3]
                if equal != (match[2] == "=="):
                    return False
            elif not self.value(clause):
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
        event = self.event or event
        event_path = self.command_dir / "event.json"
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
            self.executed.append(step.get("name", identifier))
            output_path = self.command_dir / f"{index}-output"
            env_path = self.command_dir / f"{index}-env"
            output_path.write_text("")
            env_path.write_text("")
            step_env = {
                **env,
                "GITHUB_OUTPUT": str(output_path),
                "GITHUB_ENV": str(env_path),
                **{k: self.render(v) for k, v in step.get("env", {}).items()},
            }
            if "run" in step:
                if step.get("name") == "Install Maida":
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
        self.outputs = {name: self.render(value["value"]) for name, value in self.action.get("outputs", {}).items()}
        return self

    def external(self, step, env):
        uses = step["uses"]
        c = self.consumer
        if uses.startswith("maida-ai/maida-assert"):
            subpath = uses.split("@", 1)[0].removeprefix("maida-ai/maida-assert").strip("/")
            child = Composite(c, str(Path(subpath) / "action.yml"),
                {k: self.render(v) for k, v in step.get("with", {}).items()},
                event=self.event, event_name=self.context["github.event_name"]).run()
            with open(env["GITHUB_OUTPUT"], "a") as output:
                for key, value in child.outputs.items():
                    if "\n" in value:
                        output.write(f"{key}<<END\n{value}\nEND\n")
                    else:
                        output.write(f"{key}={value}\n")
            self.children[step.get("id", subpath)] = child
            return subprocess.CompletedProcess([], int(child.failed), "\n".join(child.logs), "")
        if uses.startswith("actions/upload-artifact@"):
            self.artifacts[self.render(step["with"]["name"])] = Path(self.render(step["with"]["path"])).read_bytes()
            return subprocess.CompletedProcess([], 0, "", "")
        if uses.startswith("actions/download-artifact@"):
            target = Path(self.render(step["with"]["path"]))
            target.mkdir(parents=True, exist_ok=True)
            (target / "acceptance.json").write_bytes(self.artifacts[self.render(step["with"]["name"])])
            return subprocess.CompletedProcess([], 0, "", "")
        if uses.startswith("astral-sh/setup-uv@"):
            return c.run("uv", "--version", env=env)
        if uses.startswith("actions/setup-python@"):
            return c.run("python", "--version", env=env)
        if uses.startswith("actions/checkout@") and hasattr(self, "children"):
            assert step["with"]["persist-credentials"] is False
            c.run("git", "clone", str(c.remote), ".")
            c.run("git", "checkout", self.render(step["with"]["ref"]))
            return subprocess.CompletedProcess([], 0, "", "")
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


class Workflow:
    """Run the generated event/jobs/steps with separate disposable runner directories."""
    def __init__(self, consumer, event_name, event, *, acceptance_value=""):
        self.consumer = consumer
        source = Path(os.environ.get("MAIDA_E2E_SCAFFOLD_PATH", ROOT.parent / "maida/maida/scaffold.py"))
        scaffold = runpy.run_path(str(source))
        generated = consumer.root.parent / "generated-maida.yml"
        scaffold["write_scaffold"](generated, scaffold["WORKFLOW_TEMPLATE"], force=True)
        self.workflow = yaml.safe_load(generated.read_text())
        self.event_name, self.event = event_name, event
        self.acceptance_value = acceptance_value
        self.jobs, self.artifacts = {}, {}

    def run(self):
        triggers = self.workflow.get("on", self.workflow.get(True))
        assert self.event_name in triggers
        if self.event_name == "repository_dispatch":
            assert self.event["action"] in triggers[self.event_name]["types"]
        root = Path(tempfile.mkdtemp(dir=self.consumer.root.parent, prefix="workflow-"))
        for name, definition in self.workflow["jobs"].items():
            c = copy.copy(self.consumer)
            c.root = root / name / "workspace"
            c.root.mkdir(parents=True)
            home = c.root.parent / "home"
            home.mkdir()
            c.env = {**c.env, "HOME": str(home), "GITHUB_WORKSPACE": str(c.root),
                     "RUNNER_TEMP": str(c.root.parent), "MAIDA_DATA_DIR": str(c.root.parent / "runs"),
                     "GITHUB_SHA": self.consumer.base if self.event_name == "repository_dispatch" else self.consumer.api.head}
            if name == "capture":
                c.env.pop("GITHUB_TOKEN", None)
            job = Composite(c, "action.yml", event=self.event, event_name=self.event_name)
            job.action = {"runs": {"steps": definition["steps"]}}
            job.children, job.artifacts = {}, self.artifacts
            job.context.update({
                "env.MAIDA_AGENT_SCRIPT": "agent.py", "env.MAIDA_POLICY": "policy.yaml",
                "env.MAIDA_BASELINE": "baseline.json", "vars.MAIDA_CONFIGURATION_ACCEPTANCE": self.acceptance_value,
                "runner.temp": str(c.root.parent), "github.run_attempt": "1",
                "github.event.issue.pull_request": self.event.get("issue", {}).get("pull_request", {}),
                "github.event.comment.body": self.event.get("comment", {}).get("body", ""),
                "github.sha": c.env["GITHUB_SHA"],
            })
            for previous, result in self.jobs.items():
                job.context[f"needs.{previous}.result"] = result["result"]
                for key in ("authorized", "context", "head-sha"):
                    job.context[f"needs.{previous}.outputs.{key}"] = result["outputs"].get(key, "")
            needs = definition.get("needs", [])
            needs = [needs] if isinstance(needs, str) else needs
            condition = definition.get("if", "")
            eligible = "always()" in condition or all(self.jobs[n]["result"] == "success" for n in needs)
            if not eligible or (condition and not job.condition(condition)):
                self.jobs[name] = {"result": "skipped", "outputs": {}}
                continue
            job.run()
            outputs = {k: job.render(v) for k, v in definition.get("outputs", {}).items()}
            self.jobs[name] = {"result": "failure" if job.failed else "success", "outputs": outputs, "runner": job}
        return self
