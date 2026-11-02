"""Parsing for structured bank statement rows — table-aware with a regex fallback."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, List, Optional, Sequence

import pandas as pd

try:
    from dateutil import parser as _dateutil_parser
except ImportError:  # pragma: no cover - dateutil ships with pandas, but guard anyway
    _dateutil_parser = None


TRANSACTION_COLUMNS = ["date", "description", "debit_amount", "credit_amount", "balance"]

# Explicit strptime formats tried first (fast path, no ambiguity).
_DATE_FORMATS = [
    "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y",
    "%m/%d/%Y", "%m-%d-%Y",
    "%Y/%m/%d", "%Y-%m-%d",
    "%d/%m/%Y", "%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%d %B %Y",
    "%b %d, %Y", "%B %d, %Y", "%d.%m.%Y", "%d.%m.%y",
]

# Month-name alternation reused by both the "with year" and "no year" date patterns below.
_MONTH_NAMES = (
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
)

# Matches numeric dates (with separators), "01-Jan-2024" / "01 Jan 2024" style dates WITH
# a year, and -- as a last resort -- month-name dates with NO year at all (e.g. "Jul 03").
# The no-year alternatives are deliberately last: since re tries alternatives in order at
# each position, a full "Jul 03, 2024" is matched by the with-year pattern first and the
# bare pattern never gets a chance to truncate it early. Bare-date matches carry no year
# of their own -- see _normalize_date's default_year handling.
DATE_TOKEN_PATTERN = (
    r"\b\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}\b"
    r"|\b\d{4}[/\-]\d{1,2}[/\-]\d{1,2}\b"
    r"|\b\d{1,2}[\-\s](?:" + _MONTH_NAMES + r")[\-\s]\d{2,4}\b"
    r"|\b(?:" + _MONTH_NAMES + r")\s\d{1,2},?\s\d{4}\b"
    r"|\b(?:" + _MONTH_NAMES + r")\.?\s\d{1,2}\b(?!\s*,?\s*\d)"
    r"|\b\d{1,2}\s(?:" + _MONTH_NAMES + r")\b(?!\s*\d)"
)

# Only decimal-formatted currency amounts (e.g. 1,234.56 / 349.00) are treated as amounts.
# Bare integers are deliberately excluded so reference numbers, account numbers and UPI IDs
# embedded in the description (very common in Indian bank statements, e.g. "UPI-SWIGGY-123456")
# are never mistaken for a monetary value. A tightly-bound leading sign/currency symbol
# (e.g. "-$84.26") is captured too, since it's needed to tell debit from credit when a
# line has only a single amount and no separate running-balance column.
AMOUNT_TOKEN_PATTERN = r"-?\$?\d[\d,]*\.\d{2}"

# Fallback for statements that print whole-rupee amounts with thousands-grouping but no
# decimal places at all (e.g. "12,345" instead of "12,345.00"). Comma-grouping -- not
# just any digits -- is what makes this safe: a bare reference number or account
# fragment is essentially never comma-formatted, so this doesn't reopen the
# reference-number-contamination problem the decimal-only rule above was written to avoid.
# Only used when the strict decimal pattern finds fewer than 2 matches on a line.
COMMA_GROUPED_AMOUNT_PATTERN = r"-?\$?\d{1,3}(?:,\d{3})+(?:\.\d{2})?"

# A bare integer (no comma-grouping, no decimal point) is too easily confused with a
# reference number ANYWHERE else on a line -- except as the very last token, which is
# unambiguously the balance position (nothing legitimate trails a transaction line after
# it). Only consulted when exactly one "real" amount was found, to recover a second
# (balance) figure a bank prints with no formatting at all.
_TRAILING_BARE_INTEGER_PATTERN = re.compile(r"(?<!\S)(-?\d+)\s*$")

# The mirror case: a bare integer immediately BEFORE the line's one confirmed amount
# (e.g. "18000 Dr 111,151.00", where "18000" is the actual transaction amount and
# "111,151.00" is the balance). This is only safe to treat as an amount because of the
# mandatory Dr/Cr marker between it and the confirmed match -- without that marker, a
# bare number right before a real amount is indistinguishable from a reference number
# sitting next to the amount column (e.g. "UPI-SWIGGY-123456 349.00", where "123456" must
# NOT be treated as an amount). The marker is what makes the difference safe to trust.
_LEADING_BARE_INTEGER_PATTERN = re.compile(r"(?<!\S)(-?\d+)\s*(?:dr|cr)\s*$", re.IGNORECASE)

# An explicit Dr/Cr marker immediately after an amount (e.g. "5,000.00 Dr") is common in
# single-amount-column statement layouts and is authoritative when present -- checked
# before falling back to keyword guessing.
_DIRECTION_SUFFIX_PATTERN = re.compile(r"^\s*(dr|cr)\b", re.IGNORECASE)

_HEADER_PREFIXES = ("date", "transaction", "description", "narration", "particulars", "debit", "credit", "balance", "withdrawal", "deposit")
_FOOTER_PREFIXES = (
    "page", "statement generated", "note:", "disclaimer", "continued",
    "opening balance", "closing balance", "total", "generated on", "***",
    "statement of account", "customer id", "cif no", "micr code", "nomination",
    "registered office", "toll free", "for any queries", "computer generated",
    "contact us", "branch", "ifsc", "email",
)
# Balance-summary phrases that can appear AFTER a leading date on some statement layouts
# (e.g. "01/04/2024  Opening Balance  50,000.00  50,000.00") -- the _FOOTER_PREFIXES
# line-start check above would miss these since the line starts with the date, not the
# phrase, so this is checked against the parsed-out description instead.
_MID_LINE_BALANCE_TERMS = ("opening balance", "closing balance", "brought forward", "carried forward", "b/f", "c/f")

DEBIT_KEYWORDS = ["withdraw", "atm", "cash", "wdl", "debit", "payment", "transfer", "rent", "bill", "order", "purchase", "recharge"]
CREDIT_KEYWORDS = ["credit", "salary", "deposit", "interest", "refund", "received"]


_ACCOUNT_LABEL_PATTERN = r"(?:account(?:\s*no\.?|\s*number)|a\s*/\s*c(?:\s*(?:no\.?|number))?|acct(?:\s*no\.?|\s*number)?)"

# Masking characters vary by source: X/x, asterisk, a real bullet (•, U+2022 -- verified
# this is what a genuine masked-account PDF uses, not a decode failure -- it just renders
# as "�" in a terminal/console whose encoding can't display U+2022), and the actual
# Unicode replacement character (U+FFFD) for the rarer case of a true font-decode failure.
_MASK_CHAR_CLASS = "[Xx*•●�]"

# Some statement layouts render account fields as a labeled CARD/COLUMN grid rather than
# "Label: Value" lines -- pdfplumber (and OCR) then extracts this as one line of column
# HEADERS ("ACCOUNT HOLDER NAME ACCOUNT NUMBER IFSC CODE") followed by one line of VALUES
# ("Nisha Rao 0000 0000 3058 DEMOOIMAGE1") with no separator punctuation anywhere at all --
# verified against real test files formatted exactly this way, both as native PDF text and
# as OCR'd text from an image. The three "Label: Value" fallbacks above never match this
# because there's no label/value adjacency or separator on either line.
_COLUMN_HEADER_LINE_PATTERN = re.compile(r"account\s*holder\s*name.*account\s*number.*ifsc(?:\s*code)?", re.IGNORECASE)
# 3-6 letters + (0 or O -- OCR commonly misreads the digit 0 as the letter O) + 3-8 alphanumeric.
_COLUMN_IFSC_PATTERN = re.compile(r"\b([A-Z]{3,6})[0O]([A-Z0-9]{3,8})\b")
# A long digit run that may contain internal spaces (e.g. "0000 0000 3058").
_COLUMN_ACCOUNT_NUMBER_PATTERN = re.compile(r"\b(\d[\d\s]{6,20}\d)\b")
_COLUMN_MASKED_ACCOUNT_PATTERN = re.compile(rf"\b((?:{_MASK_CHAR_CLASS}[ \t]*){{2,}}\d{{2,6}})\b")
_IFSC_VALUE_PATTERN = r"[A-Z]{4}\s*[0O]\s*[A-Z0-9]{6}"
_METADATA_VALUE_END_PATTERN = rf"(?=\r?\n|$|\s+(?:{_ACCOUNT_LABEL_PATTERN}|i\s*f\s*s\s*c(?:\s*(?:code|no\.?|number))?))"


def _extract_column_layout_details(raw_text: str) -> Dict[str, Optional[str]]:
    """Fallback for the column/card-style account-field layout described above."""
    result: Dict[str, Optional[str]] = {"account_holder_name": None, "account_number": None, "ifsc_code": None}
    lines = raw_text.splitlines()
    for i, line in enumerate(lines):
        if not _COLUMN_HEADER_LINE_PATTERN.search(line):
            continue
        # Scan a few lines ahead for the actual values row, rather than blindly taking
        # the very next non-blank line -- some PDFs interleave stray single-character
        # watermark noise lines ("N", "T", "E", ...) between the header and the real
        # values row, and a naive "next non-blank line" grabs the noise instead
        # (verified: this happened on a real test file). Requiring the candidate to
        # actually contain an account-number-shaped digit run screens that out.
        value_line = next(
            (
                candidate.strip()
                for candidate in lines[i + 1 : i + 6]
                if len(candidate.strip()) >= 5
                and (
                    _COLUMN_ACCOUNT_NUMBER_PATTERN.search(candidate)
                    or _COLUMN_MASKED_ACCOUNT_PATTERN.search(candidate)
                    or _COLUMN_IFSC_PATTERN.search(candidate)
                )
            ),
            None,
        )
        if value_line is None:
            break

        ifsc_match = _COLUMN_IFSC_PATTERN.search(value_line)
        search_bound = ifsc_match.start() if ifsc_match else len(value_line)
        if ifsc_match:
            result["ifsc_code"] = f"{ifsc_match.group(1)}0{ifsc_match.group(2)}"

        acct_match = _COLUMN_ACCOUNT_NUMBER_PATTERN.search(value_line[:search_bound])
        if acct_match:
            result["account_number"] = re.sub(r"\s+", "", acct_match.group(1))
            name_part = value_line[: acct_match.start()].strip()
        else:
            masked_account_match = _COLUMN_MASKED_ACCOUNT_PATTERN.search(value_line[:search_bound])
            if masked_account_match:
                suffix = re.search(r"\d{2,6}$", masked_account_match.group(1))
                if suffix:
                    result["account_number"] = f"****{suffix.group(0)}"
                name_part = value_line[: masked_account_match.start()].strip()
            else:
                name_part = value_line[:search_bound].strip()
        if name_part:
            result["account_holder_name"] = name_part
        break
    return result


def extract_account_details(raw_text: str) -> Dict[str, Optional[str]]:
    """Attempt to extract account-holder metadata from the statement text.

    account_number may come back as a MASKED value (e.g. "****1842") when the source
    document only ever shows a partially-hidden number (common on U.S.-style statements
    for privacy) -- that's the best information actually available in that case, so it's
    reported as such rather than left blank.
    """
    details: Dict[str, Optional[str]] = {
        "account_holder_name": None,
        "account_number": None,
        "ifsc_code": None,
    }

    # Ordered most-specific-label-first so e.g. "Account Holder Name:" is consumed whole
    # rather than the bare "name" pattern matching midway through the label itself.
    # The separator is mandatory and the capture is bounded to end-of-line, otherwise the
    # `\s` inside the character class would happily swallow following lines too.
    name_patterns = [
        rf"account\s*holder(?:'s)?\s*name\s*[:\-]?\s*([A-Z][A-Za-z.\s]+?){_METADATA_VALUE_END_PATTERN}",
        rf"customer\s*name\s*[:\-]?\s*([A-Z][A-Za-z.\s]+?){_METADATA_VALUE_END_PATTERN}",
        rf"account\s*holder\s*[:\-]?\s*([A-Z][A-Za-z.\s]+?){_METADATA_VALUE_END_PATTERN}",
        rf"\bname\s*[:\-]?\s*([A-Z][A-Za-z.\s]+?){_METADATA_VALUE_END_PATTERN}",
        rf"\b(?:mr|mrs|ms)\.?\s+([A-Z][A-Za-z.\s]+?){_METADATA_VALUE_END_PATTERN}",
    ]
    for pattern in name_patterns:
        match = re.search(pattern, raw_text, re.IGNORECASE)
        if match:
            candidate_name = re.sub(r"\s+", " ", match.group(1)).strip()
            if re.search(r"\b(?:masked|invalid\s+test)\b", candidate_name, re.IGNORECASE) or re.fullmatch(
                r"(?:name|account\s*holder(?:'s)?(?:\s*name)?|account\s*(?:no\.?|number)|"
                r"a\s*/\s*c(?:\s*(?:no\.?|number))?|acct\s*(?:no\.?|number)|"
                r"ifsc(?:\s*(?:code|no\.?|number))?|masked)",
                candidate_name,
                re.IGNORECASE,
            ):
                continue
            details["account_holder_name"] = candidate_name
            break

    if details["account_holder_name"] is None:
        # Label-less name: some statements just print "First Last" immediately before a
        # recognizable statement-specific phrase, with no "Name:" label anywhere at all
        # (verified against real fictional test statements formatted exactly this way).
        # Anchoring on that adjacent phrase (rather than bare capitalized-words, which
        # would false-positive on a bank's own name) keeps this a low-risk fallback.
        unlabeled_match = re.search(r"\b([A-Z][a-z]+(?:\s[A-Z][a-z]+){1,2})\s+Statement\s+period\b", raw_text)
        if unlabeled_match:
            details["account_holder_name"] = unlabeled_match.group(1).strip()

    account_match = re.search(
        rf"{_ACCOUNT_LABEL_PATTERN}\s*[:\-]?\s*(\d(?:[\d \t.-]{{7,24}}\d))",
        raw_text,
        re.IGNORECASE,
    )
    if account_match:
        details["account_number"] = re.sub(r"\D", "", account_match.group(1))
    else:
        # Fall back to a masked number: either explicitly labeled ("Account Number:
        # XXXX1234" / "**** 2087")...
        masked_match = re.search(
            rf"{_ACCOUNT_LABEL_PATTERN}\s*[:\-]?\s*(?:{_MASK_CHAR_CLASS}[ \t]*){{2,}}(\d{{2,6}})\b",
            raw_text, re.IGNORECASE,
        )
        if masked_match is None:
            # ...or unlabeled but sitting right after an account-type word (e.g.
            # "Everyday Checking •••• 1842" -- no "Account Number" label at all).
            masked_match = re.search(
                rf"\b(?:checking|savings|current|cash)(?:\s+account)?\s+(?:{_MASK_CHAR_CLASS}[ \t]*){{2,}}(\d{{2,6}})\b",
                raw_text, re.IGNORECASE,
            )
        if masked_match:
            details["account_number"] = f"****{masked_match.group(1)}"

    ifsc_match = re.search(
        rf"\bI\s*F\s*S\s*C(?:\s*(?:code|no\.?|number))?\s*[:\-]?\s*({_IFSC_VALUE_PATTERN})\b",
        raw_text,
        re.IGNORECASE,
    )
    if ifsc_match is None:
        # Some banks print the code without a label, while OCR may insert spaces
        # between characters. A valid IFSC-shaped token is specific enough to use
        # as a document-wide fallback without confusing ordinary statement text.
        ifsc_match = re.search(rf"\b({_IFSC_VALUE_PATTERN})\b", raw_text, re.IGNORECASE)
    if ifsc_match:
        normalized_ifsc = re.sub(r"\s+", "", ifsc_match.group(1)).upper()
        details["ifsc_code"] = normalized_ifsc[:4] + "0" + normalized_ifsc[5:]

    if not all(details.values()):
        column_details = _extract_column_layout_details(raw_text)
        for key, value in column_details.items():
            if details[key] is None and value:
                details[key] = value

    return details


def _infer_default_year(raw_text: str) -> int:
    """Best-effort: find a plausible statement year anywhere in the document, for dates
    that omit the year entirely (e.g. "Jul 03"). Many statements print the year only
    once, in a "Statement period Jul 1-31, 2026" or bare "March 1-31, 2026" style
    header, and expect every transaction date to be understood relative to it.

    Tries, in order of specificity:
      1. A "Month D-D, YYYY" date-range (what these headers actually look like) --
         checked FIRST and most specifically because a naive "any 4-digit number
         starting with 20" scan is dangerously easy to fool: a masked account suffix
         like "Checking **** 2087" starts with "20" and reads as a plausible year, and
         if it appears earlier in the text than the real year, a leftmost-match scan
         picks the wrong one (verified: this happened on a real test file).
      2. A year near explicit "statement period/date" wording.
      3. Any plausible (20xx) year anywhere.
      4. The current year, as an absolute last resort -- verified against a real test
         file with a bare "DD Mon" date and NO year anywhere in the document at all
         (not even the masked-account-number false-positive tier 3 guards against).
         Silently dropping every transaction because a statement never happens to state
         its year in writing is worse than assuming "recent" -- the overwhelmingly most
         likely case for a document being processed today.
    """
    period_range_match = re.search(
        r"\b(?:" + _MONTH_NAMES + r")\.?\s+\d{1,2}\s*[-–]\s*\d{1,2},?\s*(20\d{2})\b",
        raw_text, re.IGNORECASE,
    )
    if period_range_match:
        return int(period_range_match.group(1))

    period_match = re.search(r"statement\s*(?:period|date)[^\n]{0,60}?\b(20\d{2})\b", raw_text, re.IGNORECASE)
    if period_match:
        return int(period_match.group(1))

    any_year_match = re.search(r"\b(20\d{2})\b", raw_text)
    if any_year_match:
        return int(any_year_match.group(1))

    return datetime.now().year
    return None


def _normalize_date(value: str, default_year: Optional[int] = None) -> Optional[str]:
    """Normalize a date-like token into ISO format when possible.

    `default_year` is used only when `value` has no year of its own (e.g. "Jul 03") --
    see _infer_default_year. This check matters because dateutil's bare parser silently
    substitutes TODAY's year for a token with none, rather than raising, so a year-less
    value must never reach it without an explicit default; otherwise a July statement
    parsed in December would get every date silently mis-dated.
    """
    value = value.strip().replace(",", "")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    has_explicit_year = bool(re.search(r"\d{4}", value))

    if has_explicit_year and _dateutil_parser is not None:
        try:
            parsed = _dateutil_parser.parse(value, dayfirst=True, fuzzy=False)
            return parsed.strftime("%Y-%m-%d")
        except (ValueError, OverflowError):
            pass

    if not has_explicit_year and default_year is not None and _dateutil_parser is not None:
        try:
            parsed = _dateutil_parser.parse(f"{value} {default_year}", dayfirst=True, fuzzy=False)
            return parsed.strftime("%Y-%m-%d")
        except (ValueError, OverflowError):
            return None

    return None


def _clean_amount(value: str) -> float:
    """Convert a textual amount to a float."""
    cleaned = value.strip()
    for junk in (",", "₹", "INR", "Rs.", "Rs", "$", " "):
        cleaned = cleaned.replace(junk, "")
    if cleaned in {"", "-", "--"}:
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _looks_like_footer_or_header(line_lower: str) -> bool:
    return line_lower.startswith(_HEADER_PREFIXES) or line_lower.startswith(_FOOTER_PREFIXES)


def _looks_like_balance_summary(description: str) -> bool:
    lowered = description.lower()
    return any(term in lowered for term in _MID_LINE_BALANCE_TERMS)


def _direction_from_suffix(remainder: str, after_index: int) -> Optional[str]:
    """Look for an explicit Dr/Cr marker right after an amount's match end position."""
    suffix_match = _DIRECTION_SUFFIX_PATTERN.match(remainder[after_index:])
    return suffix_match.group(1).lower() if suffix_match else None


