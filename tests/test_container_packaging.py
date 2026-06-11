from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_serves_existing_index_with_livez_healthcheck() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "YKM_INDEX_PATH=/data/index" in dockerfile
    assert "ykm serve --index" in dockerfile
    assert "--host 0.0.0.0" in dockerfile
    assert "/livez" in dockerfile
    assert "USER ykm" in dockerfile


def test_compose_mounts_index_read_only_and_logs_writable() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "source: ${YKM_CONTAINER_INDEX_PATH:-.ykm/real-index}" in compose
    assert "target: /data/index" in compose
    assert "read_only: true" in compose
    assert "target: /data/logs" in compose
    assert "source: ${YKM_CONTAINER_LOG_DIR:-.ykm/container-smoke/logs}" in compose
    assert "source: ." not in compose
    assert "target: /app" not in compose


def test_production_compose_matches_vps_runtime_shape() -> None:
    compose = (ROOT / "deploy" / "youknowme" / "docker-compose.yml").read_text(encoding="utf-8")

    assert "container_name: ${YKM_CONTAINER_NAME:-youknowme-phase1e}" in compose
    assert "image: ${YKM_IMAGE:-youknowme:phase1e}" in compose
    assert "${YKM_ENV_FILE:-/opt/youknowme/runtime.env}" in compose
    assert "${YKM_INDEX_MOUNT:-/opt/youknowme/index-current}" in compose
    assert "target: /data/index" in compose
    assert "read_only: true" in compose
    assert "${YKM_LOG_DIR:-/opt/youknowme/logs}" in compose
    assert "${YKM_INTAKE_DIR:-/opt/youknowme/intake}" in compose
    assert "read_only: true" in compose
    assert "cap_drop:" in compose
    assert "no-new-privileges:true" in compose
    assert "external: true" in compose
    assert "roger-knowledge-mcp" in compose
    assert "youknowme" in compose


def test_index_promotion_script_supports_compose_recreate() -> None:
    script = (ROOT / "scripts" / "relaunch-container-with-new-index.sh").read_text(
        encoding="utf-8"
    )

    assert "--compose-dir PATH" in script
    assert "COMPOSE_DIR" in script
    assert "docker compose up -d --force-recreate" in script
    assert "YKM_INDEX_MOUNT" in script


def test_docker_context_excludes_secrets_and_poc() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert ".git/" in dockerignore
    assert ".env" in dockerignore
    assert ".env.*" in dockerignore
    assert ".ykm/" in dockerignore
    assert "POC/" in dockerignore
