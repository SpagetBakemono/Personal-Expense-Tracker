"""
Statement import parsing -- turns raw statement text (pasted by hand, or
grabbed from a bank page by the browser extension) into structured
transaction candidates using Gemini's free API tier.

Nothing here touches the database. This is intentionally just text in,
structured candidates out -- callers decide what happens with the result
(e.g. a pending-review queue the user confirms before anything is saved).
"""
import json
import os
from datetime import date

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

MODEL = "gemini-3.6-flash"

EXTRACTION_PROMPT = """\
Today's date is {today}. Extract every individual transaction line from the
bank/credit-card statement text below. Ignore headers, running balances,
page navigation text, and any summary/total lines that aren't a single
transaction.

For each transaction return:
- date: ISO format YYYY-MM-DD. Most statement lines won't show a year --
  when it's missing, assume the most recent occurrence of that month/day
  on or before today's date above (never a future date, never an
  arbitrary/unrelated year). Use null only if the day itself is genuinely
  unclear (not just the year).
- amount: a positive number, no currency symbol or commas
- merchant: a short, clean description of who was paid or who paid you
- type: "expense" for money going out (purchases, payments, fees), "income" for money coming in (deposits, refunds, credits)

Return ONLY a JSON array of objects with those four fields -- no other text,
no markdown code fences.

Statement text:
---
{text}
---
"""


def parse_statement_text(text: str) -> list[dict]:
    """Sends raw statement text to Gemini and returns a list of
    {date, amount, merchant, type} dicts. Raises on a missing API key or a
    response that isn't a JSON array -- callers should expect this can fail
    (bad key, rate limit, Gemini returning something unparseable) and not
    treat it as silently safe."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set -- check .env")

    # Held in a local variable rather than chained -- letting the Client
    # get garbage-collected mid-request raised "Cannot send a request, as
    # the client has been closed".
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=MODEL,
        contents=EXTRACTION_PROMPT.format(text=text, today=date.today().isoformat()),
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )

    data = json.loads(response.text)
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON array from Gemini, got: {type(data).__name__}")
    return data
