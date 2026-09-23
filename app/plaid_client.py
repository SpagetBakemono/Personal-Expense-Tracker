"""
Plaid client wrapper -- creates Link tokens, exchanges them for access
tokens, and syncs transactions. Mirrors app/import_parser.py's shape:
env-driven credentials, a fresh client per call, no DB access here --
callers decide what happens with the result (the same pending-review
queue statement imports already feed).
"""
import json
import os
from decimal import Decimal

import certifi
import plaid
from dotenv import load_dotenv
from plaid.api import plaid_api
from plaid.model.accounts_balance_get_request import AccountsBalanceGetRequest
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.country_code import CountryCode
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products
from plaid.model.transactions_sync_request import TransactionsSyncRequest

load_dotenv()

ENVIRONMENTS = {
    "sandbox": plaid.Environment.Sandbox,
    "production": plaid.Environment.Production,
}


def _client() -> plaid_api.PlaidApi:
    env = os.getenv("PLAID_ENV", "sandbox").lower()
    if env not in ENVIRONMENTS:
        raise RuntimeError(f"PLAID_ENV must be 'sandbox' or 'production', got {env!r}")

    # Separate secrets per environment, so flipping PLAID_ENV can never
    # pair a sandbox secret with production (or vice versa).
    client_id = os.getenv("PLAID_CLIENT_ID")
    secret = os.getenv(f"PLAID_SECRET_{env.upper()}")
    if not client_id or not secret:
        raise RuntimeError(
            f"PLAID_CLIENT_ID / PLAID_SECRET_{env.upper()} are not set -- check .env"
        )

    configuration = plaid.Configuration(
        host=ENVIRONMENTS[env],
        api_key={"clientId": client_id, "secret": secret},
    )
    # python.org's macOS Python ships without a trusted CA bundle, so TLS
    # verification fails against plaid.com. Point it at certifi's bundle --
    # never switch verification off, which would let anyone on the network
    # impersonate Plaid and harvest these credentials.
    configuration.ssl_ca_cert = certifi.where()
    return plaid_api.PlaidApi(plaid.ApiClient(configuration))


def describe_error(e: Exception) -> str:
    """One readable line for the UI. Plaid API errors carry a JSON body
    with error_code/error_message; anything else (network, config) is
    just its own message. Neither ever contains the client secret or an
    access token -- those only travel in the request."""
    body = getattr(e, "body", None)
    if body:
        try:
            data = json.loads(body)
            return f"{data.get('error_code', 'PLAID_ERROR')}: {data.get('error_message', '').strip()}"
        except (ValueError, TypeError):
            pass
    return str(e)


def create_link_token() -> str:
    """A short-lived token the frontend uses to open Plaid Link. One
    generic session covers whichever bank/account the user picks in the
    Link UI -- mapping the result to a specific local Account happens
    after, in the exchange step."""
    client = _client()
    request = LinkTokenCreateRequest(
        client_name="Personal Expense Tracker",
        language="en",
        country_codes=[CountryCode("US")],
        user=LinkTokenCreateRequestUser(client_user_id="local-user"),
        products=[Products("transactions")],
    )
    return client.link_token_create(request).link_token


def exchange_public_token(public_token: str) -> str:
    """Public tokens are single-use and expire in ~30 minutes; the
    access_token this returns is the long-lived credential that actually
    gets stored (on Account.plaid_access_token)."""
    client = _client()
    request = ItemPublicTokenExchangeRequest(public_token=public_token)
    return client.item_public_token_exchange(request).access_token


def get_accounts(access_token: str) -> list[dict]:
    """The account(s) available under one linked Item, so a caller can
    show a picker if Link surfaced more than one (e.g. checking +
    savings from the same bank in one session)."""
    client = _client()
    request = AccountsGetRequest(access_token=access_token)
    response = client.accounts_get(request)
    return [
        {
            "plaid_account_id": a.account_id,
            "name": a.name,
            "official_name": a.official_name,
            "type": str(a.type),
            "subtype": str(a.subtype) if a.subtype else None,
            "mask": a.mask,
        }
        for a in response.accounts
    ]


def get_balance(access_token: str, plaid_account_id: str) -> Decimal | None:
    """Live current balance for one account under an Item -- used to
    cross-check against the app's own projected balance after a sync,
    the same way a statement's stated balance already is."""
    client = _client()
    request = AccountsBalanceGetRequest(access_token=access_token)
    response = client.accounts_balance_get(request)
    for a in response.accounts:
        if a.account_id == plaid_account_id and a.balances.current is not None:
            return Decimal(str(a.balances.current))
    return None


def sync_transactions(access_token: str, plaid_account_id: str, cursor: str | None) -> dict:
    """Wraps /transactions/sync for one account within an Item, filtered
    to just that account_id (an Item can cover several). Pending
    transactions are dropped here -- same "don't count what hasn't
    posted" rule already applied to statement Processing/Pending charges
    everywhere else in this app; once Plaid transitions one to posted, a
    later sync call returns it as a fresh `added` entry.

    Returns {transactions: [{date, amount, merchant, type}, ...],
    next_cursor, has_more} -- the transactions list is already shaped
    for create_pending_imports."""
    client = _client()
    transactions = []
    next_cursor = cursor
    has_more = True

    while has_more:
        request = TransactionsSyncRequest(access_token=access_token, cursor=next_cursor or "")
        response = client.transactions_sync(request)
        for t in response.added:
            if t.account_id != plaid_account_id or t.pending:
                continue
            # Plaid's sign convention: positive amount = money left the
            # account, regardless of account type -- unlike this app's
            # own credit-card-is-a-liability convention, which only
            # applies once a transaction is already inside our own
            # Transaction/PendingImport model.
            amount = Decimal(str(t.amount))
            transactions.append(
                {
                    "date": t.date.isoformat() if hasattr(t.date, "isoformat") else str(t.date),
                    "amount": str(abs(amount)),
                    "merchant": t.merchant_name or t.name or "(unknown)",
                    "type": "expense" if amount > 0 else "income",
                }
            )
        next_cursor = response.next_cursor
        has_more = response.has_more

    return {"transactions": transactions, "next_cursor": next_cursor}
