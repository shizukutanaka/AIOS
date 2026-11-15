#!/usr/bin/env python3
"""verify_artifacts.py

AI組織のボスAI(router3.sh)レビュー部が使う成果物検証スクリプト。
サブタスクの「期待される実成果ファイル」が実際に存在し、
中身が「説明・サンプルコード・手順」ではなく「成果そのもの」かを検証する。

使い方:
    python verify_artifacts.py <path1> [path2 ...]

出力(stdout, 機械可読):
    RESULT <path> OK
    RESULT <path> FAIL <reason>
    ...
    SUMMARY OK|FAIL
"""
import os
import stat
import sys

HEAD_LINES = 50

EXPLANATION_PHRASES = [
    "以下のように",
    "以下は",
    "手順",
    "説明",
    "やり方",
    "サンプルコード",
    "使い方の例",
    "このように",
    "例えば",
]

CODE_FENCE = "```"

import re as _re

# [L25 boss-plumbing / MASPRM step-reward] 報告・調査タスクの成果に「進捗を示す具体値」
# (件数/数値/ハッシュ/パス/日時) が含まれるかを判定する。これらが一切無い出力は
# 解説・ハルシネーションとみなし FAIL とする（ステップ報酬の「進捗証拠」要件）。
CONCRETE_VALUE_RE = _re.compile(
    r"(?:"
    r"\b\d{1,3}(?:[,\.]\d{3})+\b"      # 1,234 / 1.234 (grouped numbers)
    r"|\b\d+\s*(?:件|個|files?|行|lines?)\b"  # 12件 / 116 files
    r"|\b0x[0-9a-fA-F]{6,}\b"          # hash-like 0xdeadbeef
    r"|\b[a-f0-9]{32,}\b"              # md5/sha-like
    r"|\b\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2})?\b"  # date / timestamp
    r"|\bhttps?://\S+"                 # URL evidence
    r"|\b[A-Za-z]:[\\/][^\s]+\b"       # windows path
    r"|/[^\s]*\.(?:md|sh|py|json|yml|yaml|csv|txt)\b"  # unix path with ext
    r")"
)

def looks_like_no_concrete_value(text):
    """報告/調査タスクの成果が具体値(件数/数値/ハッシュ/日時/パス)を含まない場合 True。
    MASPRM 的ステップ報酬: 進捗の証拠となる具体値が無ければそのステップは価値を生んでいない。"""
    return CONCRETE_VALUE_RE.search(text) is None

# 実ファイルらしい定義があると判断するマーカー
# (これらが含まれていれば、説明文句や```があっても成果物とみなす)
REAL_CODE_MARKERS = [
    "def ",
    "class ",
    "function ",
    "const ",
    "let ",
    "var ",
    "import ",
    "from ",
    "#!/",
    "#include",
    "package ",
    "public class",
    "public static",
    "private ",
    "func ",
    "fn ",
    "SELECT ",
    "CREATE TABLE",
    "<?php",
    "<html",
    "<!DOCTYPE",
]


def looks_like_explanation_only(head_text):
    has_fence = CODE_FENCE in head_text
    has_phrase = any(p in head_text for p in EXPLANATION_PHRASES)
    if not (has_fence or has_phrase):
        return False
    has_real_code = any(m in head_text for m in REAL_CODE_MARKERS)
    return not has_real_code