def _trailing_bare_integer(remainder: str, after_index: int) -> Optional[str]:
    """A bare integer (no comma-grouping, no decimal point) is too easily confused with
    a reference number anywhere else on a line -- except as the very last token, which
    is unambiguously the balance position. Returns that trailing integer's text if the
    tail after the last confirmed amount match consists of exactly one such token.
    """
    tail = remainder[after_index:]
    match = _TRAILING_BARE_INTEGER_PATTERN.search(tail)
    return match.group(1) if match else None


def _leading_bare_integer(remainder: str, before_index: int) -> Optional["tuple[str, int, int]"]:
    """A bare integer immediately followed by an explicit Dr/Cr marker, itself right
    before the line's one confirmed amount match, is very likely this bank's
    transaction amount printed with no formatting (see the mandatory-marker rationale
    on _LEADING_BARE_INTEGER_PATTERN above). Returns (token, start_index, end_index)
    of just the integer (not the marker) within `remainder`, or None.
    """
    head = remainder[:before_index]
    match = _LEADING_BARE_INTEGER_PATTERN.search(head)
    if not match:
        return None
    return match.group(1), match.start(1), match.end(1)


def _assign_debit_credit(amount_items: List["tuple[str, int]"], remainder: str, description_hint: str) -> Optional[Dict[str, object]]:
    """Given the (token, match_end_index) amount items trailing a description, resolve
    debit/credit/balance.

    Bank statement lines almost always trail as one of:
      - <description> <debit> <credit> <balance>        (3 numbers -> position is authoritative)
      - <description> <amount>[ Dr/Cr] <balance>         (2 numbers -> an explicit Dr/Cr
        marker right after the amount is authoritative when present; otherwise direction
        is inferred from keywords, defaulting to debit)
      - <description> <signed amount>                    (1 number, no balance column at
        all -- common in simple "date, description, amount" layouts. The amount's own
        sign is the direction: negative = debit/withdrawal, non-negative = credit/deposit,
        an explicit Dr/Cr marker overriding that when present. Balance is left as None
        rather than fabricated, since the source genuinely doesn't state one.)
    """
    if not amount_items:
        return None

    amount_tokens = [tok for tok, _ in amount_items]
    amounts = [_clean_amount(tok) for tok in amount_tokens]

    if len(amounts) == 1:
        raw_amount = amounts[0]
        transaction_amount = abs(raw_amount)
        direction = _direction_from_suffix(remainder, amount_items[0][1])
        if direction == "cr":
            is_credit = True
        elif direction == "dr":
            is_credit = False
        else:
            is_credit = raw_amount >= 0
        if is_credit:
            return {"debit_amount": 0.0, "credit_amount": round(transaction_amount, 2), "balance": None}
        return {"debit_amount": round(transaction_amount, 2), "credit_amount": 0.0, "balance": None}

    balance = abs(amounts[-1])

    if len(amounts) >= 3:
        # Take the last three: debit, credit, balance. Anything earlier is treated as noise
        # (e.g. a stray reference number that happened to be formatted like an amount).
        debit_raw, credit_raw, balance_raw = amounts[-3:]
        return {
            "debit_amount": round(abs(debit_raw), 2),
            "credit_amount": round(abs(credit_raw), 2),
            "balance": round(abs(balance_raw), 2),
        }

    # Exactly two numbers: single transaction amount + balance. Direction must be inferred.
    transaction_amount = abs(amounts[0])

    direction = _direction_from_suffix(remainder, amount_items[0][1])
    if direction == "cr":
        return {"debit_amount": 0.0, "credit_amount": round(transaction_amount, 2), "balance": round(balance, 2)}
    if direction == "dr":
        return {"debit_amount": round(transaction_amount, 2), "credit_amount": 0.0, "balance": round(balance, 2)}

    lower_hint = description_hint.lower()
    if any(keyword in lower_hint for keyword in CREDIT_KEYWORDS):
        return {"debit_amount": 0.0, "credit_amount": round(transaction_amount, 2), "balance": round(balance, 2)}
    if any(keyword in lower_hint for keyword in DEBIT_KEYWORDS):
        return {"debit_amount": round(transaction_amount, 2), "credit_amount": 0.0, "balance": round(balance, 2)}
    # Default assumption: most unlabeled statement lines are debits (spends outweigh credits).
    return {"debit_amount": round(transaction_amount, 2), "credit_amount": 0.0, "balance": round(balance, 2)}


