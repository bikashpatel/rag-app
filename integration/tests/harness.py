"""Access to the rag-it test stack: Compose commands, the database, Redis, Qdrant, the HTTP API and the fake model
server. Every Compose command goes to the rag-it project with compose.it.yml and the generated env file only."""

import functools
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / "integration" / ".run" / "it.env"
PROJECT = os.environ.get("IT_PROJECT", "rag-it")
if not re.fullmatch(r"rag-it(-[a-z0-9]+)*", PROJECT):
    raise RuntimeError(f"Refusing the Compose project {PROJECT!r} (only rag-it or rag-it-<suffix>)")
MARKER = "rag.it.stack"  # the label compose.it.yml puts on everything in the project
FAKE_API_BASE = "http://fake-llm:8000/v1"
# Interpolation must come only from the env file: the shell's values would win over it.
_SHELL_VARIABLES = (
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "APP_PORT",
    "WORKER_MEM_LIMIT",
    "MAX_UPLOAD_MB_HARD",
    "IT_FAKE_LLM_PORT",
    "IT_FAKE_GOOGLE_PORT",
    "APP_VERSION",
    "QDRANT_API_KEY",
    "QDRANT_COLLECTION_PREFIX",
    "BACKUP_DIR",
    "BACKUP_SCHEDULE",
    "BACKUP_KEEP_DAILY",
    "BACKUP_KEEP_WEEKLY",
    "BACKUP_START_DELAY_SECONDS",
    "BACKUP_UID",
    "BACKUP_GID",
    "IT_FAKE_RAZORPAY_PORT",
    "RAZORPAY_KEY_ID",
    "RAZORPAY_KEY_SECRET",
    "RAZORPAY_WEBHOOK_SECRET",
    "RAZORPAY_WEBHOOK_SECRET_PREVIOUS",
    "COMPOSE_FILE",
    "COMPOSE_PROJECT_NAME",
    "COMPOSE_ENV_FILES",
    "COMPOSE_PROFILES",
)


# Values of the generated env file that must never appear in a log, a backup listing or an alert.
SECRETS = (
    "SECRET_KEY",
    "ADMIN_PASSWORD",
    "POSTGRES_PASSWORD",
    "TOKEN_ENCRYPTION_KEY",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "RAZORPAY_KEY_SECRET",
    "RAZORPAY_WEBHOOK_SECRET",
    "RAZORPAY_WEBHOOK_SECRET_PREVIOUS",
)


def _read_env() -> dict[str, str]:
    if not ENV_FILE.is_file():
        raise RuntimeError(f"{ENV_FILE} is missing: run the suite with integration/run.sh")
    values = {}
    for line in ENV_FILE.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            name, _, value = line.partition("=")
            values[name] = value
    return values


ENV = _read_env()
APP_URL = f"http://127.0.0.1:{ENV['APP_PORT']}"
FAKE_LLM_URL = f"http://127.0.0.1:{ENV['IT_FAKE_LLM_PORT']}"
FAKE_GOOGLE_URL = f"http://127.0.0.1:{ENV['IT_FAKE_GOOGLE_PORT']}"
FAKE_RAZORPAY_URL = f"http://127.0.0.1:{ENV['IT_FAKE_RAZORPAY_PORT']}"


def leaked_secrets(text: str) -> list[str]:
    """The names of the generated secrets (and fake provider keys) that appear in `text`."""
    leaked = [name for name in SECRETS if ENV[name] in text]
    return leaked + [key for key in ("sk-it-fake", "sk-ant-it-fake") if key in text]


def unique(prefix: str) -> str:
    """A word no other test uses, e.g. "heron3fa9c1": the hashed bag-of-words embedding matches it exactly."""
    return f"{prefix}{uuid.uuid4().hex[:6]}"


def wait_until(predicate: Callable[[], Any], timeout: float, message: str, interval: float = 0.3) -> Any:
    """Poll until `predicate()` is truthy (returned) or `timeout` seconds pass (AssertionError)."""
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"Timed out after {timeout:.0f} s: {message}")
        time.sleep(interval)


# ---------------- Compose ----------------


