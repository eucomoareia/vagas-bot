#!/bin/bash
cd "$(dirname "$0")"
set -a; . ./.env; set +a
python3 vagas_bot.py >> log.txt 2>&1
