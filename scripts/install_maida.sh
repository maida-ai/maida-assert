#!/usr/bin/env bash
# Install the reviewed engine group without changing the consumer's environment config.
set -euo pipefail

: "${MAIDA_ACTION_ROOT:?Pass the trusted Action root}"
: "${MAIDA_VERSION:?Pass a Maida version}"

if [[ "$MAIDA_VERSION" == @* && "${MAIDA_ALLOW_GIT:-true}" == true ]]; then
  requirement="git+https://github.com/maida-ai/maida.git${MAIDA_VERSION}"
elif [[ "$MAIDA_VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([a-z0-9.]+)?$ ]]; then
  requirement="maida-ai==${MAIDA_VERSION:1}"
else
  echo '::error::Use vVERSION for a released Maida version, or @REF where supported.' >&2
  exit 2
fi

manifest="$(mktemp "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/maida-install.XXXXXX")"
trap 'rm -f "$manifest"' EXIT
# --offline forbids index access during export; --locked rejects stale metadata.
uv export --locked --offline --only-group maida --no-emit-project \
  --project "$MAIDA_ACTION_ROOT" --python "$(command -v python)" --output-file "$manifest" > /dev/null
locked_version="$(sed -n 's/^maida-ai==\([^ ;\\]*\).*/v\1/p' "$manifest")"
if [[ -z "$locked_version" ]]; then
  echo '::error::The reviewed uv lock must contain maida-ai in the maida group.' >&2
  exit 2
fi

if [[ "$MAIDA_VERSION" == "$locked_version" ]]; then
  uv pip install --python "$(command -v python)" --require-hashes --only-binary=:all: -r "$manifest"
else
  echo '::warning::Custom maida-version bypasses the reviewed dependency lock; review and pin this dependency separately.'
  uv pip install --python "$(command -v python)" "$requirement"
fi
