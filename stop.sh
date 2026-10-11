#!/bin/bash
# Stop the todo-bale-bot supervisor loop and the bot process (touches nothing else).
cd "$(dirname "$0")"
[ -f run.pid ] && kill "$(cat run.pid)" 2>/dev/null
[ -f bot.lock ] && { B=$(cat bot.lock); [ -n "$B" ] && kill "$B" 2>/dev/null; }
sleep 1; rm -f run.pid
echo "todo-bale-bot stopped"
