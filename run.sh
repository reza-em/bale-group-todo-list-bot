#!/bin/bash
# Bale group to-do list bot launcher: ONE detached auto-restart supervisor, single instance (flock).
# Token comes from the environment only (never written anywhere): TODO_BALE_BOT_TOKEN
#   ./run.sh      start (idempotent)        ./stop.sh   stop
# Optional git-ignored settings in .env (e.g. TODO_STRIKE=unicode). Log: bot.log
cd "$(dirname "$0")"
DIR="$(pwd)"
if [ -f "$DIR/.env" ]; then set -a; . "$DIR/.env"; set +a; fi
PY="$DIR/venv/bin/python"; [ -x "$PY" ] || PY=python3
LOG=bot.log

if [ "$1" = "--loop" ]; then          # internal: supervisor loop
  exec 9>"run.lock"
  flock -n 9 || exit 0                # only one supervisor, ever
  echo $$ > run.pid
  umask 077
  trap 'rm -f run.pid; exit 0' TERM INT
  while true; do
    "$PY" bot.py >> "$LOG" 2>&1
    RC=$?
    echo "$(date '+%F %T') todo bot exited ($RC), restarting in 5s" >> "$LOG"
    [ "$RC" = "0" ] && [ -z "${TODO_BALE_BOT_TOKEN:-}" ] && break
    sleep 5 & wait $!
  done
  rm -f run.pid
  exit 0
fi

if [ -z "${TODO_BALE_BOT_TOKEN:-}" ]; then echo "TODO_BALE_BOT_TOKEN not set - bot not started"; exit 1; fi
if [ -f run.pid ] && kill -0 "$(cat run.pid)" 2>/dev/null; then
  echo "todo-bale-bot already running (supervisor pid $(cat run.pid))"; exit 0
fi
setsid nohup "$DIR/run.sh" --loop >/dev/null 2>&1 < /dev/null &
sleep 1; echo "todo-bale-bot started (supervisor pid $(cat run.pid 2>/dev/null)); log: $DIR/$LOG"
