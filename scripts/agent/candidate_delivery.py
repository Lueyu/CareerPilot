"""Build and verify retained loopback previews of trusted candidate worktrees."""

import argparse
import hashlib
import http.client
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

PROJECTS = {
    "careerpilot": ("docker-compose.yml", "web", 80, "/healthz", "CareerPilot"),
    "squabble": ("docker-compose.demo.yml", "app", 3000, "/api/health", "Squabble"),
}
OWNER = "local.candidate.delivery"
RECEIPT = "candidate-delivery.json"
SYSTEM_ENV = {
    "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "SYSTEMDRIVE",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA", "NUMBER_OF_PROCESSORS",
}
NGINX = """server {
    listen 80;
    root /usr/share/nginx/html;
    location / { try_files $uri $uri/ /index.html; }
    location /api/ {
        proxy_pass http://server:8000;
        proxy_buffering off;
        proxy_read_timeout 300s;
    }
    location = /healthz { proxy_pass http://server:8000; }
}
"""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def timestamp():
    return datetime.now(UTC).isoformat()


def save(path, value):
    temporary = path.with_suffix(".pending")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def attempt_path(run_dir, receipt):
    name = receipt.get("attempt", "candidate-preview")
    if not re.fullmatch(r"candidate-preview(?:-[0-9a-f]{32})?", name):
        raise ValueError("Invalid preview attempt directory.")
    path = run_dir / name
    if path.resolve() != path:
        raise ValueError("The preview attempt directory was redirected.")
    return path


def environment(private=None):
    env = {key: value for key, value in os.environ.items() if key.upper() in SYSTEM_ENV}
    if private is not None:
        for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TMP", "TEMP", "DOCKER_CONFIG"):
            env[key] = str(private)
    return env


def output(args, *, cwd=None, env=None, input_bytes=None):
    result = subprocess.run(args, check=False, cwd=cwd, env=env, input=input_bytes, capture_output=True, timeout=120)
    if result.returncode:
        raise ValueError(f"{args[0]} {args[1]} failed: {result.stderr.decode('utf-8', 'replace').strip()}")
    return result.stdout


def git(tree, *args):
    return output(["git", *args], cwd=tree, env=environment())


def candidate(run_dir, tree, base, patch_sha, project, *, starting=False):
    if project not in PROJECTS or not re.fullmatch(r"[0-9a-f]{40}", base):
        raise ValueError("Expected a supported project and full Git base SHA.")
    patch = (run_dir / "change.patch").read_bytes()
    if not re.fullmatch(r"[0-9a-f]{64}", patch_sha) or digest(patch) != patch_sha:
        raise ValueError("change.patch does not match the supplied SHA-256.")
    if Path(os.fsdecode(git(tree, "rev-parse", "--show-toplevel").rstrip())).resolve() != tree:
        raise ValueError("Workspace must be the root of the candidate worktree.")
    head = git(tree, "rev-parse", "HEAD").decode().strip()
    if head != base and (starting or git(tree, "rev-parse", "HEAD^").decode().strip() != base):
        raise ValueError("The worktree must be at the base or the published candidate child.")
    if git(tree, "ls-files", "--others", "--exclude-standard", "-z"):
        raise ValueError("Add new candidate files with git add --intent-to-add before checkpointing.")
    if git(tree, "diff", "--no-ext-diff", "--binary", base) != patch:
        raise ValueError("The worktree differs from change.patch.")
    paths = git(tree, "diff", "--name-only", "-z", base).split(b"\0")
    if any(
        part.startswith(b".env") and not part.endswith(b".example")
        for path in paths for part in path.lower().split(b"/")
    ):
        raise ValueError("Candidate patches must not contain private environment files.")
    ui_prefix = b"web/" if project == "careerpilot" else b"client/"
    ui_required = any(path.startswith(ui_prefix) for path in paths)
    binding = {"workspace": str(tree), "base": base, "patch_sha256": patch_sha, "project": project}
    return binding, patch, ui_required


def docker_host():
    endpoint = output(["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"]).decode().strip()
    if not endpoint.startswith(("npipe://", "unix://")):
        raise ValueError("Select a local Docker context before starting a preview.")
    return endpoint


def docker(receipt):
    endpoint = receipt["docker_host"]
    if not endpoint.startswith(("npipe://", "unix://")):
        raise ValueError("Preview receipt does not identify a local Docker endpoint.")
    return ["docker", "--host", endpoint]


