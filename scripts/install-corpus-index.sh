#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage: install-corpus-index.sh \
  --artifact <path-to-index-tarball.tar.gz> \
  --sha256 <path-to-.sha256-file> \
  --deploy-root /docker/youknowme/data \
  --compose-dir /docker/youknowme \
  --compose-service youknowme \
  --container-name youknowme-mcp
USAGE
}

die() {
  echo "install-corpus-index: $*" >&2
  exit 1
}

artifact=""
sha256_file=""
deploy_root=""
compose_dir=""
compose_service=""
container_name=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --artifact)
      [[ $# -ge 2 ]] || die "--artifact requires a value"
      artifact="$2"
      shift 2
      ;;
    --sha256)
      [[ $# -ge 2 ]] || die "--sha256 requires a value"
      sha256_file="$2"
      shift 2
      ;;
    --deploy-root)
      [[ $# -ge 2 ]] || die "--deploy-root requires a value"
      deploy_root="$2"
      shift 2
      ;;
    --compose-dir)
      [[ $# -ge 2 ]] || die "--compose-dir requires a value"
      compose_dir="$2"
      shift 2
      ;;
    --compose-service)
      [[ $# -ge 2 ]] || die "--compose-service requires a value"
      compose_service="$2"
      shift 2
      ;;
    --container-name)
      [[ $# -ge 2 ]] || die "--container-name requires a value"
      container_name="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      die "unknown argument: $1"
      ;;
  esac
done

[[ -n "$artifact" ]] || die "--artifact is required"
[[ -n "$sha256_file" ]] || die "--sha256 is required"
[[ -n "$deploy_root" ]] || die "--deploy-root is required"
[[ -n "$compose_dir" ]] || die "--compose-dir is required"
[[ -n "$compose_service" ]] || die "--compose-service is required"
[[ -n "$container_name" ]] || die "--container-name is required"

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

require_command docker
require_command python3
require_command sha256sum
require_command tar

abs_path() {
  python3 - "$1" <<'PY'
from pathlib import Path
import sys

print(Path(sys.argv[1]).resolve(strict=True))
PY
}

artifact="$(abs_path "$artifact")"
sha256_file="$(abs_path "$sha256_file")"
deploy_root="$(python3 - "$deploy_root" <<'PY'
from pathlib import Path
import sys

print(Path(sys.argv[1]).resolve())
PY
)"
compose_dir="$(python3 - "$compose_dir" <<'PY'
from pathlib import Path
import sys

print(Path(sys.argv[1]).resolve())
PY
)"

[[ -f "$artifact" ]] || die "artifact is not a file: $artifact"
[[ -f "$sha256_file" ]] || die "sha256 file is not a file: $sha256_file"
[[ -d "$deploy_root" ]] || die "deploy root is not a directory: $deploy_root"
[[ -f "$compose_dir/docker-compose.yml" ]] || die "compose file is missing: $compose_dir/docker-compose.yml"

artifact_dir="$(dirname "$artifact")"
echo "Verifying artifact checksum..."
(cd "$artifact_dir" && sha256sum -c "$sha256_file")

builds_dir="$deploy_root/index-builds"
current_link="$deploy_root/index-current"
mkdir -p "$builds_dir"

staging_dir="$(mktemp -d "$builds_dir/.install.XXXXXXXXXX")"
tmp_link="$deploy_root/.index-current.tmp.$$"
cleanup() {
  rm -rf "$staging_dir"
  rm -f "$tmp_link"
}
trap cleanup EXIT

echo "Extracting artifact..."
if tar -tzf "$artifact" | grep -Eq '(^/|(^|/)\.\.(/|$))'; then
  die "artifact contains unsafe paths"
fi
tar -xzf "$artifact" -C "$staging_dir"
if find "$staging_dir" -type l | grep -q .; then
  die "artifact contains symlinks"
fi

metadata="$(
  python3 - "$staging_dir" <<'PY'
from pathlib import Path
import json
import sys

root = Path(sys.argv[1])
candidates = [root / "index", root]
index = next((path for path in candidates if (path / "manifest.json").is_file()), None)
if index is None:
    raise SystemExit("manifest.json not found at artifact root or index/")

required = [index / "manifest.json", index / "chunks.jsonl", index / "lancedb"]
missing = [path.name for path in required if not path.exists()]
if missing:
    raise SystemExit(f"missing required index paths: {', '.join(missing)}")
if not (index / "lancedb").is_dir():
    raise SystemExit("lancedb is not a directory")

manifest = json.loads((index / "manifest.json").read_text(encoding="utf-8"))
source_commit = str(manifest.get("source_commit") or "")
build_id = str(manifest.get("build_id") or "")
if not source_commit:
    raise SystemExit("manifest source_commit is required")
if not build_id:
    raise SystemExit("manifest build_id is required")

def safe(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "-" for char in value)[:120]

safe_source = safe(source_commit)
safe_build = safe(build_id)
if not safe_source or not safe_build:
    raise SystemExit("manifest source_commit/build_id did not produce a safe install id")

print(index)
print(f"{safe_source}-{safe_build}")
PY
)" || die "$metadata"

extracted_index="$(printf '%s\n' "$metadata" | sed -n '1p')"
install_id="$(printf '%s\n' "$metadata" | sed -n '2p')"
build_dir="$builds_dir/$install_id"

[[ -n "$extracted_index" ]] || die "could not determine extracted index path"
[[ -n "$install_id" ]] || die "could not determine install id"
[[ ! -e "$build_dir" ]] || die "index build already exists: $build_dir"

echo "Installing index build $install_id..."
mv "$extracted_index" "$build_dir"
touch "$build_dir"

for required_path in "$build_dir/manifest.json" "$build_dir/chunks.jsonl" "$build_dir/lancedb"; do
  [[ -e "$required_path" ]] || die "installed index is missing required path: $required_path"
done
[[ -d "$build_dir/lancedb" ]] || die "installed lancedb path is not a directory"

if [[ -e "$current_link" && ! -L "$current_link" ]]; then
  die "index-current exists but is not a symlink: $current_link"
fi
ln -s "$build_dir" "$tmp_link"
mv -T "$tmp_link" "$current_link"
[[ -f "$current_link/manifest.json" ]] || die "index-current does not point to a valid index"

echo "Recreating container $compose_service..."
docker compose -f "$compose_dir/docker-compose.yml" up -d --force-recreate "$compose_service"

echo "Waiting for $container_name to become healthy..."
healthy="false"
for _ in $(seq 1 60); do
  status="$(docker inspect -f '{{.State.Health.Status}}' "$container_name" 2>/dev/null || true)"
  if [[ "$status" == "healthy" ]]; then
    healthy="true"
    break
  fi
  sleep 2
done

if [[ "$healthy" != "true" ]]; then
  echo "Container $container_name did not become healthy." >&2
  docker logs --tail 50 "$container_name" >&2 || true
  exit 1
fi

echo "Pruning old index builds..."
python3 - "$builds_dir" "$build_dir" <<'PY'
from pathlib import Path
import os
import shutil
import stat
import sys

builds_dir = Path(sys.argv[1]).resolve()
current = Path(sys.argv[2]).resolve()
builds = sorted(
    (path for path in builds_dir.iterdir() if path.is_dir()),
    key=lambda path: path.stat().st_mtime,
    reverse=True,
)
for path in builds[3:]:
    resolved = path.resolve()
    if resolved == current:
        continue

    errors = []

    def make_writable_and_retry(function, item, exc_info):
        try:
            os.chmod(item, stat.S_IWUSR | stat.S_IRUSR | stat.S_IXUSR)
            function(item)
        except OSError as err:
            errors.append(err)

    try:
        shutil.rmtree(path, onerror=make_writable_and_retry)
    except OSError as err:
        errors.append(err)
    if errors or path.exists():
        err = errors[0] if errors else "path still exists after prune attempt"
        print(f"warning: could not fully prune {path}: {err}", file=sys.stderr)
PY

echo "Installed corpus index $install_id"
