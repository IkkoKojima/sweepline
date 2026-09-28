# sweepline

Issue 駆動の自動実装パイプラインを **Claude Code プラグイン**として配布するリポジトリ。
GitHub Issues に `pv:ready` を付けると、Claude Code on the web のクラウドセッション (routine) が
「計画 → 計画レビュー (Codex / Fable) → 実装 (Opus) → 検証 → PR → 自動マージ」まで無人で通し、`/sweepline:release` で配信する。

設計と経緯: [pokemonitor/docs/pipeline/plugin-plan.md](https://github.com/IkkoKojima/pokemonitor/blob/main/docs/pipeline/plugin-plan.md)
(v4.2 の運用設計は同 `cloud-session-pipeline-plan.md`。pokemonitor は private なので第三者には開けない。要点はこの README にある)。

## ダッシュボード (sweepline dashboard)

issue をスマホのブラウザからカンバンで動かす Web が別リポジトリ [IkkoKojima/sweepline_dashboard](https://github.com/IkkoKojima/sweepline_dashboard) にある。GitHub アカウントでログインし、GitHub App `sweepline-dashboard` を自分の repo にインストールすると、`sweepline.toml` のある repo の issue が 6 列 (バックログ / 着手 / 進行中 / 要確認 / マージ済み / 完了) で見える。列の移動はラベル操作に対応し、「進行中」への移動は `/sweepline:impl N` を入力済みにした Claude Code on the web の画面を開いて利用者が送信する (トークンは預からない)。

## 導入 (オーナーの作業)

一度だけ (アカウント):

```
claude plugin marketplace add IkkoKojima/sweepline
claude plugin install sweepline@sweepline
claude                 # 任意の repo で
/web-setup             # 確認 Enter 1 回。以後この gh が見える全 repo をクラウドが clone できる
```

リポジトリごと:

```
claude                 # 対象 repo で
/sweepline:setup        # スタック検出 → sweepline.toml → クラウド環境 (API) → routine → ラベル / 固定 issue → 疎通 run → 貼り付け案内
```

`/sweepline:setup` は**オーナーの対話セッション**で実行する (routine 作成に使う `RemoteTrigger` はサブエージェントや非対話実行には無い)。
生成した `sweepline.toml` は **main に入って初めて効く** (クラウドは main を clone する) ので、保護ブランチや CI がある repo では PR とその完了を待ってから疎通 run に進む。
既存 CI があれば、その apt 依存・テストコマンド・集約 check 名を `sweepline.toml` に揃える (setup が案内する)。

貼り付けるもの: 環境の API credentials に `OPENAI_API_KEY` (任意。無ければ計画レビューは Fable 代替)。配信を使うなら `/sweepline:setup --deploy` の案内に従う。

日常: `/sweepline:req <一言>` で issue → 定時 sweep を待つか `/sweepline:run N` → 固定 issue `#sweepline-status` の要約 → `/sweepline:release patch` → 実機確認の結果を返す。
マージ済みの実装を直したいときは、issue を分けずに修正依頼を出す (下の「修正依頼」)。

## スキル

| スキル | どこで | 役割 |
|---|---|---|
| `/sweepline:setup` | ローカル | 導入・更新ウィザード (`--update` kit 更新 / `--deploy` 配信設定 / `--check` 確認のみ) |
| `/sweepline:req` | どこでも | 一言 → テンプレ準拠 issue |
| `/sweepline:run [N...]` | ローカル | sweep routine を今すぐ 1 回起動 (クラウドで呼ばれたら、そのセッションで impl / fix / sweep を実行) |
| `/sweepline:sweep` | クラウド (routine) | 無人運転の入口 |
| `/sweepline:impl N` | クラウド | 1 issue を計画 → レビュー → 実装 → 検証 → PR → マージ (修正依頼のある issue は修正ラウンド) |
| `/sweepline:fix N <直してほしいこと>` | どこでも | マージ済みの issue に修正依頼を出す (クラウドの impl 環境ならそのまま修正の PR まで) |
| `/sweepline:release` | クラウド (deploy 環境) | 版上げ → ノート → release PR → providers で配信 → 実機確認 |

## 修正依頼 (マージ済みの issue を、同じ issue のまま直す)

実装ログを読んで意図と違う実装に気づいたとき、実機確認でバグを見つけたときは、新しい issue を作らずに**元の issue に修正依頼を出す**。
パイプラインが同じ issue のまま「修正計画 → 計画レビュー → 実装 → 検証 → PR → マージ」を通し、issue は実機確認待ちに戻る。

| 入口 | 操作 | 着手 |
|---|---|---|
| コマンド | `/sweepline:fix 46 スマホ幅で移動シートが 2 重に開く` | クラウド (impl 環境) はそのセッションがすぐ |
| issue コメント | GitHub で issue に `修正 スマホ幅で移動シートが 2 重に開く` とコメント | 次の sweep が受け付けて着手 (急ぐなら `/sweepline:run 46`) |
| dashboard | マージ済みのカード → 「修正を依頼」 | 手渡しシートで送信すればすぐ。しなければ次の sweep |

- コメントの書式: 先頭が `修正` か `/sweepline:fix` で、直後に空白 (全角も)・改行・コロンを挟んで依頼の文。「修正しました」のように続けて書いた文は依頼にならない
- 受け付けるのは repo に書き込める人 (`author_association` が OWNER / MEMBER / COLLABORATOR) のコメントだけ。受け付けた依頼には 👀、修正をマージしたら 🚀 が付く
- 状態は `merged_unverified` を付けたまま `ready` を重ねる (新しいラベルは無い)。取り下げは `ready` を外すだけ
- 依頼の文は「何を直すか」としてだけ読む。禁止領域・検証・マージの経路などパイプラインの規則は依頼では変えられない
- 修正の PR は本文に `Rework-Request: <依頼コメントの URL>` 行を持つ (マージ前に `verify_checklist.py check --rework` が検証)。この行が処理済みの記録で、
  `gh.sh fix-requests N` が「何回目の修正か」と「未処理の依頼」を引く
- `/sweepline:release` の実機確認で NG のときも、新しい issue ではなく元 issue への修正依頼になる。チェックリストは最後の修正の PR の観点を展開する

## 版の更新 (kit の固定と、dashboard との版差)

各 repo のプラグインは `sweepline.toml` の `kit` (commit sha) とクラウド環境の init_script で固定されている (無人の routine の下で勝手に変わらないよう、
上げるタイミングは owner が決める)。更新は **owner がローカルの Claude Code でその repo を開き** `/sweepline:setup --update` (環境の init_script と
`kit` を最新にして commit → main へ)。クラウドセッションには利用者の claude.ai OAuth が置かれないため、クラウドからは環境を書き換えられない。

- sweep の要約と、クラウドセッション開始時の 1 行に、新しい版の有無が出る (`scripts/kit_update_check.sh`。配布元 main の `plugin.json` と比べる)
- sweepline dashboard は `kit` から版を引き、版に依る機能 (修正依頼 = 0.4.0 以上) を満たさない repo では出さず、更新の案内を出す
- 約束事: dashboard が新しい約束事 (ラベル・コメントの書式) に依存する機能を足すときは最低版を宣言し、プラグインは後方互換を保つ

## sweepline.toml

repo ルートに置く唯一の設定 (`/sweepline:setup` が生成)。省略した項目は preset が補い、書いた値が常に優先する。

```toml
version = 1
kit = "<commit sha>"           # setup が書く。環境の setup script と一致

[models]
session = "fable"              # routine のモデル
implementer = "opus"           # 実装サブエージェント
plan_review = "gpt-6-astra"    # Codex。API 制限時は Fable 代替

[sweep]
max_issues = 3
cron = "0 4,16 * * *"          # UTC
hours_budget = 2

[env]                          # クラウド環境 (省略時 name = "sweepline-" + stacks 名)
name = "sweepline-flutter"
extra_hosts = []               # 既定リスト + stack のホストに追加
extra_apt = []                 # setup で apt-get install
[env.vars]
SWEEPLINE_ENV = "impl"

[[stacks]]                     # 複数可。path で monorepo を分ける。変更ファイルのパスで検証を選ぶ
name = "flutter"               # flutter / node / python / deno / generic
path = "."
flutter_version = "3.41.9"     # stack 固有オプション (preset の opts)
verify_always = ["flutter analyze --no-fatal-infos", "timeout 1200 flutter test"]   # 省略時は preset
[stacks.verify_paths]          # glob → 追加コマンド
"ml/**" = ["python3 -m pytest ml/tests -q"]

[labels]                       # 省略時の既定。既存ラベルと衝突するときだけ変える
ready = "pv:ready"
in_progress = "pv:in-progress"
merged_unverified = "pv:merged-unverified"
blocked = "pv:blocked"
skipped = "pv:skipped"
release = "release"

[verify]
forbidden_paths = []           # 既定 (sweepline.toml .claude/** .github/** codemagic.yaml) に追加
review_focus = []              # 計画レビューで特に見る領域
[verify.env]                   # 検証コマンドに渡すダミー値 (秘密は書かない)

[merge]
wait_for_checks = []           # 例: ["ci-ok"]。既存 CI が緑になるまで待ってから squash

[release]
version_stack = "flutter"      # 版を持つ stack
notes_dir = "release_notes"
[[release.providers]]
type = "codemagic"             # codemagic / vercel / cloudflare-pages / supabase-mcp / actions-dispatch / shell
workflows = ["android-internal", "ios-testflight"]
```

### stack preset

| name | 検出 | verify_always | build_smoke | 追加ホスト |
|---|---|---|---|---|
| flutter | `pubspec.yaml` | `flutter pub get` / `flutter analyze --no-fatal-infos` / `flutter test` | `pubspec.*` `android/**` → `flutter build apk --debug` | dl.google.com, maven.google.com |
| node | `package.json` | `npm ci` / lint / test / build (`--if-present`) | なし | nodejs.org |
| python | `pyproject.toml` / `requirements*.txt` | uv (`uv.lock` があれば) or pip → pytest | なし | astral.sh |
| deno | `deno.json` / `supabase/functions` (検出時の path は `supabase`) | `deno test -A` | なし | deno.land, dl.deno.land, jsr.io, esm.sh |
| generic | なし | なし (sweepline.toml に書く) | なし | なし |

### provider インターフェース

`scripts/providers/<type>.sh <fn>`: `preflight` / `deploy <version> <sha>` / `status <id>` / `secrets_list` / `notes_limits`。`supabase-mcp` は MCP を使う手順書。
各 `[[release.providers]]` には共通で `only_if_changed = ["glob", ...]` を書ける (PREV..RELEASE_SHA にその差分が無ければ「スキップ (差分なし)」)。`--scope type,...` は provider の type で絞る。

## 既知の制約

- pip 経路の python は隔離 venv (`<stack path>/.sweepline/venv`) で動く。`python_version` は uv がある環境でその venv の版になる (無ければ VM 既定の 3.11)
- `release.version_stack` が無い (版が `pubspec.yaml` / `package.json` / `pyproject.toml` に無い) repo では、`/sweepline:release` の版上げは手動
- 既存の自動化 (issue コメントをコマンドとして読む workflow など) がある repo では、`/sweepline:setup` の案内に従って衝突を確認する
- routine の作成はオーナーの OAuth が要る。対話セッションでは `RemoteTrigger`、それ以外は `scripts/routine_api.py ensure`

## プラグインの更新 (作者向け)

`claude plugin update` は `plugins/sweepline/.claude-plugin/plugin.json` の `version` が上がったときだけ新しい版を取り込む。
公開する変更を main に入れたら `version` (と marketplace.json の同名項目) を必ず上げる。利用側は `claude plugin marketplace update sweepline && claude plugin update sweepline@sweepline`。

## 配布の仕組み

クラウド環境の setup script がこのリポジトリの pinned tarball (`codeload.github.com/.../tar.gz/<sha>`) を `/opt/sweepline/kit` に展開し、
ローカルパスの marketplace として `claude plugin install sweepline@sweepline` する。セッションでは `CLAUDE_PLUGIN_ROOT` にこのプラグインが載り、
hook とスキルが有効になる。更新は `/sweepline:setup --update` (環境の setup script の sha を書き換える)。

## レイアウト

```
.claude-plugin/marketplace.json
plugins/sweepline/
  .claude-plugin/plugin.json
  skills/{setup,req,impl,fix,sweep,release,run}/SKILL.md
  agents/{implementer,plan-reviewer}.md
  hooks/hooks.json                 SessionStart (クラウド限定)
  scripts/  sweepline_config.py  env_api.py  gh.sh  routine_body.py  verify.sh  codex_review.sh
            verify_checklist.py  fix_requests.py  kit_update_check.sh  pr_checks.sh  release_notes.sh  session_start.sh  providers/*.sh
  stacks/   <name>_session.sh      セッション開始時のスタック固有処理 (キャッシュ復元・依存解決)
  setup/    bootstrap.sh <stack>.sh finish.sh   環境 setup script の部品 (env_api.py render が結合)
  templates/ routine-prompt.md pr-body.md issue-body.md CLAUDE-sweepline-section.md status-issue-body.md
tests/                             スクリプトの単体テスト (`python -m unittest discover -s tests`。ネットワークと gh は使わない)
```
