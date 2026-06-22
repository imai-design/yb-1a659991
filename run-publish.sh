#!/bin/sh
# launchd / 手動どちらからでも：ボードからスナップショット取得→暗号化→push
export GIT_ASKPASS="$HOME/.yarukoto-snapshot/git-askpass.sh"
export GIT_TERMINAL_PROMPT=0
cd "$HOME/.yarukoto-snapshot" || exit 1
exec ./.venv/bin/python publish.py >> /tmp/yb-snapshot.log 2>&1
