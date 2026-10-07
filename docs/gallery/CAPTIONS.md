# Gallery captions

Every shot is made by `scripts/gallery.py` from the real UI (dark mode, 1440x900 unless noted). Where the page clears a form on success, the shot stacks the form before (top) over the result (bottom).


## Home

- `01-home.png`: Home: the Try-it panel opens already answered. The page ran its default question (top 5 products by revenue last quarter) against the app's own API; the answer shows the chart, table and the SQL that ran, with KPI cards below.
- `02-tryit-1.png`: Try-it, example 1: input `top 5 products by revenue last quarter`. Output: SQL, a 5-row table and bar chart; "last quarter" is the newest quarter in the data.
- `03-tryit-2.png`: Try-it, example 2: input `monthly revenue in 2011`. Output: a 12-month table and a line chart from SQL with a 2011 filter.
- `04-tryit-3.png`: Try-it, example 3: input `is mahine sab se zyada bikne wali cheez` (Roman Urdu). Output: understood as "this month, top products"; 10 rows and a bar chart.
- `05-tryit-4.png`: Try-it, example 4: input `revenue by country`. Output: 32 countries ranked by net revenue, with the SQL.
- `06-home-phone.png`: Home at phone width (390 px): the same Try-it page reflowed, navigation along the top.

## Data

- `07-data-schema.png`: Schema browser on the shipped sample business: the five business tables with row counts; order_items and orders expanded to show column types and sample rows.
- `08-data-upload-file.png`: Upload by file: `bakery_sales.csv` (40 order lines) chosen through the file picker, signed in as a manager. Output: the "Imported bakery_sales (40 rows)" notice, the table with inferred types and sample rows, and the mapping form that turns it into business tables.
- `09-data-paste-csv.png`: Paste CSV: top is the example rows typed into the paste box (3 data rows); bottom is the result after Import, the new `pasted_sales` table with its columns, types and rows.

## Ask

- `10-ask-english.png`: Ask in English: `Top 10 products by revenue in 2011`. Output: bar chart, table, and the editable SQL that ran; marked as built-in parser, read-only checked.
- `11-ask-roman-urdu.png`: Ask in Roman Urdu: `har mahine ki bikri` (sales every month). Output: the badge "Roman Urdu understood", a 25-month line chart and table.
- `12-ask-refused-write.png`: A write attempt: after a one-row question, the SQL box is edited to `DELETE FROM orders` and run. Output: refused, "only SELECT is permitted, got Delete"; the data is untouched.

## Forecasts

- `13-forecast.png`: Forecasts: horizon set to 13 weeks and Run forecast pressed. Output: totals add up (yes), 39 of 60 series beat seasonal naive in the backtest, chart with forecast band, error by level, and every series.

## Fraud & anomalies

- `14-alerts-detail.png`: Fraud and anomalies filtered to high severity: 24 matching. Each alert lists its reasons with the numbers, e.g. a 10,953.50 refund 5.7 robust deviations above the typical refund and larger than all the customer ever bought.

## Customer risk

- `15-risk-fairness.png`: Customer risk page: scorecard metrics and the fairness panel by country group and tenure band, with disparate impact against the four-fifths screen.
- `16-risk-customer.png`: One decision with reasons: a DECLINE customer opened from the list. Output: score 496 against cut-off 539, points per feature, ranked reason codes, and the credit-limit ticket form.

## Workflows

- `17-workflows-sign-in.png`: Sign in: Amna Khan (staff) chosen and her PIN, read from the data folder's pins.txt, typed in the sidebar (masked) before pressing Sign in.
- `18-workflows-raise-refund.png`: Raise a refund ticket: top is the form as filled (refund, order 489437, customer 15362, 250); bottom is the result, ticket #1 pending with two manager gates because the refund is over 100.
- `19-workflows-first-approval.png`: First approval: Bilal Ahmed (manager) opens the ticket, types a comment and approves. The first gate turns done and the decision and audit entry appear; Amna cannot approve her own ticket.
- `20-workflows-second-approval.png`: Second person: Sana Malik (a different manager) approves with a comment. Both gates are done, status approved, and Execute is offered; the audit shows the fully-approved entry.
- `21-workflows-audit-verify.png`: Audit verify, after executing the ticket: pressing Verify reports "Audit intact: 8 entries, signed head ok, 1 tickets match the trail"; the executed refund shows in the ledger.

## Dashboard

- `22-dashboard.png`: Dashboard in full: KPI cards with 13-week sparklines and change against the previous 30 days, and net revenue by month, country, product and category.

## Export

- `23-export-query-csv.png`: CSV export of a query: Download CSV pressed under the monthly revenue result (25 rows). Bottom is the real file that was saved, analyst-export.csv.
- `24-export-audit-csv.png`: CSV export of the audit trail: Download CSV on the Workflows page. Bottom is the saved file, 8 hash-chained entries with prev_hash and hash columns.

## About

- `25-about.png`: About and guide: what the app is, what it does and how to use it, with the expected input and output of each feature, limits, privacy and the maker.
