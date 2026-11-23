"""Provision a private native API and worker for an authenticated account."""

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
from threading import Lock
import time
from urllib.request import ProxyHandler, Request, build_opener

from .relay import LoopbackRelay


IMAGE = "careerpilot-tenant-native:parity-20261124"
DATABASE_IMAGE = "postgres:16-alpine"
HTTP = build_opener(ProxyHandler({}))
TENANT_NETWORKS = ("private", "repo_egress", "egress")


@dataclass(frozen=True)
class Tenant:
    base_url: str
    workspace_slug: str
    api_token: str = field(repr=False)
    database_container: str

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "X-Workspace": self.workspace_slug,
        }

    @property
    def psql_args(self) -> tuple[str, ...]:
        return (
            "docker", "exec", "-i", self.database_container,
            "psql", "-X", "-v", "ON_ERROR_STOP=1", "-U", "careerpilot", "-d", "careerpilot",
        )


class TenantRuntime:
    """One per trusted gateway process. Call ensure only after account verification."""

    def __init__(self, secret: str | None = None):
        secret = os.environ.get("TENANT_RUNTIME_SECRET", "") if secret is None else secret
        if len(secret.encode()) < 32:
            raise RuntimeError("Set a stable random TENANT_RUNTIME_SECRET in the trusted gateway")
        endpoint = os.environ.get("DOCKER_HOST")
        if not endpoint or os.environ.get("DOCKER_CONTEXT"):
            endpoint = self._docker("context", "inspect", "--format", "{{.Endpoints.docker.Host}}")
        if not endpoint.startswith(("unix:///", "npipe:////./pipe/")):
            raise RuntimeError("Tenant provisioning requires a local Docker engine")
        self._secret = secret.encode()
        configured_state = os.environ.get("TENANT_RUNTIME_STATE_DIR")
        state_base = Path(configured_state) if configured_state else Path(tempfile.gettempdir())
        if configured_state and not state_base.is_absolute():
            raise RuntimeError("TENANT_RUNTIME_STATE_DIR must be an absolute path")
        self._state = state_base / "careerpilot-parity-tenant-locks"
        self._state.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._guard = Lock()
        self._relays: dict[str, LoopbackRelay] = {}
        self._ready: dict[str, tuple[Tenant, float]] = {}
        self._database_relays: dict[str, set[LoopbackRelay]] = {}

    @staticmethod
    def _docker(*args: str, input_data: str | None = None) -> str:
        result = subprocess.run(
            ["docker", *args], input=input_data, capture_output=True, encoding="utf-8",
            errors="replace", timeout=180, check=False,
        )
        if result.returncode:
            # Compose input contains credentials. Avoid echoing Docker diagnostics to the gateway.
            raise RuntimeError(f"Docker operation {args[0]} failed (exit {result.returncode})")
        return result.stdout.strip()

    def _credential(self, purpose: str, slug: str) -> str:
        return hmac.new(self._secret, f"{purpose}:{slug}".encode(), "sha256").hexdigest()

    @contextmanager
    def _provision_lock(self, project: str):
        connection = sqlite3.connect(self._state / f"{project}.sqlite", timeout=180)
        try:
            connection.execute("BEGIN EXCLUSIVE")
            yield
        finally:
            connection.close()

    @staticmethod
    def _project(workspace_slug: str) -> str:
        if not isinstance(workspace_slug, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", workspace_slug):
            raise ValueError("Use the verified, stable lowercase account slug")
        if workspace_slug in {"personal", "demo", "lueyu", "owner"}:
            raise ValueError("The owner and shared workspaces cannot be provisioned as tenants")

        return "cp-parity-" + hashlib.sha256(workspace_slug.encode()).hexdigest()[:24]

    def ensure(self, workspace_slug: str, display_role: str = "") -> Tenant:
        project = self._project(workspace_slug)
        with self._guard:
            ready = self._ready.get(project)
            if ready and time.monotonic() < ready[1]:
                return ready[0]
        with self._provision_lock(project):
            with self._guard:
                ready = self._ready.get(project)
                if ready and time.monotonic() < ready[1]:
                    return ready[0]

            if ready:
                try:
                    self._check_workspace(ready[0])
                except (OSError, RuntimeError):
                    pass
                else:
                    with self._guard:
                        self._ready[project] = (ready[0], time.monotonic() + 120)
                    return ready[0]

            token = self._credential("api", workspace_slug)
            password = self._credential("database", workspace_slug)
            upstream = _model_upstream()
            with self._provision_lock("network-allocation"):
                networks = self._network_subnets(project, with_model=bool(upstream))
                compose = _compose(
                    workspace_slug, display_role, token, password, networks, upstream,
                    self._credential("model-relay", workspace_slug),
                    self._credential("repo-relay", workspace_slug),
                )
                self._docker(
                    "compose", "--project-name", project, "-f", "-", "create",
                    "--pull", "never",
                    input_data=json.dumps(compose),
                )
            self._docker(
                "compose", "--project-name", project, "-f", "-", "up", "-d",
                "--pull", "never", "--remove-orphans", "--wait", "--wait-timeout", "120",
                input_data=json.dumps(compose),
            )
            with self._guard:
                relay = self._relays.get(project)
                if relay is None:
                    relay = LoopbackRelay(f"{project}-api-1", "127.0.0.1", 8000)
                    self._relays[project] = relay
            tenant = Tenant(
                base_url=f"http://127.0.0.1:{relay.port}",
                workspace_slug=workspace_slug,
                api_token=token,
                database_container=f"{project}-postgres-1",
            )
            self._check_workspace(tenant)
            with self._guard:
                self._ready[project] = (tenant, time.monotonic() + 120)
            return tenant

    @staticmethod
    def _check_workspace(tenant: Tenant) -> None:
        request = Request(f"{tenant.base_url}/api/v1/settings/workspaces", headers=tenant.headers)
        with HTTP.open(request, timeout=25) as response:
            result = json.load(response)
        if result["default_slug"] != tenant.workspace_slug or [
            workspace["slug"] for workspace in result["workspaces"]
        ] != [tenant.workspace_slug]:
            raise RuntimeError("Unexpected workspace in tenant API")

    def _network_subnets(self, project: str, with_model: bool) -> dict[str, str]:
        ids = self._docker("network", "ls", "--quiet").splitlines()
        networks = json.loads(self._docker("network", "inspect", *ids))
        occupied = []
        assigned = {}
        project_networks = {f"{project}_{name}": name for name in TENANT_NETWORKS}
        for network in networks:
            name = network["Name"]
            for config in (network["IPAM"]["Config"] or []):
                subnet = ipaddress.ip_network(config["Subnet"])
                if subnet.version != 4:
                    continue
                occupied.append(subnet)
                if name in project_networks:
                    labels = network.get("Labels") or {}
                    if labels.get("com.docker.compose.project") != project:
                        raise RuntimeError("Tenant network name belongs to another project")
                    assigned[project_networks[name]] = str(subnet)

        # Small subnets remain available when Docker's default /16 pool is exhausted.
        pool = ipaddress.ip_network("10.241.0.0/16")
        subnets = list(pool.subnets(new_prefix=29))
        start = int(project.removeprefix("cp-parity-"), 16) % len(subnets)
        result = {}
        required = TENANT_NETWORKS if with_model else TENANT_NETWORKS[:2]
        for name in required:
            if name in assigned:
                result[name] = assigned[name]
                continue
            for subnet in subnets[start:] + subnets[:start]:
                if not any(subnet.overlaps(existing) for existing in occupied):
                    occupied.append(subnet)
                    result[name] = str(subnet)
                    break
            else:
                raise RuntimeError("No available subnet in the tenant Docker network pool")
        return result

    @contextmanager
    def database(self, workspace_slug: str, display_role: str = ""):
        tenant = self.ensure(workspace_slug, display_role)
        project = self._project(workspace_slug)
        with self._provision_lock(project):
            with self._guard:
                if project not in self._ready:
                    raise RuntimeError("Tenant was removed before opening PostgreSQL")
                relay = LoopbackRelay(f"{project}-api-1", "postgres", 5432)
                self._database_relays.setdefault(project, set()).add(relay)
        try:
            password = self._credential("database", tenant.workspace_slug)
            yield f"postgresql://careerpilot:{password}@127.0.0.1:{relay.port}/careerpilot"
        finally:
            with self._guard:
                connections = self._database_relays.get(project)
                if connections is not None:
                    connections.discard(relay)
                    if not connections:
                        self._database_relays.pop(project)
            relay.close()

    def remove(self, workspace_slug: str) -> None:
        """Remove only the account selected by a password-verified gateway."""
        project = self._project(workspace_slug)
        label = f"label=com.docker.compose.project={project}"
        with self._provision_lock(project):
            with self._guard:
                api = self._relays.pop(project, None)
                self._ready.pop(project, None)
                relays = self._database_relays.pop(project, set())
            if api is not None:
                api.close()
            for relay in relays:
                relay.close()

            containers = self._docker("ps", "--all", "--quiet", "--filter", label).splitlines()
            networks = self._docker("network", "ls", "--format", "{{.Name}}", "--filter", label).splitlines()
            volumes = self._docker("volume", "ls", "--quiet", "--filter", label).splitlines()
            if containers:
                self._docker("rm", "--force", *containers)
            with self._provision_lock("network-allocation"):
                for network in networks:
                    if network in {f"{project}_{name}" for name in TENANT_NETWORKS}:
                        self._docker("network", "rm", network)
            for volume in volumes:
                if volume in {f"{project}_pgdata", f"{project}_personal"}:
                    self._docker("volume", "rm", volume)

    def close(self) -> None:
        """Release host listeners after the gateway finishes serving requests."""
        with self._guard:
            relays = list(self._relays.values())
            for connections in self._database_relays.values():
                relays.extend(connections)
            self._database_relays.clear()
            self._relays.clear()
            self._ready.clear()
        for relay in relays:
            relay.close()


def _model_upstream() -> dict[str, str]:
    config = {
        "MODEL_UPSTREAM_BASE_URL": os.environ.get("SEED_MODEL_BASE_URL", "").strip(),
        "MODEL_UPSTREAM_API_KEY": os.environ.get("SEED_MODEL_KEY", "").strip(),
        "MODEL_UPSTREAM_MODEL": os.environ.get("SEED_MODEL_NAME", "").strip(),
    }
    if not any(config.values()):
        return {}
    if not all(config.values()):
        raise RuntimeError("Tenant model relay requires all three SEED_MODEL settings")
    return config


def _compose(
    slug: str, display_role: str, token: str, password: str,
    subnets: dict[str, str], upstream: dict[str, str], model_token: str, repo_token: str,
) -> dict:
    native = {
        "image": IMAGE,
        "environment": {
            "DATABASE_URL": f"postgresql+asyncpg://careerpilot:{password}@postgres:5432/careerpilot",
            "DEFAULT_WORKSPACE_SLUG": slug,
            "API_TOKEN": token,
            "DEBUG": "false",
            "TENANT_DISPLAY_ROLE": display_role.strip()[:160].replace("$", "$$"),
            "HOME": "/tmp",
        },
        "networks": ["private"],
        "dns": ["127.0.0.1"],
        "user": "65532:65532",
        "read_only": True,
        "volumes": ["personal:/personal"],
        "tmpfs": ["/tmp:rw,nosuid,nodev,noexec,mode=1777"],
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "init": True,
        "restart": "unless-stopped",
    }
    compose = {
        "services": {
            "postgres": {
                "image": DATABASE_IMAGE,
                "environment": {
                    "POSTGRES_USER": "careerpilot",
                    "POSTGRES_DB": "careerpilot",
                    "POSTGRES_PASSWORD": password,
                    "POSTGRES_INITDB_ARGS": "--auth-host=scram-sha-256",
                },
                "volumes": ["pgdata:/var/lib/postgresql/data"],
                "networks": ["private"],
                "dns": ["127.0.0.1"],
                "healthcheck": {
                    "test": ["CMD", "pg_isready", "-h", "127.0.0.1", "-U", "careerpilot", "-d", "careerpilot"],
                    "interval": "2s", "timeout": "3s", "retries": 30,
                },
                "restart": "unless-stopped",
            },
            "api": {
                **native,
                "command": [
                    "sh", "-ec",
                    "python bootstrap.py && python migrate.py && python -m app.tenant_init"
                    " && exec python -m uvicorn app.tenant_init:create_app --factory --host 127.0.0.1 --port 8000",
                ],
                "depends_on": {"postgres": {"condition": "service_healthy"}},
                "healthcheck": {
                    "test": ["CMD", "curl", "-fsS", "http://127.0.0.1:8000/healthz"],
                    "interval": "2s", "timeout": "3s", "retries": 45,
                },
            },
            "worker": {
                **native,
                "command": [
                    "sh", "-ec", "rm -f /tmp/worker-ready && exec python -m app.project_worker"
                    " --ready-file /tmp/worker-ready",
                ],
                "healthcheck": {
                    "test": ["CMD", "test", "-f", "/tmp/worker-ready"],
                    "interval": "2s", "timeout": "3s", "retries": 30,
                },
                "depends_on": {"api": {"condition": "service_healthy"}},
            },
        },
        "volumes": {"pgdata": {}, "personal": {}},
        "networks": {"private": {
            "internal": True,
            "driver_opts": {"com.docker.network.bridge.gateway_mode_ipv4": "isolated"},
            "enable_ipv6": False,
            "ipam": {"config": [{"subnet": subnets["private"]}]},
        }},
    }

    private = ipaddress.ip_network(subnets["private"])
    repo_ip = str(private.broadcast_address - 2)
    native["environment"].update({
        "REPO_RELAY_URL": f"http://{repo_ip}:8081",
        "REPO_RELAY_TOKEN": repo_token,
    })
    compose["services"]["repo-relay"] = _relay_service(
        "repo_relay", "repo_egress", repo_ip, 8081,
        {"REPO_RELAY_BIND_IP": repo_ip, "REPO_RELAY_TOKEN": repo_token},
    )
    if upstream:
        model_ip = str(private.broadcast_address - 1)
        native["environment"].update({
            "LLM_PROVIDER": "openai",
            "LLM_API_KEY": model_token,
            "LLM_BASE_URL": f"http://{model_ip}:8080/v1",
            "LLM_MODEL": upstream["MODEL_UPSTREAM_MODEL"].replace("$", "$$"),
        })
        compose["services"]["model-relay"] = _relay_service(
            "model_relay", "egress", model_ip, 8080,
            {
                **{key: value.replace("$", "$$") for key, value in upstream.items()},
                "MODEL_RELAY_BIND_IP": model_ip,
                "MODEL_RELAY_TOKEN": model_token,
            },
        )

    for name, subnet in subnets.items():
        if name != "private":
            compose["networks"][name] = {
                "enable_ipv6": False,
                "ipam": {"config": [{"subnet": subnet}]},
            }
    return compose


def _relay_service(module: str, egress: str, address: str, port: int, environment: dict[str, str]) -> dict:
    return {
        "image": IMAGE,
        "command": ["python", "-m", f"deploy.tenant.{module}"],
        "environment": environment,
        "networks": {"private": {"ipv4_address": address}, egress: {}},
        "sysctls": {"net.ipv4.ip_forward": "0"},
        "user": "65532:65532",
        "read_only": True,
        "tmpfs": ["/tmp:rw,nosuid,nodev,noexec,mode=1777"],
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "init": True,
        "restart": "unless-stopped",
        "healthcheck": {
            "test": [
                "CMD", "python", "-c",
                f"import socket; socket.create_connection(('{address}', {port}), 2).close()",
            ],
            "interval": "2s", "timeout": "3s", "retries": 30,
        },
    }