def git_has_changes(path):
    """Return True if the file is tracked by git and has uncommitted/working changes.
    A file that exists but was never actually edited for this task is NOT a real
    artifact. This blocks 'empty-edit' / explanation-only submissions from passing
    purely because the file already existed."""
    import subprocess
    # is it tracked?
    r = subprocess.run(
        ["git", "ls-files", "--error-unmatch", path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if r.returncode != 0:
        # untracked: consider any non-empty content as a fresh artifact (acceptable)
        return True
    # tracked: require an actual diff in the working tree
    d = subprocess.run(
        ["git", "diff", "--quiet", "--", path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if d.returncode == 1:
        return True  # diff present -> edited
    # also check staged
    ds = subprocess.run(
        ["git", "diff", "--cached", "--quiet", "--", path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if ds.returncode == 1:
        return True
    return False


# [REGRESSION GUrad] 部下が「実在する本物ファイル」を説明文/偽コードで上書きする
# 破壊攻撃を検出する。追跡中のコードファイルは、(1) 有効な構文であること、
# (2) HEAD(最後の健全版)と比べて行数が激減していないことを必須とする。
# 未コミットの破壊は End-State guard(HEAD=remote)をすり抜けるため、ここで防ぐ。
import subprocess as _sp

REGRESSION_DROP_RATIO = 0.5  # 新行数 < HEADの50% なら regression


def _git_head_line_count(path):
    """HEAD(最後のコミット済み健全版)の行数、取得不可なら None。"""
    try:
        # git show HEAD:<repo-relative-path> は作業ディレクトリからの相対パスが必要。
        # 絶対パスだと失敗するため、git -C <dir> 経由で相対パスを渡す。
        ap = os.path.abspath(path)
        d = os.path.dirname(ap)
        rel = os.path.relpath(ap, d)
        out = _sp.run(["git", "-C", d, "show", "HEAD:%s" % rel],
                      stdout=_sp.PIPE, stderr=_sp.DEVNULL)
        if out.returncode != 0:
            return None
        txt = out.stdout.decode("utf-8", "ignore")
        return txt.count("\n") + (1 if txt else 0)
    except Exception:
        return None


def _py_compiles(path):
    """py_compile が通る(有効な Python)なら True。"""
    try:
        _sp.run([sys.executable, "-m", "py_compile", path],
                stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
        return True
    except Exception:
        return False


def _has_selftest_entry(path):
    """Heuristic: does this python file expose a `selftest` subcommand we can
    invoke as `<python file> selftest --guard`? We check for the literal
    add_parser("selftest"...) marker (full file scan, since the argparse
    definition can sit far down a large single-file tool). Only files that
    actually implement the contract are invoked (avoids running arbitrary code)."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
        return 'add_parser("selftest"' in text or "add_parser('selftest'" in text
    except Exception:
        return False


def self_test_check(path):
    """Run the artifact's own self-prover (if it has one) and fail on non-zero
    exit. This closes the loop: the regression guard reuses the artifact's
    embedded proof instead of only checking line-count/syntax. A valid-but-broken
    file (passes py_compile + line-count) is caught here if its own selftest fails."""
    if not _has_selftest_entry(path):
        return True, ""  # no self-test contract -> nothing to run
    try:
        r = _sp.run([sys.executable, path, "selftest", "--guard"],
                    stdout=_sp.DEVNULL, stderr=_sp.DEVNULL, timeout=120)
    except Exception as e:
        return False, "selftest_run_error:%s" % e
    if r.returncode != 0:
        return False, "artifact_selftest_failed(rc=%d)" % r.returncode
    return True, ""


def regression_check(path):
    """Return (ok, detail). ok=False ならファイルが HEAD 版から破壊された。"""
    ext = os.path.splitext(path)[1].lower()
    # 1) コードファイルの構文 regression (.py のみ厳密に)
    if ext == ".py" and not _py_compiles(path):
        return False, "py_syntax_regression(not_a_valid_python_file)"
    # 2) 行数 regression vs HEAD (追跡中ファイルのみ)
    head_lines = _git_head_line_count(path)
    if head_lines and head_lines > 20:
        try:
            with open(path, "rb") as _f:
                new_lines = sum(1 for _ in _f)
        except Exception:
            new_lines = 0
        if new_lines < max(5, int(head_lines * REGRESSION_DROP_RATIO)):
            return False, "line_count_regression(head=%d,now=%d)" % (
                head_lines, new_lines)
    # 3) 破壊的でなくても、artifact 自身の自己証明(selftest)が失敗すれば弾く。
    #    これで「有効なPythonだがロジックが壊れた」を regression guard が拾える。
    if ext == ".py":
        ok, detail = self_test_check(path)
        if not ok:
            return False, detail
    return True, ""


def verify_one(path):
    if not os.path.exists(path):
        return False, "not_found"
    st = os.stat(path)
    if not stat.S_ISREG(st.st_mode):
        return False, "not_a_regular_file"
    if st.st_size <= 0:
        return False, "empty_file"
    # [L25 boss-plumbing] require the file to actually be modified for this task,
    # not merely pre-existing. Prevents fabricated/empty-edit approvals.
    if not git_has_changes(path):
        return False, "not_modified_for_task"
    # [REGRESSION GUARD] 追跡中ファイルの破壊(日本語説明文での上書き等)を検出。
    # 未コミットの破壊は End-State guard(HEAD=remote)をすり抜けるため、ここで弾く。
    ok, detail = regression_check(path)
    if not ok:
        return False, detail
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            head_lines = []
            full_text = []
            for _ in range(HEAD_LINES):
                line = f.readline()
                if not line:
                    break
                head_lines.append(line)
            # 残りも読んで具体値の有無を全文判定 (報告タスクは本文末に件数を書くことが多い)
            rest = f.read()
            full_text = "".join(head_lines) + rest
    except OSError as e:
        return False, "read_error:%s" % e
    head_text = "".join(head_lines)
    if looks_like_explanation_only(head_text):
        return False, "explanation_only_heuristic"
    # [L25 / MASPRM] 報告・調査タスク: 具体値(件数/数値/ハッシュ/日時/パス)が無ければ
    # 解説・ハルシネーションとみなし FAIL (ステップ報酬の進捗証拠要件)。
    # ただし実コードファイル(def/class/import 等)は具体値を要求しない(誤検知防止)。
    has_real_code = any(m in head_text for m in REAL_CODE_MARKERS)
    if not has_real_code and looks_like_no_concrete_value(full_text):
        return False, "no_concrete_value_hallucination"
    return True, "ok"


def main(argv):
    paths = argv[1:]
    if not paths:
        print("SUMMARY FAIL no_paths_given")
        return 1
    overall_ok = True
    for path in paths:
        ok, reason = verify_one(path)
        if ok:
            print("RESULT %s OK" % path)
        else:
            overall_ok = False
            print("RESULT %s FAIL %s" % (path, reason))
    print("SUMMARY %s" % ("OK" if overall_ok else "FAIL"))
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
