#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
やることボード 閲覧版スナップショット publisher
================================================
ローカルで動いている「お台帳ボード」(http://127.0.0.1:5050) からタスク状態を取得し、
パスフレーズで AES-GCM 暗号化して GitHub Pages 用ディレクトリへ書き出し、git push する。

- PC OFF でも、公開URL(noindex)をスマホで開き合言葉を入れれば最新スナップショットが見られる。
- 公開リポに置くのは暗号文(cipher)だけ。合言葉なしでは中身は読めない。
- 書き込み系の操作（起こす/状態変更）は閲覧版には存在しない＝安全（read-only）。

依存: cryptography（同ディレクトリ .venv に導入済み）/ git / Python 3.10+
実行: ~/.yarukoto-snapshot/.venv/bin/python publish.py [--no-push]
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

DIR = Path(__file__).resolve().parent
BOARD = "http://127.0.0.1:5050"
BOARD_DATA = Path.home() / ".yarukoto-board" / "data"
PBKDF2_ITER = 600_000  # OWASP(PBKDF2-SHA256)推奨。弱い合言葉に変えた場合の総当たり耐性を底上げ
JST = timezone(timedelta(hours=9))

# 表示するタスクとして扱う scan の種別（会話履歴/日記等のノイズは除外）
ACTIONABLE_KINDS = {"project", "idea", "inbox", "task", "usertask", "cowork"}
# 「未着手・要対応」とみなす状態
TODO_STATUS = {"pending", "review", "awaiting", "未着手", "レビュー", "要対応"}


def _get_json(path: str, timeout: float = 8.0):
    """ローカルボードの API を叩く。失敗時は None。"""
    try:
        with urllib.request.urlopen(f"{BOARD}{path}", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001 — 取得失敗は握って None（呼び出し側で判断）
        print(f"  ! {path} 取得失敗: {e}", file=sys.stderr)
        return None


def _read_data_json(name: str, default):
    f = BOARD_DATA / name
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return default


def _clip(s: str, n: int) -> str:
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _norm_status(override, fallback: str) -> str:
    """status_override は {"status": "..."} 形式 or 文字列 or 無し。常に文字列へ正規化。"""
    if isinstance(override, dict):
        return override.get("status") or fallback or ""
    if isinstance(override, str) and override:
        return override
    return fallback or ""


def build_model() -> dict | None:
    """閲覧版ボードのデータモデルを構築。ボード未起動なら None。"""
    health = _get_json("/api/health", timeout=4)
    if not health:
        print("  ! ボード(5050)が応答しません。スナップショットをスキップします。", file=sys.stderr)
        return None

    scan = _get_json("/api/scan") or {}
    galaxy = _get_json("/api/galaxy") or []
    usertasks = _read_data_json("usertasks.json", [])
    hidden = set(_read_data_json("hidden.json", []) or [])
    status_override = _read_data_json("status_override.json", {}) or {}

    kpis = scan.get("kpis", {}) if isinstance(scan, dict) else {}

    # 1) あなたのやること（手動追加） — 完了/非表示を除外
    user_rows = []
    for t in usertasks if isinstance(usertasks, list) else []:
        tid = t.get("id", "")
        st = _norm_status(status_override.get(tid), t.get("status", ""))
        if st in ("done", "completed", "完了"):
            continue
        if tid in hidden:
            continue
        user_rows.append({
            "title": _clip(t.get("title", ""), 90),
            "note": _clip(t.get("note", ""), 160),
            "status": st or "in_progress",
            "category": t.get("category", ""),
        })

    # 2) いま動いてる（galaxy = アクティブ）
    active_rows = []
    for t in galaxy if isinstance(galaxy, list) else []:
        active_rows.append({
            "proj": _clip(t.get("proj", ""), 24),
            "title": _clip(t.get("title", ""), 80),
            "hhmm": t.get("hhmm", ""),
            "status": t.get("status", ""),
        })

    # 3) 未着手・要対応（scan items から actionable のみ）
    todo_rows = []
    items = scan.get("items", []) if isinstance(scan, dict) else []
    for it in items:
        if it.get("id") in hidden:
            continue
        kind = it.get("kind", "")
        st = it.get("status", "")
        if kind in ACTIONABLE_KINDS and st in TODO_STATUS:
            todo_rows.append({
                "title": _clip(it.get("title", ""), 84),
                "kind_label": it.get("kind_label", kind),
                "proj": _clip(it.get("proj", "") or it.get("project", ""), 24),
                "status": st,
            })
    todo_rows = todo_rows[:60]  # 上限（暗号ペイロードを軽く保つ）

    now = datetime.now(JST)
    return {
        "v": 1,
        "updated_at": now.isoformat(),
        "updated_label": now.strftime("%Y-%m-%d %H:%M"),
        "kpis": {
            "total": kpis.get("total"),
            "pending": kpis.get("pending"),
            "in_progress": kpis.get("in_progress"),
            "review": kpis.get("review"),
            "completed": kpis.get("completed"),
            "completed_today": kpis.get("completed_today"),
            "usertask": kpis.get("usertask"),
        },
        "user_tasks": user_rows,
        "active": active_rows,
        "todo": todo_rows,
    }


def encrypt_model(model: dict, passphrase: str) -> dict:
    salt = os.urandom(16)
    iv = os.urandom(12)
    key = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, PBKDF2_ITER, dklen=32)
    plaintext = json.dumps(model, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ct = AESGCM(key).encrypt(iv, plaintext, None)  # 末尾16byteにGCMタグを含む（Web Crypto互換）
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    # updated_label は暗号文(model)の中にのみ含める。外側に平文で出さない（更新日時の漏洩防止）。
    return {
        "v": 1,
        "kdf": {"name": "PBKDF2", "hash": "SHA-256", "iter": PBKDF2_ITER, "salt": b64(salt)},
        "cipher": "AES-GCM",
        "iv": b64(iv),
        "ct": b64(ct),
    }


def get_passphrase() -> str:
    """合言葉は passphrase.txt（gitignore済・ローカルのみ）から読む。
    既定値は持たない（publish.py は公開リポにあるため、ここに合言葉を書くと暗号が無効化される）。"""
    f = DIR / "passphrase.txt"
    if f.exists():
        p = f.read_text(encoding="utf-8").strip()
        if p:
            return p
    sys.exit("ERROR: passphrase.txt が空かありません。強い合言葉を書いてください（このファイルはgit対象外）。")


def git_push(changed: bool):
    if not changed:
        print("  = 変更なし（push不要）")
        return 0
    def run(*a):
        return subprocess.run(["git", *a], cwd=DIR, capture_output=True, text=True)
    # 機密ファイルの巻き込み事故を防ぐため add 対象を明示（git add -A は使わない）
    run("add", "payload.json", "index.html", "robots.txt", "README.md",
        "publish.py", "run-publish.sh", ".gitignore")
    msg = f"snapshot {datetime.now(JST).strftime('%Y-%m-%d %H:%M')}"
    c = run("commit", "-m", msg)
    if c.returncode != 0 and "nothing to commit" in (c.stdout + c.stderr):
        print("  = nothing to commit")
        return 0
    p = run("push", "origin", "main")
    if p.returncode != 0:
        print(f"  ! push失敗: {p.stderr.strip()}", file=sys.stderr)
        return 1
    print("  ✓ pushed")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-push", action="store_true", help="ローカル生成のみ（git pushしない）")
    args = ap.parse_args()

    model = build_model()
    if model is None:
        # ボード未起動：前回スナップショットを温存して静かに終了（更新失敗で壊さない）
        return 0

    payload = encrypt_model(model, get_passphrase())
    out = DIR / "payload.json"
    before = out.read_text(encoding="utf-8") if out.exists() else ""
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    n_user = len(model["user_tasks"]); n_active = len(model["active"]); n_todo = len(model["todo"])
    print(f"  ✓ snapshot: やること{n_user} / 稼働中{n_active} / 要対応{n_todo} / 更新{model['updated_label']}")

    if not args.no_push:
        return git_push(True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
