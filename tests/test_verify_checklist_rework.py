"""verify_checklist.py の修正ラウンド対応 (check --rework、generate のまとめ方) と、既存の check を壊していないことのテスト。

ネットワークと gh は使わない (generate は取得を差し替える)。

  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import contextlib
import io
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "sweepline" / "scripts"))
import sweepline_config as pc  # noqa: E402
import verify_checklist as vc  # noqa: E402

TEMPLATE = ROOT / "plugins" / "sweepline" / "templates" / "pr-body.md"
FORBIDDEN = list(pc.DEFAULT_FORBIDDEN) + ["content/legal/**", ".env.example"]
URL = "https://github.com/o/r/issues/46#issuecomment-789"


def filled(refs="Refs #46", extra=""):
    """既存のテンプレ (templates/pr-body.md) を埋めた PR 本文。extra は Refs 行の直後に足す行。"""
    text = TEMPLATE.read_text(encoding="utf-8")
    assert text.startswith("Refs #N\n"), "テンプレの先頭行が変わった"
    fills = iter([
        "- シートを開く操作を 1 回にまとめた",
        "- head abc1234 / `npm test` 成功 (120 passed) / 未実行: なし",
        "- ボード → カードをタップ → シートが 1 枚だけ開く (400px)\n- 同じ操作を 1280px で → モーダルが 1 枚だけ開く",
        "なし",
        "- P1 / 二重起動の防止 / 採用 / 再現テストあり",
        "計画レビュー 2 往復 (approved)。実装: なし (方針)",
    ])
    # 見出しの前 (Refs 行と、修正ラウンドの Rework-Request 行の案内) は head で置き換え、見出しの下の案内を埋める
    top, sep, sections = text.partition("\n## ")
    assert sep and "Rework-Request:" in top, "テンプレに Rework-Request 行の案内が無い"
    sections = re.sub(r"<!--.*?-->", lambda m: next(fills), sections, flags=re.DOTALL)
    assert next(fills, None) is None, "テンプレの見出しの数が変わった"
    head = refs + ("\n" + extra if extra else "")
    return f"{head}\n{sep}{sections}"


def errors(body, files=(), rework=False, allow_sweepline=False):
    return vc.check(body, list(files), allow_sweepline, FORBIDDEN, rework=rework)


class ExistingCheckTest(unittest.TestCase):
    """今までの check の挙動 (見出し 6 つ、Closes 禁止、禁止パス)。"""

    def test_テンプレを埋めた本文は通る(self):
        self.assertEqual(errors(filled(), ["lib/sheet.ts", "tests/unit/sheet.test.ts"]), [])

    def test_rework_を渡さない呼び出し_従来の引数_も通る(self):
        self.assertEqual(vc.check(filled(), ["lib/sheet.ts"], False, FORBIDDEN), [])

    def test_CRLF_の本文も通る(self):
        self.assertEqual(errors(filled().replace("\n", "\r\n")), [])

    def test_Refs_が無い(self):
        got = errors(filled(refs="関連: #46"))
        self.assertEqual(got, ["`Refs #<issue>` 行が無い (1 行に 1 つ)"])

    def test_Closes_は禁止(self):
        for word in ["Closes", "closes", "Fixes", "Resolved"]:
            with self.subTest(word=word):
                got = errors(filled(extra=f"{word} #46"))
                self.assertEqual(got, ["`Closes/Fixes/Resolves #N` は使わない (実機確認まで issue を開けておく)"])

    def test_見出しが無い(self):
        body = filled().replace("## 判断した点\nなし\n\n", "")
        self.assertEqual(errors(body), ["見出し `## 判断した点` が無い"])

    def test_見出しが_2_回ある(self):
        body = filled() + "\n## テスト結果\nもう 1 回\n"
        self.assertEqual(errors(body), ["見出し `## テスト結果` が 2 回ある"])

    def test_見出しの順序が違う(self):
        body = filled()
        a = body.index("## 変更点")
        b = body.index("## テスト結果")
        c = body.index("## 実機確認の観点")
        swapped = body[:a] + body[b:c] + body[a:b] + body[c:]
        got = errors(swapped)
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].startswith("見出しの順序が規約と違う"))

    def test_本文が空の見出し(self):
        body = filled().replace("## 判断した点\nなし\n", "## 判断した点\n")
        self.assertEqual(errors(body), ["`## 判断した点` の本文が空 (無ければ「なし」と書く)"])

    def test_コードフェンスの中の見出しは数えない(self):
        body = filled().replace("- シートを開く操作を 1 回にまとめた", "```md\n## テスト結果\n```\n- シートを開く操作を 1 回にまとめた")
        self.assertEqual(errors(body), [])

    def test_禁止パス(self):
        got = errors(filled(), ["lib/sheet.ts", ".github/workflows/ci.yml", "content/legal/terms.md", "sweepline.toml"])
        self.assertEqual(len(got), 3)
        self.assertTrue(all(e.startswith("禁止パスを変更している: ") for e in got))
        self.assertIn(".github/workflows/ci.yml", got[0])
        self.assertIn("--allow-sweepline", got[0])
        self.assertIn("content/legal/terms.md", got[1])
        self.assertIn("verify.forbidden_paths", got[1])
        self.assertIn("sweepline.toml", got[2])

    def test_allow_sweepline_は既定の禁止パスだけ解除する(self):
        got = errors(filled(), [".github/workflows/ci.yml", "content/legal/terms.md"], allow_sweepline=True)
        self.assertEqual(len(got), 1)
        self.assertIn("content/legal/terms.md", got[0])


class ReworkCheckTest(unittest.TestCase):
    def test_正しい(self):
        self.assertEqual(errors(filled(extra=f"Rework-Request: {URL}"), ["lib/sheet.ts"], rework=True), [])

    def test_CRLF_でも正しい(self):
        body = filled(extra=f"Rework-Request: {URL}").replace("\n", "\r\n")
        self.assertEqual(errors(body, rework=True), [])

    def test_複数の依頼に応える(self):
        extra = f"Rework-Request: {URL}\nRework-Request: https://github.com/o/r/issues/46#issuecomment-790"
        self.assertEqual(errors(filled(extra=extra), rework=True), [])

    def test_Refs_が複数ならどれかと一致すればよい(self):
        body = filled(refs="Refs #12\nRefs #46", extra=f"Rework-Request: {URL}")
        self.assertEqual(errors(body, rework=True), [])

    def test_本文の後ろに書いてもよい(self):
        body = filled() + f"\nRework-Request: {URL}\n"
        self.assertEqual(errors(body, rework=True), [])

    def test_rework_なのに行が無い(self):
        got = errors(filled(), rework=True)
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].startswith("`Rework-Request:` 行が無い"), got[0])

    def test_rework_でなければ行は無くてよい(self):
        self.assertEqual(errors(filled(), rework=False), [])

    def test_形が違う(self):
        bad = [
            "Rework-Request:",                                                          # 値が無い
            "Rework-Request: 789",
            "Rework-Request: #issuecomment-789",
            "Rework-Request: https://github.com/o/r/issues/46",                         # コメント id が無い
            "Rework-Request: https://github.com/o/r/issues/46#issuecomment-",
            "Rework-Request: https://github.com/o/r/issues/46#issuecomment-abc",
            "Rework-Request: https://github.com/o/r/pull/46#issuecomment-789",          # issues ではない
            "Rework-Request: http://github.com/o/r/issues/46#issuecomment-789",         # https ではない
            "Rework-Request: https://example.com/o/r/issues/46#issuecomment-789",
            "Rework-Request: https://github.com/o/issues/46#issuecomment-789",          # repo が無い
            f"Rework-Request: <{URL}>",
            f"Rework-Request: {URL} https://github.com/o/r/issues/46#issuecomment-790",  # 1 行に 2 つ
            f"Rework-Request: {URL} (スマホ幅の件)",
            f"  Rework-Request: {URL}",                                                 # 行頭からではない
            f"rework-request: {URL}",                                                   # 小文字
            f"Rework-Request：{URL}",                                                   # 全角コロン
            f"Rework-Request : {URL}",
        ]
        for line in bad:
            for rework in (True, False):
                with self.subTest(line=line, rework=rework):
                    got = errors(filled(extra=line), rework=rework)
                    self.assertEqual(len(got), 1, got)
                    self.assertTrue(got[0].startswith("`Rework-Request:` 行の形が規約と違う"), got[0])
                    self.assertIn(line.strip(), got[0])

    def test_正しい行と形の違う行が混ざる(self):
        got = errors(filled(extra=f"Rework-Request: {URL}\nRework-Request: 790"), rework=True)
        self.assertEqual(len(got), 1)
        self.assertIn("`Rework-Request: 790`", got[0])

    def test_issue_番号が_Refs_と違う(self):
        url = "https://github.com/o/r/issues/47#issuecomment-789"
        for rework in (True, False):
            with self.subTest(rework=rework):
                got = errors(filled(extra=f"Rework-Request: {url}"), rework=rework)
                self.assertEqual(got, [f"`Rework-Request:` の issue #47 が `Refs #N` 行に無い: {url}"])

    def test_Refs_も無ければ両方を挙げる(self):
        got = errors(filled(refs=f"Rework-Request: {URL}"), rework=True)
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0], "`Refs #<issue>` 行が無い (1 行に 1 つ)")
        self.assertTrue(got[1].startswith("`Rework-Request:` の issue #46 が `Refs #N` 行に無い"))

    def test_文中の_Rework_Request_は行として数えない(self):
        body = filled().replace("- シートを開く操作を 1 回にまとめた", "- 本文の規約に Rework-Request: を足した")
        self.assertEqual(errors(body), [])
        got = errors(body, rework=True)
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].startswith("`Rework-Request:` 行が無い"))

    def test_既存の違反と一緒に列挙する(self):
        body = filled(extra="Closes #46").replace("## 判断した点\nなし\n\n", "")
        got = errors(body, [".github/workflows/ci.yml"], rework=True)
        self.assertEqual(len(got), 4)
        self.assertTrue(got[0].startswith("`Closes/Fixes/Resolves #N`"))
        self.assertTrue(got[1].startswith("`Rework-Request:` 行が無い"))
        self.assertEqual(got[2], "見出し `## 判断した点` が無い")
        self.assertTrue(got[3].startswith("禁止パスを変更している: .github/workflows/ci.yml"))


class ReworkLinesTest(unittest.TestCase):
    def test_rework_lines(self):
        body = f"Refs #46\nRework-Request: {URL}\nRework-Request: x\n"
        self.assertEqual(vc.rework_lines(body), [
            {"line": f"Rework-Request: {URL}", "ok": True, "url": URL, "owner": "o", "repo": "r", "issue": 46, "comment_id": 789},
            {"line": "Rework-Request: x", "ok": False},
        ])
        self.assertEqual(vc.rework_lines(""), [])

    def test_rework_issues_は_Refs_と一致するものだけ(self):
        other = "https://github.com/o/r/issues/47#issuecomment-1"
        self.assertEqual(vc.rework_issues(f"Refs #46\nRework-Request: {URL}\n"), [46])
        self.assertEqual(vc.rework_issues(f"Refs #46\nRework-Request: {other}\n"), [])
        self.assertEqual(vc.rework_issues(f"Refs #46\nRefs #47\nRework-Request: {other}\nRework-Request: {URL}\n"), [46, 47])
        self.assertEqual(vc.rework_issues(f"Rework-Request: {URL}\n"), [])
        self.assertEqual(vc.rework_issues("Refs #46\nRework-Request: 789\n"), [])
        self.assertEqual(vc.rework_issues("Refs #46\n"), [])


class CheckCliTest(unittest.TestCase):
    """main の check (--rework の受け渡しと exit code)。"""

    def run_check(self, body, *flags):
        with tempfile.TemporaryDirectory() as d:
            b = Path(d) / "pr.md"
            f = Path(d) / "files.txt"
            b.write_text(body, encoding="utf-8", newline="\n")
            f.write_text("lib/sheet.ts\n", encoding="utf-8", newline="\n")
            out, err = io.StringIO(), io.StringIO()
            cfg = {"repo": "o/r", "labels": dict(pc.DEFAULT_LABELS), "verify": {"forbidden_paths": []}}
            with mock.patch.object(vc.pc, "load", return_value=cfg), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = vc.main(["--root", d, "check", "--pr-body", str(b), "--changed-files", str(f), *flags])
            return code, out.getvalue(), err.getvalue()

    def test_rework_無しは今までどおり(self):
        code, out, err = self.run_check(filled())
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, "ok: 本文の構成と変更範囲 (1 files) は規約どおり\n")

    def test_rework_で行が無ければ_exit_1(self):
        code, out, err = self.run_check(filled(), "--rework")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        lines = err.splitlines()
        self.assertEqual(lines[0], "PR 本文 / 変更範囲の検証に失敗:")
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[1].startswith("  - `Rework-Request:` 行が無い"))

    def test_rework_で正しければ_exit_0(self):
        code, out, err = self.run_check(filled(extra=f"Rework-Request: {URL}"), "--rework")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, "ok: 本文の構成と変更範囲 (1 files) は規約どおり\n")


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------

def pr(number, title, sha, body, merged_at="2026-09-01T00:00:00Z"):
    """マージ済み PR の JSON (使う項目だけ)。"""
    return {"number": number, "title": title, "merge_commit_sha": sha, "body": body, "merged_at": merged_at}


def pr_body(refs, observe, decisions="なし", codex="なし", rework=()):
    head = "".join(f"Refs #{n}\n" for n in refs) + "".join(f"Rework-Request: {u}\n" for u in rework)
    return (
        f"{head}\n## 変更点\n- x\n\n## テスト結果\n- ok\n\n## 実機確認の観点\n{observe}\n\n"
        f"## 判断した点\n{decisions}\n\n## Codex 指摘の採否\n{codex}\n\n## Codex 往復\nなし\n"
    )


def legacy_issue_lines(entries):
    """修正ラウンドを入れる前の generate の書き方 (出力を変えていないことの突き合わせ用)。"""
    out = []
    for e in entries:
        out.append(f"- PR #{e['pr']} {e['title']} (`{e['sha']}`)")
        out.append("  - [ ] 実機確認の観点:")
        out.extend(f"    {ln}" for ln in (e["observe"] or "(未記入)").splitlines())
        if e["decisions"] and e["decisions"] != "なし":
            out.append("  - 判断した点:")
            out.extend(f"    {ln}" for ln in e["decisions"].splitlines())
        if e["codex"] and e["codex"] != "なし":
            out.append("  - Codex 指摘の採否 (未解決があれば確認):")
            out.extend(f"    {ln}" for ln in e["codex"].splitlines())
    return out


FIRST = pr(30, "シートを足す", "aaaaaaa1111", pr_body([46], "- ボード → カード → シートが開く\n- 1280px でも開く",
                                                   decisions="- 選択肢 A / 採用 / 理由", codex="- P1 / 採用"))
SECOND = pr(33, "シートの幅", "bbbbbbb2222", pr_body([46], "- 400px で横にはみ出さない"))
FIX1 = pr(35, "シートが 2 重に開くのを直す", "ccccccc3333",
          pr_body([46], "- ボード → カード → シートが 1 枚だけ開く (400px)\n- 1280px でも 1 枚だけ", decisions="- 連打を無視 / 採用 / 簡単",
                  rework=[URL]))
FIX2 = pr(38, "閉じるボタン", "ddddddd4444",
          pr_body([46], "- シート → 閉じる → 閉じる", rework=["https://github.com/o/r/issues/46#issuecomment-800"]))
LATER = pr(40, "文言の調整", "eeeeeee5555", pr_body([46], "- シートの見出しが「詳細」"))


class PrEntryTest(unittest.TestCase):
    def test_修正でない_PR(self):
        e = vc.pr_entry(FIRST)
        self.assertEqual(e["pr"], 30)
        self.assertEqual(e["title"], "シートを足す")
        self.assertEqual(e["sha"], "aaaaaaa")
        self.assertEqual(e["observe"], "- ボード → カード → シートが開く\n- 1280px でも開く")
        self.assertEqual(e["decisions"], "- 選択肢 A / 採用 / 理由")
        self.assertEqual(e["codex"], "- P1 / 採用")
        self.assertEqual(e["refs"], ["46"])
        self.assertIs(e["rework"], False)
        self.assertEqual(e["rework_issues"], [])

    def test_修正の_PR(self):
        e = vc.pr_entry(FIX1)
        self.assertIs(e["rework"], True)
        self.assertEqual(e["rework_issues"], [46])

    def test_CRLF_の本文(self):
        e = vc.pr_entry(pr(35, "t", "ccccccc3333", FIX1["body"].replace("\n", "\r\n")))
        self.assertIs(e["rework"], True)
        self.assertEqual(e["refs"], ["46"])
        self.assertEqual(e["observe"], "- ボード → カード → シートが 1 枚だけ開く (400px)\n- 1280px でも 1 枚だけ")

    def test_形の違う行や_Refs_と違う_issue_は修正にしない(self):
        for line in ["Rework-Request: 789", "Rework-Request: https://github.com/o/r/issues/47#issuecomment-789"]:
            with self.subTest(line=line):
                e = vc.pr_entry(pr(35, "t", "ccccccc3333", pr_body([46], "- x").replace("Refs #46\n", f"Refs #46\n{line}\n")))
                self.assertIs(e["rework"], False)
                self.assertEqual(e["rework_issues"], [])

    def test_本文が無い_PR(self):
        e = vc.pr_entry(pr(31, "手動の PR", "fffffff6666", None))
        self.assertEqual((e["refs"], e["observe"], e["rework"]), ([], "", False))


class IssueLinesTest(unittest.TestCase):
    def test_修正の_PR_が無ければ出力は今までと同じ(self):
        entries = [vc.pr_entry(FIRST), vc.pr_entry(SECOND)]
        self.assertEqual(vc.issue_lines("46", entries), [
            "- PR #30 シートを足す (`aaaaaaa`)",
            "  - [ ] 実機確認の観点:",
            "    - ボード → カード → シートが開く",
            "    - 1280px でも開く",
            "  - 判断した点:",
            "    - 選択肢 A / 採用 / 理由",
            "  - Codex 指摘の採否 (未解決があれば確認):",
            "    - P1 / 採用",
            "- PR #33 シートの幅 (`bbbbbbb`)",
            "  - [ ] 実機確認の観点:",
            "    - 400px で横にはみ出さない",
        ])

    def test_修正の_PR_が無ければ以前の書き方と一致する(self):
        empty = pr(32, "観点の無い PR", "9999999aaaa", "Refs #46\n\n## 変更点\n- x\n")
        for prs in ([FIRST], [FIRST, SECOND], [SECOND, LATER, FIRST], [empty], [FIRST, empty]):
            entries = [vc.pr_entry(p) for p in prs]
            with self.subTest(prs=[p["number"] for p in prs]):
                self.assertEqual(vc.issue_lines("46", entries), legacy_issue_lines(entries))

    def test_修正より前の_PR_は_1_行にまとめる(self):
        entries = [vc.pr_entry(FIRST), vc.pr_entry(FIX1)]
        self.assertEqual(vc.issue_lines("46", entries), [
            "- PR #30 シートを足す (`aaaaaaa`) — 観点は PR #35 (修正) にまとめた",
            "- PR #35 シートが 2 重に開くのを直す (`ccccccc`) (修正)",
            "  - [ ] 実機確認の観点:",
            "    - ボード → カード → シートが 1 枚だけ開く (400px)",
            "    - 1280px でも 1 枚だけ",
            "  - 判断した点:",
            "    - 連打を無視 / 採用 / 簡単",
        ])

    def test_最後の修正より前は修正の_PR_もまとめる(self):
        entries = [vc.pr_entry(p) for p in (FIRST, SECOND, FIX1, FIX2)]
        self.assertEqual(vc.issue_lines("46", entries), [
            "- PR #30 シートを足す (`aaaaaaa`) — 観点は PR #38 (修正) にまとめた",
            "- PR #33 シートの幅 (`bbbbbbb`) — 観点は PR #38 (修正) にまとめた",
            "- PR #35 シートが 2 重に開くのを直す (`ccccccc`) (修正) — 観点は PR #38 (修正) にまとめた",
            "- PR #38 閉じるボタン (`ddddddd`) (修正)",
            "  - [ ] 実機確認の観点:",
            "    - シート → 閉じる → 閉じる",
        ])

    def test_最後の修正より後の_PR_は今までどおり展開する(self):
        entries = [vc.pr_entry(p) for p in (FIRST, FIX1, LATER)]
        got = vc.issue_lines("46", entries)
        self.assertEqual(got[0], "- PR #30 シートを足す (`aaaaaaa`) — 観点は PR #35 (修正) にまとめた")
        self.assertEqual(got[1], "- PR #35 シートが 2 重に開くのを直す (`ccccccc`) (修正)")
        self.assertEqual(got[-3:], [
            "- PR #40 文言の調整 (`eeeeeee`)",
            "  - [ ] 実機確認の観点:",
            "    - シートの見出しが「詳細」",
        ])

    def test_修正の_PR_だけでも印を付ける(self):
        got = vc.issue_lines(46, [vc.pr_entry(FIX1)])
        self.assertEqual(got[0], "- PR #35 シートが 2 重に開くのを直す (`ccccccc`) (修正)")
        self.assertEqual(got[1], "  - [ ] 実機確認の観点:")

    def test_複数の_issue_を_Refs_する_PR_は依頼のあった_issue_だけ修正として扱う(self):
        first47 = pr(31, "別の issue の実装", "1111111aaaa", pr_body([47], "- 設定 → 保存 → 保存される"))
        both = pr(36, "2 つの issue にまたがる修正", "2222222bbbb", pr_body([46, 47], "- まとめた観点", rework=[URL]))
        e30, e31, e36 = vc.pr_entry(FIRST), vc.pr_entry(first47), vc.pr_entry(both)
        self.assertEqual(e36["rework_issues"], [46])
        self.assertEqual(vc.issue_lines("46", [e30, e36])[:2], [
            "- PR #30 シートを足す (`aaaaaaa`) — 観点は PR #36 (修正) にまとめた",
            "- PR #36 2 つの issue にまたがる修正 (`2222222`) (修正)",
        ])
        self.assertEqual(vc.issue_lines("47", [e31, e36]), legacy_issue_lines([e31, e36]))


class GenerateTest(unittest.TestCase):
    """generate 全体 (git と gh api を差し替える)。"""

    def generate(self, pulls, issues=None):
        shas = [p["merge_commit_sha"] for p in pulls]
        issues = issues or {"46": "シートで詳細を開く", "47": "設定の保存"}

        def fake_run(cmd, cwd=None):
            if cmd[:2] == ["git", "rev-list"]:
                return "\n".join(shas) + "\n"
            if cmd[:2] == ["git", "log"]:
                return "".join(f"{s} commit {i}\n" for i, s in enumerate(shas)) + "0000000ffff 直 push の commit\n"
            raise AssertionError(cmd)

        def fake_gh(path, paginate=True):
            if path.startswith("repos/o/r/pulls?"):
                return list(pulls)
            if re.fullmatch(r"repos/o/r/pulls/\d+/commits\?per_page=100", path):
                return []
            if path.startswith("repos/o/r/issues?"):
                return [{"number": 12, "title": "前回の持ち越し"}, {"number": 46, "title": "シートで詳細を開く"}]
            m = re.fullmatch(r"repos/o/r/issues/(\d+)", path)
            if m:
                return {"number": int(m.group(1)), "title": issues[m.group(1)]}
            raise AssertionError(path)

        with mock.patch.object(vc, "_run", side_effect=fake_run), mock.patch.object(vc, "_gh_json", side_effect=fake_gh):
            return vc.generate("o/r", "v1.0.0", "abc1234", dict(pc.DEFAULT_LABELS), "main", ".")

    def test_修正の_PR_が無い範囲の出力は今までと同じ(self):
        unref = pr(34, "Refs の無い PR", "8888888cccc", "## 実機確認の観点\n- x\n", merged_at="2026-09-03T00:00:00Z")
        second = dict(SECOND, merged_at="2026-09-02T00:00:00Z")
        self.assertEqual(self.generate([second, unref, FIRST]), "\n".join([
            "# 検証チェックリスト (v1.0.0..abc1234)",
            "",
            "対象 commit 3 / マージ PR 3 / issue 1",
            "",
            "## #46 シートで詳細を開く",
            "- PR #30 シートを足す (`aaaaaaa`)",
            "  - [ ] 実機確認の観点:",
            "    - ボード → カード → シートが開く",
            "    - 1280px でも開く",
            "  - 判断した点:",
            "    - 選択肢 A / 採用 / 理由",
            "  - Codex 指摘の採否 (未解決があれば確認):",
            "    - P1 / 採用",
            "- PR #33 シートの幅 (`bbbbbbb`)",
            "  - [ ] 実機確認の観点:",
            "    - 400px で横にはみ出さない",
            "",
            "## 観点未登録の PR (Refs か「実機確認の観点」が無い)",
            "- PR #34 Refs の無い PR (`8888888`)",
            "",
            "## PR の無い commit (直 push / 旧形式)",
            "- `0000000` 直 push の commit",
            "",
            "## 持ち越し (前回以前に pv:merged-unverified のまま)",
            "- #12 前回の持ち越し",
            "",
        ]) + "\n")

    def test_修正の_PR_がある_issue_だけまとめる(self):
        first47 = pr(31, "設定を保存する", "1111111aaaa", pr_body([47], "- 設定 → 保存 → 保存される"), merged_at="2026-09-01T12:00:00Z")
        fix = dict(FIX1, merged_at="2026-09-04T00:00:00Z")
        got = self.generate([fix, first47, FIRST])
        self.assertEqual(got, "\n".join([
            "# 検証チェックリスト (v1.0.0..abc1234)",
            "",
            "対象 commit 3 / マージ PR 3 / issue 2",
            "",
            "## #46 シートで詳細を開く",
            "- PR #30 シートを足す (`aaaaaaa`) — 観点は PR #35 (修正) にまとめた",
            "- PR #35 シートが 2 重に開くのを直す (`ccccccc`) (修正)",
            "  - [ ] 実機確認の観点:",
            "    - ボード → カード → シートが 1 枚だけ開く (400px)",
            "    - 1280px でも 1 枚だけ",
            "  - 判断した点:",
            "    - 連打を無視 / 採用 / 簡単",
            "",
            "## #47 設定の保存",
            "- PR #31 設定を保存する (`1111111`)",
            "  - [ ] 実機確認の観点:",
            "    - 設定 → 保存 → 保存される",
            "",
            "## PR の無い commit (直 push / 旧形式)",
            "- `0000000` 直 push の commit",
            "",
            "## 持ち越し (前回以前に pv:merged-unverified のまま)",
            "- #12 前回の持ち越し",
            "",
        ]) + "\n")


if __name__ == "__main__":
    unittest.main()
