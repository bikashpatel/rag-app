"""Flow 1: every service is up and isolated, the env admin can log in, and roles are enforced through nginx."""

import hashlib
import json

import httpx
import pytest
from harness import APP_URL, ENV, FAKE_API_BASE, MARKER, PROJECT, check, client, docker, exec_in, python_in, services

EXPECTED_SERVICES = {
    "postgres",
    "redis",
    "qdrant",
    "backend",
    "worker",
    "frontend",
    "monitor",
    "backup",
    "fake-llm",
    "fake-google",
    "fake-alerts",
    "fake-razorpay",
}
# What a backend-image container may have in its environment: the generated env file, docker-compose.yml's
# `environment`, the image's ENV and what Docker adds. Anything else would have come from somewhere else (.env).
COMPOSE_ENVIRONMENT = {
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "REDIS_URL",
    "QDRANT_URL",
    "UPLOAD_DIR",
    "LITELLM_LOCAL_MODEL_COST_MAP",
    "APP_VERSION",
}
IMAGE_ENVIRONMENT = {
    "PATH",
    "HOME",
    "HOSTNAME",
    "LANG",
    "GPG_KEY",
    "PYTHON_VERSION",
    "PYTHON_SHA256",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONUNBUFFERED",
    "PIP_NO_CACHE_DIR",
    "PIP_DISABLE_PIP_VERSION_CHECK",
    # Docker adds these from the client's proxy settings (~/.docker/config.json), never from an env file
    *(
        name
        for proxy in ("HTTP_PROXY", "HTTPS_PROXY", "FTP_PROXY", "NO_PROXY", "ALL_PROXY")
        for name in (proxy, proxy.lower())
    ),
}
# Prints True only when the service's own environment (its PID 1, not this exec) is exactly the generated one; never
# prints a value.
ENV_CHECK = """
import hashlib, json, sys
expected = json.loads(sys.stdin.read())
with open("/proc/1/environ", "rb") as handle:
    pairs = [item.split(b"=", 1) for item in handle.read().split(b"\\0") if b"=" in item]
environ = {name.decode(): value.decode() for name, value in pairs}
same = all(hashlib.sha256(environ.get(name, "").encode()).hexdigest() == digest
           for name, digest in expected["digests"].items())
unexpected = set(environ) - set(expected["allowed"])
print(same and not unexpected, len(unexpected))
"""
EGRESS_CHECK = """
import socket
reached = []
for host in ("api.openai.com", "api.anthropic.com", "oauth2.googleapis.com", "gmail.googleapis.com"):
    try:
        socket.create_connection((host, 443), timeout=3).close()
        reached.append(host)
    except OSError:
        pass
print(not reached)
"""


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def test_project_is_isolated() -> None:
    assert PROJECT != "rag-app"
    found = services()
    assert set(found) == EXPECTED_SERVICES, found
    assert all(info["name"].startswith(f"{PROJECT}-") for info in found.values()), found
    label = f"label=com.docker.compose.project={PROJECT}"
    volumes = docker("volume", "ls", "--filter", label, "--format", "{{.Name}}").split()
    names = ("pgdata", "redisdata", "qdrantdata", "uploads", "backupstatus")
    assert sorted(volumes) == sorted(f"{PROJECT}_{name}" for name in names)
    networks = docker("network", "ls", "--filter", label, "--format", "{{.Name}}").split()
    assert sorted(networks) == [f"{PROJECT}_default", f"{PROJECT}_edge"]
    internal = docker("network", "inspect", f"{PROJECT}_default", "--format", "{{.Internal}}").strip()
    assert internal == "true"
    marked = ("--filter", label, "--filter", f"label={MARKER}=1")
    assert len(docker("ps", "-aq", *marked).split()) == len(found)
    assert sorted(docker("volume", "ls", *marked, "--format", "{{.Name}}").split()) == sorted(volumes)
    assert sorted(docker("network", "ls", *marked, "--format", "{{.Name}}").split()) == sorted(networks)


def test_every_service_is_running_and_healthy() -> None:
    found = services()
    assert all(info["state"] == "running" for info in found.values()), found
    assert all(info["health"] == "healthy" for info in found.values()), found


def test_health_through_nginx() -> None:
    response = httpx.get(f"{APP_URL}/api/health", timeout=10)
    assert response.status_code == 200
    page = httpx.get(f"{APP_URL}/", timeout=10)
    assert page.status_code == 200 and "<html" in page.text.lower()
    assert page.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize("service", ["backend", "worker", "monitor"])
def test_container_environment_is_only_the_generated_env_file(service: str) -> None:
    """Backend, worker and monitor got exactly the generated env file: every value of it, fake keys, nothing from
    .env."""
    assert ENV["OPENAI_API_KEY"] == "sk-it-fake" and ENV["ANTHROPIC_API_KEY"] == "sk-ant-it-fake"
    digests = {name: _digest(value) for name, value in ENV.items()}
    allowed = sorted(set(ENV) | COMPOSE_ENVIRONMENT | IMAGE_ENVIRONMENT)
    output = python_in(service, ENV_CHECK, stdin=json.dumps({"digests": digests, "allowed": allowed}))
    assert output == "True 0", f"{service}: environment check printed {output!r}"


