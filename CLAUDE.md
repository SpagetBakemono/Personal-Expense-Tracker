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
- The first 7 expense categories (by id) get a dedicated color in the
  Trends "all categories" view; the rest fold into a shared "Other" bucket
  (see `get_category_color_series` in `services.py`).

## Misc

- On macOS there's a separate double-clickable launcher app installed in
  `~/Applications` (outside this repo) that starts the server and opens
  Chrome -- see `launch.command` for the script it's built from.
- `anthropic` was in `requirements.txt` from the original scaffold but
  unused; the statement-import feature uses `google-genai` (Gemini) instead,
  since Gemini has a genuine ongoing free tier and Anthropic's API is
  billed separately from a Claude Pro/Max subscription.
