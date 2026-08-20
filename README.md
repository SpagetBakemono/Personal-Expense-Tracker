# Expense Tracker

AI-enabled personal expense tracking and insights generator.

A personal income/expense tracker. This README is "how do I run it" and
"what's actually built" -- kept in sync with the app, not aspirational.

## Run it locally

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env             # optional -- only needed for statement import (see below)

uvicorn app.main:app --reload
```

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
- Statement import (in progress): paste raw bank/card statement text,
  parsed into transaction candidates via Gemini's free API tier
  (`app/import_parser.py`) -- not yet wired to a review queue in the UI.

## Not built yet

- The statement-import review queue (confirm/edit/discard parsed
  transactions before they're saved)
- A UI for adding custom categories (currently a fixed, seeded list)
- Budgets per category
- Deployment to Prod (Postgres + hosting)

## Project layout

```
app/
  main.py            FastAPI app + startup (creates tables, seeds categories)
  database.py        DB engine/session (SQLite in Dev, set DATABASE_URL for Prod)
  models.py          Account, Category, Transaction
  services.py        Balance calculation, monthly summaries, trends, reimbursements
  templating.py       Shared Jinja2Templates instance (cache-busts static assets)
  import_parser.py    Gemini-based statement text -> transaction candidates
  routers/           dashboard.py, accounts.py, transactions.py, trends.py
  templates/         Jinja2 HTML
  static/            CSS
```
