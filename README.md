# Money Flow Momentum

Production-ready Bybit V5 cross-sectional flow impulse trading bot for linear USDT perpetuals.

## Features

- **Real-time flow metrics**: Aggressive buy/sell volume, delta, CVD, VWAP, buy/sell ratio from public trade stream
- **Cross-sectional ranking**: Scores all symbols, trades top long/short candidates
- **Robust risk management**: ATR stops, multi-target TPs, daily drawdown kill switch, cooldowns
- **Bybit V5**: REST + WebSocket, testnet/mainnet, one-way mode
- **Deployable**: Ubuntu CLI, systemd service, log rotation

## Quick Start

### 1. Install (Ubuntu)

```bash
git clone https://github.com/your-repo/Money-flow-momentum.git
cd Money-flow-momentum
chmod +x install.sh
./install.sh
```

### 2. Bootstrap Config

```bash
python bootstrap_config.py
```

Prompts for:
- Bybit API key/secret
- Testnet/mainnet
- Risk per trade, max positions, account size

Writes `.env` and `config/config.yaml`.

### 3. Run

```bash
source venv/bin/activate
python run_bot.py run
```

## Project Structure

```
src/
  main.py           # Entry, run loop
  config/           # Config loading
  exchange/         # Bybit V5 client
  data/             # Universe, market state, features
  signals/          # Flow impulse scoring
  portfolio/        # Position manager
  risk/             # Risk engine
  execution/        # Order execution
  storage/          # SQLite persistence
  backtest/         # Replay/backtest
  utils/
tests/
config/
scripts/
```

## Configuration

- `config/config.yaml` – strategy, risk, execution
- `.env` – API keys (never commit)

See `config/config.yaml.example` for full options.

## Scripts

- `scripts/paper_trade.sh` – Paper mode (testnet)
- `scripts/live_trade.sh` – Live (mainnet)
- `scripts/check_health.sh` – Health check

## Systemd

```bash
sudo cp money-flow-momentum.service /etc/systemd/system/
# Edit User/WorkingDirectory if needed
sudo systemctl daemon-reload
sudo systemctl enable money-flow-momentum
sudo systemctl start money-flow-momentum
sudo journalctl -u money-flow-momentum -f
```

## Log Rotation

Logs go to `logs/bot.log` when using systemd. Add `/etc/logrotate.d/money-flow-momentum`:

```
/home/ubuntu/Money-flow-momentum/logs/*.log {
    daily
    rotate 7
    compress
    missingok
}
```

## Tests

```bash
pytest tests/ -v
```

## Requirements

- Python 3.11+
- Ubuntu 20.04+ (or similar Linux)

## License

MIT
