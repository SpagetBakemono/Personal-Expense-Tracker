# Working conventions for this repo

Notes for whichever Claude Code session touches this project next, so
decisions made once don't need re-deriving. If something here goes stale,
fix it in the same change that makes it stale.

## Git workflow

- Commit directly to `main`. No feature branches, no PRs -- explicit user
  preference, established after going back and forth on it. Don't
  reintroduce a branch/PR flow without asking first.
- Split commits by logical feature/concern where the diff allows it
  cleanly. When two features touch the same function in an interleaved
  way, it's fine to bundle them into one commit rather than force an
  artificial split -- but verify with `git diff --cached` (not just
  answer-counting through `git add -p`) before trusting a split actually
  landed the right hunks in the right commit.
- Smoke-test before every push: start the server, hit the main routes
  (`/`, `/trends`, `/accounts`, `/transactions/new`), confirm 200s, check
  actual rendered content where a change could plausibly break rendering.
  This has caught real bugs (a 422 on an empty query param, a template
  reading the wrong field) before they reached `main`.
- Never commit `expense_tracker.db` or `.env` -- both gitignored. If a
  schema change needs testing against real data, back up the db file
  first (`cp expense_tracker.db expense_tracker.db.bak`, delete the
  backup once verified).

## Database

- No migrations (no Alembic) -- `Base.metadata.create_all()` only creates
  *missing tables*, it does not alter existing ones. Adding or removing a
  column on `Transaction` or `Account` needs a manual
  `ALTER TABLE ... ADD/DROP COLUMN` against the real `expense_tracker.db`
  in addition to the model change, or the app breaks on next insert.
- Real user data lives only in the gitignored `expense_tracker.db`. When
  testing against it, prefer read-only inspection; if you need to insert
  test rows, tag them identifiably (e.g. a note starting with `TEST `) and
  delete them again before finishing.

## App structure

- All routers share one `Jinja2Templates` instance from `app/templating.py`
  (registers a `static_version()` global for cache-busted static assets).
  Don't create a per-router `Jinja2Templates(...)` -- that was the
  original scaffold's pattern and was deliberately consolidated.
- Query params that can arrive as an empty string (e.g. a `<select>`'s
  "All ..." option posting `?account_id=`) must be typed `str | None` in
  the route signature and parsed manually (`int(x) if x else None`) --
  FastAPI 422s on `""` for a declared `int` param. Bit us twice already.

## Money/domain logic

- Credit card balances represent what you *owe* -- a liability, not an
  asset. Income/credits applied directly to a card reduce what's owed
  (subtract), not add to it; this was a real bug once (see git log).
- Categories are a fixed, seeded list (`DEFAULT_EXPENSE_CATEGORIES` /
  `DEFAULT_INCOME_CATEGORIES` in `services.py`) -- no add-category UI yet.
  `seed_default_categories()` only inserts names that don't already exist,
  so adding to the list is safe to apply to an existing database.
- The first 7 categories of each kind (by id) get a dedicated color in
  the Trends stacked charts (expense and income alike); the rest fold into
  a shared "Other" bucket (see `get_category_color_series` in
  `services.py`). The colors (`CATEGORY_COLOR_SLOTS`) come from the app's
  palette family and passed the dataviz validator only for *adjacent*
  slots -- so charts stack series in that fixed order, never sorted by
  amount.

## UI / charts

- Palette: "Dark Green Tropical" -- navy `#13243B`, dark green `#153D35`,
  green `#1D8B65`, teal `#2C9D90`, off-white `#F3F3F1` (tokens at the top
  of `style.css`). User-chosen; don't swap it out.
- Trends charts are drawn client-side by `app/static/charts.js` (plain
  SVG, no chart library -- keep third-party JS out of an app holding bank
  tokens). The route passes plain data via a `|tojson` script blob.
  Every chart needs a real Y-axis; the balance chart starts at $0 and the
  mouse wheel zooms its floor.
