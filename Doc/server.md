# Production server

Where TradeLab runs, how it is reached, updated and stopped. No secrets here: they live only
on the server, in the files named below.

**Real money since 2026-10-04:** the BTC ladder trades on Binance spot (sub-account, $2,000 in
20 parts of $100). The simulator and Demo books are stopped; their history is kept.


## Location

| | |
|---|---|
| URL | https://tradelab.odenis-corp.com (two locks: an Nginx password, then the app's login) |
| Server | Contabo VPS, Ubuntu 24.04 · IP 169.58.62.226 · `ssh root@169.58.62.226` (key only) |
| Project user | `trade_lab` (uid 1001), no sudo, isolated from the other projects |
| Code | `/home/trade_lab/TradeLab` (clone of GitHub, branch `master`, venv in `.venv`) |
| Postgres | `/home/trade_lab/deploy` (Docker, `pgvector/pgvector:pg16`, 127.0.0.1:5432) |
| Repository | `whitefox-hub/TradeLab`, read with a read-only deploy key |


## Services (systemd, start at boot, restart on failure)

| Unit | What | Notes |
|---|---|---|
| `tradelab-ladder@live` | The real book: `scripts/run_btc_ladder.py --venue live` | Binance spot, database `tradelab_live` |
| `tradelab-app` | The operator app on 127.0.0.1:8010 | `TRADELAB_APP_BOOK=live`: it serves the real book and its database |
| `tradelab-ladder@sim` / `@demo` | Simulator and Demo books | stopped and disabled since 2026-10-04 |

```
systemctl status tradelab-ladder@live
journalctl -u tradelab-ladder@live -f
```

The book holds a lock on its state directory, so a second copy cannot start; restarting it is
safe (it resumes from `data/forward/btc-ladder-live/` and reconciles with Binance first).


## Emergency stop

| To | Do |
|---|---|
| Stop trading (no buys, no sells; the BTC stays in the account) | `ssh root@169.58.62.226 systemctl stop tradelab-ladder@live` |
| Sell every part at market, even at a loss, and pause buying 24 h | **Close** on Home |


## Databases

| Database | Holds |
|---|---|
| `tradelab_live` | Real money only: runs, positions, audit trail, its risk limits (`real-btc-ladder-2000`: up to 100% of the budget in BTC, no drawdown or daily-loss halt, $5,000 absolute cap, technical limits unchanged) and its `btc_ladder` setting |
| `tradelab` | The laboratory: history of the simulator and Demo books |

The runner picks the database by mode and cannot cross them (`TRADELAB_LIVE_DB`, Plan.md).
Migrations go to both: `POSTGRES_DB=tradelab_live alembic upgrade head`.


## Configuration (on the server)

| File | Holds | Readable by |
|---|---|---|
| `TradeLab/.env` | Postgres connection, `TRADELAB_LIVE_DB` | `trade_lab` |
| `TradeLab/secrets/api.env` | App login, session secret, Binance key (trade yes, withdraw no, held to 169.58.62.226), `TRADELAB_APP_BOOK=live` | `trade_lab` |
| `deploy/postgres.env` | Postgres password | `trade_lab` |
| `/etc/nginx/tradelab.htpasswd` | First lock (Nginx) | root, www-data |
| `/root/credentials/tradelab-web.txt` | That first lock's password | root |

Nginx: `/etc/nginx/sites-available/tradelab.odenis-corp.com` (HTTPS with HSTS, basic auth,
10 requests/s per IP, sign-in limited to 6 a minute) and `/etc/nginx/conf.d/tradelab-ratelimit.conf`.


## Update after a push

As root on the server; the pull always as `trade_lab`, never as root.

```
sudo -u trade_lab git -C /home/trade_lab/TradeLab pull --ff-only
sudo -u trade_lab /home/trade_lab/TradeLab/.venv/bin/pip install -q -e "/home/trade_lab/TradeLab[collectors,api]"
systemctl restart tradelab-app tradelab-ladder@live
```

Locally, `./tradelab.sh start` no longer starts the books: two copies on one account would each
act on their own parts.


## Backups and checks

- Nightly at 03:20 to `/var/backups/tradelab` (30 days): both databases and the books' state,
  script `/usr/local/sbin/backup-tradelab`.
- `ssh root@169.58.62.226 server-watch` answers `all well` or the list of problems: services,
  the real book fresh (no divergence, no refused order, its parts matching the Binance account),
  containers, sites and certificates, last night's backups, disk.
