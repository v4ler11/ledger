 Bash
#!/bin/bash

SRC="/Users/valerii/code/ledger/"
DST="xi-gpu:/home/valerii/code/ledger"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
GRAY='\033[0;37m'
NC='\033[0m'

echo -e "${BLUE}Press Enter to sync, or Ctrl+C to exit${NC}"

while true; do
  read -p $'\033[0;36mPress Enter to sync: \033[0m'
  echo -e "${GRAY}Syncing...${NC}"
  rsync -avz \
    --exclude '.venv/' \
    --exclude '.git/' \
    --exclude '__pycache__/' \
    --exclude '.idea/' \
    --exclude '.DS_Store' \
    --exclude '*.egg-info' \
    --exclude 'uv.lock' \
    "$SRC/" "$DST/"
  echo -e "${YELLOW}Sync completed${NC}"
  echo ""
done