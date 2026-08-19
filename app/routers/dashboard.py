from datetime import date

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import (
    get_all_balances,
    get_month_summary,
    get_pending_reimbursements,
    get_total_balance,
    get_trailing_average_expense,
    get_trailing_average_income,
)
from app.templating import templates

router = APIRouter()


@router.get("/")
def dashboard(
    request: Request,
    account_id: str | None = None,
    db: Session = Depends(get_db),
):
    today = date.today()
    balances = get_all_balances(db)
    total_balance = get_total_balance(balances)

    # account_id arrives as a string because the "All accounts" option's
    # empty value (?account_id=) would otherwise fail int parsing outright
    # (FastAPI 422s on "" for an int param, missing or not).
    parsed_account_id = int(account_id) if account_id else None
    selected_account = next((a for a, _ in balances if a.id == parsed_account_id), None)
    # An account_id that doesn't match any real account (stale link, typo'd
    # URL) falls back to unfiltered rather than silently showing nothing.
    effective_account_id = selected_account.id if selected_account else None

    summary = get_month_summary(db, today.year, today.month, effective_account_id)

    expense_avg, expense_avg_months = get_trailing_average_expense(
        db, account_id=effective_account_id
    )
    living_expense_avg, living_expense_avg_months = get_trailing_average_expense(
        db, account_id=effective_account_id, living_only=True
    )
    income_avg, income_avg_months = get_trailing_average_income(
        db, account_id=effective_account_id
    )

    pending = get_pending_reimbursements(db, effective_account_id)

    max_category = max(summary["by_category"].values()) if summary["by_category"] else 1

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "summary": summary,
            "balances": balances,
            "total_balance": total_balance,
            "selected_account_id": effective_account_id,
            "selected_account": selected_account,
            "expense_avg": expense_avg,
            "expense_avg_months": expense_avg_months,
            "living_expense_avg": living_expense_avg,
            "living_expense_avg_months": living_expense_avg_months,
            "income_avg": income_avg,
            "income_avg_months": income_avg_months,
            "pending": pending,
            "max_category": max_category,
            "month_name": today.strftime("%B %Y"),
        },
    )
