# Oil monitor

A small Python background worker that collects public WTI and Brent crude
prices, plus energy news, and appends one machine-readable JSON object per
collection cycle to a JSON Lines file.

## Run

Requires Python 3.10 or newer; there are no third-party dependencies.

```sh
python3 oil_monitor.py
```

The worker runs continuously, polling every 15 minutes by default. Stop it
with Ctrl+C. To collect only one cycle:

```sh
python3 oil_monitor.py --once
```

Choose a different interval or output location with:

```sh
python3 oil_monitor.py --interval 600 --output data/oil.jsonl
```

By default, it writes:

- `data/oil_monitor.jsonl` — append-only collection records.
- `data/oil_monitor_state.sqlite3` — IDs of news articles already emitted.

Each JSON Lines record has a UTC `collected_at` timestamp, `prices`, newly
published `news`, and an `errors` array. Price entries include a UTC market
timestamp, price, previous daily close, absolute and percentage change, and
`price_change_sentiment` (`bullish`, `bearish`, `neutral`, or `unknown`).
Sentiment describes the price move versus the previous daily close; it is not
an assessment of the news or investment advice. On the first price record, the
sentiment is `unknown` if the provider has not supplied a previous close.

Price data comes from Yahoo Finance's public chart endpoint for `CL=F` (WTI)
and `BZ=F` (Brent). News comes from OilPrice.com and the U.S. Energy
Information Administration's Today in Energy RSS feed. Individual source
failures are logged and included in that cycle's `errors`; other sources
continue to be collected.

For a long-running deployment, run this process under your operating system's
service manager or a container supervisor configured to restart it if it exits.

### Deploy as a Render background worker

The included `render.yaml` defines a continuously running worker with a
persistent disk for its JSONL output and SQLite news-deduplication state.
Deploy it by pushing this project to GitHub, then in Render choose **New** >
**Blueprint**, connect the repository, and apply the `render.yaml` Blueprint.
Render's `starter` worker plan and persistent disk are paid resources; review
the current price in Render before applying the Blueprint. The worker polls
every 15 minutes and automatically restarts if it exits unexpectedly.

### Run on GitHub Actions without a paid server

The included `.github/workflows/oil-monitor.yml` runs one collection every six
hours and also supports manual runs from the repository's **Actions** tab.
Each run appends to that day's JSONL file under `data/oil/`, saves the SQLite
news-deduplication database, and commits both to the repository. This retains
the data in Git instead of relying on expiring workflow artifacts or a hosted
disk. The workflow needs Actions to have read-and-write repository permissions;
the workflow requests `contents: write` for this purpose.

Push the workflow and worker to GitHub, then open **Actions** in the repository
to confirm the workflow is enabled. Start a run using **Oil monitor** > **Run
workflow**, or wait for its next scheduled run. GitHub Actions is scheduled,
not continuously running, and scheduled starts may be delayed. Git-backed
history grows over time; keep an eye on repository size. In a public repository,
collected news and prices are public along with the source code.

### Run as a user service on Linux with systemd

From the project directory, install and start the included service:

```sh
mkdir -p ~/.config/systemd/user
cp oil-monitor.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now oil-monitor.service
```

Check its status and logs with `systemctl --user status oil-monitor` and
`journalctl --user -u oil-monitor -f`. To keep the service running after
logout and start it at boot, enable lingering for your account with
`sudo loginctl enable-linger "$USER"`. Stop it with
`systemctl --user disable --now oil-monitor`.
