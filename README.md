# Expense Tracker

AI-enabled personal expense tracking and insights generator.

A personal income/expense tracker. This README is "how do I run it" and
"what's actually built" -- kept in sync with the app, not aspirational.

## Run it locally

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env             # optional -- only for bank sync / statement import (see below)
chmod 600 .env

uvicorn app.main:app --host 127.0.0.1 --reload
```

Always bind to `127.0.0.1` -- never `0.0.0.0`, which would expose your
financial data to every device on the same network.

Then open http://127.0.0.1:8000 in your browser.

On first run it creates a local SQLite file (`expense_tracker.db`) and
seeds a starter set of categories automatically. From there:

1. Go to **Accounts** and add your accounts (checking, cash, any credit
   cards) with their current balance as of today.
2. Go to **Add** to log transactions. Pick "Transfer" for credit card
   payments (moving money from checking to pay down the card) -- don't log
   those as a second expense.

On macOS, there's also a double-clickable `Expense Tracker` app (installed
separately in `~/Applications`, findable via Spotlight) that launches the
server and opens Chrome automatically -- see `launch.command` for the
underlying script if setting that up again.

## What's built

- **Accounts**: running balances derived from the transaction log (not a
  stored counter), credit cards tracked as what you owe. Editable after
  creation (name, type, opening balance).
- **Transactions**: income / expense / transfer, with category, a
  reimbursable flag (tracked separately from the money actually arriving),
  and an "exclude from living expenses" flag for one-off costs like tuition
  or a deposit. Editable and deletable.
- **Dashboard**: current month's Income/Expenses/Net and Living
  Expenses/Income/Living Net (two rows -- with and without one-off costs
  factored in), each showing this month's actual figure alongside a
  trailing-average "typical month" figure. Spending by category, account
  balances, pending reimbursements, recent transactions. Filterable to a
  single account.
- **Trends** (`/trends`): a category's spend over a custom date range,
  either isolated (just that category) or broken out across every category
  at once with a fixed, validated color per category.
- **Bank sync via Plaid**: link an account from the Accounts page ("Connect
  with Plaid"); new transactions are pulled automatically every time the
  app launches (plus a per-account "Sync now"). Needs `PLAID_CLIENT_ID`,
  `PLAID_SECRET_SANDBOX` / `PLAID_SECRET_PRODUCTION`, `PLAID_ENV` and
  `PLAID_TOKEN_KEY` in `.env` -- see `.env.example`. Access tokens are
  encrypted at rest.
- **Statement paste** (`/import`): for anything Plaid can't reach -- paste
  raw statement text, parsed by Gemini (needs `GEMINI_API_KEY`).
- **Review queue** (`/import/review`): everything imported, from either
  source, waits here for Confirm/Discard before touching your real ledger.
  Likely duplicates are flagged, and each import cross-checks the bank's
  stated balance against the app's.

## Not built yet

- A UI for adding custom categories (currently a fixed, seeded list)
- Budgets per category
- Deployment to Prod (Postgres + hosting)

## Project layout

```
app/
  main.py            FastAPI app, security middleware, startup (tables, seeds, Plaid sync)
  database.py        DB engine/session (SQLite in Dev, set DATABASE_URL for Prod)
  models.py          Account, Category, Transaction, PendingImport, ImportCapture
  services.py        Balances, summaries, trends, reimbursements, review queue
  templating.py      Shared Jinja2Templates instance (cache-busts static assets)
  plaid_client.py    Plaid API calls (link, exchange, sync, balance) -- no DB
  plaid_sync.py      Plaid -> review queue, per account and on startup
  token_crypto.py    Encrypts Plaid access tokens at rest
  import_parser.py   Gemini-based statement text -> transaction candidates
  routers/           dashboard, accounts, transactions, trends, imports, plaid_routes
  templates/         Jinja2 HTML
  static/            CSS
```
