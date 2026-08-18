# Expense Tracker (v0.1)

AI enabled personal expense tracking and insights generator.

A personal income/expense tracker. See `plan.md`-equivalent notes in the
project doc for the full design rationale (credit card handling, cadence,
reimbursements, environments). This README is just "how do I run it."

## Run it locally (Dev)

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env             # defaults are fine for Dev, no editing needed

uvicorn app.main:app --reload
```

Then open http://127.0.0.1:8000 in your browser.

On first run it creates a local SQLite file (`expense_tracker.db`) and
seeds a starter set of categories automatically. From there:

1. Go to **Accounts** and add your accounts (checking, cash, any credit
   cards) with their current balance as of today.
2. Go to **Add** to log transactions. Pick "Transfer" for credit card
   payments (moving money from checking to pay down the card) — don't log
   those as a second expense.

## What's built so far (v0.1)

- Accounts with running balances (credit cards tracked as what you owe)
- Transactions: income / expense / transfer, with category, cadence
  (one-time/monthly/quarterly/semesterly/annual), and a reimbursable flag
- Dashboard: this month's income/expenses/net, a 12-month trailing average
  (so a one-off like tuition doesn't distort the monthly view), spending by
  category, account balances, pending reimbursements, recent transactions

## Not built yet (see plan)

- CSV import (and historical backfill)
- Budgets per category
- AI insights digest (savings rate, emergency fund runway, spending trend
  nudges, interest-cost awareness)
- Deployment to Prod (Postgres + hosting)

## Project layout

```
app/
  main.py          FastAPI app + startup (creates tables, seeds categories)
  database.py      DB engine/session (SQLite in Dev, set DATABASE_URL for Prod)
  models.py        Account, Category, Transaction
  services.py      Balance calculation, monthly summary, reimbursements
  routers/         dashboard.py, accounts.py, transactions.py
  templates/       Jinja2 HTML
  static/          CSS
```