def _docker() -> str:
    found = shutil.which("docker")
    if found:
        return found
    candidate = Path.home() / ".docker" / "bin" / "docker"
    if candidate.is_file():
        return str(candidate)
    raise RuntimeError("docker isn't on PATH")


def docker(*args: str, timeout: float = 60) -> str:
    """A docker CLI call: read-only (ps, volume ls, network ls, inspect, info), or `update` of a test stack container
    (limit_cpus)."""
    result = subprocess.run([_docker(), *args], capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"docker {args[0]} failed ({result.returncode}): {result.stderr[-2000:]}")
    return result.stdout


LIVE_PROJECT = "rag-app"


def live_containers() -> dict[str, tuple[str, str]]:
    """name -> (container id, state) of the live rag-app project, read with `docker ps` only (nothing else ever touches
    it); {} when it isn't running here (CI)."""
    try:
        output = docker(
            "ps",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={LIVE_PROJECT}",
            "--format",
            "{{.Names}} {{.ID}} {{.State}}",
        )
    except (RuntimeError, OSError):
        return {}
    return {name: (container, state) for name, container, state in (line.split() for line in output.splitlines())}


@functools.cache
def _own_project() -> None:
    """Everything already in the project carries compose.it.yml's marker label: no other stack is ever touched."""
    label = f"label=com.docker.compose.project={PROJECT}"
    marker = '{{.Label "' + MARKER + '"}}'
    listed = [
        *docker("ps", "-a", "--filter", label, "--format", "container {{.Names}} " + marker).splitlines(),
        *docker("volume", "ls", "--filter", label, "--format", "volume {{.Name}} " + marker).splitlines(),
        *docker("network", "ls", "--filter", label, "--format", "network {{.Name}} " + marker).splitlines(),
    ]
    foreign = [line.strip() for line in listed if line.split(" ")[2:] != ["1"]]
    if foreign:
        raise RuntimeError(f"Refusing: the Compose project {PROJECT!r} holds objects without the marker: {foreign}")


def _compose_command(*args: str) -> tuple[list[str], dict[str, str]]:
    _own_project()
    environment = {name: value for name, value in os.environ.items() if name not in _SHELL_VARIABLES}
    command = [
        _docker(),
        "compose",
        "-p",
        PROJECT,
        "--project-directory",
        str(ROOT),
        "-f",
        str(ROOT / "docker-compose.yml"),
        "-f",
        str(ROOT / "integration" / "compose.it.yml"),
        "--env-file",
        str(ENV_FILE),
        *args,
    ]
    return command, environment


def compose(*args: str, timeout: float = 180, stdin: str | None = None) -> str:
    """A docker compose command for the test stack (RuntimeError when it fails); its stdout."""
    command, environment = _compose_command(*args)
    result = subprocess.run(
        command, input=stdin, capture_output=True, text=True, env=environment, timeout=timeout, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"docker compose {args[0]} failed ({result.returncode}): {result.stderr[-2000:]}")
    return result.stdout


def compose_started(*args: str) -> subprocess.Popen[str]:
    """A docker compose command for the test stack, left running: the caller waits for it (stderr in stdout)."""
    command, environment = _compose_command(*args)
    return subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=environment)


def limit_cpus(service: str, cpus: float | None) -> None:
    """Gives a container of the test stack `cpus` CPUs (None: all of the machine's)."""
    (name,) = [item["name"] for key, item in services().items() if key == service]
    value = cpus if cpus is not None else int(docker("info", "--format", "{{.NCPU}}").strip())
    docker("update", "--cpus", str(value), name)


def script(name: str, *args: str, timeout: float = 900) -> subprocess.CompletedProcess[str]:
    """Runs scripts/<name> (backup.sh, restore.sh) against the test stack: Compose through integration/stack.sh, the
    maintenance flag in integration/.run. Returns the finished process whatever its exit code."""
    _own_project()
    environment = {name: value for name, value in os.environ.items() if name not in _SHELL_VARIABLES}
    environment |= {
        "RAG_COMPOSE": str(ROOT / "integration" / "stack.sh"),
        "RAG_MAINTENANCE_FILE": str(ROOT / "integration" / ".run" / "maintenance"),
        "IT_PROJECT": PROJECT,
    }
    return subprocess.run(
        [str(ROOT / "scripts" / name), *args],
        capture_output=True,
        text=True,
        env=environment,
        timeout=timeout,
        check=False,
    )