def _parse_single_line(line: str, default_year: Optional[int] = None) -> Optional[Dict[str, object]]:
    """Parse one transaction line into a structured row when possible."""
    cleaned = re.sub(r"\s+", " ", line).strip()
    if not cleaned:
        return None
    if _looks_like_footer_or_header(cleaned.lower()):
        return None

    date_match = re.search(DATE_TOKEN_PATTERN, cleaned)
    if not date_match:
        return None

    iso_date = _normalize_date(date_match.group(0), default_year=default_year)
    if iso_date is None:
        return None

    remainder = cleaned[date_match.end():].strip()

    # Only decimal amounts count as monetary tokens; everything before the first one is description.
    amount_matches = list(re.finditer(AMOUNT_TOKEN_PATTERN, remainder))
    if len(amount_matches) < 2:
        # Fallback: some statements print whole-rupee amounts with comma-grouping but no
        # decimal places (e.g. "12,345" rather than "12,345.00"). Comma-grouping is what
        # makes this safe to treat as a real amount rather than a reference number.
        fallback_matches = list(re.finditer(COMMA_GROUPED_AMOUNT_PATTERN, remainder))
        if len(fallback_matches) >= 2:
            amount_matches = fallback_matches

    if not amount_matches:
        return None

    amount_items: List["tuple[str, int]"] = [(m.group(0), m.end()) for m in amount_matches]
    description_end = amount_matches[0].start()

    if len(amount_items) == 1:
        # Only one real (decimal or comma-grouped) amount found -- check both sides of
        # it for a bare (unformatted) integer that's the other transaction/balance figure:
        #   - trailing: "...349.00 51450" -- confirmed amount first, bare balance after.
        #   - leading:  "...18000 Dr 111,151.00" -- bare amount (with a Dr/Cr marker)
        #     before a confirmed balance. The marker is mandatory for the leading case
        #     specifically because without it, a bare number right before a real amount
        #     is indistinguishable from a reference number sitting next to the amount
        #     column (e.g. "UPI-SWIGGY-123456 349.00" must NOT treat "123456" as an amount).
        trailing = _trailing_bare_integer(remainder, amount_matches[-1].end())
        if trailing is not None:
            amount_items.append((trailing, len(remainder)))
        else:
            leading = _leading_bare_integer(remainder, amount_matches[0].start())
            if leading is not None:
                leading_token, leading_start, leading_end = leading
                amount_items = [(leading_token, leading_end), amount_items[0]]
                description_end = leading_start

    description = remainder[:description_end].strip(" -:|\t")
    description = re.sub(r"\s+", " ", description)
    # Strip stray leading punctuation noise (common OCR misreads of whitespace/artifacts).
    description = re.sub(r"^[^A-Za-z0-9]+", "", description).strip()
    if not description:
        description = "UNKNOWN"

    if _looks_like_balance_summary(description):
        # e.g. "01/04/2024  Opening Balance  50,000.00  50,000.00" -- the date is at the
        # front so the line-start header/footer check above never sees "opening balance".
        return None

    resolved = _assign_debit_credit(amount_items, remainder, remainder)
    if resolved is None:
        return None

    return {
        "date": iso_date,
        "description": description,
        "debit_amount": resolved["debit_amount"],
        "credit_amount": resolved["credit_amount"],
        "balance": resolved["balance"],
    }


