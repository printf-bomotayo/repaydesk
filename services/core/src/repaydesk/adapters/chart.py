"""The ledger accounts Repaydesk posts to. Same codes in every Ledger adapter.

A repayment is: debit the collection account it arrived in; credit loan principal and
interest income. A line nobody can place yet is: debit the collection account; credit
repayment suspense. Clearing suspense later moves it from suspense to the loan.
"""

# (code, name, Odoo account type)
CHART = [
    ("131000", "Loans to customers: principal", "asset_current"),
    ("410000", "Interest income on loans", "income"),
    ("219000", "Repayment suspense", "liability_current"),
    ("219500", "Customer overpayments", "liability_current"),
    ("300100", "Loan book opening balance", "equity"),
]

PRINCIPAL = "131000"
INTEREST_INCOME = "410000"
SUSPENSE = "219000"
OVERPAYMENTS = "219500"
OPENING_BALANCE = "300100"

_COLLECTION = {
    "bank_csv": "110100",
    "bank_mt940": "110200",
    "virtual_account": "110300",
    "pos": "110400",
    "branch_cash": "110500",
}

# Odoo journal short codes, one per collection account, plus the repayment journal.
JOURNAL_CODES = {
    "bank_csv": "NGT",
    "bank_mt940": "CST",
    "virtual_account": "PRL",
    "pos": "TPY",
    "branch_cash": "CSH",
}
REPAYMENT_JOURNAL = "RPY"


def collection_account_code(channel: str) -> str:
    return _COLLECTION[channel]
