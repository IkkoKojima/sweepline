#!/bin/bash
# live_kit.sh — 実行時に使う kit (このプラグインの実体) の場所と版。
#
# クラウド環境のスナップショットには setup 時の kit (ランチャ) が焼かれていて、環境 API を使わずに差し替えられない。
# そこでセッション開始時 (SessionStart hook) に、repo の sweepline.toml の `kit` が指す版を配布元から取って
# `live` に置き、スキル (版が違えば live の SKILL.md を読んで従う) とスクリプト (KIT は live を優先) がそれを使う。
# 更新 = sweepline.toml の `kit` を変えて main に入れるだけ (ローカルの環境 API は要らない)。
#
#   live_kit.sh sync     クラウドで hook から: toml の kit の版を live に取る。1 行 `SYNCED=live|snapshot|failed|skipped REF=… VERSION=… (理由)`
#   live_kit.sh path     使う kit の plugins/sweepline ディレクトリ (live があれば live、無ければこのスクリプトの kit)
#   live_kit.sh status   `LIVE=<dir|none> VERSION=<live の版|none> SNAPSHOT=<この kit の版> REF=<toml の kit|none>` (スキルの冒頭が読む)
#
# `kit` の書き方: commit sha / タグ (`0.5.0` か `v0.5.0`) / `latest` (配布元の main に追従)。
# 置き場所は SWEEPLINE_ROOT/live (/opt/sweepline が書けなければ $HOME/sweepline)。常に exit 0 (飾りではないが、失敗しても
# スナップショットの kit で動ける。失敗は stdout の 1 行で分かる)。
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SELF_KIT="$(cd "$HERE/.." && pwd)"                 # このスクリプトの plugins/sweepline
KIT_REPO="${KIT_REPO:-IkkoKojima/sweepline}"
PY="${SWEEPLINE_PYTHON:-}"
if [[ -z "$PY" ]]; then
  if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=python; fi
fi

root_dir() {
  local r="${SWEEPLINE_ROOT:-/opt/sweepline}"
  if [[ -d "$r" && -w "$r" ]] || mkdir -p "$r" 2>/dev/null && [[ -w "$r" ]]; then printf '%s' "$r"; else printf '%s' "${HOME:-/root}/sweepline"; fi
}

live_dir() {  # live の plugins/sweepline (無ければ空)
  local d
  for d in "$(root_dir)/live/plugins/sweepline" /opt/sweepline/live/plugins/sweepline "${HOME:-/root}/sweepline/live/plugins/sweepline"; do
    if [[ -d "$d/scripts" && -f "$d/.claude-plugin/plugin.json" ]]; then printf '%s' "$d"; return 0; fi
  done
  return 1
}

version_of() {  # <plugins/sweepline のディレクトリ> → plugin.json の version (読めなければ空)
  [[ -f "$1/.claude-plugin/plugin.json" ]] || return 0
  "$PY" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("version", ""))' "$1/.claude-plugin/plugin.json" 2>/dev/null | tr -d '\r'
}

toml_kit() {  # カレント (repo) の sweepline.toml の kit (無ければ空)
  [[ -f sweepline.toml ]] || return 0
  "$PY" "$HERE/sweepline_config.py" get kit --default "" 2>/dev/null | tr -d '\r'
}

cmd_path() {
  local live
  if live="$(live_dir)"; then printf '%s\n' "$live"; else printf '%s\n' "$SELF_KIT"; fi
}

cmd_status() {
  local live lv ref
  ref="$(toml_kit)"
  if live="$(live_dir)"; then lv="$(version_of "$live")"; else live=none; lv=none; fi
  echo "LIVE=$live VERSION=${lv:-unknown} SNAPSHOT=$(version_of "$SELF_KIT") REF=${ref:-none}"
}

# 配布元の tarball を <出力ディレクトリ> に展開する。タグは `v` 付きも試す。成功なら 0
fetch_kit() {
  local ref="$1" out="$2" tgz try
  tgz="$(mktemp 2>/dev/null || echo "${TMPDIR:-/tmp}/sweepline-live.tgz")"
  for try in "$ref" "v$ref"; do
    if curl -fsSL --max-time 90 -o "$tgz" "https://codeload.github.com/$KIT_REPO/tar.gz/$try" 2>/dev/null \
       && mkdir -p "$out" && tar -xzf "$tgz" -C "$out" --strip-components=1 2>/dev/null \
       && [[ -f "$out/.claude-plugin/marketplace.json" ]]; then
      rm -f "$tgz"; printf '%s\n' "$try" > "$out/.ref"; return 0
    fi
    rm -rf "$out"
  done
  rm -f "$tgz"
  return 1
}

cmd_sync() {
  local ref root live new snap_sha snap_ver live_ver
  ref="$(toml_kit)"
  root="$(root_dir)"
  # 前回の live が残っていても (同じ VM で hook が 2 回走ったなど) 使わない。取れなかったときはスナップショットの kit に戻る
  rm -rf "$root/live" "$root/live.new" 2>/dev/null
  snap_sha="$(cat "$root/kit/.sha" 2>/dev/null || cat /opt/sweepline/kit/.sha 2>/dev/null)"
  snap_ver="$(version_of "$SELF_KIT")"
  if [[ -z "$ref" ]]; then echo "SYNCED=skipped REF=none VERSION=${snap_ver:-unknown} (sweepline.toml に kit が無い → スナップショットの kit を使う)"; return 0; fi
  if [[ "$ref" == "latest" ]]; then ref=main; fi
  if [[ -n "$snap_sha" && "$ref" == "$snap_sha" ]]; then echo "SYNCED=snapshot REF=$ref VERSION=${snap_ver:-unknown} (toml の kit はスナップショットと同じ)"; return 0; fi
  live="$root/live"; new="$root/live.new"
  if fetch_kit "$ref" "$new"; then
    rm -rf "$live" && mv "$new" "$live" || { rm -rf "$new"; echo "SYNCED=failed REF=$ref VERSION=${snap_ver:-unknown} (live の入れ替えに失敗 → スナップショットの kit を使う)"; return 0; }
    chmod -R a+rX "$live" 2>/dev/null || true
    live_ver="$(version_of "$live/plugins/sweepline")"
    echo "SYNCED=live REF=$(cat "$live/.ref" 2>/dev/null || echo "$ref") VERSION=${live_ver:-unknown} SNAPSHOT=${snap_ver:-unknown} DIR=$live/plugins/sweepline"
  else
    echo "SYNCED=failed REF=$ref VERSION=${snap_ver:-unknown} (配布元 codeload.github.com から取れない → スナップショットの kit を使う。kit の値が sha / タグ / latest か確認)"
  fi
  return 0
}

case "${1:-}" in
  sync)   cmd_sync ;;
  path)   cmd_path ;;
  status) cmd_status ;;
  *)      echo "使い方: live_kit.sh {sync|path|status}" >&2; exit 2 ;;
esac
exit 0