def exec_in(service: str, *command: str, stdin: str | None = None, timeout: float = 60) -> str:
    return compose("exec", "-T", service, *command, stdin=stdin, timeout=timeout)


def python_in(service: str, code: str, stdin: str | None = None) -> str:
    """Run Python code in a backend-image container (backend, worker, fake-llm) and return its stdout."""
    return exec_in(service, "python", "-c", code, stdin=stdin).strip()


def services() -> dict[str, dict[str, str]]:
    """service -> {"state", "health", "name"} for every container of the project."""
    output = compose("ps", "-a", "--format", "json").strip()
    if not output:
        return {}
    rows = json.loads(output) if output.startswith("[") else [json.loads(line) for line in output.splitlines()]
    return {
        row["Service"]: {"state": row["State"], "health": row.get("Health", ""), "name": row["Name"]} for row in rows
    }


def sql(query: str) -> list[list[str]]:
    """Rows of a query in the test stack's Postgres (psql over the container's local socket)."""
    output = exec_in(
        "postgres", "psql", "-U", "rag", "-d", "rag", "-tA", "-F", "\t", "-v", "ON_ERROR_STOP=1", "-c", query
    )
    return [line.split("\t") for line in output.splitlines() if line]


def sql_value(query: str) -> str:
    rows = sql(query)
    assert len(rows) == 1 and len(rows[0]) == 1, rows
    return rows[0][0]


def redis_cli(*args: str) -> str:
    return exec_in("redis", "redis-cli", *args).strip()


_QDRANT = """
import json, os, urllib.error, urllib.request, sys
results = []
for call in json.loads(sys.stdin.read()):
    body = json.dumps(call["body"]).encode() if call.get("body") is not None else None
    request = urllib.request.Request(os.environ["QDRANT_URL"] + call["path"], data=body, method=call["method"],
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            results.append({"status": response.status, "body": json.load(response)})
    except urllib.error.HTTPError as error:
        results.append({"status": error.code, "body": None})
print(json.dumps(results))
"""


