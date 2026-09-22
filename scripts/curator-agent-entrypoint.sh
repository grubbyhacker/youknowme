#!/usr/bin/env bash
# Fixed container invocation contract for the youknowme-curator agent image.
#
# The platform mounts /input read-only and supplies the typed work item at
# /input/work-item.json; results are written under /output. Mode and all other
# parameters travel inside the work item, never on this command line -- so this
# entrypoint takes no mode argument and passes none.
#
# Any additional arguments are forwarded to `curator run` unchanged, which lets
# the platform pass operational flags (e.g. --run-id) without changing the
# contract. The input and output paths are fixed and are not overridable here.
set -euo pipefail

INPUT_WORK_ITEM="${YKM_CURATOR_INPUT:-/input/work-item.json}"
OUTPUT_DIR="${YKM_CURATOR_OUTPUT:-/output}"

if [[ ! -f "${INPUT_WORK_ITEM}" ]]; then
  printf 'curator-agent: required work item %s is missing\n' "${INPUT_WORK_ITEM}" >&2
  exit 64
fi

mkdir -p "${OUTPUT_DIR}"

exec curator run \
  --task "${INPUT_WORK_ITEM}" \
  --output "${OUTPUT_DIR}" \
  "$@"