- "Living" comes first (and is the default) wherever Living/Total toggles
  appear. Toggles are full-width `.segmented` rows (CSS radio + `~`, no
  JS; per-id rules in `style.css`).

## Importing transactions

Plaid sync (`app/plaid_sync.py`, runs in a background thread at launch
and every `SYNC_INTERVAL_HOURS` after, plus a per-account "Sync now") posts straight to the ledger -- no
review queue (the user found the queue too much work once real data
flowed). What keeps that safe:
- Every row a sync touches carries a unique `plaid_transaction_id` (or
  `plaid_pair_transaction_id` for the other side of a transfer), so a
  re-sync is idempotent.
- A new Plaid transaction first tries to *adopt* an unlinked hand-entered
  row with the same amount dated 5 days before to 1 day after it (banks
  post late, never early). Transactions are processed posted-before-pending,
  oldest first, and each claims the *oldest* candidate -- MTA posts several
  days of $3 fares in one batch, and nearest-date matching double-posted
  some. Adopted rows keep their own date/note/category.
- On an account's first sync (cursor None), anything dated before the
  ledger's latest row is adopt-only -- that period was already
  reconciled by hand.
- Pending transactions post with `pending=True` and are updated in place
  when they post. After each sync, Plaid's *posted* balance is compared
  against the ledger minus pending rows.
- The user wants the app to run itself, not to babysit syncing: only
  problems the user must fix (a bank asking to sign in again --
  `NEEDS_USER_ERRORS`) get the red card on Dashboard/Accounts. Other sync
  failures just retry next cycle. A balance gap shows only after it has
  persisted `DRIFT_GRACE_DAYS`, as a quiet line on Accounts. Both pages
  show "Bank data updated X ago" instead.
  Exception: a gap that some subset of pending rows exactly explains isn't
  flagged -- the bank's balance often counts a charge as posted before
  Plaid's feed stops calling it pending, and a later sync resolves it.
- Card payments seen from both checking and the card merge into one
  TRANSFER.

Manual statement paste (`/import`, Gemini-parsed) still exists for
accounts Plaid can't reach (Cash), and still goes through the
`/import/review` queue. Anything before an account's
`opening_balance_date` is ignored by both. (A Chrome capture extension
existed before Plaid; it was removed.)

## Security

This app holds real financial data and Plaid bank credentials. Binding to
127.0.0.1 keeps other *machines* out, but not other *websites* open in the
same browser -- the layers in `app/main.py` exist for that. Don't weaken
them without asking:

- `TrustedHostMiddleware` (127.0.0.1/localhost only) blocks DNS rebinding.
- `reject_cross_site_writes` rejects any POST/PUT/PATCH/DELETE whose
  `Origin` isn't this app -- CSRF protection.
- There is deliberately no CORS middleware, so no other site can read
  responses. It was once `allow_origins=["*"]` (for a since-removed browser
  extension), which let any website read data off the local server.
- Always bind uvicorn to `127.0.0.1`, never `0.0.0.0`.
- Plaid access tokens are Fernet-encrypted before hitting the db
  (`app/token_crypto.py`, key `PLAID_TOKEN_KEY` in `.env`). Never return a
  token (or its ciphertext) to the browser.
- Never disable TLS verification to fix a certificate error -- point the
  client at `certifi.where()` instead (see `app/plaid_client.py`).
- Any value interpolated into inline JS (e.g. an `onsubmit="confirm(...)"`)
  goes through `|tojson` inside a single-quoted attribute -- HTML
  autoescaping alone doesn't protect a JS context.
- `.env` and `expense_tracker.db` are `chmod 600`.

## Misc

- On macOS there's a separate double-clickable launcher app installed in
  `~/Applications` (outside this repo) that starts the server and opens
  Chrome -- see `launch.command` for the script it's built from.
- `anthropic` was in `requirements.txt` from the original scaffold but
  unused; the statement-import feature uses `google-genai` (Gemini) instead,
  since Gemini has a genuine ongoing free tier and Anthropic's API is
  billed separately from a Claude Pro/Max subscription.