def qdrant(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Qdrant REST calls ({method, path, body}) made from the backend container: Qdrant isn't published."""
    return json.loads(python_in("backend", _QDRANT, stdin=json.dumps(calls)))


def qdrant_points(model_id: int, doc_ids: list[int]) -> dict[int, int]:
    """doc id -> its points in the collection of one embedding model (0 when the collection doesn't exist)."""
    calls = [
        {
            "method": "POST",
            "path": f"/collections/rag_chunks_{model_id}/points/count",
            "body": {"filter": {"must": [{"key": "doc_id", "match": {"value": doc_id}}]}, "exact": True},
        }
        for doc_id in doc_ids
    ]
    counts = {}
    for doc_id, result in zip(doc_ids, qdrant(calls), strict=True):
        assert result["status"] in (200, 404), result
        counts[doc_id] = int(result["body"]["result"]["count"]) if result["status"] == 200 else 0
    return counts


def qdrant_vector_size(model_id: int) -> int | None:
    (result,) = qdrant([{"method": "GET", "path": f"/collections/rag_chunks_{model_id}"}])
    if result["status"] == 404:
        return None
    return int(result["body"]["result"]["config"]["params"]["vectors"]["size"])


def stored_names(doc_ids: list[int]) -> dict[int, str]:
    """doc id -> its file name in UPLOAD_DIR (documents.stored_name)."""
    if not doc_ids:
        return {}
    ids = ",".join(str(int(doc_id)) for doc_id in doc_ids)
    return {int(doc_id): name for doc_id, name in sql(f"select id, stored_name from documents where id in ({ids})")}


def table_counts() -> dict[str, int]:
    """Rows in every table of the test stack's database."""
    tables = [row[0] for row in sql("select tablename from pg_tables where schemaname = 'public' order by 1")]
    query = " union all ".join(f"select '{table}', count(*) from public.\"{table}\"" for table in tables)
    return {table: int(count) for table, count in sql(query)}


def qdrant_collections() -> dict[str, int]:
    """Collection -> its exact point count."""
    (listed,) = qdrant([{"method": "GET", "path": "/collections"}])
    names = [item["name"] for item in listed["body"]["result"]["collections"]]
    counts = qdrant(
        [{"method": "POST", "path": f"/collections/{name}/points/count", "body": {"exact": True}} for name in names]
    )
    return {name: int(result["body"]["result"]["count"]) for name, result in zip(names, counts, strict=True)}


def stored_files() -> set[str]:
    """The files on the uploads volume, as the backend sees it."""
    return set(exec_in("backend", "ls", "-1", "/data/uploads").split())


# ---------------- HTTP API ----------------


def client() -> httpx.Client:
    return httpx.Client(base_url=APP_URL, timeout=60, headers={"User-Agent": "rag-it-integration-tests"})


def check(response: httpx.Response, status: int = 200) -> httpx.Response:
    assert response.status_code == status, (
        f"{response.request.method} {response.request.url.path} -> {response.status_code}, expected {status}: "
        f"{response.text[:800]}"
    )
    return response


def login(username: str, password: str) -> httpx.Client:
    session = client()
    check(session.post("/api/auth/login", json={"username": username, "password": password}))
    return session


@dataclass
class User:
    id: int
    username: str
    password: str
    client: httpx.Client

    def login_again(self) -> httpx.Client:
        return login(self.username, self.password)


def upload(session: httpx.Client, filename: str, data: bytes, *, scope: str = "private", status: int = 201) -> dict:
    response = session.post("/api/documents", files={"file": (filename, data)}, data={"scope": scope})
    return check(response, status).json()


def documents(session: httpx.Client) -> list[dict]:
    return check(session.get("/api/documents")).json()


def document(session: httpx.Client, doc_id: int) -> dict | None:
    return next((item for item in documents(session) if item["id"] == doc_id), None)


def wait_document(session: httpx.Client, doc_id: int, status: str = "ready", timeout: float = 90) -> dict:
    """Poll GET /documents until the document reaches `status` (AssertionError if it ends in another one)."""

    def settled() -> dict | None:
        item = document(session, doc_id)
        assert item is not None, f"document {doc_id} isn't visible"
        if item["status"] == status:
            return item
        if item["status"] in ("ready", "failed"):
            raise AssertionError(f"document {doc_id} is {item['status']} ({item['error']}), expected {status}")
        return None

    return wait_until(settled, timeout, f"document {doc_id} to become {status}")


def wait_queue_idle(admin: httpx.Client, timeout: float = 120) -> None:
    """Every document of every user is ready or failed and the ingest queue is empty."""

    def idle() -> bool:
        busy = [
            item
            for item in check(admin.get("/api/admin/documents")).json()
            if item["status"] in ("pending", "processing")
        ]
        return not busy and check(admin.get("/api/admin/stats")).json()["queue_length"] == 0

    wait_until(idle, timeout, "the ingest queue to drain", interval=0.5)


@dataclass
class ChatResult:
    status: int
    events: list[tuple[str, Any]] = field(default_factory=list)
    times: list[float] = field(default_factory=list)  # time.monotonic() at each event's arrival
    body: Any = None  # the JSON error of a refused request
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.events]

    def all(self, name: str) -> list[Any]:
        return [data for event, data in self.events if event == name]

    def first(self, name: str) -> Any:
        found = self.all(name)
        assert found, f"no {name} event in {self.names}"
        return found[0]

    @property
    def meta(self) -> dict:
        return self.first("meta")

    @property
    def sources(self) -> list[dict]:
        return self.all("sources")[-1]["sources"]

    @property
    def text(self) -> str:
        return "".join(data["content"] for data in self.all("delta"))

    @property
    def done(self) -> dict:
        return self.first("done")