def compose(run, receipt):
    return docker(receipt) + [
        "compose", "--env-file", str(run / "empty.env"), "--project-directory", str(run / "source"),
        "-p", receipt["owner"], "-f", str(run / "compose.json"),
    ]


def configure(run, receipt, trusted_compose):
    source = run / "source"
    source_config = run / "trusted-compose.yml"
    source_config.write_bytes(trusted_compose)
    (run / "empty.env").touch()
    env = environment(run / "home")
    if receipt["project"] == "careerpilot":
        # Owned attempt only; no personal .env or role is read or changed.
        output([sys.executable, "personal_credentials.py", "init", "--output", str(run / "preview.env")],
               cwd=source / "server", env=env)
        env.update(preview_credentials(run))
    config = json.loads(output(
        compose(run, receipt)[:-1] + [str(source_config), "config", "--format", "json"], env=env,
    ))
    project = receipt["project"]
    _, web_name, port, _, _ = PROJECTS[project]
    database = "postgres" if project == "careerpilot" else "db"
    if project == "careerpilot":
        credentials = preview_credentials(run)
        backend_credentials = {
            "API_TOKEN": credentials["API_TOKEN"],
            "CAREERPILOT_SECRETS_KEY": credentials["CAREERPILOT_SECRETS_KEY"],
            "DATABASE_URL": f"postgresql+asyncpg://careerpilot:{credentials['POSTGRES_PASSWORD']}@postgres:5432/careerpilot",
        }
        for name in ("server", "worker"):
            config["services"][name].setdefault("environment", {}).update(backend_credentials)
        postgres = config["services"]["postgres"]
        postgres.setdefault("environment", {})["POSTGRES_PASSWORD"] = credentials["POSTGRES_PASSWORD"]
        server = config["services"]["server"]
        config["services"]["preflight"] = {
            "build": server["build"],
            "environment": dict(server["environment"]),
            "command": ["python", "personal_credentials.py", "check"],
        }
        postgres.setdefault("depends_on", {})["preflight"] = {
            "condition": "service_completed_successfully", "required": True,
        }
    for service in config["services"].values():
        service.pop("container_name", None)
        service.pop("ports", None)
        service.pop("restart", None)
        service["labels"] = {OWNER: receipt["owner"]}
    for name, target in ((web_name, port), (database, 5432)):
        config["services"][name]["ports"] = [{"host_ip": "127.0.0.1", "target": target, "protocol": "tcp"}]
    if project == "careerpilot":
        web = config["services"]["web"]
        dockerfile = (source / "web/Dockerfile").read_text(encoding="utf-8")
        before, marker, after = dockerfile.partition("RUN npm run build")
        if not marker:
            raise ValueError("CareerPilot web Dockerfile needs an updated preview adapter.")
        web["build"].pop("dockerfile", None)
        web["build"]["dockerfile_inline"] = (
            before + "ENV VITE_API_BASE=/api/v1 VITE_WORKSPACE=demo\n" + marker + after
        )
        nginx = run / "preview-nginx.conf"
        nginx.write_text(NGINX, encoding="utf-8")
        web["volumes"] = [{"type": "bind", "source": str(nginx),
                           "target": "/etc/nginx/conf.d/default.conf", "read_only": True}]
        server = config["services"]["server"]
        dockerfile = (source / "server/Dockerfile").read_text(encoding="utf-8")
        commands = [line.removeprefix("CMD ") for line in dockerfile.splitlines() if line.startswith("CMD ")]
        if server.get("command") is not None or len(commands) != 1 or "python -m app.seed &&" not in commands[0]:
            raise ValueError("CareerPilot API startup needs an updated preview adapter.")
        startup_command = json.loads(commands[0])
        startup_command[-1] = startup_command[-1].replace(
            "python -m app.seed &&",
            'python -m app.seed --demo && python provision_recovery_control.py --confirm "PROVISION KNOWN INSTALLATION careerpilot" &&',
        )
        server["command"] = startup_command
    else:
        config["services"]["app"]["environment"].update(
            PAPER_TRADING_ENABLED="false", SQUABBLE_UPDATE_MODE="off",
        )
    if project == "careerpilot":
        output([sys.executable, "personal_credentials.py", "create-private-file", "--output", str(run / "compose.json")],
               cwd=source / "server", env=env)
        (run / "compose.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        save(run / "compose.json", config)


def preview_credentials(run):
    """Read the init CLI's fixed generated format; never place secrets in receipts."""
    values = dict(line.split("=", 1) for line in (run / "preview.env").read_text(encoding="utf-8").splitlines())
    return {key: values[key] for key in ("API_TOKEN", "POSTGRES_PASSWORD", "CAREERPILOT_SECRETS_KEY")}


def containers(run, receipt):
    owner = receipt["owner"]
    if not re.fullmatch(r"candidate-[0-9a-f]{32}", owner):
        raise ValueError("Invalid preview owner.")
    env = environment(run / "home")
    command = docker(receipt)
    ids = output(command + ["ps", "-aq", "--no-trunc", "--filter", f"label={OWNER}={owner}"], env=env).decode().split()
    if not ids:
        return []
    result = []
    for item in json.loads(output(command + ["inspect", *ids], env=env)):
        labels = item["Config"]["Labels"]
        if labels.get(OWNER) != owner or labels.get("com.docker.compose.project") != owner:
            raise ValueError("Container ownership mismatch.")
        if Path(labels["com.docker.compose.project.config_files"]).resolve() != run / "compose.json":
            raise ValueError("Container belongs to a different deployment directory.")
        result.append(item)
    return result


def mapped_port(container, target):
    ports = container["NetworkSettings"]["Ports"].get(f"{target}/tcp")
    if not ports or len(ports) != 1 or ports[0]["HostIp"] != "127.0.0.1":
        raise ValueError("Expected exactly one loopback port binding.")
    return int(ports[0]["HostPort"])


def request(port, path, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", path, headers=headers or {})
        response = conn.getresponse()
        limit = 32 * 1024 * 1024
        body = response.read(limit + 1)
        if response.status != 200 or not body or len(body) > limit:
            raise ValueError(f"Preview HTTP {path} returned {response.status}.")
        return response.getheader("Content-Type", ""), body
    finally:
        conn.close()


def application_check(run, receipt):
    project = receipt["project"]
    _, service, target, health_path, title = PROJECTS[project]
    owned = containers(run, receipt)
    resources = {item["Config"]["Labels"]["com.docker.compose.service"]: item for item in owned}
    required = {"preflight", "postgres", "server", "worker", "web"} if project == "careerpilot" else {"db", "app"}
    if len(owned) != len(required) or resources.keys() != required or not all(
        item["State"]["Running"] for name, item in resources.items() if name != "preflight"
    ) or (project == "careerpilot" and (resources["preflight"]["State"]["Running"]
                                      or resources["preflight"]["State"].get("ExitCode") != 0)):
        raise ValueError("The candidate's application containers are not all running.")
    port = mapped_port(resources[service], target)
    content_type, page = request(port, "/")
    script = re.search(rb'<script\b[^>]*\bsrc="(/[A-Za-z0-9_./-]+\.js)"', page)
    if "text/html" not in content_type or title.encode() not in page or not script:
        raise ValueError("The preview did not serve its expected product HTML and JS entry point.")
    asset = script[1].decode()
    asset_type, asset_body = request(port, asset)
    if "javascript" not in asset_type:
        raise ValueError("The preview did not serve its JavaScript bundle.")
    health = json.loads(request(port, health_path)[1])
    workspace = None
    if project == "careerpilot":
        if health.get("ok") is not True or health.get("service") != "careerpilot-api":
            raise ValueError("CareerPilot API readiness failed.")
        headers = {"X-Workspace": "demo", "X-Api-Token": preview_credentials(run)["API_TOKEN"]}
        dashboard = json.loads(request(port, "/api/v1/dashboard", headers)[1])
        workspace = dashboard.get("workspace", {})
        if workspace.get("slug") != "demo" or workspace.get("is_demo") is not True:
            raise ValueError("CareerPilot did not reach the seeded preview workspace.")
        workspace = workspace["slug"]
    elif health.get("status") != "ok":
        raise ValueError("Squabble database readiness failed.")
    return {
        "preview_url": f"http://127.0.0.1:{port}/",
        "health_path": health_path, "health": health, "workspace": workspace, "asset": asset,
        "html_sha256": digest(page), "asset_sha256": digest(asset_body),
        "containers": {name: {"id": item["Id"], "image": item["Image"]}
                       for name, item in resources.items()},
    }


def run_check(run, name, argv, cwd, env):
    log = run / f"{name}.log"
    with log.open("xb") as stream:
        result = subprocess.run(argv, check=False, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=900)
    body = log.read_bytes()
    evidence = {"argv": argv, "exit_code": result.returncode, "log": log.name, "sha256": digest(body)}
    if result.returncode or not body.strip() or re.search(
        r"(?mi)^(?:#|ℹ) (?:pass|tests) 0\s*$|^(?:=+\s*)?(?:\d+ skipped|no tests ran) in ",
        body.decode("utf-8", "replace"),
    ):
        raise ValueError(f"{name} did not pass with executed assertions. See {log}.")
    return evidence


def start(run_dir, workspace, base, patch_sha, project, regression, ui=None):
    run_dir, workspace = run_dir.resolve(), workspace.resolve()
    binding, patch, ui_required = candidate(run_dir, workspace, base, patch_sha, project, starting=True)
    if ui_required and not ui:
        raise ValueError("Client changes require a candidate browser command via --ui.")
    record = run_dir / RECEIPT
    if record.exists():
        previous = read(record)
        if not previous.get("stopped_at"):
            raise ValueError("Stop the previous deployment before starting another attempt.")
        save(attempt_path(run_dir, previous) / RECEIPT, previous)
    endpoint = docker_host()
    while True:
        token = uuid4().hex
        receipt = dict(binding, attempt=f"candidate-preview-{token}", owner=f"candidate-{token}",
                       docker_host=endpoint, status="starting")
        run = attempt_path(run_dir, receipt)
        try:
            run.mkdir()
            break
        except FileExistsError:
            continue
    (run / "source").mkdir()
    (run / "home").mkdir()
    save(record, receipt)
    try:
        filename = PROJECTS[project][0]
        trusted_compose = git(workspace, "show", f"{base}:{filename}")
        archive = git(workspace, "archive", "--format=tar", base)
        source = run / "source"
        with tarfile.open(fileobj=io.BytesIO(archive)) as snapshot:
            for member in snapshot.getmembers():
                if not (member.isfile() or member.isdir()):
                    raise ValueError(f"Unsupported snapshot member: {member.name}")
                if any(part.startswith(".env") and not part.endswith(".example")
                       for part in Path(member.name.lower()).parts):
                    raise ValueError(f"Private environment file in the tracked snapshot: {member.name}")
            snapshot.extractall(source, filter="data")
        if patch:
            outside_git = environment()
            outside_git["GIT_CEILING_DIRECTORIES"] = str(run)
            output(["git", "-c", "core.autocrlf=false", "apply", "--binary", "--whitespace=nowarn", "-"],
                   cwd=source, env=outside_git, input_bytes=patch)
        configure(run, receipt, trusted_compose)
        env = environment(run / "home")
        with (run / "startup.log").open("xb") as stream:
            result = subprocess.run(compose(run, receipt) + ["up", "-d", "--build", "--wait", "--wait-timeout", "240"],
                                    check=False, cwd=source, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=1800)
        if result.returncode:
            raise ValueError(f"Compose startup failed. See {run / 'startup.log'}.")
        if project == "careerpilot":
            # This newly owned attempt was provisioned above. Ordinary runtime
            # startup and receipt verification never execute enrollment.
            enrollment = output(compose(run, receipt) + [
                "exec", "-T", "server", "python", "token_lifecycle.py", "enroll-env", "--confirm", "ENROLL careerpilot",
            ], cwd=source, env=env)
            (run / "enrollment.log").write_bytes(enrollment)
            receipt["credential_enrollment"] = json.loads(enrollment)
        deadline = time.monotonic() + 120
        while True:
            try:
                app = application_check(run, receipt)
                break
            except (ValueError, OSError) as error:
                if time.monotonic() >= deadline:
                    raise ValueError(f"Candidate HTTP check did not become ready: {error}") from error
                time.sleep(2)
        receipt["preview_url"] = app["preview_url"]
        receipt["application"] = app
        save(record, receipt)
        db_name = "postgres" if project == "careerpilot" else "db"
        database = next(item for item in containers(run, receipt) if item["Id"] == app["containers"][db_name]["id"])
        db_port = mapped_port(database, 5432)
        credentials = preview_credentials(run) if project == "careerpilot" else {}
        db_url = (f"postgresql+asyncpg://careerpilot:{credentials['POSTGRES_PASSWORD']}@127.0.0.1:{db_port}/careerpilot"
                  if project == "careerpilot" else f"postgresql://postgres:postgres@127.0.0.1:{db_port}/portfolio")
        if project == "careerpilot":
            env["PYTHONPATH"] = str(source / "server")
            env.update(credentials)
            env["CANDIDATE_API_TOKEN"] = credentials["API_TOKEN"]
        env.update(CANDIDATE_PREVIEW_URL=app["preview_url"], CANDIDATE_DATABASE_URL=db_url, DATABASE_URL=db_url)
        receipt["regression"] = run_check(run, "regression", regression, source, env)
        if ui:
            receipt["ui"] = run_check(run, "ui", ui, source, env)
        candidate(run_dir, workspace, base, patch_sha, project)
        if application_check(run, receipt) != app:
            raise ValueError("The application changed during acceptance.")
        receipt.update(status="passed", accepted_at=timestamp())
        save(record, receipt)
        return receipt
    except Exception as error:
        receipt.update(status="failed", error=str(error))
        save(record, receipt)
        raise


def verify_delivery(run_dir: Path, workspace: Path, base: str, patch_sha: str, project: str) -> dict:
    """Read the prior candidate receipt and verify the exact application is still reachable."""
    run_dir, workspace = run_dir.resolve(), workspace.resolve()
    try:
        binding, _, ui_required = candidate(run_dir, workspace, base, patch_sha, project)
        receipt = read(run_dir / RECEIPT)
        if receipt.get("status") != "passed" or receipt.get("stopped_at") or any(receipt.get(key) != val for key, val in binding.items()):
            raise ValueError("No passed delivery receipt for this candidate.")
        run = attempt_path(run_dir, receipt)
        checks = ["regression"]
        if ui_required or "ui" in receipt:
            checks.append("ui")
        for name in checks:
            proof = receipt[name]
            if (proof["log"] != f"{name}.log" or type(proof["exit_code"]) is not int
                    or proof["exit_code"] != 0 or not proof["argv"]
                    or proof["sha256"] != digest((run / proof["log"]).read_bytes())):
                raise ValueError(f"Invalid or changed {name} evidence.")
        current = application_check(run, receipt)
        if current != receipt["application"] or current["preview_url"] != receipt["preview_url"]:
            raise ValueError("The running application differs from the accepted preview.")
        return receipt
    except (KeyError, OSError, TypeError, subprocess.SubprocessError) as error:
        raise ValueError(f"Cannot verify candidate delivery: {error}") from error


def stop(run_dir):
    run_dir = run_dir.resolve()
    record = run_dir / RECEIPT
    if not run_dir.is_dir():
        return {"status": "absent"}
    with lifecycle_lock(run_dir):
        if not record.exists():
            return {"status": "absent"}
        receipt = read(record)
        run = attempt_path(run_dir, receipt)
        active = [item["Id"] for item in containers(run, receipt) if item["State"]["Running"]]
        if active:
            output(docker(receipt) + ["stop", *active], env=environment(run / "home"))
        if "stopped_at" not in receipt:
            receipt["stopped_at"] = timestamp()
            save(record, receipt)
    return {"status": "stopped", "stopped_at": receipt["stopped_at"]}


@contextmanager
def lifecycle_lock(run_dir):
    """Serialize lifecycle changes and release the lock on process exit."""
    with (run_dir / "candidate-preview.lock").open("a+b") as stream:
        try:
            if os.name == "nt":
                import msvcrt
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError(f"Preview operation in progress for {run_dir}.") from error
        yield


def argv_json(value):
    args = json.loads(value)
    if not isinstance(args, list) or not args or any(not isinstance(arg, str) or not arg for arg in args):
        raise argparse.ArgumentTypeError("Expected a nonempty JSON array of command arguments.")
    return args


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    for action in ("start", "check", "stop"):
        options = actions.add_parser(action)
        options.add_argument("--run-dir", type=Path, required=True)
        if action != "stop":
            options.add_argument("--workspace", type=Path, required=True)
            options.add_argument("--base", required=True)
            options.add_argument("--patch-sha", required=True)
            options.add_argument("--project", choices=PROJECTS, required=True)
        if action == "start":
            options.add_argument("--regression", type=argv_json, required=True)
            options.add_argument("--ui", type=argv_json)
    args = parser.parse_args()
    try:
        if args.action == "stop":
            receipt = stop(args.run_dir)
        else:
            identity = (args.run_dir, args.workspace, args.base, args.patch_sha, args.project)
            if args.action == "start":
                with lifecycle_lock(args.run_dir.resolve()):
                    receipt = start(*identity, args.regression, args.ui)
            else:
                receipt = verify_delivery(*identity)
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(receipt, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
