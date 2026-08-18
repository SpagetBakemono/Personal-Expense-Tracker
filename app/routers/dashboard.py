from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import (
    get_all_balances,
    get_month_summary,
    get_pending_reimbursements,
    get_trailing_average_expense,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    today = date.today()
    summary = get_month_summary(db, today.year, today.month)
    balances = get_all_balances(db)
    trailing_avg = get_trailing_average_expense(db)
    pending = get_pending_reimbursements(db)

    max_category = max(summary["by_category"].values()) if summary["by_category"] else 1

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "summary": summary,
            "balances": balances,
            "trailing_avg": trailing_avg,
            "pending": pending,
            "max_category": max_category,
            "month_name": today.strftime("%B %Y"),
        },
    )
