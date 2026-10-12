"""Odoo Community as the finance ledger, over JSON-RPC.

Repayments are journal entries: debit the collection account the money arrived in, credit
loan principal and interest income (or suspense). One entry per reference: `post_entry`
refuses a reference it has seen. Odoo can't enforce that itself without a custom module, so
the posting gateway also keeps its own unique key per posting; this check is the second line.
"""

from __future__ import annotations

import itertools
import re
import threading
from datetime import date
from typing import Any

import httpx
from opentelemetry import trace

from repaydesk.adapters.chart import CHART, JOURNAL_CODES, REPAYMENT_JOURNAL, collection_account_code
from repaydesk.adapters.ports import AlreadyPosted, JournalEntry, JournalLine, LedgerEntry

tracer = trace.get_tracer("repaydesk.adapters.odoo")


class OdooError(Exception):
    pass


class OdooLedger:
    def __init__(self, url: str, db: str, username: str, password: str, timeout: float = 120):
        self.url, self.db, self.username, self.password = url.rstrip("/"), db, username, password
        self.http = httpx.Client(timeout=timeout)
        self._ids = itertools.count(1)
        self._uid: int | None = None
        self._lock = threading.Lock()
        self._accounts: dict[str, int] = {}
        self._journals: dict[str, int] = {}

    # ---- plumbing ------------------------------------------------------------------------------

    def _rpc(self, service: str, method: str, *args: Any) -> Any:
        payload = {"jsonrpc": "2.0", "method": "call", "id": next(self._ids),
                   "params": {"service": service, "method": method, "args": list(args)}}
        res = self.http.post(f"{self.url}/jsonrpc", json=payload).json()
        if "error" in res:
            data = res["error"].get("data", {})
            raise OdooError(data.get("message") or res["error"].get("message"))
        return res["result"]

    @property
    def uid(self) -> int:
        if self._uid is None:
            self._uid = self._rpc("common", "login", self.db, self.username, self.password)
            if not self._uid:
                raise OdooError("Odoo login failed")
        return self._uid

    def _x(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        return self._rpc("object", "execute_kw", self.db, self.uid, self.password, model, method, list(args), kwargs)

    def health(self) -> bool:
        try:
            return bool(self.uid)
        except (httpx.HTTPError, OdooError):
            return False

    # ---- setup ---------------------------------------------------------------------------------

    def ensure_setup(self, bank_name: str, collection_accounts: dict[str, tuple[str, str]]) -> None:
        with tracer.start_as_current_span("odoo.ensure_setup"):
            company = self._x("res.company", "read", [1], fields=["currency_id"])[0]
            ngn = self._x("res.currency", "search", [["name", "=", "NGN"], ["active", "in", [True, False]]])[0]
            nigeria = self._x("res.country", "search", [["code", "=", "NG"]])[0]
            self._x("res.currency", "write", [ngn], {"active": True})
            changes: dict[str, Any] = {"name": bank_name, "country_id": nigeria}
            if company["currency_id"][0] != ngn:
                changes["currency_id"] = ngn  # only possible before the first entry
            self._x("res.company", "write", [1], changes)

            wanted = [(code, name, kind) for code, name, kind in CHART]
            wanted += [(collection_account_code(k), f"Collections: {name}", "asset_cash")
                       for k, (name, _n) in collection_accounts.items()]
            # Odoo's generic chart already uses some of these codes (110100 is "Stock Valuation",
            # 131000 "Tax Paid"), so an existing account is renamed and retyped to what Repaydesk
            # means by it, not taken as it is.
            for code, name, kind in wanted:
                found = self._x("account.account", "search_read", [["code", "=", code]],
                                fields=["name", "account_type"], limit=1)
                if not found:
                    self._accounts[code] = self._x("account.account", "create",
                                                   {"code": code, "name": name, "account_type": kind})
                    continue
                self._accounts[code] = found[0]["id"]
                if (found[0]["name"], found[0]["account_type"]) != (name, kind):
                    self._x("account.account", "write", [found[0]["id"]], {"name": name, "account_type": kind})

            for key, (name, number) in collection_accounts.items():
                code = JOURNAL_CODES[key]
                if not self._journal(code, required=False):
                    self._journals[code] = self._x("account.journal", "create", {
                        "name": name, "code": code, "type": "bank",
                        "default_account_id": self._account(collection_account_code(key)),
                        "bank_acc_number": number if number.isdigit() else False,
                    })
            if not self._journal(REPAYMENT_JOURNAL, required=False):
                self._journals[REPAYMENT_JOURNAL] = self._x("account.journal", "create", {
                    "name": "Loan repayments", "code": REPAYMENT_JOURNAL, "type": "general",
                })

    def _account(self, code: str, required: bool = True) -> int | None:
        if code not in self._accounts:
            found = self._x("account.account", "search", [["code", "=", code]], limit=1)
            if not found:
                if required:
                    raise KeyError(f"unknown account {code}")
                return None
            self._accounts[code] = found[0]
        return self._accounts[code]

    def _journal(self, code: str, required: bool = True) -> int | None:
        if code not in self._journals:
            found = self._x("account.journal", "search", [["code", "=", code]], limit=1)
            if not found:
                if required:
                    raise KeyError(f"unknown journal {code}")
                return None
            self._journals[code] = found[0]
        return self._journals[code]

    def _xmlid(self, xmlid: str) -> int:
        module, name = xmlid.split(".")
        found = self._x("ir.model.data", "search_read", [["module", "=", module], ["name", "=", name]],
                        fields=["res_id"], limit=1)
        if not found:
            raise OdooError(f"Odoo has no {xmlid}")
        return found[0]["res_id"]

    def ensure_read_user(self, login: str, password: str) -> None:
        """A login that can read the books and change nothing: an internal user in Odoo's read-only
        accounting group, and no other. For the ledger MCP server (ADR 0008)."""
        groups = [self._xmlid("base.group_user"), self._xmlid("account.group_account_readonly")]
        values = {"name": "Repaydesk (read only)", "login": login, "password": password,
                  "groups_id": [(6, 0, groups)]}
        found = self._x("res.users", "search", [["login", "=", login]], context={"active_test": False})
        if found:
            self._x("res.users", "write", found, values)
        else:
            self._x("res.users", "create", values)

    # ---- entries -------------------------------------------------------------------------------

    def find_entry(self, reference: str) -> str | None:
        found = self._x("account.move", "search_read", [["ref", "=", reference], ["state", "!=", "cancel"]],
                        fields=["name"], limit=1)
        return found[0]["name"] if found else None

    def post_entry(self, entry: JournalEntry) -> str:
        if not entry.balanced():
            raise ValueError(f"unbalanced entry {entry.reference}")
        with tracer.start_as_current_span("odoo.post_entry") as span, self._lock:
            span.set_attribute("ledger.reference", entry.reference)
            if self.find_entry(entry.reference):
                raise AlreadyPosted(entry.reference)
            move_id = self._x("account.move", "create", {
                "move_type": "entry",
                "journal_id": self._journal(entry.journal),
                "date": entry.on.isoformat(),
                "ref": entry.reference,
                "narration": entry.memo,
                "line_ids": [
                    (0, 0, {"account_id": self._account(line.account_code), "name": line.label or entry.memo,
                            "debit": line.debit / 100, "credit": line.credit / 100})
                    for line in entry.lines
                ],
            })
            self._x("account.move", "action_post", [move_id])
            return self._x("account.move", "read", [move_id], fields=["name"])[0]["name"]

    def read_entry(self, reference: str) -> LedgerEntry | None:
        """An entry by its reference, with each line's account code, as Odoo holds it now."""
        found = self._x("account.move", "search_read", [["ref", "=", reference], ["state", "!=", "cancel"]],
                        fields=["name", "date", "state", "narration", "journal_id", "line_ids"], limit=1)
        if not found:
            return None
        move = found[0]
        rows = self._x("account.move.line", "read", move["line_ids"], fields=["account_id", "debit", "credit", "name"])
        lines = tuple(JournalLine(r["account_id"][1].split()[0], round(r["debit"] * 100), round(r["credit"] * 100),
                                  r["name"] or "") for r in rows)
        memo = re.sub(r"<[^>]+>", "", move["narration"] or "").strip()
        journal = move["journal_id"][1] if move["journal_id"] else ""
        return LedgerEntry(move["name"], move["state"],
                           JournalEntry(date.fromisoformat(move["date"]), reference, memo, journal, lines))

    def balance(self, account_code: str, as_of: date | None = None) -> int:
        domain = [["account_id", "=", self._account(account_code)], ["parent_state", "=", "posted"]]
        if as_of:
            domain.append(["date", "<=", as_of.isoformat()])
        groups = self._x("account.move.line", "read_group", domain, ["balance:sum"], [])
        return round((groups[0].get("balance") or 0) * 100) if groups else 0
