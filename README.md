# やることボード 閲覧版スナップショット

PC（Mac）を閉じていても、スマホからやることボードの中身を見られるようにする仕組み。

## 何をしているか

- Mac上で動く「お台帳ボード」(http://127.0.0.1:5050) からタスク状態を取得
- 中身を**パスフレーズで AES-GCM 暗号化**して `payload.json` を生成
- GitHub Pages（公開リポ・noindex）へ git push
- スマホで公開URLを開き、合言葉を入れると最新スナップショットが見られる

公開リポに置くのは**暗号文だけ**。合言葉なしでは中身は読めない。閲覧専用で、起こす/状態変更などの操作系は無い（安全）。

## 公開URL

https://imai-design.github.io/yb-1a659991/

合言葉は `passphrase.txt`（このディレクトリ・gitignore済みでローカルのみ）。変更したいときはこのファイルを書き換えて次回 publish で反映。

## 自動更新

`com.ryoseiworld.yarukoto-snapshot`（launchd）が**15分ごと＋Mac起動時**に `run-publish.sh` を実行し、最新スナップショットを push する。
Mac が起動中はスナップショットが自動で新しくなり、Mac を閉じると最後のスナップショットが残る。

- 手動更新: `./run-publish.sh`
- 状態確認: `launchctl list | grep yarukoto-snapshot`
- 止める: `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.ryoseiworld.yarukoto-snapshot.plist`
- 再開: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ryoseiworld.yarukoto-snapshot.plist`

## 限界（正直に）

- Mac が OFF の間は内容は更新されない（最後のスナップショットが見える）。「リアルタイム」ではなく Mac 起動中に定期更新。
- 操作系（▶起こす等）は Mac が必要なので閲覧版には無い。見るのは「何をやるか」まで。

## ファイル

| ファイル | 役割 |
|---|---|
| `publish.py` | 取得→暗号化→push 本体 |
| `index.html` | 静的な閲覧ページ（Web Crypto で復号して描画） |
| `payload.json` | 暗号化されたボードデータ（毎回更新） |
| `run-publish.sh` | launchd/手動共通の実行ラッパ（無人push認証込み） |
| `passphrase.txt` | 合言葉（ローカルのみ・gitignore） |
| `.ghtoken` / `git-askpass.sh` | 無人push用（ローカルのみ・gitignore） |

依存: cryptography（`.venv`）/ git / Python 3.10+
