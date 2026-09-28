"""fix_requests.py の純関数のテスト (ネットワークと gh は使わない)。

  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "sweepline" / "scripts"))
import fix_requests as fr  # noqa: E402

REPO = "o/r"
ISSUE = 46


def comment(cid, body, assoc="OWNER", login="IkkoKojima", utype="User", created="2026-09-01T00:00:00Z", updated=None):
    """GitHub の issue コメント JSON (使う項目だけ)。"""
    return {
        "id": cid,
        "html_url": f"https://github.com/{REPO}/issues/{ISSUE}#issuecomment-{cid}",
        "body": body,
        "user": {"login": login, "type": utype},
        "author_association": assoc,
        "created_at": created,
        "updated_at": updated or created,
    }


def pull(number, body, merged=False, state=None, head="claude/task-46-x", repo=REPO):
    """確定済みの PR JSON (repos/{repo}/pulls/{number} の使う項目だけ)。"""
    return {
        "number": number,
        "state": state or ("closed" if merged else "open"),
        "merged_at": "2026-09-02T00:00:00Z" if merged else None,
        "merged": merged,
        "body": body,
        "head": {"ref": head},
        "base": {"repo": {"full_name": repo}},
    }


def xref(number, body="Refs #46\n", repo=REPO, is_pr=True, how="full_name"):
    """timeline の cross-referenced イベント。how は repo の載り方 (full_name / repository_url / html_url / none)。"""
    src = {"number": number}
    if body is not ...:
        src["body"] = body
    if is_pr:
        src["pull_request"] = {"url": f"https://api.github.com/repos/{repo}/pulls/{number}"}
    if how == "full_name":
        src["repository"] = {"full_name": repo}
    elif how == "repository_url":
        src["repository_url"] = f"https://api.github.com/repos/{repo}"
    elif how == "html_url":
        src["html_url"] = f"https://github.com/{repo}/pull/{number}"
    return {"event": "cross-referenced", "source": {"type": "issue", "issue": src}}


def rework_url(cid, issue=ISSUE, repo=REPO):
    return f"https://github.com/{repo}/issues/{issue}#issuecomment-{cid}"


class ParseRequestTest(unittest.TestCase):
    def test_依頼の書式(self):
        cases = {
            "修正 スマホ幅でシートが 2 重に開く": "スマホ幅でシートが 2 重に開く",
            "修正　全角空白でもよい": "全角空白でもよい",
            "修正:\n内容": "内容",
            "修正：内容": "内容",
            "修正\n内容": "内容",
            "修正\r\n内容\r\n": "内容",
            "修正\t内容": "内容",
            "修正: 1 行目\n\n2 行目\n": "1 行目\n\n2 行目",
            "/sweepline:fix\n内容": "内容",
            "/sweepline:fix 内容": "内容",
            "/sweepline:fix: 内容": "内容",
            "/sweepline:fix　内容": "内容",
        }
        for body, want in cases.items():
            with self.subTest(body=body):
                self.assertEqual(fr.parse_request(body), want)

    def test_区切りが無ければ依頼ではない(self):
        for body in [
            "修正しました",
            "修正ありがとう",
            "修正、お願いします",
            "修正依頼: シートが 2 重に開く",
            "修正#1",
            "/sweepline:fixes 内容",
            "/sweepline:fix内容",
            "/sweepline:impl 46",
            "sweepline:fix 内容",
            "これは修正 ではない",
            "> 修正 引用の中",
            "- 修正 箇条書きの中",
            "sweepline: PR #30 をマージしました (squash → main abc1234)",
            "",
            "   ",
        ]:
            with self.subTest(body=body):
                self.assertIsNone(fr.parse_request(body))

    def test_本文が無いコメントは依頼ではない(self):
        self.assertIsNone(fr.parse_request(None))

    def test_BOM_と先頭の空白を除く(self):
        for body in [
            "﻿修正 内容",
            "  修正 内容",
            "\n\n修正 内容",
            "　修正 内容",
            "﻿ \n\t修正 内容",
            "﻿/sweepline:fix 内容",
        ]:
            with self.subTest(body=body):
                self.assertEqual(fr.parse_request(body), "内容")

    def test_内容が空なら空文字(self):
        for body in ["修正", "修正 ", "修正:", "修正：", "修正\n", "修正 :　\n", "/sweepline:fix", "/sweepline:fix\n\n", "﻿ 修正"]:
            with self.subTest(body=body):
                self.assertEqual(fr.parse_request(body), "")

    def test_内容の前後の空白だけを除く(self):
        self.assertEqual(fr.parse_request("修正  a  b \n c  \n"), "a  b \n c")


class PrBodyTest(unittest.TestCase):
    def test_has_refs(self):
        self.assertTrue(fr.has_refs("Refs #46\n\n## 変更点\nx\n", 46))
        self.assertTrue(fr.has_refs("前置き\r\nRefs #46\r\n\r\n## 変更点\r\n", 46))
        self.assertTrue(fr.has_refs("Refs #12\nRefs #46\n", 46))
        self.assertTrue(fr.has_refs("Refs  #46  \n", 46))

    def test_has_refs_の否定例(self):
        for body in [
            "Refs #460\n",          # 前方一致では数えない
            "Refs #4\n",
            "refs #46\n",           # 大文字小文字を区別する (REFS_RE と同じ)
            "  Refs #46\n",         # 行頭から
            "Refs #46 と #47\n",    # 1 行に 1 つ
            "See Refs #46\n",
            "Closes #46\n",
            "#46 を直した\n",
            "",
            None,
        ]:
            with self.subTest(body=body):
                self.assertFalse(fr.has_refs(body, 46))

    def test_rework_request_ids(self):
        body = (
            "Refs #46\n"
            f"Rework-Request: {rework_url(789)}\n"
            f"Rework-Request: {rework_url(123)}\n"
            f"Rework-Request: {rework_url(789)}\n"      # 重複
            "\n## 変更点\nx\n"
        )
        self.assertEqual(fr.rework_request_ids(body), [123, 789])

    def test_rework_request_ids_は_CRLF_でも読む(self):
        body = f"Refs #46\r\nRework-Request: {rework_url(5)}\r\n\r\n## 変更点\r\nx\r\n"
        self.assertEqual(fr.rework_request_ids(body), [5])

    def test_rework_request_ids_は形の違う行を数えない(self):
        body = (
            "Refs #46\n"
            "Rework-Request: https://github.com/o/r/issues/46\n"                       # コメント id が無い
            "Rework-Request: https://github.com/o/r/pull/46#issuecomment-1\n"          # issues ではない
            "Rework-Request: http://github.com/o/r/issues/46#issuecomment-2\n"         # https ではない
            "Rework-Request: https://example.com/o/r/issues/46#issuecomment-3\n"
            f"Rework-Request: {rework_url(4)} {rework_url(5)}\n"                       # 1 行に 2 つ
            f"  Rework-Request: {rework_url(6)}\n"                                     # 行頭からではない
            f"rework-request: {rework_url(7)}\n"
            f"本文の中の Rework-Request: {rework_url(8)}\n"
        )
        self.assertEqual(fr.rework_request_ids(body), [])
        self.assertEqual(fr.rework_request_ids(""), [])
        self.assertEqual(fr.rework_request_ids(None), [])


class PrCandidatesTest(unittest.TestCase):
    def test_同じ_repo_の_PR_だけを候補にする(self):
        timeline = [
            {"event": "labeled", "label": {"name": "pv:ready"}},
            xref(30),
            xref(30),                                        # 同じ PR の 2 回目の参照
            xref(41, how="repository_url"),
            xref(42, how="html_url"),
            xref(50, repo="other/repo"),                     # 別 repo の PR
            xref(51, repo="other/repo", how="repository_url"),
            xref(52, repo="other/repo", how="html_url"),
            xref(53, how="none"),                            # repo が分からない
            xref(60, is_pr=False),                           # issue からの参照
            {"event": "cross-referenced", "source": {"type": "issue"}},
            {"event": "cross-referenced"},
        ]
        self.assertEqual(fr.pr_candidates(timeline, REPO, ISSUE), [30, 41, 42])

    def test_repo_は大文字小文字を区別しない(self):
        self.assertEqual(fr.pr_candidates([xref(30, repo="O/R")], "o/r", ISSUE), [30])
        self.assertEqual(fr.pr_candidates([xref(30, repo="o/r", how="html_url")], "O/R", ISSUE), [30])

    def test_本文に_Refs_が無いと分かる候補は外し_本文が無ければ残す(self):
        timeline = [
            xref(30, body="Refs #46\r\n\r\n## 変更点\r\n"),
            xref(31, body="#46 に関連する別の変更"),          # Refs 行が無い
            xref(32, body="Refs #47\n"),                     # 別の issue
            xref(33, body=None),                             # 本文が null
            xref(34, body=...),                              # 本文の項目が無い
        ]
        self.assertEqual(fr.pr_candidates(timeline, REPO, ISSUE), [30, 33, 34])

    def test_空の_timeline(self):
        self.assertEqual(fr.pr_candidates([], REPO, ISSUE), [])


class SummarizeTest(unittest.TestCase):
    def test_初回実装の前は_round_0(self):
        got = fr.summarize(ISSUE, [], [])
        self.assertEqual(got, {
            "issue": 46, "round": 0, "merged_prs": [], "open_prs": [], "handled": [], "requests": [], "ignored": [],
        })

    def test_キーの順序(self):
        got = fr.summarize(ISSUE, [comment(1, "修正 a")], [pull(30, "Refs #46\n", merged=True)], REPO)
        self.assertEqual(list(got), ["issue", "round", "merged_prs", "open_prs", "handled", "requests", "ignored"])
        self.assertEqual(list(got["requests"][0]),
                         ["id", "url", "author", "association", "created_at", "updated_at", "content"])

    def test_未処理の依頼(self):
        c = comment(789, "修正 スマホ幅でシートが 2 重に開く", created="2026-09-03T01:02:03Z", updated="2026-09-03T04:05:06Z")
        got = fr.summarize(ISSUE, [comment(700, "確認しました。問題なし"), c], [pull(30, "Refs #46\n", merged=True)], REPO)
        self.assertEqual(got["round"], 1)
        self.assertEqual(got["merged_prs"], [30])
        self.assertEqual(got["handled"], [])
        self.assertEqual(got["ignored"], [])
        self.assertEqual(got["requests"], [{
            "id": 789,
            "url": "https://github.com/o/r/issues/46#issuecomment-789",
            "author": "IkkoKojima",
            "association": "OWNER",
            "created_at": "2026-09-03T01:02:03Z",
            "updated_at": "2026-09-03T04:05:06Z",
            "content": "スマホ幅でシートが 2 重に開く",
        }])

    def test_受理するのは_OWNER_MEMBER_COLLABORATOR(self):
        comments = [
            comment(1, "修正 a", assoc="OWNER"),
            comment(2, "修正 b", assoc="MEMBER"),
            comment(3, "修正 c", assoc="COLLABORATOR"),
            comment(4, "修正 d", assoc="CONTRIBUTOR", login="someone"),
            comment(5, "修正 e", assoc="NONE", login="someone"),
            comment(6, "修正 f", assoc="FIRST_TIME_CONTRIBUTOR", login="someone"),
        ]
        got = fr.summarize(ISSUE, comments, [], REPO)
        self.assertEqual([r["id"] for r in got["requests"]], [1, 2, 3])
        self.assertEqual(got["ignored"], [
            {"id": 4, "author": "someone", "association": "CONTRIBUTOR", "reason": "not_authorized"},
            {"id": 5, "author": "someone", "association": "NONE", "reason": "not_authorized"},
            {"id": 6, "author": "someone", "association": "FIRST_TIME_CONTRIBUTOR", "reason": "not_authorized"},
        ])

    def test_author_association_が無ければ受理しない(self):
        c = comment(1, "修正 a")
        del c["author_association"]
        got = fr.summarize(ISSUE, [c], [], REPO)
        self.assertEqual(got["requests"], [])
        self.assertEqual(got["ignored"], [{"id": 1, "author": "IkkoKojima", "association": "NONE", "reason": "not_authorized"}])

    def test_Bot_は受理しない(self):
        comments = [
            comment(1, "修正 a", assoc="COLLABORATOR", login="some-app[bot]", utype="Bot"),
            comment(2, "修正 b", assoc="NONE", login="other[bot]", utype="Bot"),
        ]
        got = fr.summarize(ISSUE, comments, [], REPO)
        self.assertEqual(got["requests"], [])
        self.assertEqual([(i["id"], i["reason"]) for i in got["ignored"]], [(1, "bot"), (2, "bot")])

    def test_内容が空の依頼は受理しない(self):
        got = fr.summarize(ISSUE, [comment(1, "修正"), comment(2, "/sweepline:fix\n\n"), comment(3, "修正 a")], [], REPO)
        self.assertEqual([r["id"] for r in got["requests"]], [3])
        self.assertEqual(got["ignored"], [
            {"id": 1, "author": "IkkoKojima", "association": "OWNER", "reason": "empty"},
            {"id": 2, "author": "IkkoKojima", "association": "OWNER", "reason": "empty"},
        ])

    def test_書式が依頼でないコメントは_ignored_にも入れない(self):
        comments = [comment(1, "修正しました", assoc="NONE"), comment(2, "修正ありがとう"), comment(3, "LGTM", utype="Bot")]
        got = fr.summarize(ISSUE, comments, [], REPO)
        self.assertEqual(got["requests"], [])
        self.assertEqual(got["ignored"], [])

    def test_処理済みの依頼は_requests_にも_ignored_にも入れない(self):
        pulls = [
            pull(30, "Refs #46\n\n## 変更点\nx\n", merged=True),
            pull(35, f"Refs #46\nRework-Request: {rework_url(101)}\nRework-Request: {rework_url(102)}\n\n## 変更点\nx\n", merged=True),
        ]
        comments = [
            comment(101, "修正 1 つ目", created="2026-09-03T00:00:00Z"),
            comment(102, "修正 2 つ目", assoc="NONE", created="2026-09-03T00:00:01Z"),   # 処理済みなら理由も出さない
            comment(103, "修正 3 つ目", created="2026-09-04T00:00:00Z"),
        ]
        got = fr.summarize(ISSUE, comments, pulls, REPO)
        self.assertEqual(got["round"], 2)
        self.assertEqual(got["merged_prs"], [30, 35])
        self.assertEqual(got["handled"], [101, 102])
        self.assertEqual([r["id"] for r in got["requests"]], [103])
        self.assertEqual(got["ignored"], [])

    def test_見送りの報告コメントに載せた依頼は処理済み(self):
        # PR を作らずに見送った依頼 (お礼など)。載せないと次の sweep がまた受け付ける
        comments = [
            comment(1, "修正 ありがとう"),
            comment(2, "修正 シートを 1 回だけ開く"),
            comment(3, f"sweepline: 修正依頼を見送りました (ラウンド 1)\nRework-Request: {rework_url(1)}\n理由: 修正の指示として読めない"),
        ]
        out = fr.summarize(ISSUE, comments, [pull(30, "Refs #46\n", merged=True)], REPO)
        self.assertEqual(out["handled"], [1])
        self.assertEqual([r["id"] for r in out["requests"]], [2])
        self.assertEqual(out["ignored"], [])
        self.assertEqual(out["round"], 1)

    def test_マージ報告コメントに載せた依頼も処理済み(self):
        report = f"  sweepline: PR #35 をマージしました (squash → main abc1234)\n対応した修正依頼:\nRework-Request: {rework_url(1)}\n"
        comments = [comment(1, "修正 シートを 1 回だけ開く"), comment(2, report)]
        out = fr.summarize(ISSUE, comments, [pull(30, "Refs #46\n", merged=True)], REPO)
        self.assertEqual(out["handled"], [1])
        self.assertEqual(out["requests"], [])

    def test_報告コメントでないものや書けない人の_Rework_Request_は処理済みにしない(self):
        line = f"Rework-Request: {rework_url(1)}"
        comments = [
            comment(1, "修正 シートを 1 回だけ開く"),
            comment(2, f"引用: sweepline: 見送り\n{line}"),                               # 先頭が sweepline: でない
            comment(3, f"sweepline: 修正依頼を見送りました\n{line}", assoc="NONE"),        # 書き込めない人
            comment(4, f"sweepline: 修正依頼を見送りました\n{line}", utype="Bot"),         # bot
            comment(5, "sweepline: 修正依頼を見送りました\nRework-Request: issuecomment-1"),  # 行の形が違う
            comment(6, f"sweepline: 修正依頼を見送りました\n{rework_url(1)}"),             # Rework-Request: の行でない
        ]
        out = fr.summarize(ISSUE, comments, [], REPO)
        self.assertEqual(out["handled"], [])
        self.assertEqual([r["id"] for r in out["requests"]], [1])
        self.assertEqual(fr.report_handled_ids(comments), [])
        self.assertEqual(fr.report_handled_ids(None), [])

    def test_未マージの_PR_の_Rework_Request_は処理済みにしない(self):
        pulls = [
            pull(30, "Refs #46\n", merged=True),
            pull(41, f"Refs #46\nRework-Request: {rework_url(101)}\n", head="claude/task-46-fix1-sheet"),
            pull(42, f"Refs #46\nRework-Request: {rework_url(102)}\n", state="closed"),   # マージせずに閉じた
        ]
        got = fr.summarize(ISSUE, [comment(101, "修正 a"), comment(102, "修正 b")], pulls, REPO)
        self.assertEqual(got["round"], 1)
        self.assertEqual(got["handled"], [])
        self.assertEqual([r["id"] for r in got["requests"]], [101, 102])
        self.assertEqual(got["open_prs"], [{"number": 41, "head": "claude/task-46-fix1-sheet"}])

    def test_Refs_の無い_PR_の_Rework_Request_は処理済みにしない(self):
        pulls = [pull(36, f"Refs #47\nRework-Request: {rework_url(101, issue=47)}\n", merged=True)]
        got = fr.summarize(ISSUE, [comment(101, "修正 a")], pulls, REPO)
        self.assertEqual(got["round"], 0)
        self.assertEqual(got["handled"], [])
        self.assertEqual([r["id"] for r in got["requests"]], [101])

    def test_round_は_Refs_のあるマージ済み_PR_だけを数える(self):
        pulls = [
            pull(30, "Refs #46\n", merged=True),
            pull(31, "#46 に関連\n", merged=True),                       # Refs 行が無い
            pull(32, "Refs #47\n", merged=True),                         # 別の issue
            pull(33, "Refs #460\n", merged=True),
            pull(34, "Refs #46\n", merged=True, repo="other/repo"),      # 別 repo の PR
            pull(35, "Refs #46\n", state="closed"),                      # マージせずに閉じた
            pull(36, "Refs #46\n"),                                      # open
            pull(37, "Refs #12\r\nRefs #46\r\n", merged=True),           # CRLF、複数の Refs
            pull(30, "Refs #46\n", merged=True),                         # 重複
        ]
        got = fr.summarize(ISSUE, [], pulls, REPO)
        self.assertEqual(got["merged_prs"], [30, 37])
        self.assertEqual(got["round"], 2)
        self.assertEqual(got["open_prs"], [{"number": 36, "head": "claude/task-46-x"}])

    def test_merged_at_が無くても_merged_が真ならマージ済み(self):
        p = pull(30, "Refs #46\n", merged=True)
        p["merged_at"] = None
        self.assertEqual(fr.summarize(ISSUE, [], [p], REPO)["merged_prs"], [30])

    def test_repo_を渡さなければ_PR_の_repo_を見ない(self):
        got = fr.summarize(ISSUE, [], [pull(34, "Refs #46\n", merged=True, repo="other/repo")])
        self.assertEqual(got["merged_prs"], [34])

    def test_PR_の_repo_は大文字小文字を区別しない(self):
        got = fr.summarize(ISSUE, [], [pull(30, "Refs #46\n", merged=True, repo="O/R")], "o/r")
        self.assertEqual(got["merged_prs"], [30])

    def test_open_prs(self):
        pulls = [
            pull(45, "Refs #46\n", head="claude/task-46-fix2-b"),
            pull(41, "Refs #46\n", head="claude/task-46-fix1-sheet"),
            pull(43, "Refs #47\n", head="claude/task-47-x"),             # 別の issue
            pull(44, "関連: #46\n", head="feature/x"),                    # Refs 行が無い
            pull(46, "Refs #46\n", state="closed"),
            pull(47, "Refs #46\n", merged=True),
        ]
        got = fr.summarize(ISSUE, [], pulls, REPO)
        self.assertEqual(got["open_prs"], [
            {"number": 41, "head": "claude/task-46-fix1-sheet"},
            {"number": 45, "head": "claude/task-46-fix2-b"},
        ])
        self.assertEqual(got["merged_prs"], [47])

    def test_並び順は_created_at_同時刻は_id(self):
        comments = [
            comment(30, "修正 c", created="2026-09-05T00:00:00Z"),
            comment(20, "修正 b2", created="2026-09-04T00:00:00Z"),
            comment(10, "修正 b1", created="2026-09-04T00:00:00Z"),
            comment(40, "修正 a", created="2026-09-03T00:00:00Z"),
            comment(25, "修正 x", assoc="NONE", login="s", created="2026-09-04T12:00:00Z"),
            comment(5, "修正", created="2026-09-04T06:00:00Z"),
            comment(15, "修正 y", utype="Bot", created="2026-09-02T00:00:00Z"),
        ]
        got = fr.summarize(ISSUE, comments, [], REPO)
        self.assertEqual([r["id"] for r in got["requests"]], [40, 10, 20, 30])
        self.assertEqual([i["id"] for i in got["ignored"]], [15, 5, 25])

    def test_html_url_が無ければ_repo_から_URL_を組み立てる(self):
        c = comment(789, "修正 a")
        del c["html_url"]
        self.assertEqual(fr.summarize(ISSUE, [c], [], REPO)["requests"][0]["url"],
                         "https://github.com/o/r/issues/46#issuecomment-789")

    def test_依頼の_URL_は_Rework_Request_行にそのまま書ける(self):
        got = fr.summarize(ISSUE, [comment(789, "修正 a")], [pull(30, "Refs #46\n", merged=True)], REPO)
        body = f"Refs #46\nRework-Request: {got['requests'][0]['url']}\n"
        self.assertEqual(fr.rework_request_ids(body), [789])
        after = fr.summarize(ISSUE, [comment(789, "修正 a")], [pull(30, "Refs #46\n", merged=True), pull(35, body, merged=True)], REPO)
        self.assertEqual(after["requests"], [])
        self.assertEqual(after["round"], 2)


class MainTest(unittest.TestCase):
    """main の入出力 (取得は差し替える)。"""

    def run_main(self, argv, **patches):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as st:
            for name, val in patches.items():
                st.enter_context(mock.patch.object(fr, name, val))
            st.enter_context(contextlib.redirect_stdout(out))
            st.enter_context(contextlib.redirect_stderr(err))
            code = fr.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_JSON_を_1_個出す(self):
        fetch = mock.Mock(return_value=([comment(789, "修正 スマホ幅で…")], [pull(30, "Refs #46\n", merged=True)]))
        code, out, err = self.run_main(["--repo", REPO, "list", "46"], fetch=fetch)
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        fetch.assert_called_once_with(REPO, 46)
        self.assertEqual(out.count("\n"), 1)
        self.assertIn("スマホ幅で…", out)                     # ensure_ascii=False
        got = json.loads(out)
        self.assertEqual(got["round"], 1)
        self.assertEqual([r["id"] for r in got["requests"]], [789])

    def test_gh_api_の失敗は_exit_1(self):
        boom = subprocess.CalledProcessError(1, ["gh", "api", "repos/o/r/issues/46/comments?per_page=100"], stderr="gh: Not Found (HTTP 404)")
        code, out, err = self.run_main(["--repo", REPO, "list", "46"], fetch=mock.Mock(side_effect=boom))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("HTTP 404", err)

    def test_gh_が無ければ_exit_1(self):
        code, out, err = self.run_main(["--repo", REPO, "list", "46"], fetch=mock.Mock(side_effect=FileNotFoundError("gh")))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertTrue(err.strip())

    def test_repo_が決まらなければ_exit_2(self):
        fetch = mock.Mock()
        with mock.patch.object(fr.pc, "load", return_value={"repo": ""}):
            code, out, err = self.run_main(["list", "46"], fetch=fetch)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("repo", err)
        fetch.assert_not_called()

    def test_repo_は設定から読む(self):
        fetch = mock.Mock(return_value=([], []))
        with mock.patch.object(fr.pc, "load", return_value={"repo": "cfg/repo"}):
            code, out, _ = self.run_main(["list", "46"], fetch=fetch)
        self.assertEqual(code, 0)
        fetch.assert_called_once_with("cfg/repo", 46)
        self.assertEqual(json.loads(out)["round"], 0)

    def test_repo_の形が違えば_exit_2(self):
        fetch = mock.Mock()
        code, out, err = self.run_main(["--repo", "o/r/../x", "list", "46"], fetch=fetch)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        fetch.assert_not_called()

    def test_issue_番号が不正なら_exit_2(self):
        fetch = mock.Mock()
        code, out, _ = self.run_main(["--repo", REPO, "list", "0"], fetch=fetch)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        fetch.assert_not_called()


class FetchTest(unittest.TestCase):
    """fetch が叩くパス (gh は差し替える)。"""

    def test_候補の_PR_だけを取りに行く(self):
        calls = []

        def fake(path, paginate=True):
            calls.append((path, paginate))
            if path.endswith("/comments?per_page=100"):
                return [comment(789, "修正 a")]
            if path.endswith("/timeline?per_page=100"):
                return [xref(30), xref(31, body="関連 #46"), xref(50, repo="other/repo"), xref(41, body=None)]
            if path == "repos/o/r/pulls/30":
                return pull(30, "Refs #46\n", merged=True)
            if path == "repos/o/r/pulls/41":
                return pull(41, "Refs #46\n", head="claude/task-46-fix1-sheet")
            raise AssertionError(f"想定外のパス: {path}")

        with mock.patch.object(fr.vc, "_gh_json", side_effect=fake):
            comments, pulls = fr.fetch(REPO, ISSUE)
        self.assertEqual(calls, [
            ("repos/o/r/issues/46/comments?per_page=100", True),
            ("repos/o/r/issues/46/timeline?per_page=100", True),
            ("repos/o/r/pulls/30", False),
            ("repos/o/r/pulls/41", False),
        ])
        got = fr.summarize(ISSUE, comments, pulls, REPO)
        self.assertEqual(got["merged_prs"], [30])
        self.assertEqual(got["open_prs"], [{"number": 41, "head": "claude/task-46-fix1-sheet"}])
        self.assertEqual([r["id"] for r in got["requests"]], [789])

    def test_コメントが_1_件も無い(self):
        with mock.patch.object(fr.vc, "_gh_json", return_value=[]):
            self.assertEqual(fr.fetch(REPO, ISSUE), ([], []))


if __name__ == "__main__":
    unittest.main()
