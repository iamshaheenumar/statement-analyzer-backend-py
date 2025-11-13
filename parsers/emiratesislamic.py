import re
import datetime
from common.pdf_utils import open_pdf_safe, normalize_transactions, summarize_transactions, normalize_date

BANK_NAME = "Emirates Islamic"
CARD_TYPE = "credit"

# Example: "14 AUG   12 AUG   RTA-ETISALAT DUBAI ARE   100.00"
LINE_REGEX = re.compile(
    r"^(\d{2}\s+[A-Z]{3})\s+(\d{2}\s+[A-Z]{3})\s+(.+?)\s+([\d,]+\.\d{2})(CR)?$",
    re.IGNORECASE,
)

FROM_TO_REGEX = re.compile(
    r"^\s*(From|To)\s*:?\s*(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9}\s+\d{4})\s*$",
    re.IGNORECASE,
)

SKIP_KEYWORDS = [
    "opening balance",
    "primary card no",
    "rewards summary",
    "cashback",
    "card limit",
    "minimum payment due",
    "payment due date",
    "profit/other charges",
    "current balance",
    "profit reversal",
    "finance charges",
]

def clean_amount(val: str) -> float:
    if not val:
        return 0.0
    v = val.replace(",", "").replace("CR", "").strip()
    try:
        return float(v)
    except ValueError:
        return 0.0

def _strip_ordinal(s: str) -> str:
    return re.sub(r'(\d+)(st|nd|rd|th)', r'\1', s, flags=re.IGNORECASE).strip()

def _parse_full_date(s: str) -> str | None:
    # Accepts "11th Jul 2025" or "11 Jul 2025" etc. Returns ISO date string.
    s_clean = _strip_ordinal(s)
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            dt = datetime.datetime.strptime(s_clean, fmt).date()
            return dt.isoformat()
        except ValueError:
            continue
    return None

def _normalize_range_token(raw: str) -> str | None:
    token = raw.strip().strip(":").lower()
    if not token:
        return None
    if token in {"from", "to"}:
        return token
    # Handle common Arabic renderings seen in PDFs (e.g. "ىلإ" for "To").
    arabic_map = {
        "من": "from",
        "من ": "from",
        "الى": "to",
        "إلى": "to",
        "ىلإ": "to",
    }
    return arabic_map.get(token)

def parse_emiratesislamic(file_path: str, password: str | None = None):
    transactions = []
    statement_from = None
    statement_to = None

    pdf = open_pdf_safe(file_path, password)
    if isinstance(pdf, dict) and "error" in pdf:
        return pdf  # error dict

    pending_range = None

    with pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for line in text.splitlines():
                raw = line.strip()
                if not raw:
                    continue

                low = raw.lower()
                if any(k in low for k in SKIP_KEYWORDS):
                    continue

                # Look for From / To lines (e.g. "From:11th Jul 2025")
                m_range = FROM_TO_REGEX.match(raw)
                if m_range:
                    which, date_str = m_range.groups()
                    parsed = _parse_full_date(date_str)
                    if parsed:
                        if which.lower() == "from":
                            statement_from = parsed
                        else:
                            statement_to = parsed
                    pending_range = None
                    continue

                normalized_range = _normalize_range_token(raw)
                if normalized_range:
                    if normalized_range == "from" and statement_from:
                        continue
                    if normalized_range == "to" and statement_to:
                        continue
                    pending_range = normalized_range
                    continue

                if pending_range:
                    parsed = _parse_full_date(raw)
                    if parsed:
                        if pending_range == "from":
                            statement_from = parsed
                        else:
                            statement_to = parsed
                        pending_range = None
                        continue
                    # If the line wasn't a recognizable date, keep the pending flag
                    # so the next lines still have a chance to supply the date.

                m = LINE_REGEX.match(raw)
                if not m:
                    continue

                _, txn_date_raw, desc, amt_raw, cr = m.groups()
                amt_val = clean_amount(amt_raw)

                debit, credit = 0.0, 0.0
                if cr or "payment received" in desc.lower():
                    credit = amt_val
                else:
                    debit = amt_val

                # Parse transaction date with just month and day
                txn_date = normalize_date(txn_date_raw.strip(), "%d %b")
                
                # If we have from_date and to_date, determine the correct year
                if statement_from and statement_to:
                    from_year = int(statement_from[:4])
                    to_year = int(statement_to[:4])
                    
                    # Convert string date to datetime object for comparison
                    txn_dt = datetime.datetime.strptime(txn_date, "%Y-%m-%d").replace(year=to_year)
                    
                    # If the month is in the first few months of the statement and to_date is in the later months,
                    # it likely means the transaction is from the previous year
                    statement_to_dt = datetime.datetime.strptime(statement_to, "%Y-%m-%d")
                    if statement_to_dt.month < 6 and txn_dt.month > 6:
                        txn_date = txn_dt.replace(year=from_year).strftime("%Y-%m-%d")
                    else:
                        txn_date = txn_dt.strftime("%Y-%m-%d")
                
                transactions.append({
                    "transaction_date": txn_date,
                    "description": desc.strip(),
                    "debit": debit,
                    "credit": credit,
                    "amount": amt_val,
                    "bank": BANK_NAME,
                    "card_type": CARD_TYPE,
                })

    normalized = normalize_transactions(transactions, BANK_NAME, CARD_TYPE)
    result = {
        "bank": BANK_NAME,
        "card_type": CARD_TYPE,
        "summary": summarize_transactions(normalized),
        "transactions": normalized,
        "from_date": statement_from,
        "to_date": statement_to,
    }
    return result