def chat(
    session: httpx.Client,
    message: str,
    *,
    conversation_id: int | None = None,
    document_ids: list[int] | None = None,
    model_id: int | None = None,
    timeout: float = 120,
) -> ChatResult:
    """POST /chat/stream and read the whole SSE stream (comment lines such as ": keepalive" are ignored)."""
    body = {"conversation_id": conversation_id, "message": message, "model_id": model_id, "document_ids": document_ids}
    with session.stream("POST", "/api/chat/stream", json=body, timeout=timeout) as response:
        if response.status_code != 200:
            response.read()
            return ChatResult(response.status_code, body=response.json(), headers=dict(response.headers))
        assert response.headers["content-type"].startswith("text/event-stream")
        result = ChatResult(200)
        name, data = None, []
        for line in response.iter_lines():
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
            elif not line and name is not None:
                result.events.append((name, json.loads("\n".join(data))))
                result.times.append(time.monotonic())
                name, data = None, []
        return result


def assert_sse_contract(result: ChatResult) -> None:
    """The event order of SPEC.md section 6 (Chat)."""
    assert result.status == 200, result.body
    names = result.names
    assert names and names[0] == "meta", names
    assert "sources" in names and names.index("sources") == 1, names
    terminal = [index for index, name in enumerate(names) if name in ("done", "error")]
    assert terminal == [len(names) - 1], names
    allowed = {"meta", "sources", "warning", "delta", "file", "plugin", "done", "error"}
    assert set(names) <= allowed, names
    meta = result.meta
    assert {"conversation_id", "title", "user_message_id", "model"} <= set(meta), meta


def google_state() -> dict:
    """The fake Google's record of drafts, sends, revocations and grants."""
    return check(httpx.get(f"{FAKE_GOOGLE_URL}/_state", timeout=10)).json()


def gmail_connect(user: User) -> str:
    """The browser flow: connect -> the fake's consent (answers at once) -> the app's callback. Returns the result."""
    start = check(user.client.post("/api/plugins/gmail/connect", json={}, headers={"Origin": APP_URL})).json()
    authorize = urlparse(start["authorize_url"])
    assert f"{authorize.scheme}://{authorize.netloc}" == FAKE_GOOGLE_URL
    query = parse_qs(authorize.query)
    assert query["code_challenge_method"] == ["S256"] and query["access_type"] == ["offline"]
    assert "code_verifier" not in query and "client_secret" not in query
    consent = httpx.get(start["authorize_url"], follow_redirects=False, timeout=10)
    assert consent.status_code == 302, consent.text
    callback = urlparse(consent.headers["location"])
    assert f"{callback.scheme}://{callback.netloc}" == APP_URL and callback.path == "/api/plugins/gmail/oauth/callback"
    finished = user.client.get(f"{callback.path}?{callback.query}", follow_redirects=False)
    assert finished.status_code == 303
    return parse_qs(urlparse(finished.headers["location"]).query)["oauth"][0]


def alerts_received() -> list[dict]:
    """What the fake alert receiver got (webhook and ntfy), oldest first."""
    code = (
        "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/_log', timeout=10).read().decode())"
    )
    return json.loads(python_in("fake-alerts", code))["received"]


# ---------------- The fake model server ----------------


class FakeLLM:
    """Client of integration/fake_llm/server.py (published on 127.0.0.1)."""

    def __init__(self) -> None:
        self.http = httpx.Client(base_url=FAKE_LLM_URL, timeout=30)

    def log(self, kind: str | None = None, after: int = 0) -> list[dict]:
        params: dict[str, Any] = {"after": after}
        if kind:
            params["kind"] = kind
        return check(self.http.get("/_log", params=params)).json()["entries"]

    def mark(self) -> int:
        """The sequence number of the latest request so far: log(after=mark) is what came after."""
        return check(self.http.get("/_log", params={"after": 10**12})).json()["last"]

    def config(self, **knobs: Any) -> dict:
        return check(self.http.post("/_config", json=knobs)).json()

    @contextmanager
    def knobs(self, **knobs: Any) -> Iterator[None]:
        before = self.config()
        self.config(**knobs)
        try:
            yield
        finally:
            self.config(**{name: before[name] for name in knobs})


def system_prompt(entry: dict) -> str:
    return next((m["content"] for m in entry["messages"] if m["role"] == "system"), "")
