# Review notes, 2026-10-07

Hostile read of Analyst-in-a-Box before a fix pass. Each item was reproduced through the real code or HTTP API first (scratch scripts, not kept), fixed, and pinned by a regression test in `tests/test_security.py` unless noted.

## Weaknesses found

| # | Area | Severity | Finding | Fix |
|---|---|---|---|---|
| 1 | Four-eyes | high | The acting user was a drop-down plus a name in the request body. One person could raise a refund as Amna and approve it as Bilal and Sana. | PIN sign-in, server-side session cookie (HttpOnly, SameSite=Strict, stored hashed); the actor is the session, a body naming someone else gets 403. |
| 2 | Refunds | high | Over-refund: three pending tickets for the full 426.30 of one order were approved and executed (1,278.90 paid); only executed refunds counted. | Open tickets count against the order; execute re-checks the order total against the ledger. |
| 3 | Workflow race | high | Eight simultaneous approvals by one manager were all recorded (check-then-insert); concurrent execute could pay twice. | State changes run under one lock; unique indexes on one approval per person per ticket and one ledger row per ticket. |
| 4 | Audit | medium | Deleting the last entries, deleting all entries, or recomputing the whole chain after an edit all verified "intact"; editing `tickets` directly left no trace. | HMAC-signed head file with a key outside the database; verify compares them, and cross-checks tickets, approvals, ledger and credit limits against the trail. |
| 5 | SQL | medium | String bomb: `printf('%.*c', 900000000, 'x')` made a 900 MB string in 6.6 s and returned it. A blob or Infinity cell would have made the JSON response a 500. | `SQLITE_LIMIT_LENGTH` 1 MB and other limits, 5,000-char cells, 4 MB per result, blobs and non-finite floats made JSON-safe. |
| 6 | HTTP | medium | No Origin, Host or body-size checks: cross-origin multipart upload and JSON writes with a foreign Host were accepted (CSRF, DNS rebinding). No CSP. | Guard middleware, CSP, nosniff, frame deny, no-store on the API. |
| 7 | Upload | medium | A cell over 131,072 characters gave a 500; a decompression-bomb xlsx was unpacked; the whole request body was read before the size check; 2,500 columns gave a 500. | Chunked read with a cap, content-length check, CSV field and row caps, zip unpacked-size check before openpyxl, column cap. |
| 8 | Upload | medium | Silent data corruption: `123456789012345678` became `123456789012345680` (via float), `00123` became 123, `99999999999999999999` raised, `1,5` became 15. | Strict integer inference (no leading zeros, 64-bit, no thousands guessing), long ids stay text, NaN text is missing. |
| 9 | Tickets | medium | `NaN` amounts passed every comparison; `inf` slipped through when no order check ran. | Finite and below 1e12. |
| 10 | Sources | low | The app's own database (PIN hashes, audit) could be added as a data source and queried. | Refused. |
| 11 | Sources | low | A table name containing `"` broke the schema browser (unquoted identifier). | Identifiers quoted. |
| 12 | SQL | low | `main.sqlite_master` was stopped only by the "unknown table" check and the authoriser, not the catalogue rule. | Base name checked. |
| 13 | UI | low | A page that finished loading after the user navigated away painted over the new page. | Route sequence guard; leftovers hit a sink instead of throwing. |
| 14 | UI | low | The inline theme script would be blocked by a CSP. | Moved to `static/theme.js`. |

## Tried and held

Stacked statements, `/* ; */` comments, `--` newline tricks, Unicode lookalike semicolons (U+037E, U+FF1B), zero-width characters, `ATTACH`, `PRAGMA` (statement and `pragma_*` table functions), `load_extension` (lower, upper, quoted), CTE-wrapped `DELETE`/`INSERT`/`UPDATE`, `REPLACE`, `VACUUM INTO`, `EXPLAIN`, `sqlite_master`/`sqlite_schema`/`sqlite_temp_master` in four quoting styles, `LIMIT -1` and `LIMIT 99999999` (still 1,000 rows), a recursive CTE counting forever and a four-way cross join (both time out at the 8 s deadline), 5,000-deep parentheses and 20,000-term sums (parser refuses). The authoriser alone, with the validator bypassed, refused every write. No bypass found.

## Left open

- PINs are short secrets for four fixed people; no SSO, no user management in the UI, sessions 12 h.
- Someone with file access to the data folder can edit the database, the head file and its key together.
- No upload quota; PostgreSQL URL is connected to as typed (SSRF-ish on a shared network, manager-only).
- PostgreSQL path untested against a live server.

## Product gaps (not built)

1. Add and remove people (and roles) from the UI.
2. Saved questions and a scheduled digest of KPIs, alerts and open tickets.
3. Refund execution that actually calls a payment provider (today it writes the app's ledger only).
4. Revenue (not just unit) forecasts and what-if prices.
5. Per-ticket attachments and comments thread.

## Added in this pass

- CSV export of any query result (formula-safe, 50,000 rows) and of the audit trail.
- PIN sign-in with change and reset, lockout, audited logins.
- Audit head signing and cross-check of live tables against the trail.