def parse_statement_text(raw_text: str) -> pd.DataFrame:
    """Parse raw extracted statement text into a transaction DataFrame (line-by-line regex path).

    Used as the fallback when table-based extraction (see `parse_table_rows`) is unavailable
    or does not yield usable results, and as the sole path for OCR'd (image-based) statements.

    Note: multi-line wrapped descriptions (a description that spills onto a second line with
    no date/amount of its own) are intentionally NOT auto-appended to the previous row. An
    earlier version of this tried to detect and glue such lines back on, but the same shape
    (a line with no date and no amount) is indistinguishable from page headers/titles/footers,
    which vary per bank and can't be reliably enumerated -- that heuristic was observed gluing
    a page-2 title onto the last transaction of page 1. Silently corrupting a real row is worse
    than leaving a wrapped line unrecovered, so this is a documented known limitation instead.
    """
    if not raw_text or not raw_text.strip():
        return pd.DataFrame(columns=TRANSACTION_COLUMNS)

    default_year = _infer_default_year(raw_text)
    lines = [line for line in raw_text.splitlines() if line.strip()]
    rows: List[Dict[str, object]] = []

    for line in lines:
        row = _parse_single_line(line, default_year=default_year)
        if row is not None:
            rows.append(row)

    if not rows:
        return pd.DataFrame(columns=TRANSACTION_COLUMNS)

    df = pd.DataFrame(rows)
    df = df.drop_duplicates(subset=TRANSACTION_COLUMNS, keep="first")
    df = df.reset_index(drop=True)

    for col in TRANSACTION_COLUMNS:
        if col not in df.columns:
            df[col] = None

    return df[TRANSACTION_COLUMNS]


