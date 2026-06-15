from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

from ykm.artifact import package_index
from ykm.build import build_index
from ykm.embeddings import FakeEmbeddingProvider


SCRIPT = Path("scripts/install-corpus-index.sh")


def test_install_corpus_index_script_installs_packaged_index(tmp_path: Path) -> None:
    packaged, index_path = _package_fixture_index(tmp_path)
    deploy_root, compose_dir, env, docker_log = _install_harness(tmp_path)

    result = _run_install(packaged, deploy_root, compose_dir, env)

    assert result.returncode == 0
    manifest = json.loads((index_path / "manifest.json").read_text(encoding="utf-8"))
    expected_id = f"{_safe(manifest['source_commit'])}-{_safe(manifest['build_id'])}"
    current = deploy_root / "index-current"
    builds_dir = deploy_root / "index-builds"
    assert current.is_symlink()
    assert current.resolve() == builds_dir / expected_id
    assert (current / "manifest.json").exists()
    assert (current / "chunks.jsonl").exists()
    assert (current / "lancedb").is_dir()
    assert len([path for path in builds_dir.iterdir() if path.is_dir()]) <= 3

    docker_output = docker_log.read_text(encoding="utf-8")
    assert "compose -f" in docker_output
    assert "up -d --force-recreate youknowme" in docker_output
    assert "inspect -f {{.State.Health.Status}} youknowme-mcp" in docker_output


def test_install_corpus_index_prune_failure_warns_without_failing(tmp_path: Path) -> None:
    packaged, _index_path = _package_fixture_index(tmp_path)
    deploy_root, compose_dir, env, _docker_log = _install_harness(tmp_path)
    fake_lib = tmp_path / "fake-python"
    fake_lib.mkdir()
    (fake_lib / "shutil.py").write_text(
        """from __future__ import annotations

import os
from pathlib import Path


def rmtree(path, ignore_errors=False, onerror=None, *args, **kwargs):
    path = Path(path)
    if path.name == "stale-fails":
        raise PermissionError(13, "Permission denied", "latest_version_hint.json")
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_dir():
            os.rmdir(child)
        else:
            child.unlink()
    os.rmdir(path)
""",
        encoding="utf-8",
    )
    env["PYTHONPATH"] = str(fake_lib)
    builds_dir = deploy_root / "index-builds"
    failing = builds_dir / "stale-fails"
    failing.mkdir()
    (failing / "latest_version_hint.json").write_text("old\n", encoding="utf-8")
    old_time = time.time() - 2000
    os.utime(failing, (old_time, old_time))

    result = _run_install(packaged, deploy_root, compose_dir, env)

    assert result.returncode == 0
    assert "warning: could not fully prune" in result.stderr
    assert "stale-fails" in result.stderr
    assert "latest_version_hint.json" in result.stderr
    assert "Installed corpus index" in result.stdout


def test_install_corpus_index_script_has_valid_bash_syntax() -> None:
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def _package_fixture_index(tmp_path: Path) -> tuple[dict[str, str], Path]:
    index_path = tmp_path / "index"
    artifacts = tmp_path / "artifacts"
    build_index(Path("fixtures/corpus"), index_path, FakeEmbeddingProvider())
    packaged = package_index(index_path, artifacts)
    return packaged, index_path


def _install_harness(tmp_path: Path) -> tuple[Path, Path, dict[str, str], Path]:
    deploy_root = tmp_path / "deploy"
    builds_dir = deploy_root / "index-builds"
    builds_dir.mkdir(parents=True)
    for idx in range(4):
        stale = builds_dir / f"stale-{idx}"
        stale.mkdir()
        old_time = time.time() - 1000 - idx
        os.utime(stale, (old_time, old_time))

    compose_dir = tmp_path / "compose"
    compose_dir.mkdir()
    (compose_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_sha256sum(fake_bin / "sha256sum")
    _write_fake_docker(fake_bin / "docker", tmp_path / "docker.log")
    _write_fake_mv(fake_bin / "mv")

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    return deploy_root, compose_dir, env, tmp_path / "docker.log"


def _run_install(
    packaged: dict[str, str],
    deploy_root: Path,
    compose_dir: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(SCRIPT),
            "--artifact",
            packaged["tarball"],
            "--sha256",
            packaged["sha256"],
            "--deploy-root",
            str(deploy_root),
            "--compose-dir",
            str(compose_dir),
            "--compose-service",
            "youknowme",
            "--container-name",
            "youknowme-mcp",
        ],
        check=True,
        env=env,
        text=True,
        capture_output=True,
    )


def _write_fake_sha256sum(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
import hashlib
import pathlib
import sys

if len(sys.argv) != 3 or sys.argv[1] != "-c":
    raise SystemExit("fake sha256sum only supports -c <file>")

sha_file = pathlib.Path(sys.argv[2])
ok = True
for line in sha_file.read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    expected, filename = line.split(None, 1)
    filename = filename.strip()
    digest = hashlib.sha256(pathlib.Path(filename).read_bytes()).hexdigest()
    if digest == expected:
        print(f"{filename}: OK")
    else:
        print(f"{filename}: FAILED")
        ok = False
raise SystemExit(0 if ok else 1)
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _write_fake_docker(path: Path, log_path: Path) -> None:
    path.write_text(
        f"""#!/usr/bin/env bash
printf '%s\\n' "$*" >> {log_path}
if [[ "$1" == "inspect" ]]; then
  echo healthy
  exit 0
fi
if [[ "$1" == "logs" ]]; then
  echo fake logs
  exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _write_fake_mv(path: Path) -> None:
    real_mv = shutil.which("mv") or "/bin/mv"
    path.write_text(
        f"""#!/usr/bin/env bash
if [[ "$1" == "-T" ]]; then
  src="$2"
  dst="$3"
  rm -f "$dst"
  exec {real_mv} "$src" "$dst"
fi
exec {real_mv} "$@"
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _safe(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "-" for char in value)[:120]
