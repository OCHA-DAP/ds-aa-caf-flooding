#!/usr/bin/env bash
# Publish the CAR floods site: build -> encrypt -> gh-pages.
#
#   SITE_PASSWORD=... scripts/publish.sh          # rebuild pages from data/, encrypt, push
#   SKIP_BUILD=1 SITE_PASSWORD=... scripts/publish.sh   # push the existing site_encrypted/
#
# Run scripts/build_data.py first (needs blob + prod DB access). The password is
# never stored in this (public) repo: pass it in the environment.
#
# Rules from ds-aa-tracking's incidents, adapted for a site with a product subfolder:
# - never `git add -A` on gh-pages (no .gitignore there; it would sweep in the
#   UNENCRYPTED site_build/)
# - stage an explicit whitelist with literal ./ paths
# - assert the staged tree, and the pushed tree, are exactly that whitelist, and
#   that every page is a staticrypt page with none of the plaintext page markers
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SITE_PASSWORD:?set SITE_PASSWORD (shared internally, not stored in the repo)}"

PAGES=(index.html impact-vs-rainfall/index.html)
EXPECTED=$(printf '%s\n' "${PAGES[@]}" .nojekyll | sort)

if [ -z "${SKIP_BUILD:-}" ]; then
  uv run python scripts/build_site.py
  rm -rf site_encrypted
  for p in "${PAGES[@]}"; do
    # one call per page so the output keeps the site layout; all share the salt
    # in .staticrypt.json, so "remember me" carries across pages
    STATICRYPT_PASSWORD="$SITE_PASSWORD" npx -y staticrypt@3.5.4 "site_build/$p" \
      -d "site_encrypted/$(dirname "$p")" --short --remember 30 \
      --template-title "CAR floods — restricted" \
      --template-instructions "OCHA Centre for Humanitarian Data. Password shared internally." >/dev/null
  done
fi

for p in "${PAGES[@]}"; do
  f="site_encrypted/$p"
  grep -q "staticrypt" "$f" || { echo "FATAL: $f is not a staticrypt page" >&2; exit 1; }
  # markers from the page body; none can occur in staticrypt's hex ciphertext
  if grep -q -e 'id="page-data"' -e 'id="page-files"' -e '"b64":' -e 'class="kpi"' -e 'class="k"' "$f"; then
    echo "FATAL: $f contains plaintext page content" >&2; exit 1
  fi
done

# gh-pages lives in its own temporary worktree, so publishing never switches
# branches in the working tree (where the unencrypted build sits untracked).
git fetch -q origin
WT="$(mktemp -d)/gh-pages"
trap 'git worktree remove --force "$WT" 2>/dev/null || true' EXIT
git worktree prune
if git rev-parse -q --verify origin/gh-pages >/dev/null; then
  git worktree add -q -B gh-pages "$WT" origin/gh-pages
else
  # first publish: start an orphan branch (dropping any local gh-pages left by a
  # failed earlier attempt, which never reached origin)
  git branch -D gh-pages >/dev/null 2>&1 || true
  git worktree add -q --detach "$WT"
  (
    cd "$WT"
    git checkout -q --orphan gh-pages
    git rm -rq --cached .
    find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
  )
fi

for p in "${PAGES[@]}"; do
  mkdir -p "$WT/$(dirname "$p")"
  cp "site_encrypted/$p" "$WT/$p"
done
touch "$WT/.nojekyll"
(
  cd "$WT"
  for f in "${PAGES[@]}" .nojekyll; do git add -f "./$f"; done
  if [ "$(git ls-files | sort)" != "$EXPECTED" ]; then
    echo "FATAL: gh-pages index is not exactly the whitelist:" >&2
    git ls-files >&2
    exit 1
  fi
  if git diff --cached --quiet && git rev-parse -q --verify HEAD >/dev/null; then
    echo "nothing changed — not publishing"
  else
    git commit -q -m "Publish site $(date +%F)"
    git push -q origin gh-pages
  fi
)
git fetch -q origin gh-pages
if [ "$(git ls-tree -r --name-only origin/gh-pages | sort)" != "$EXPECTED" ]; then
  echo "FATAL: pushed gh-pages tree is not exactly the whitelist!" >&2
  exit 1
fi
echo "published ✓ (tree = whitelist)"