# ---------------------------------------------------------------------------
# Table-based parsing (preferred path for text PDFs when a table structure is detected)
# ---------------------------------------------------------------------------

_COLUMN_KEYWORDS = {
    "date": ["date"],
    "description": ["description", "narration", "particulars", "details", "transaction detail", "remarks"],
    "debit": ["debit", "withdrawal", "dr amount", "dr."],
    "credit": ["credit", "deposit", "cr amount", "cr."],
    "amount": ["amount"],
    "balance": ["balance", "running balance", "closing balance"],
}


def _classify_header_cell(cell: str) -> Optional[str]:
    if not cell:
        return None
    normalized = re.sub(r"\s+", " ", str(cell)).strip().lower()
    for role, keywords in _COLUMN_KEYWORDS.items():
        if any(keyword in normalized for keyword in keywords):
            return role
    return None


def _find_header_row(table: Sequence[Sequence[Optional[str]]]) -> Optional[Dict[str, int]]:
    """Scan a table's rows for one that looks like a column header, return role->index map."""
    for row in table[:3]:  # header is virtually always in the first couple of rows
        role_map: Dict[str, int] = {}
        for idx, cell in enumerate(row):
            role = _classify_header_cell(cell)
            if role and role not in role_map:
                role_map[role] = idx
        if "date" in role_map and ("debit" in role_map or "credit" in role_map or "amount" in role_map):
            return role_map
    return None


