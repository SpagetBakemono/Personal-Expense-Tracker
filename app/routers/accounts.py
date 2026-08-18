from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Account, AccountType
from app.services import get_all_balances

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/accounts")
def list_accounts(request: Request, db: Session = Depends(get_db)):
    balances = get_all_balances(db)
    return templates.TemplateResponse(
        request, "accounts.html", {"balances": balances}
    )


@router.get("/accounts/new")
def new_account_form(request: Request):
    return templates.TemplateResponse(
        request,
        "account_new.html",
        {"account_types": list(AccountType), "today": date.today().isoformat()},
    )


@router.post("/accounts")
def create_account(
    name: str = Form(...),
    type: str = Form(...),
    opening_balance: str = Form("0"),
    opening_balance_date: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        balance = Decimal(opening_balance or "0")
    except InvalidOperation:
        balance = Decimal(0)

    account = Account(
        name=name.strip(),
        type=AccountType(type),
        opening_balance=balance,
        opening_balance_date=date.fromisoformat(opening_balance_date),
    )
    db.add(account)
    db.commit()
    return RedirectResponse(url="/accounts", status_code=303)