def test_frontend_gets_no_secrets() -> None:
    output = exec_in("frontend", "sh", "-c", 'test -z "$OPENAI_API_KEY$ANTHROPIC_API_KEY$SECRET_KEY" && echo true')
    assert output.strip() == "true"


def test_the_backup_service_gets_only_the_database_password() -> None:
    names = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "SECRET_KEY", "ADMIN_PASSWORD", "TOKEN_ENCRYPTION_KEY")
    secrets = "".join(f"${name}" for name in (*names, "GOOGLE_OAUTH_CLIENT_SECRET"))
    output = exec_in("backup", "sh", "-c", f'test -z "{secrets}" && test -n "$PGPASSWORD" && echo true')
    assert output.strip() == "true"


@pytest.mark.parametrize("service", ["backend", "worker", "monitor"])
def test_no_internet_from_the_app_containers(service: str) -> None:
    """The default network is internal: no real provider or Google endpoint is reachable, whatever the config."""
    assert python_in(service, EGRESS_CHECK) == "True"


def test_admin_from_the_env_file_can_log_in(admin: httpx.Client) -> None:
    me = check(admin.get("/api/auth/me")).json()
    assert me["username"] == ENV["ADMIN_USERNAME"] and me["is_admin"] is True
    config = check(admin.get("/api/admin/config")).json()
    assert config["admin_username"] == ENV["ADMIN_USERNAME"]


def test_wrong_password_is_rejected(stack: None) -> None:
    response = client().post("/api/auth/login", json={"username": ENV["ADMIN_USERNAME"], "password": "wrong-password"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/admin/users"),
        ("GET", "/api/admin/models"),
        ("GET", "/api/admin/stats"),
        ("GET", "/api/admin/usage"),
        ("GET", "/api/admin/documents"),
        ("GET", "/api/admin/plugins"),
        ("POST", "/api/admin/reindex"),
    ],
)
def test_non_admin_cannot_use_admin_routes(make_user, method: str, path: str) -> None:
    user = make_user("plain")
    assert user.client.request(method, path).status_code == 403
    assert client().request(method, path).status_code == 401


def test_non_admin_cannot_create_users_or_models(make_user) -> None:
    user = make_user("plain")
    body = {"username": "it-sneaky", "password": "sneaky-password"}
    assert user.client.post("/api/admin/users", json=body).status_code == 403
    model = {"display_name": "Sneaky", "litellm_model": "openai/gpt-4o-mini", "kind": "chat"}
    assert user.client.post("/api/admin/models", json=model).status_code == 403
    assert (
        user.client.post("/api/documents", files={"file": ("a.txt", b"x" * 50)}, data={"scope": "shared"}).status_code
        == 403
    )


def test_anonymous_requests_are_refused(stack: None) -> None:
    anonymous = client()
    for path in ("/api/auth/me", "/api/documents", "/api/conversations", "/api/models", "/api/me/usage"):
        assert anonymous.get(path).status_code == 401, path
    upload = anonymous.post("/api/documents", files={"file": ("a.txt", b"hello")})
    assert upload.status_code == 401


def test_only_fake_models_are_enabled(admin: httpx.Client, models: dict, make_user) -> None:
    listed = check(admin.get("/api/admin/models")).json()
    enabled = [model for model in listed if model["enabled"]]
    assert {model["id"] for model in enabled} == {model["id"] for model in models.values()}
    assert all(model["api_base"] == FAKE_API_BASE and model["api_key_env"] is None for model in enabled)
    stats = check(admin.get("/api/admin/stats")).json()
    assert stats["active_embedding_model"]["id"] == models["embed_a"]["id"]
    assert stats["active_vision_model"]["id"] == models["vision"]["id"]
    assert stats["active_image_model"]["id"] == models["image"]["id"]
    user = make_user("models")
    chat_models = check(user.client.get("/api/models")).json()
    assert [model["id"] for model in chat_models] == [models["chat"]["id"], models["chat2"]["id"]]
    assert chat_models[0]["is_default"] and chat_models[0]["can_create_files"] and chat_models[0]["can_create_images"]


def test_admin_model_tests_reach_the_fake(admin: httpx.Client, models: dict, fake) -> None:
    mark = fake.mark()
    chat = check(admin.post(f"/api/admin/models/{models['chat']['id']}/test")).json()
    assert chat["ok"] and "OK" in chat["message"], chat
    embedding = check(admin.post(f"/api/admin/models/{models['embed_a']['id']}/test")).json()
    assert embedding["ok"] and embedding["embedding_dim"] == 256, embedding
    vision = check(admin.post(f"/api/admin/models/{models['vision']['id']}/test")).json()
    assert vision["ok"], vision
    image = check(admin.post(f"/api/admin/models/{models['image']['id']}/test")).json()
    assert image["ok"] and image["image_data_url"].startswith("data:image/webp;base64,"), image
    calls = fake.log(after=mark)
    assert [call["kind"] for call in calls] == ["test", "embedding", "vision", "image"]
    assert {call["auth"] for call in calls} == {"placeholder"}
