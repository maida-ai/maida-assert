#!/usr/bin/env bash
# Package only committed source; callers supply the tag event's immutable SHA.
set -euo pipefail

if [[ ! "${GITHUB_REF:-}" =~ ^refs/tags/v[0-9]+(\.[0-9]+)*([.-][A-Za-z0-9.-]+)?$ ]]; then
  echo '::error::Release builds require a version tag (vVERSION).' >&2
  exit 2
fi
if [[ ! "${GITHUB_SHA:-}" =~ ^[0-9a-f]{40}$ ]] || [[ "$(git rev-parse HEAD)" != "$GITHUB_SHA" ]]; then
  echo '::error::Checkout does not match the release event commit.' >&2
  exit 2
fi

output="${1:?Pass the release output directory}"
mkdir -p "$output"
git archive --format=tar --prefix=maida-assert/ "$GITHUB_SHA" | gzip -n > "$output/maida-assert.tar.gz"
(
  cd "$output"
  sha256sum maida-assert.tar.gz > SHA256SUMS
  sha256sum --check SHA256SUMS
)
