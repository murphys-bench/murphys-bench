#!/usr/bin/env bash
# Cut a Murphy's Bench release tag. Run on the DEV machine, after CI is green on main.
#
#   scripts/release.sh v0.1.0
#
# A release tag marks a CI-validated commit as deployable. On the server,
# `scripts/update.sh` with no argument deploys the latest such tag — so tagging is
# how a tested commit becomes the thing prod runs. Semver: vMAJOR.MINOR.PATCH.
#
# Guards (fail-loud): valid version format, on main, clean tree, local main is in
# sync with origin/main (so the tag points at a commit GitHub Actions actually
# ran), and the tag isn't already taken.
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

fail() { echo "RELEASE FAILED: $*" >&2; exit 1; }

V="${1:-}"
[[ "$V" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] \
    || fail "usage: scripts/release.sh vMAJOR.MINOR.PATCH   (e.g. v0.1.0)"

[ "$(git rev-parse --abbrev-ref HEAD)" = "main" ] \
    || fail "must be on the 'main' branch to cut a release"

git diff --quiet && git diff --cached --quiet \
    || fail "working tree not clean — commit or stash first"

git fetch --quiet origin main || fail "git fetch failed"
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] \
    || fail "local main != origin/main — push first so CI validates the commit you're tagging"

git rev-parse "$V" >/dev/null 2>&1 && fail "tag $V already exists"

SHA="$(git rev-parse --short HEAD)"
git tag -a "$V" -m "Murphy's Bench $V" || fail "git tag failed"
git push origin "$V" || fail "git push of tag $V failed (tag created locally; re-push with: git push origin $V)"

echo
echo "Released $V at $SHA."
echo "Deploy it on a server:"
echo "    cd /opt/murphys-bench && scripts/update.sh        # no-arg = latest tag = $V"
echo "    cd /opt/murphys-bench && scripts/update.sh $V     # or pin explicitly"
