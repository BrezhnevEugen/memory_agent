#!/usr/bin/env bash
# Bump ./VERSION (semver) and open a new CHANGELOG section.
#   ./bump.sh patch|minor|major   or   ./bump.sh 1.2.3
set -euo pipefail
cd "$(dirname "$0")"
cur=$(tr -d '[:space:]' < VERSION); IFS=. read -r M m p <<< "$cur"
case "${1:-patch}" in
  major) new="$((M+1)).0.0" ;;
  minor) new="$M.$((m+1)).0" ;;
  patch) new="$M.$m.$((p+1))" ;;
  *) new="$1" ;;
esac
echo "$new" > VERSION
{ printf '# Changelog\n\n## %s — %s\n\n- \n\n' "$new" "$(date +%Y-%m-%d)"; tail -n +2 CHANGELOG.md; } > CHANGELOG.tmp && mv CHANGELOG.tmp CHANGELOG.md
echo "$cur → $new  (edit CHANGELOG.md, then ./build.sh && ./release.sh)"
