# Analyst-in-a-Box

An AI back office for a small business that runs on your own computer, with no GPU, no account and no model required. It is for the owner or small team of a shop or wholesaler who has orders in a spreadsheet or database and no analyst.

Connect a database or drop in a spreadsheet, then ask questions in English or Roman Urdu and see the SQL that answered them, forecast demand with totals that add up, get odd orders and refunds flagged with reasons, score customers with a points scorecard and a fairness panel, push refunds and credit-limit changes through approval gates with a tamper-evident audit trail, and watch the KPIs on a dashboard. A language model is optional and its output is always checked.

**See it:** [showcase page with every screen](https://analyst-in-a-box-bay.vercel.app) · [Kaggle notebook running the engine](https://www.kaggle.com/code/muhammadhammas13/analyst-in-a-box-demo)

## What it does

- **Ask your data** in English or Roman Urdu: chart, table and the SQL that ran, editable, read-only and exportable as CSV.
- **Data**: bundled sample business, SQLite, PostgreSQL, CSV/TSV/Excel upload or pasted CSV, with a schema browser and a mapping form for your own sheets.
- **Forecasts**: weekly demand per product and category, reconciled so the levels add up, with a backtest against seasonal naive.
- **Fraud and anomalies**: robust z-score and rule checks on orders, refunds and trading days, every alert with its reasons, one click to open a review ticket.
- **Customer risk**: a points scorecard with reason codes per customer and a fairness panel.
- **Workflows**: refund and credit-limit tickets, PIN sign-in, approval gates that depend on the amount, four-eyes rule, and a hash-chained audit trail with Verify.
- **Dashboard**: revenue, orders, order value, active customers and refund rate with sparklines; CSV export of any result and of the audit trail.

## Gallery

Every picture is a real screenshot of the running app, taken by `scripts/gallery.py` (headless Edge, dark mode, sample data, a temporary data folder); the same text is in [docs/gallery/CAPTIONS.md](docs/gallery/CAPTIONS.md).

### Home

![01-home](docs/gallery/01-home.png)

Home: the Try-it panel opens already answered. The page ran its default question (top 5 products by revenue last quarter) against the app's own API; the answer shows the chart, table and the SQL that ran, with KPI cards below.

![02-tryit-1](docs/gallery/02-tryit-1.png)

Try-it, example 1: input `top 5 products by revenue last quarter`. Output: SQL, a 5-row table and bar chart; "last quarter" is the newest quarter in the data.

![03-tryit-2](docs/gallery/03-tryit-2.png)

Try-it, example 2: input `monthly revenue in 2011`. Output: a 12-month table and a line chart from SQL with a 2011 filter.

![04-tryit-3](docs/gallery/04-tryit-3.png)

Try-it, example 3: input `is mahine sab se zyada bikne wali cheez` (Roman Urdu). Output: understood as "this month, top products"; 10 rows and a bar chart.

![05-tryit-4](docs/gallery/05-tryit-4.png)

Try-it, example 4: input `revenue by country`. Output: 32 countries ranked by net revenue; the chart is horizontal bars with whole country names, the 12 largest drawn and "20 more in the table", with the SQL.

![06-home-phone](docs/gallery/06-home-phone.png)

Home at phone width (390 px): content first. Sign-in sits behind one Sign in button, and the eight pages are a labelled 4 x 2 tab grid.


### Data

![07-data-schema](docs/gallery/07-data-schema.png)

Schema browser on the shipped sample business: the five business tables with row counts; order_items and orders expanded to show column types and sample rows.

![08-data-upload-file](docs/gallery/08-data-upload-file.png)

Upload by file: `bakery_sales.csv` (40 order lines) chosen through the file picker, signed in as a manager. Output: the "Imported bakery_sales (40 rows)" notice, the table with inferred types and sample rows, and the mapping form that turns it into business tables.

![09-data-paste-csv](docs/gallery/09-data-paste-csv.png)

Paste CSV: top is the example rows typed into the paste box (3 data rows); bottom is the result after Import, the new `pasted_sales` table with its columns, types and rows.


### Ask

![10-ask-english](docs/gallery/10-ask-english.png)

Ask in English: `Top 10 products by revenue in 2011`. Output: bar chart, table, and the editable SQL that ran; marked as built-in parser, read-only checked.

![11-ask-roman-urdu](docs/gallery/11-ask-roman-urdu.png)

Ask in Roman Urdu: `har mahine ki bikri` (sales every month). Output: the badge "Roman Urdu understood", a 25-month line chart and table.

![12-ask-refused-write](docs/gallery/12-ask-refused-write.png)

A write attempt: after a one-row question, the SQL box is edited to `DELETE FROM orders` and run. Output: refused, "only SELECT is permitted, got Delete", marked "not run"; the earlier result is cleared, not left under the refusal, and the SQL stays editable.


### Forecasts

![13-forecast](docs/gallery/13-forecast.png)

Forecasts: horizon set to 13 weeks and Run forecast pressed. Output: totals add up (yes), 39 of 60 series beat seasonal naive in the backtest, a note that no 52-week season is fitted on two years of weeks, the total charted against the same weeks last year (691,373 forecast against 281,795 a year earlier), the backtest table by level in full, and every series with last year, its own forecast and the reconciled one.

![13b-forecast-series](docs/gallery/13b-forecast-series.png)

Every series, close up: product 37410 sold two bulk orders (6,012 and 19,164 units) in early 2010 and nothing since. Its own forecast (TSB) is 1 unit over 13 weeks; MinT reconciliation adds 346, flagged "+346 reconciled". 22151 (own forecast 794, reconciled 469) is the same effect in the other direction.


### Fraud & anomalies

![14-alerts-detail](docs/gallery/14-alerts-detail.png)

Fraud and anomalies filtered to high severity: 24 matching. Each alert lists its reasons with the numbers, e.g. a 10,953.50 refund 5.7 robust deviations above the typical refund and larger than all the customer ever bought.


### Customer risk

![15-risk-fairness](docs/gallery/15-risk-fairness.png)

Customer risk page: scorecard metrics and the fairness panel by country group and tenure band, with disparate impact against the four-fifths screen.

![16-risk-customer](docs/gallery/16-risk-customer.png)

One decision with reasons: a DECLINE customer opened from the list. Output: score 496 against cut-off 539, points per feature, ranked reason codes, and the credit-limit ticket form.


### Workflows

![17-workflows-sign-in](docs/gallery/17-workflows-sign-in.png)

Sign in: Amna Khan (staff) chosen and her PIN, read from the data folder's pins.txt, typed in the sidebar (masked) before pressing Sign in.

![18-workflows-raise-refund](docs/gallery/18-workflows-raise-refund.png)

Raise a refund ticket: top is the form as filled (refund, order 489437, customer 15362, 250); bottom is the result, ticket #1 pending with two manager gates because the refund is over 100.

![19-workflows-first-approval](docs/gallery/19-workflows-first-approval.png)

First approval: Bilal Ahmed (manager) opens the ticket, types a comment and approves. The first gate turns done and the decision and audit entry appear; Amna cannot approve her own ticket.

![20-workflows-second-approval](docs/gallery/20-workflows-second-approval.png)

Second person: Sana Malik (a different manager) approves with a comment. Both gates are done, status approved, and Execute is offered; the audit shows the fully-approved entry.

![21-workflows-audit-verify](docs/gallery/21-workflows-audit-verify.png)

Audit verify, after executing the ticket: pressing Verify reports "Audit intact: 8 entries, signed head ok, 1 tickets match the trail"; the executed refund shows in the ledger.


### Dashboard

![22-dashboard](docs/gallery/22-dashboard.png)

Dashboard in full: KPI cards with 13-week sparklines and change against the previous 30 days, and net revenue by month, country, product and category.


### Export

![23-export-query-csv](docs/gallery/23-export-query-csv.png)

CSV export of a query: Download CSV pressed under the monthly revenue result (25 rows). Bottom is the real file that was saved, analyst-export.csv.

![24-export-audit-csv](docs/gallery/24-export-audit-csv.png)

CSV export of the audit trail: Download CSV on the Workflows page. Bottom is the saved file, 8 hash-chained entries with prev_hash and hash columns.


### About

![25-about](docs/gallery/25-about.png)

About and guide: what the app is, what it does and how to use it, with the expected input and output of each feature, limits, privacy and the maker.



## Inputs

| Feature | Upload from the computer | Paste | Hint and example |
|---|---|---|---|
| Data (spreadsheets) | file picker or drag and drop; .csv, .tsv, .txt, .xlsx, .xlsm; 40 MB, 1,000,000 rows, 500 columns; progress bar, clear errors | paste CSV text | header-row format shown, Load example button |
| Data (databases) | SQLite file path | PostgreSQL URL | placeholder shows both forms |
| Ask your data | not applicable (a question is text) | type or paste a question or SQL | example chips, input hint |
| Dashboard, forecasts, fraud, risk, workflows | read what is in the active source | not applicable | an empty or non-business source points to Data |

Importing needs a manager sign-in. The in-app **About & guide** page (also the **?** button) covers what it is, how to use each feature with its expected input and output, privacy, the roadmap and the maker.

## Tech stack

- **Backend**: Python 3.11+, FastAPI, Uvicorn, SQLite, optional PostgreSQL
- **SQL safety**: sqlglot parses every query; only a single SELECT runs, on a read-only connection
- **Forecasting**: [demand-forecast-platform](https://github.com/hammasbuilds/demand-forecast-platform) (ETS, TSB for intermittent demand, MinT/bottom-up/top-down reconciliation, rolling-origin backtests)
- **Risk scoring**: [credit-risk-engine](https://github.com/hammasbuilds/credit-risk-engine) (WoE binning, points scorecard, reason codes, fairness metrics)
- **Uploads**: CSV/TSV, Excel via openpyxl
- **Security**: PBKDF2-hashed PINs, approval policy enforced server-side, SHA-256 hash-chained audit log
- **Frontend**: vanilla JavaScript, HTML and CSS with SVG charts, no build step; light and dark themes
- **Optional LLM**: Ollama, Anthropic or any OpenAI-compatible API; generated SQL goes through the same read-only checks
- **Tooling**: uv, pytest, ruff, Playwright for the UI audit and gallery

## Run it

```
uv run analyst-in-a-box        # starts the server and opens http://127.0.0.1:8780
```

or double-click `run.bat` (or the "Analyst-in-a-Box" desktop shortcut). The first run builds the sample database in about ten seconds. Options: `--port`, `--no-browser`, `--db FILE`, `--reset-pin "Name"`. Data lives in `~/.analyst-in-a-box` (set `ANALYST_HOME` to move it). Python 3.11+ and [uv](https://docs.astral.sh/uv/); everything runs on the CPU and offline.

Optional language model (never required), set before launching:

```
ANALYST_LLM=ollama     ANALYST_LLM_MODEL=qwen2.5-coder:7b          # CPU Ollama on localhost:11434
ANALYST_LLM=anthropic  ANTHROPIC_API_KEY=...                       # ANALYST_LLM_MODEL defaults to claude-haiku-4-5
ANALYST_LLM=openai     OPENAI_API_KEY=...  ANALYST_OPENAI_BASE_URL=...
```

**Signing in.** Reading, asking and exporting need no sign-in. Anything that changes state (tickets and their approvals, dismissing alerts, uploads, adding or removing a data source) needs a name and a PIN. On the first start after this version the app gives each of the four people a random 8-character PIN, stores only salted PBKDF2 hashes, and writes the PINs once to `pins.txt` in the data folder: hand each person their own line and delete the file. A lost PIN is replaced with `analyst-in-a-box --reset-pin "Bilal Ahmed"`; anyone can change their own PIN from the sidebar. Five wrong PINs lock that name for 60 seconds.

PostgreSQL needs the optional driver: `uv sync --extra postgres`.

## Licence

MIT. Sample data: UCI Online Retail II, CC BY 4.0. The Manrope typeface in `src/analyst_in_a_box/static/fonts` is bundled under the SIL Open Font License 1.1.

Built by Muhammad Hammas · [github.com/hammasbuilds](https://github.com/hammasbuilds)