def parse_table_rows(tables: Sequence[Sequence[Sequence[Optional[str]]]]) -> pd.DataFrame:
    """Parse pdfplumber-extracted tables (one list of rows per page) into a transaction DataFrame.

    This is far more reliable than line-regex parsing for banks that render statements as
    genuine PDF tables, since column identity comes from position rather than keyword guessing.
    """
    rows: List[Dict[str, object]] = []
    role_map: Optional[Dict[str, int]] = None

    for table in tables:
        if not table:
            continue
        if role_map is None:
            role_map = _find_header_row(table)
            if role_map is None:
                continue

        for raw_row in table:
            if all(_classify_header_cell(c) for c in raw_row if c):
                continue  # skip repeated header rows (common on subsequent pages)

            def cell(role: str) -> str:
                idx = role_map.get(role)
                if idx is None or idx >= len(raw_row):
                    return ""
                value = raw_row[idx]
                return "" if value is None else str(value).strip()

            date_raw = cell("date")
            if not date_raw:
                continue
            iso_date = _normalize_date(re.sub(r"\s+", " ", date_raw))
            if iso_date is None:
                continue

            description = re.sub(r"\s+", " ", cell("description")).strip() or "UNKNOWN"

            if "debit" in role_map or "credit" in role_map:
                debit_amount = _clean_amount(cell("debit"))
                credit_amount = _clean_amount(cell("credit"))
            else:
                # Single "amount" column: sign or a Dr/Cr marker (sometimes embedded in the
                # amount cell itself, e.g. "500.00 Dr") indicates direction.
                amount_cell = cell("amount")
                amount_value = abs(_clean_amount(amount_cell))
                is_credit = bool(re.search(r"\bcr\b", amount_cell, re.IGNORECASE)) or amount_value != _clean_amount(amount_cell)
                if is_credit:
                    debit_amount, credit_amount = 0.0, amount_value
                else:
                    debit_amount, credit_amount = amount_value, 0.0

            balance = abs(_clean_amount(cell("balance")))

            rows.append({
                "date": iso_date,
                "description": description,
                "debit_amount": round(debit_amount, 2),
                "credit_amount": round(credit_amount, 2),
                "balance": round(balance, 2),
            })

    if not rows:
        return pd.DataFrame(columns=TRANSACTION_COLUMNS)

    df = pd.DataFrame(rows)
    df = df.drop_duplicates(subset=TRANSACTION_COLUMNS, keep="first")
    df = df.reset_index(drop=True)
    return df[TRANSACTION_COLUMNS]


def parse_statement(raw_text: str) -> Dict[str, object]:
    """Return account metadata and transactions extracted from the raw statement text."""
    transactions = parse_statement_text(raw_text)
    account_details = extract_account_details(raw_text)
    return {
        "account_details": account_details,
        "transactions": transactions,
    }
