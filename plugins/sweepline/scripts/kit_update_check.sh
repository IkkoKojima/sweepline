#!/bin/bash
# kit_update_check.sh — プラグイン (kit) に新しい版があるかを見る (sweep の要約と SessionStart の 1 行用)。
#
#   kit_update_check.sh            → 1 行: CURRENT=<版> LATEST=<版|unknown> UPDATE=yes|no|unknown LATEST_SHA=<main の sha|unknown>
#
# 今の版はこのスクリプトの隣の .claude-plugin/plugin.json、最新は配布元 (公開 repo) の main の plugin.json を
# raw.githubusercontent.com から取る (5 秒で諦める。届かなければ LATEST=unknown)。飾りなので常に exit 0。
# 更新はオーナーがローカルで `/sweepline:setup --update` (クラウドセッションからは環境を書き換えられない)。
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIT_REPO="${KIT_REPO:-IkkoKojima/sweepline}"
PY="${SWEEPLINE_PYTHON:-}"
if [[ -z "$PY" ]]; then
  if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=python; fi
fi

version_of() {  # <plugin.json のパス or 空> → version (読めなければ空)
  [[ -n "$1" && -s "$1" ]] || return 0
  "$PY" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("version", ""))' "$1" 2>/dev/null | tr -d '\r'
}

CURRENT="$(version_of "$HERE/../.claude-plugin/plugin.json")"
TMP="$(mktemp 2>/dev/null || echo "${TMPDIR:-/tmp}/sweepline-plugin.json")"
LATEST=""
if curl -fsSL --max-time 5 -o "$TMP" "https://raw.githubusercontent.com/$KIT_REPO/main/plugins/sweepline/.claude-plugin/plugin.json" 2>/dev/null; then
  LATEST="$(version_of "$TMP")"
fi
rm -f "$TMP" 2>/dev/null

# main の sha (sweepline.toml の kit に書く値)。クラウドセッションの GitHub proxy は attach していない repo への api.github.com を
# 403 にする (公開 repo でも) ので git ls-remote を先に使い、api.github.com は git が無いときの予備 (届かなければ unknown)
LATEST_SHA=""
if command -v git >/dev/null 2>&1; then
  if command -v timeout >/dev/null 2>&1; then TO="timeout 10"; else TO=""; fi
  LATEST_SHA="$(GIT_TERMINAL_PROMPT=0 $TO git ls-remote "https://github.com/$KIT_REPO.git" refs/heads/main 2>/dev/null | cut -f1 | tr -d '\r')"
fi
[[ -n "$LATEST_SHA" ]] || LATEST_SHA="$(curl -fsSL --max-time 5 -H 'Accept: application/vnd.github+json' "https://api.github.com/repos/$KIT_REPO/commits/main" 2>/dev/null \
  | "$PY" -c 'import json,sys; print(json.load(sys.stdin).get("sha", ""))' 2>/dev/null | tr -d '\r')"

UPDATE=unknown
if [[ -n "$CURRENT" && -n "$LATEST" ]]; then
  # 桁ごとに数で比べる (足りない桁は 0)
  UPDATE="$("$PY" -c '
import sys
def parts(v): return [int(x) for x in v.strip().lstrip("v").split(".") if x.isdigit()]
a, b = parts(sys.argv[1]), parts(sys.argv[2])
n = max(len(a), len(b)); a += [0] * (n - len(a)); b += [0] * (n - len(b))
print("yes" if a < b else "no")' "$CURRENT" "$LATEST" 2>/dev/null | tr -d '\r')"
  [[ "$UPDATE" == yes || "$UPDATE" == no ]] || UPDATE=unknown
fi
echo "CURRENT=${CURRENT:-unknown} LATEST=${LATEST:-unknown} UPDATE=$UPDATE LATEST_SHA=${LATEST_SHA:-unknown}"
exit 0
