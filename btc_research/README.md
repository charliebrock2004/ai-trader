# BTC research

Independent historical research module for BTCUSDT spot. It asks whether a
small set of pre-registered strategies still has a positive expected return
after costs, out of sample. It does not place orders, does not read private
API keys, and does not use PeakSwiftBig's database or login.

This is not the AI Trader paper desk and it is not a live trading engine.
Claude owns that integration. See [INTEGRATION.md](INTEGRATION.md).

## Run

From this directory, with Python 3.10+ and no third-party packages:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m btc_research run --start 2019-01-01 --cache data/cache/BTCUSDT_1h.csv --out outputs/latest
```

The loader uses Binance public market data (`data-api.binance.vision`, then
`api.binance.com`). No key. If both fail, it tries the public monthly zip
files on `data.binance.vision`. It caches candles locally and will not fill
gaps.

`outputs/latest/report.md` is the human report. `summary.json` is the
machine-readable result (`btc-research-summary/v1`).

## What the numbers are

Every performance figure is a **historical backtest**. The summary says
paper trading was not run and live trading was not run. A passing gate would
still not be permission to trade.

## Tests

```bash
cd btc_research
PYTHONPATH=src:tests python3 -m unittest discover -s tests -v
```

`PYTHONPATH` needs `tests` as well as `src` because the fixtures live beside
the tests. The tests do not use the network.
