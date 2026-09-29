#!/usr/bin/env python3
"""プラグインの版を上げる (公開する変更を main に入れる前に、作者が実行する)。

  bump_version.py 0.5.1        plugin.json の version と、skills/*/SKILL.md・agents/*.md の版の注釈を書き換える
  bump_version.py --check      注釈が plugin.json と揃っているか (揃っていなければ exit 1。tests が呼ぶ)

版の注釈 (`<!-- sweepline skill version: X.Y.Z -->`) は、クラウドセッションで「live の kit の方が違う版なら live の文書を読む」
判断に使う (スキルの冒頭の live kit の節)。文書に版が埋まっていないと、Skill ツールが先に読み込んだ古い文書と live を見分けられない。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
MANIFEST = PLUGIN / ".claude-plugin" / "plugin.json"
MARKER_RE = re.compile(r"<!-- sweepline skill version: ([^ ]+) -->")
PROSE_RE = re.compile(r"この文書の版 \((\d+(?:\.\d+)*)\)")   # live kit の節の本文 (モデルが VERSION= と見比べる数字)
VERSION_RE = re.compile(r"^\d+(\.\d+)*$")


def documents() -> list[Path]:
    return sorted([*PLUGIN.glob("skills/*/SKILL.md"), *PLUGIN.glob("agents/*.md")])


def manifest_version() -> str:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["version"]


def check() -> list[str]:
    """揃っていない文書の一覧 (空なら OK)。"""
    want = manifest_version()
    bad = []
    for doc in documents():
        body = doc.read_text(encoding="utf-8")
        found = MARKER_RE.findall(body)
        if found != [want]:
            bad.append(f"{doc.relative_to(PLUGIN)}: {found or '注釈なし'} (plugin.json は {want})")
        prose = PROSE_RE.findall(body)
        if any(p != want for p in prose):
            bad.append(f"{doc.relative_to(PLUGIN)}: 本文の「この文書の版 ({', '.join(prose)})」が plugin.json ({want}) と違う")
    return bad


def bump(version: str) -> None:
    if not VERSION_RE.match(version):
        raise SystemExit(f"bump_version: 版は X.Y.Z の形で: {version!r}")
    text = MANIFEST.read_text(encoding="utf-8")
    new = re.sub(r'"version":\s*"[^"]*"', f'"version": "{version}"', text, count=1)
    MANIFEST.write_text(new, encoding="utf-8", newline="\n")
    for doc in documents():
        body = doc.read_text(encoding="utf-8")
        if not MARKER_RE.search(body):
            raise SystemExit(f"bump_version: 版の注釈が無い: {doc.relative_to(PLUGIN)}")
        body = MARKER_RE.sub(f"<!-- sweepline skill version: {version} -->", body)
        body = PROSE_RE.sub(f"この文書の版 ({version})", body)
        doc.write_text(body, encoding="utf-8", newline="\n")
    print(f"version {version}: plugin.json と {len(documents())} 文書")


def main(argv: list[str]) -> int:
    if argv == ["--check"]:
        bad = check()
        for line in bad:
            print(line, file=sys.stderr)
        return 1 if bad else 0
    if len(argv) == 1 and not argv[0].startswith("-"):
        bump(argv[0])
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
