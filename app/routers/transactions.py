from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    Account,
    Category,
    ReimbursementStatus,
    Transaction,
    TransactionType,
)
from app.templating import templates

router = APIRouter()


@router.get("/transactions/new")
def new_transaction_form(request: Request, db: Session = Depends(get_db)):
    accounts = db.scalars(select(Account).order_by(Account.name)).all()
    categories = db.scalars(select(Category).order_by(Category.name)).all()
    return templates.TemplateResponse(
        request,
        "transaction_new.html",
        {
            "accounts": accounts,
            "categories": categories,
            "today": date.today().isoformat(),
        },
    )


@router.post("/transactions")
def create_transaction(
    date_: str = Form(..., alias="date"),
    amount: str = Form(...),
    type: str = Form(...),
    account_id: int = Form(...),
    to_account_id: str = Form(""),
    category_id: str = Form(""),
    note: str = Form(""),
    reimbursable: str = Form(""),
    db: Session = Depends(get_db),
):
    txn_type = TransactionType(type)

    txn = Transaction(
        date=date.fromisoformat(date_),
        amount=amount,
        type=txn_type,
        account_id=account_id,
        to_account_id=int(to_account_id) if (txn_type == TransactionType.TRANSFER and to_account_id) else None,
        category_id=int(category_id) if category_id else None,
        note=note.strip() or None,
        reimbursable=bool(reimbursable) and txn_type == TransactionType.EXPENSE,
    )
    if txn.reimbursable:
        txn.reimbursement_status = ReimbursementStatus.PENDING

    db.add(txn)
    db.commit()
    return RedirectResponse(url="/", status_code=303)


@router.get("/transactions/{transaction_id}/edit")
def edit_transaction_form(transaction_id: int, request: Request, db: Session = Depends(get_db)):
    txn = db.get(Transaction, transaction_id)
    if txn is None:
        return RedirectResponse(url="/", status_code=303)
    accounts = db.scalars(select(Account).order_by(Account.name)).all()
    categories = db.scalars(select(Category).order_by(Category.name)).all()
    return templates.TemplateResponse(
        request,
        "transaction_new.html",
        {
            "accounts": accounts,
            "categories": categories,
            "today": date.today().isoformat(),
            "txn": txn,
        },
    )


@router.post("/transactions/{transaction_id}/edit")
def update_transaction(
    transaction_id: int,
    date_: str = Form(..., alias="date"),
    amount: str = Form(...),
    type: str = Form(...),
    account_id: int = Form(...),
    to_account_id: str = Form(""),
    category_id: str = Form(""),
    note: str = Form(""),
    reimbursable: str = Form(""),
    db: Session = Depends(get_db),
):
    txn = db.get(Transaction, transaction_id)
    if txn is None:
        return RedirectResponse(url="/", status_code=303)

    txn_type = TransactionType(type)
    was_reimbursable = txn.reimbursable

    txn.date = date.fromisoformat(date_)
    txn.amount = amount
    txn.type = txn_type
    txn.account_id = account_id
    txn.to_account_id = (
        int(to_account_id) if (txn_type == TransactionType.TRANSFER and to_account_id) else None
    )
    txn.category_id = int(category_id) if category_id else None
    txn.note = note.strip() or None
    txn.reimbursable = bool(reimbursable) and txn_type == TransactionType.EXPENSE

    # Only (re)open a reimbursement when it's newly marked reimbursable --
    # editing an already-pending or already-received one shouldn't reset
    # its status back to pending.
    if txn.reimbursable and not was_reimbursable:
        txn.reimbursement_status = ReimbursementStatus.PENDING
    elif not txn.reimbursable:
        txn.reimbursement_status = None

    db.commit()
    return RedirectResponse(url="/", status_code=303)


@router.post("/transactions/{transaction_id}/delete")
def delete_transaction(transaction_id: int, db: Session = Depends(get_db)):
    txn = db.get(Transaction, transaction_id)
    if txn:
        db.delete(txn)
        db.commit()
    return RedirectResponse(url="/", status_code=303)


@router.post("/transactions/{transaction_id}/mark-reimbursed")
def mark_reimbursed(transaction_id: int, db: Session = Depends(get_db)):
    """Flip a pending reimbursement to received. Logging the actual incoming
    cash as its own income transaction is a separate, deliberate step (see
    /transactions/new) -- this just closes out the receivable."""
    txn = db.get(Transaction, transaction_id)
    if txn:
        txn.reimbursement_status = ReimbursementStatus.RECEIVED
        db.commit()
    return RedirectResponse(url="/", status_code=303)
