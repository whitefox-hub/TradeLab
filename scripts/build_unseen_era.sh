#!/usr/bin/env bash
# PREREG-010's unseen past (2021-11 -> 2024-09): the survivorship-neutral daily panel, then
# the 5-minute set on its monthly universe. Both resume where they stopped.
#   ./scripts/build_unseen_era.sh      # detached; progress in logs/unseen_era.log
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . secrets/api.env; set +a
.venv/bin/python scripts/build_binance_pit_panel.py --start 2021-11-01 --end 2024-09-24 \
  --out data/parquet/binance_pit_2022
.venv/bin/python scripts/build_intraday_dataset.py --start 2021-11-01 --end 2024-09-24 \
  --panel data/parquet/binance_pit_2022 --out data/parquet/intraday_2022 --workers 24
