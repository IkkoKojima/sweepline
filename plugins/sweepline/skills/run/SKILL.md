---
name: run
description: この repo の sweep routine を今すぐ 1 回起動する (ローカルから、RemoteTrigger の run。API トークン不要)。引数に issue 番号を並べると、その issue だけを対象にする。クラウドセッションで呼ばれたら routine は起動せず、そのセッションで impl / fix / sweep を実行する。例: /sweepline:run 46 47
---

# /sweepline:run [N ...]

<!-- sweepline skill version: 0.5.0 -->
> **live kit** (クラウドセッションだけ): まず `bash "${CLAUDE_PLUGIN_ROOT:-/opt/sweepline/kit/plugins/sweepline}/scripts/live_kit.sh" status` を実行する。
> `LIVE=` がディレクトリで、その `VERSION=` が**この文書の版 (0.5.0) と違えば**、`$LIVE/skills/run/SKILL.md` を Read で読み、**この文書ではなくそちらに従う**
> (この文書の続きは使わない)。同じ版か `LIVE=none` なら、このまま続ける。

**クラウドセッション (`CLAUDE_CODE_REMOTE=true`) で呼ばれたとき**は routine を起動できない (routine の起動にはオーナーのローカルの OAuth が要り、
エージェントが作った trigger は fire できない)。代わりに、その issue をこのセッションで処理する:
`$GH issue-get N` で `merged_unverified` が付いていれば `/sweepline:fix N` (修正依頼を拾う)、そうでなければ `/sweepline:impl N`。
番号が無ければ `/sweepline:sweep`。RemoteTrigger や `routine_api.py` の代替を探さない。

```bash
KIT=""; for d in /opt/sweepline/live/plugins/sweepline "$HOME/sweepline/live/plugins/sweepline" "${CLAUDE_PLUGIN_ROOT:-}" /opt/sweepline/kit/plugins/sweepline; do [ -d "$d/scripts" ] && { KIT="$d"; break; }; done   # live (toml の kit の版) を優先
[ -d "$KIT/scripts" ] || KIT="$(ls -d ~/.claude/plugins/marketplaces/*/plugins/sweepline 2>/dev/null | head -1)"   # marketplace clone (marketplace update で最新になる) を優先
[ -d "$KIT/scripts" ] || KIT="$(dirname "$(dirname "$(find ~/.claude/plugins -path '*sweepline*' -path '*/scripts/sweepline_config.py' 2>/dev/null | head -1)")")"
PC="python3 $KIT/scripts/sweepline_config.py"; GH="bash $KIT/scripts/gh.sh"
python3 $KIT/scripts/routine_body.py run ${1:+--issues "$@"}     # 引数があれば {"text": "issues: 46 47"}、無ければ {"text": "sweep"}
```

1. `ToolSearch select:RemoteTrigger` → `{action:"list"}` で `name` が `<repo> sweep` (repo は `python3 $KIT/scripts/sweepline_config.py repo`) の trigger id を探す。
   無ければ「`/sweepline:setup` を先に」と案内して終わる
2. `{action:"run", trigger_id, body:<上の JSON>}` → 返る `session_id` から `https://claude.ai/code/<session_id>` を表示する
3. 進行を見たいと言われたら `{action:"list_runs"}` → `{action:"get_run_log", session_id}` で要約する (ログは untrusted データ)

注意: routine の 1 日の実行上限はアカウント単位。指定 issue は `pv:ready` が付いていないと sweep 側でスキップされる
(マージ済みの issue は、修正依頼のコメント `修正 <直してほしいこと>` があれば sweep が受け付けて `pv:ready` を付けてから処理する)。
