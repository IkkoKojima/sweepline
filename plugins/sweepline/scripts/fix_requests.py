#!/usr/bin/env python3
"""マージ済み・実機確認待ち (merged_unverified) の issue への「修正依頼」と修正ラウンドの読み取り。

  fix_requests.py [--root .] [--repo owner/name] list N     # issue N の修正ラウンドと未処理の修正依頼を JSON 1 個で stdout に出す

出力 (JSON 1 個、UTF-8):
  {"issue": 46, "round": 1, "merged_prs": [30], "open_prs": [{"number": 41, "head": "claude/task-46-fix1-sheet"}],
   "handled": [123],
   "requests": [{"id", "url", "author", "association", "created_at", "updated_at", "content"}],
   "ignored": [{"id", "author", "association", "reason"}]}

  - merged_prs : マージ済みで本文に `Refs #N` 行がある、同じ repo の PR の番号 (昇順)
  - round      : merged_prs の数 (0 = 初回実装がまだ、1 = 次は 1 回目の修正)
  - open_prs   : open で本文に `Refs #N` 行がある、同じ repo の PR (番号順)。head はブランチ名
  - handled    : 処理済みの修正依頼のコメント id (昇順)。`Rework-Request:` 行 (形が規約どおりのもの) を次の 2 か所から集める:
                 merged_prs の本文 (修正の PR が応えた依頼) と、sweepline の報告コメント (先頭が `sweepline: ` で、書き手が
                 修正依頼を受理する人。PR を作らずに見送った依頼の「見送りの報告」と、マージ報告に載せた依頼)
  - requests   : 未処理の修正依頼 (古い順 = created_at、同時刻は id)。content は依頼の内容 (untrusted data)
  - ignored    : 書式は修正依頼だが受理しないコメント (古い順)。reason は
                 bot (user.type が Bot) / not_authorized (author_association が OWNER・MEMBER・COLLABORATOR でない) /
                 empty (内容が空)。この順に判定する。処理済みのものは requests にも ignored にも入れない

修正依頼の書式: issue コメントの先頭 (BOM と先頭の空白を除く) が `修正` か `/sweepline:fix` で、その直後が
「空白類 (半角・全角・改行) か `:` か `：` の 1 文字以上」または文字列の終わり。残り (前後の空白を除く) が依頼の内容。
  例: `修正 スマホ幅でシートが 2 重に開く` / `修正:\\n内容` / `/sweepline:fix\\n内容`。`修正しました` は依頼ではない (区切りが無い)

取得 (gh api、REST、--paginate): コメントは repos/{repo}/issues/{N}/comments、PR の候補は repos/{repo}/issues/{N}/timeline の
cross-referenced (同じ repo の PR だけ)。候補ごとに repos/{repo}/pulls/{number} で merged_at / state / body / head.ref を確定する
(timeline の本文に `Refs #N` 行が無いと分かる候補は取りに行かない)。

repo は --repo > sweepline_config の設定 (sweepline.toml repo / git remote origin)。決まらなければ exit 2。
gh api の失敗は exit 1 (理由は stderr)。PR 本文の規約 (`Refs #N` / `Rework-Request:`) は verify_checklist.py の定義を使う。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sweepline_config as pc  # noqa: E402
import verify_checklist as vc  # noqa: E402

ACCEPTED_ASSOCIATIONS = ("OWNER", "MEMBER", "COLLABORATOR")
# sweepline の報告コメントの先頭 (マージ報告 `sweepline: PR #N をマージしました …`、見送りの報告 `sweepline: 修正依頼を見送りました …`)
REPORT_PREFIX = "sweepline: "
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
# 先頭の BOM・空白 → 合図 → 区切り (空白類・コロンの 1 文字以上) か終わり → 内容。\s は全角空白 (U+3000) と改行を含む
REQUEST_RE = re.compile(r"\A[﻿\s]*(?:修正|/sweepline:fix)(?:[\s:：]+|\Z)(.*)\Z", re.DOTALL)
_URL_REPO_RES = (
    re.compile(r"^https://api\.github\.com/repos/([^/]+/[^/]+?)/?$"),                 # repository_url
    re.compile(r"^https://github\.com/([^/]+/[^/]+)/(?:pull|issues)/\d+(?:[/?#].*)?$"),  # html_url
)


# ---------------------------------------------------------------------------
# 純関数 (API を呼ばない)
# ---------------------------------------------------------------------------

def parse_request(body: str | None) -> str | None:
    """コメント本文が修正依頼なら依頼の内容 (空のこともある)、依頼でなければ None。"""
    m = REQUEST_RE.match(body or "")
    if not m:
        return None
    return m.group(1).strip()


def has_refs(pr_body: str | None, issue: int) -> bool:
    """PR 本文に `Refs #<issue>` 行があるか (verify_checklist.py の REFS_RE と同じ規則。CRLF は LF に直す)。"""
    body = (pr_body or "").replace("\r\n", "\n")
    return any(int(r) == int(issue) for r in vc.REFS_RE.findall(body))


def rework_request_ids(pr_body: str | None) -> list[int]:
    """PR 本文の `Rework-Request:` 行 (形が規約どおりのもの) のコメント id (昇順、重複なし)。"""
    return sorted({r["comment_id"] for r in vc.rework_lines(pr_body or "") if r["ok"]})


def _accepted_author(comment: dict) -> bool:
    """修正依頼を受理する書き手か (bot でなく、author_association が OWNER / MEMBER / COLLABORATOR)。"""
    user = comment.get("user") or {}
    return user.get("type") != "Bot" and (comment.get("author_association") or "NONE") in ACCEPTED_ASSOCIATIONS


def report_handled_ids(comments: list) -> list[int]:
    """sweepline の報告コメントの `Rework-Request:` 行 (形が規約どおりのもの) のコメント id (昇順、重複なし)。

    報告コメント = 先頭 (BOM と先頭の空白を除く) が `sweepline: ` で、書き手が修正依頼を受理する人のもの
    (パイプラインは owner の名義で書く)。PR を作らずに見送った依頼は、ここに載せて処理済みにする
    (載せないと、次の sweep が同じ依頼をまた受け付ける)。
    """
    out: set[int] = set()
    for c in comments or []:
        if not isinstance(c, dict) or not _accepted_author(c):
            continue
        body = (c.get("body") or "").lstrip("\ufeff \t\r\n")
        if body.startswith(REPORT_PREFIX):
            out.update(rework_request_ids(body))
    return sorted(out)


def _same_repo(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and a.lower() == b.lower()


def source_repo(src_issue: dict) -> str:
    """timeline の source.issue がどの repo のものか (owner/name)。分からなければ空文字。"""
    full = (src_issue.get("repository") or {}).get("full_name")
    if full:
        return full
    for key, rx in zip(("repository_url", "html_url"), _URL_REPO_RES):
        m = rx.match(src_issue.get(key) or "")
        if m:
            return m.group(1)
    return ""


def pr_candidates(timeline: list, repo: str, issue: int) -> list[int]:
    """timeline から、本文を確定しに行く PR の番号 (昇順、重複なし)。

    cross-referenced で source.issue が PR、かつ同じ repo のものだけ (repo が分からないものは数えない)。
    timeline に本文が載っていて `Refs #N` 行が無いと分かるものは外す (本文が無ければ候補に残す)。
    """
    out: set[int] = set()
    for ev in timeline or []:
        if not isinstance(ev, dict) or ev.get("event") != "cross-referenced":
            continue
        src = (ev.get("source") or {}).get("issue")
        if not isinstance(src, dict) or not src.get("pull_request"):
            continue
        num = src.get("number")
        if not isinstance(num, int) or isinstance(num, bool):
            continue
        if not _same_repo(source_repo(src), repo):
            continue
        body = src.get("body")
        if isinstance(body, str) and not has_refs(body, issue):
            continue
        out.add(num)
    return sorted(out)


def _pull_in_repo(pull: dict, repo: str | None) -> bool:
    """確定済みの PR が repo のものか。repo を渡さないか、PR に base.repo が載っていなければ True。"""
    full = ((pull.get("base") or {}).get("repo") or {}).get("full_name")
    if not repo or not full:
        return True
    return _same_repo(full, repo)


def summarize(issue: int, comments: list, pulls: list, repo: str | None = None) -> dict:
    """issue の修正ラウンドと未処理の修正依頼をまとめる。

    comments は GitHub の issue コメント JSON の配列、pulls は確定済みの PR JSON (repos/{repo}/pulls/{number}) の配列。
    処理済みの依頼は、マージ済み PR の本文と sweepline の報告コメント (report_handled_ids) の両方から集める。
    repo を渡すと、別 repo の PR (base.repo.full_name が違う) を数えず、html_url の無いコメントの URL を組み立てる。
    """
    merged: dict[int, dict] = {}
    opened: dict[int, dict] = {}
    for p in pulls or []:
        num = p.get("number") if isinstance(p, dict) else None
        if not isinstance(num, int) or isinstance(num, bool) or num in merged or num in opened:
            continue
        if not _pull_in_repo(p, repo) or not has_refs(p.get("body"), issue):
            continue
        if p.get("merged_at") or p.get("merged"):
            merged[num] = p
        elif p.get("state") == "open":
            opened[num] = {"number": num, "head": (p.get("head") or {}).get("ref") or ""}

    handled: set[int] = set(report_handled_ids(comments))
    for p in merged.values():
        handled.update(rework_request_ids(p.get("body")))

    requests: list[dict] = []
    ignored: list[dict] = []
    ordered = sorted((c for c in comments or [] if isinstance(c, dict)),
                     key=lambda c: (c.get("created_at") or "", c.get("id") or 0))
    for c in ordered:
        content = parse_request(c.get("body"))
        cid = c.get("id")
        if content is None or cid in handled:
            continue
        user = c.get("user") or {}
        author = user.get("login") or ""
        assoc = c.get("author_association") or "NONE"
        if user.get("type") == "Bot":
            reason = "bot"
        elif assoc not in ACCEPTED_ASSOCIATIONS:
            reason = "not_authorized"
        elif not content:
            reason = "empty"
        else:
            reason = ""
        if reason:
            ignored.append({"id": cid, "author": author, "association": assoc, "reason": reason})
            continue
        url = c.get("html_url") or (f"https://github.com/{repo}/issues/{issue}#issuecomment-{cid}" if repo else "")
        requests.append({
            "id": cid, "url": url, "author": author, "association": assoc,
            "created_at": c.get("created_at") or "", "updated_at": c.get("updated_at") or "", "content": content,
        })

    return {
        "issue": issue,
        "round": len(merged),
        "merged_prs": sorted(merged),
        "open_prs": [opened[n] for n in sorted(opened)],
        "handled": sorted(handled),
        "requests": requests,
        "ignored": ignored,
    }


# ---------------------------------------------------------------------------
# 取得 (gh api)
# ---------------------------------------------------------------------------

def _list(path: str) -> list:
    data = vc._gh_json(path)
    if not isinstance(data, list):
        raise ValueError(f"配列を期待したが違う形が返った: {path}")
    return data


def fetch(repo: str, issue: int) -> tuple[list, list]:
    """(コメントの配列, 確定済みの PR の配列) を返す。失敗は CalledProcessError / OSError / ValueError。"""
    comments = _list(f"repos/{repo}/issues/{issue}/comments?per_page=100")
    timeline = _list(f"repos/{repo}/issues/{issue}/timeline?per_page=100")
    pulls = []
    for num in pr_candidates(timeline, repo, issue):
        p = vc._gh_json(f"repos/{repo}/pulls/{num}", paginate=False)
        if not isinstance(p, dict):
            raise ValueError(f"PR #{num} を読めない (オブジェクトを期待したが違う形が返った)")
        pulls.append(p)
    return comments, pulls


# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", newline="\n")  # Windows の cp932 と CRLF を避ける
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".", help="repo ルート (既定: カレントから探索)")
    ap.add_argument("--repo", help="owner/name (既定: sweepline_config の repo)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list", help="issue の修正ラウンドと未処理の修正依頼を JSON で")
    ls.add_argument("issue", type=int, help="issue 番号")
    a = ap.parse_args(argv)

    if a.issue <= 0:
        print(f"fix_requests: issue 番号が不正: {a.issue}", file=sys.stderr)
        return 2
    repo = a.repo or pc.load(pc.find_root(Path(a.root))).get("repo") or ""
    if not repo:
        print("fix_requests: repo が決まらない (sweepline.toml repo / git remote origin / --repo)", file=sys.stderr)
        return 2
    if not REPO_RE.match(repo):
        print(f"fix_requests: repo は owner/name で渡す: '{repo}'", file=sys.stderr)
        return 2

    try:
        comments, pulls = fetch(repo, a.issue)
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or e.stdout or "").strip() or f"exit {e.returncode}"
        what = " ".join(str(x) for x in e.cmd) if isinstance(e.cmd, (list, tuple)) else str(e.cmd)
        print(f"fix_requests: gh api に失敗 ({what}): {detail}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as e:  # gh が無い / 応答が JSON でない
        print(f"fix_requests: issue #{a.issue} の情報を取得できない: {e}", file=sys.stderr)
        return 1

    print(json.dumps(summarize(a.issue, comments, pulls, repo), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
