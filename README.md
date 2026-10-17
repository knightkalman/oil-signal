# Oil monitor

A small Python background worker that collects public WTI and Brent crude
prices, plus energy news, and appends one machine-readable JSON object per
collection cycle to a JSON Lines file.

## Run

Requires Python 3.10 or newer; there are no third-party dependencies.

```sh
python3 oil_monitor.py
```

The worker runs continuously, polling every five minutes by default. Stop it
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

### AI analysis of news

Each scheduled run passes newly collected headlines and feed summaries to
Google's managed **Antigravity agent** through the Gemini Interactions API.
At most once an hour, it also reassesses up to 5 older stories whose
assessments are at least 24 hours old, retaining the previous assessments.
For these reassessments, it provides observed WTI/Brent price moves since
publication as context; the agent is instructed not to treat those moves as
proof that a story caused them. The schedule state is stored in
`data/oil_analysis_state.json`.
This is a headless API integration, not the Antigravity VS Code plugin; the
plugin runs in the editor and is not available to GitHub Actions. The
integration uses the managed agent ID `antigravity-preview-09-2026` and asks
for schema-constrained JSON describing likely oil-price impact, a short
summary, a textual evidence-validity label, a rationale, and uncalibrated
confidence. Tools are disabled for this classification request. Agent requests
can run for up to five minutes. The agent
receives the public feed text; it is treated as untrusted input and is not
independently fact-checked. The managed agent requires interaction storage;
Google may retain the submitted public headlines, market context, and results
under its API terms. Do not include private data in this request.

To enable it, create a Gemini API key in [Google AI Studio](https://aistudio.google.com/app/apikey),
then add it to this public repository under **Settings** > **Secrets and
variables** > **Actions** > **New repository secret**, named exactly
`GEMINI_API_KEY`. Do not commit the key or put it in the workflow file. Google
API access may have free-tier limits or usage charges; check the current
[Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing) and
[terms](https://ai.google.dev/gemini-api/terms) first. The managed Antigravity
agent is a preview API and availability or pricing may change.

If the secret is missing, the collector still saves prices and news and marks
`news_analysis.status` as `skipped`. If the API call or output validation
fails, it records `failed` and the error rather than inventing an analysis.
Successful per-article labels distinguish expected oil-price impact from
evidence quality: `validity` means only how clearly the supplied text supports
the model's interpretation, not whether its claims are true. The dashboard
shows direction, evidence quality, summary, and uncalibrated model confidence.

Price data comes from Yahoo Finance's public chart endpoint for `CL=F` (WTI)
and `BZ=F` (Brent), using one-minute provider data and retaining each
collection's latest observation to limit duplicate storage. News
comes from OilPrice.com, EIA Today in Energy, EIA press releases, BBC Business,
and CNBC Business. Broad business feeds are filtered for oil, fuel, gas, and
energy topics. Individual source failures are logged and included in that
cycle's `errors`; other sources continue to be collected.

For a long-running deployment, run this process under your operating system's
service manager or a container supervisor configured to restart it if it exits.

### Deploy as a Render background worker

The included `render.yaml` defines a continuously running worker with a
persistent disk for its JSONL output and SQLite news-deduplication state.
Deploy it by pushing this project to GitHub, then in Render choose **New** >
**Blueprint**, connect the repository, and apply the `render.yaml` Blueprint.
Render's `starter` worker plan and persistent disk are paid resources; review
the current price in Render before applying the Blueprint. The worker polls
every five minutes and automatically restarts if it exits unexpectedly.

### Run on GitHub Actions without a paid server

The included `.github/workflows/oil-monitor.yml` runs one collection every five
minutes and also supports manual runs from the repository's **Actions** tab.
Each run appends to that day's JSONL file under `data/oil/`, saves the SQLite
news-deduplication database and reassessment schedule state, and commits them
to the repository. This retains the data in Git instead of relying on expiring
workflow artifacts or a hosted disk. The workflow needs Actions to have
read-and-write repository permissions;
the workflow requests `contents: write` for this purpose. It also publishes the
interactive dashboard to GitHub Pages after each collection.

Push the workflow and worker to GitHub, then open **Actions** in the repository
to confirm the workflow is enabled. Start a run using **Oil monitor** > **Run
workflow**, or wait for its next scheduled run. GitHub Actions is scheduled,
not continuously running, and scheduled starts may be delayed. Git-backed
history grows over time; keep an eye on repository size. In a public repository,
collected news and prices are public along with the source code.

The dashboard is at `https://<owner>.github.io/<repository>/` for a project
repository. Enable GitHub Pages in the repository's **Settings** > **Pages**
with **GitHub Actions** as the deployment source. Free GitHub Pages hosting
requires a public repository; publishing also makes collected prices and news
public. The dashboard charts WTI and Brent prices, marks published news on the
timeline, and labels sentiment validity as valid, stale, or insufficient data.
Validity requires positive current and previous-close prices and a provider
quote timestamp no more than 48 hours old. Sentiment is a descriptive
comparison to the previous daily close, not a forecast or investment advice.
The chart plots collected provider-price observations, overlays each
collection's bullish/bearish/neutral price-direction marker, and colors news
markers using the latest Antigravity assessment (bullish, bearish, mixed,
neutral, unclear, or unassessed). News markers link to corresponding
published stories. GitHub Actions and the provider can delay five-minute
updates; an open dashboard re-fetches the published data every five minutes.
This is scheduled collection, not a live quote stream.

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
