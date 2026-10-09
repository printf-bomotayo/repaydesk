"""Apache Fineract as the core banking system, over its REST API.

Fineract quirks this adapter absorbs, so nothing above it has to know:
- every write needs `locale` and `dateFormat`; amounts are decimal naira;
- external ids are unique per resource, which is what makes repayments idempotent;
- name search is a case-sensitive substring match, so we search word by word in title case;
- there is no field for a trading name, so it lives in a `client_business` datatable that
  only supports exact lookups. Fuzzy search belongs in the borrower directory;
- that datatable can list which clients have a row, but not the rows themselves, so reading
  every trading name is one call per borrower. The directory listing caches them (see
  `_trading_names`).
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any

import httpx
from opentelemetry import trace

from repaydesk.adapters.ports import (
    AlreadyPosted,
    BorrowerMatch,
    DirectoryBorrower,
    DueInstallment,
    LoanSummary,
    LoanView,
    PostedRepayment,
)
from repaydesk.domain import Borrower, Branch, Channel, Group, Loan, Product, bank_today, to_kobo

tracer = trace.get_tracer("repaydesk.adapters.fineract")

DATES = {"dateFormat": "yyyy-MM-dd", "locale": "en"}
SHORT_NAMES = {"QUICK": "QUIK", "SME": "SME"}  # Fineract allows four characters
BUSINESS_TABLE = "client_business"
PAGE = 500
TRADING_NAMES_REREAD_SECONDS = 24 * 3600
# Fineract's own rule for user passwords (validation.msg.user.password.does.not.match.regexp).
# What each of Repaydesk's own Fineract logins may do (ADR 0002). Reads for the API, the directory
# and rule matching; one write, a repayment, for the posting gateway.
READ_ONLY = ("ALL_FUNCTIONS_READ",)
POSTING = ("READ_LOAN", "REPAYMENT_LOAN", "READ_PAYMENTTYPE")
PASSWORD_POLICY = r"(?!.*(.)\1)(?!.*\s)(?=.*\d)(?=.*[a-z])(?=.*[A-Z])(?=.*[^\w\s]).{12,50}"


def _naira(kobo: int) -> str:
    return f"{kobo / 100:.2f}"


def _date(parts: list[int] | None) -> date | None:
    return date(*parts) if parts else None


class FineractError(Exception):
    def __init__(self, status: int, body: Any):
        self.status, self.body = status, body
        super().__init__(f"Fineract {status}: {body}")

    @property
    def is_duplicate(self) -> bool:
        text = str(self.body)
        return "unique" in text or "duplicate" in text


class FineractCoreBanking:
    def __init__(self, base_url: str, tenant: str, username: str, password: str, timeout: float = 60):
        self.http = httpx.Client(
            base_url=base_url.rstrip("/"),
            auth=(username, password),
            headers={"Fineract-Platform-TenantId": tenant},
            timeout=timeout,
            limits=httpx.Limits(max_connections=16),
        )
        self._lock = threading.Lock()
        self._offices: dict[str, int] = {}
        self._products: dict[str, int] = {}
        self._product_codes: dict[int, str] = {}
        self._payment_types: dict[str, int] = {}
        self._clients: dict[str, int] = {}
        self._trading: dict[int, str | None] = {}
        self._trading_read_at = 0.0

    # ---- plumbing ------------------------------------------------------------------------------

    def _call(self, method: str, path: str, body: dict | None = None, params: dict | None = None) -> Any:
        r = self.http.request(method, path, json=body, params=params)
        if r.status_code >= 400:
            try:
                detail = r.json()
            except ValueError:
                detail = r.text
            raise FineractError(r.status_code, detail)
        return r.json() if r.content else None

    def _get_or_none(self, path: str, params: dict | None = None) -> Any:
        try:
            return self._call("GET", path, params=params)
        except FineractError as e:
            if e.status == 404:
                return None
            raise

    def health(self) -> bool:
        try:
            self._call("GET", "/offices", params={"limit": 1})
            return True
        except (httpx.HTTPError, FineractError):
            return False

    # ---- setup ---------------------------------------------------------------------------------

    def ensure_setup(self, branches: Iterable[Branch], products: Iterable[Product]) -> None:
        with tracer.start_as_current_span("fineract.ensure_setup"):
            self._call("PUT", "/currencies", {"currencies": ["NGN"]})

            offices = {o.get("externalId"): o["id"] for o in self._call("GET", "/offices")}
            for b in branches:
                if b.code not in offices:
                    res = self._call("POST", "/offices", {
                        **DATES, "name": b.name, "parentId": 1, "openingDate": "2020-01-01", "externalId": b.code,
                    })
                    offices[b.code] = res["resourceId"]
            self._offices = {k: v for k, v in offices.items() if k}

            existing = {p["shortName"]: p["id"] for p in self._call("GET", "/loanproducts")}
            for p in products:
                short = SHORT_NAMES[p.code]
                if short not in existing:
                    existing[short] = self._call("POST", "/loanproducts", self._product_body(p))["resourceId"]
                self._products[p.code] = existing[short]
                self._product_codes[existing[short]] = p.code

            types = {t["name"]: t["id"] for t in self._call("GET", "/paymenttypes")}
            for i, channel in enumerate(Channel, start=1):
                if channel.value not in types:
                    types[channel.value] = self._call("POST", "/paymenttypes", {
                        "name": channel.value, "description": f"Repayment by {channel.value}",
                        "isCashPayment": channel is Channel.BRANCH_CASH, "position": i,
                    })["resourceId"]
            self._payment_types = types

            tables = {t["registeredTableName"] for t in self._call("GET", "/datatables")}
            if BUSINESS_TABLE not in tables:
                self._call("POST", "/datatables", {
                    "datatableName": BUSINESS_TABLE, "apptableName": "m_client", "entitySubType": "PERSON",
                    "multiRow": False,
                    "columns": [{"name": "trading_name", "type": "String", "length": 120, "mandatory": False}],
                })

    def ensure_service_user(self, username: str, password: str, role: str, permissions: Iterable[str]) -> None:
        """A login for one of Repaydesk's services, with a role that grants only `permissions`
        (ADR 0002: only the posting gateway may write). Re-running resets the role's permissions
        and the password, so both follow the configuration."""
        if not re.fullmatch(PASSWORD_POLICY, password):
            raise ValueError(f"the password for {username} doesn't meet Fineract's policy: 12 to 50 characters with "
                             "upper and lower case, a digit and a symbol, no spaces, no character twice in a row")
        try:
            self._ensure_service_user(username, password, role, permissions)
        except FineractError as e:
            # Fineract echoes a rejected password back in its error; keep only the messages.
            errors = e.body.get("errors", []) if isinstance(e.body, dict) else []
            messages = "; ".join(x.get("defaultUserMessage", "") for x in errors) or "no detail"
            raise FineractError(e.status, f"setting up {username}: {messages}") from None

    def _ensure_service_user(self, username: str, password: str, role: str, permissions: Iterable[str]) -> None:
        with tracer.start_as_current_span("fineract.ensure_service_user") as span:
            span.set_attribute("fineract.username", username)
            roles = {r["name"]: r["id"] for r in self._call("GET", "/roles")}
            if role not in roles:
                created = self._call("POST", "/roles", {"name": role, "description": f"Repaydesk: {username}"})
                roles[role] = created["resourceId"]
            wanted = set(permissions)
            current = self._call("GET", f"/roles/{roles[role]}/permissions")["permissionUsageData"]
            changes = {p["code"]: p["code"] in wanted for p in current if p.get("selected") != (p["code"] in wanted)}
            if changes:
                self._call("PUT", f"/roles/{roles[role]}/permissions", {"permissions": changes})
            login = {"password": password, "repeatPassword": password, "roles": [roles[role]],
                     "passwordNeverExpires": True}
            users = {u["username"]: u["id"] for u in self._call("GET", "/users")}
            if username in users:
                self._call("PUT", f"/users/{users[username]}", login)
            else:
                self._call("POST", "/users", {**login, "username": username, "firstname": "Repaydesk",
                                              "lastname": username, "officeId": 1, "sendPasswordToEmail": False})

    @staticmethod
    def _product_body(p: Product) -> dict:
        return {
            **DATES,
            "name": p.name, "shortName": SHORT_NAMES[p.code], "externalId": p.code, "currencyCode": "NGN",
            "digitsAfterDecimal": 2, "inMultiplesOf": 0,
            "principal": 100000, "minPrincipal": 1000, "maxPrincipal": 50000000,
            "numberOfRepayments": p.installments, "repaymentEvery": 1, "repaymentFrequencyType": 2,  # months
            "interestRatePerPeriod": p.monthly_flat_rate * 100, "interestRateFrequencyType": 2,  # per month
            "amortizationType": 1, "interestType": 1,  # equal instalments, flat interest
            "interestCalculationPeriodType": 1,
            "transactionProcessingStrategyCode": "mifos-standard-strategy",
            "accountingRule": 1,  # none: the ledger is Odoo
            "daysInMonthType": 1, "daysInYearType": 1, "isInterestRecalculationEnabled": False,
        }

    def _load_maps(self) -> None:
        if self._offices and self._products and self._payment_types:
            return
        with self._lock:
            self._offices = {o.get("externalId"): o["id"] for o in self._call("GET", "/offices") if o.get("externalId")}
            for p in self._call("GET", "/loanproducts"):
                code = next((c for c, s in SHORT_NAMES.items() if s == p["shortName"]), None)
                if code:
                    self._products[code] = p["id"]
                    self._product_codes[p["id"]] = code
            self._payment_types = {t["name"]: t["id"] for t in self._call("GET", "/paymenttypes")}

    def _client_id(self, borrower_ref: str) -> int:
        if borrower_ref not in self._clients:
            c = self._get_or_none(f"/clients/external-id/{borrower_ref}")
            if not c:
                raise KeyError(f"unknown borrower {borrower_ref}")
            self._clients[borrower_ref] = c["id"]
        return self._clients[borrower_ref]

    # ---- writes (seeder and posting gateway only) ----------------------------------------------

    def create_borrower(self, b: Borrower) -> None:
        self._load_maps()
        try:
            res = self._call("POST", "/clients", {
                **DATES, "officeId": self._offices[b.branch_code], "legalFormId": 1,
                "firstname": b.first_name, "lastname": b.last_name, "mobileNo": b.phone, "externalId": b.ref,
                "active": True, "activationDate": b.joined_on.isoformat(), "submittedOnDate": b.joined_on.isoformat(),
            })
            self._clients[b.ref] = res["clientId"]
        except FineractError as e:
            if not e.is_duplicate:
                raise
            self._client_id(b.ref)
        if b.business_name:
            try:
                self._call("POST", f"/datatables/{BUSINESS_TABLE}/{self._clients[b.ref]}",
                           {"trading_name": b.business_name, "locale": "en"})
            except FineractError as e:
                if not (e.is_duplicate or e.status in (400, 403)):
                    raise

    def create_group(self, g: Group) -> None:
        self._load_maps()
        if self._group_exists(g.ref):
            return
        members = [self._client_id(ref) for ref in g.member_refs]
        joined = date(*max(self._get_or_none(f"/clients/{m}")["activationDate"] for m in members)).isoformat()
        try:
            self._call("POST", "/groups", {
                **DATES, "officeId": self._offices[g.branch_code], "name": g.name, "externalId": g.ref,
                "active": True, "activationDate": joined, "submittedOnDate": joined,
                "clientMembers": members,
            })
        except FineractError as e:
            if not e.is_duplicate:
                raise

    def _group_exists(self, ref: str) -> bool:
        page = self._call("GET", "/groups", params={"externalId": ref, "limit": 1})
        items = page.get("pageItems", page) if isinstance(page, dict) else page
        return any(i.get("externalId") == ref for i in items)

    def create_loan(self, loan: Loan) -> None:
        self._load_maps()
        with tracer.start_as_current_span("fineract.create_loan") as span:
            span.set_attribute("loan.ref", loan.ref)
            existing = self._get_or_none(f"/loans/external-id/{loan.ref}")
            if existing:
                loan_id, status = existing["id"], existing["status"]
            else:
                product = next(p for p in _products() if p.code == loan.product)
                res = self._call("POST", "/loans", {
                    **DATES, "clientId": self._client_id(loan.borrower_ref), "productId": self._products[loan.product],
                    "externalId": loan.ref, "loanType": "individual", "principal": _naira(loan.principal),
                    "loanTermFrequency": product.installments, "loanTermFrequencyType": 2,
                    "numberOfRepayments": product.installments, "repaymentEvery": 1, "repaymentFrequencyType": 2,
                    "interestRatePerPeriod": product.monthly_flat_rate * 100, "amortizationType": 1, "interestType": 1,
                    "interestCalculationPeriodType": 1, "transactionProcessingStrategyCode": "mifos-standard-strategy",
                    "submittedOnDate": loan.disbursed_on.isoformat(),
                    "expectedDisbursementDate": loan.disbursed_on.isoformat(),
                })
                loan_id, status = res["loanId"], {"pendingApproval": True}
            on = loan.disbursed_on.isoformat()
            if status.get("pendingApproval"):
                self._call("POST", f"/loans/{loan_id}", {**DATES, "approvedOnDate": on}, {"command": "approve"})
                status = {"waitingForDisbursal": True}
            if status.get("waitingForDisbursal"):
                self._call("POST", f"/loans/{loan_id}", {
                    **DATES, "actualDisbursementDate": on,
                    "paymentTypeId": self._payment_types.get(Channel.BANK_CSV.value),
                }, {"command": "disburse"})

    def post_repayment(self, loan_ref: str, on: date, amount: int, idempotency_key: str,
                       channel: Channel | None = None) -> None:
        self._load_maps()
        body = {**DATES, "transactionDate": on.isoformat(), "transactionAmount": _naira(amount),
                "externalId": idempotency_key}
        if channel:
            body["paymentTypeId"] = self._payment_types.get(channel.value)
        try:
            self._call("POST", f"/loans/external-id/{loan_ref}/transactions", body, {"command": "repayment"})
        except FineractError as e:
            if e.is_duplicate:
                raise AlreadyPosted(idempotency_key) from e
            if e.status == 404:
                raise KeyError(f"unknown loan {loan_ref}") from e
            raise

    # ---- reads (what agents see, through read-only MCP tools) ----------------------------------

    def _to_match(self, client_id: int, name: str | None = None, phone: str | None = None) -> BorrowerMatch:
        c = self._call("GET", f"/clients/{client_id}")
        loans = self._call("GET", f"/clients/{client_id}/accounts", params={"fields": "loanAccounts"})
        business = self._get_or_none(f"/datatables/{BUSINESS_TABLE}/{client_id}")
        offices = {v: k for k, v in self._offices.items()} if self._offices else {}
        if not offices:
            self._load_maps()
            offices = {v: k for k, v in self._offices.items()}
        return BorrowerMatch(
            borrower_ref=c.get("externalId", ""),
            name=c.get("displayName", name or ""),
            phone=c.get("mobileNo", phone or ""),
            branch_code=offices.get(c.get("officeId"), ""),
            business_name=(business[0].get("trading_name") if business else None),
            loan_refs=tuple(loan["externalId"] for loan in loans.get("loanAccounts", []) if loan.get("externalId")),
        )

    def get_borrower(self, borrower_ref: str) -> BorrowerMatch | None:
        c = self._get_or_none(f"/clients/external-id/{borrower_ref}", params={"fields": "id"})
        return self._to_match(c["id"]) if c else None

    def search_borrowers(self, text: str, limit: int = 10) -> list[BorrowerMatch]:
        words = [w.title() for w in "".join(c if c.isalnum() else " " for c in text).split() if len(w) > 1]
        scores: dict[int, int] = {}
        for w in words:
            for hit in self._call("GET", "/search", params={"query": w, "resource": "clients", "exactMatch": "false"}):
                scores[hit["entityId"]] = scores.get(hit["entityId"], 0) + 1
        business = self._call("GET", f"/datatables/{BUSINESS_TABLE}/query", params={
            "columnFilter": "trading_name", "valueFilter": text.strip().title(), "resultColumns": "client_id",
        })
        for row in business or []:
            scores[row["client_id"]] = scores.get(row["client_id"], 0) + len(words)
        ranked = sorted(scores, key=lambda cid: (-scores[cid], cid))[:limit]
        return [self._to_match(cid) for cid in ranked]

    def find_by_phone(self, phone: str) -> BorrowerMatch | None:
        digits = "".join(c for c in phone if c.isdigit())
        if digits.startswith("234"):
            digits = "0" + digits[3:]
        hits = self._call("GET", "/search", params={"query": digits, "resource": "clients", "exactMatch": "true"})
        return self._to_match(hits[0]["entityId"]) if hits else None

    def get_loan(self, loan_ref: str) -> LoanView | None:
        self._load_maps()
        j = self._get_or_none(f"/loans/external-id/{loan_ref}", params={"associations": "repaymentSchedule"})
        if not j:
            return None
        rows = tuple(
            DueInstallment(p["period"], _date(p["dueDate"]), to_kobo(p["totalDueForPeriod"]),
                           to_kobo(p.get("totalPaidForPeriod", 0)))
            for p in j["repaymentSchedule"]["periods"]
            if p.get("period")
        )
        s, st = j.get("summary", {}), j["status"]
        status = "overpaid" if st.get("overpaid") else ("closed" if st.get("closed") else "active")
        client = self._call("GET", f"/clients/{j['clientId']}", params={"fields": "externalId"})
        return LoanView(
            ref=loan_ref,
            borrower_ref=client.get("externalId", ""),
            product=self._product_codes.get(j["loanProductId"], str(j["loanProductId"])),
            principal=to_kobo(j["principal"]),
            disbursed_on=_date(j["timeline"].get("actualDisbursementDate")),
            status=status,
            outstanding=to_kobo(s.get("totalOutstanding", 0)),
            arrears=to_kobo(s.get("totalOverdue", 0)),
            days_late=(j.get("delinquent") or {}).get("pastDueDays", 0) or 0,
            installments=rows,
        )

    def list_repayments(self, loan_ref: str) -> list[PostedRepayment]:
        j = self._get_or_none(f"/loans/external-id/{loan_ref}", params={"associations": "transactions"})
        if not j:
            return []
        return [
            PostedRepayment(loan_ref, _date(t["date"]), to_kobo(t["amount"]), t.get("externalId", ""),
                            ((t.get("paymentDetailData") or {}).get("paymentType") or {}).get("name"))
            for t in j.get("transactions", [])
            if t["type"].get("repayment") and not t.get("manuallyReversed")
        ]


    # ---- the whole book, for the borrower directory --------------------------------------------

    def _pages(self, path: str) -> Iterator[dict]:
        offset = 0
        while True:
            page = self._call("GET", path, params={"limit": PAGE, "offset": offset})
            yield from page["pageItems"]
            offset += PAGE
            if offset >= page["totalFilteredRecords"]:
                return

    def _trading_names(self) -> dict[int, str]:
        """Every client's trading name. Fineract lists which clients have one in one call, then
        each name is a call of its own, so names are kept between syncs: a sync reads only the
        clients it hasn't seen, and everything again once a day in case a name was edited."""
        ids: set[int] = set()
        page, total = 0, None
        while total is None or len(ids) < total:
            res = self._call("POST", f"/datatables/{BUSINESS_TABLE}/query", {
                "page": page, "size": 5000, "request": {"baseQuery": {"resultColumns": ["client_id"]}},
            })
            total = res["total"]
            if not res["content"]:
                break
            ids.update(r["client_id"] for r in res["content"])
            page += 1
        if time.monotonic() - self._trading_read_at > TRADING_NAMES_REREAD_SECONDS:
            self._trading, self._trading_read_at = {}, time.monotonic()
        missing = sorted(ids - self._trading.keys())

        def one(client_id: int) -> str | None:
            rows = self._get_or_none(f"/datatables/{BUSINESS_TABLE}/{client_id}")
            return rows[0].get("trading_name") if rows else None

        with ThreadPoolExecutor(8) as pool:
            self._trading.update(zip(missing, pool.map(one, missing), strict=True))
        for gone in self._trading.keys() - ids:
            del self._trading[gone]
        return {k: v for k, v in self._trading.items() if v}

    def list_borrowers(self) -> Iterator[DirectoryBorrower]:
        with tracer.start_as_current_span("fineract.list_borrowers"):
            self._load_maps()
            office_code = {v: k for k, v in self._offices.items()}
            groups: dict[int, tuple[str, str]] = {}
            for g in self._call("GET", "/groups"):
                detail = self._call("GET", f"/groups/{g['id']}", params={"associations": "clientMembers"})
                for m in detail.get("clientMembers", []):
                    groups[m["id"]] = (g.get("externalId") or str(g["id"]), g["name"])
            trading = self._trading_names()
            for c in self._pages("/clients"):
                if not c.get("externalId"):
                    continue  # not created by a bank process that gives customers a reference
                group = groups.get(c["id"])
                yield DirectoryBorrower(
                    ref=c["externalId"], core_id=str(c["id"]), name=c.get("displayName", ""),
                    phone=c.get("mobileNo"), branch_code=office_code.get(c.get("officeId")),
                    branch_name=c.get("officeName"), business_name=trading.get(c["id"]),
                    group_ref=group[0] if group else None, group_name=group[1] if group else None,
                    joined_on=_date(c.get("activationDate")), active=bool(c.get("active")),
                )

    def list_loans(self) -> Iterator[LoanSummary]:
        with tracer.start_as_current_span("fineract.list_loans"):
            self._load_maps()
            for j in self._pages("/loans"):
                if not j.get("externalId"):
                    continue
                st, s = j["status"], j.get("summary") or {}
                status = ("overpaid" if st.get("overpaid") else "closed" if st.get("closed")
                          else "active" if st.get("active") else "other")
                # The list leaves `delinquent.pastDueDays` at 0; only a single loan's read fills it.
                # Its summary does carry the date it fell overdue, which gives the same number.
                since = _date(s.get("overdueSinceDate"))
                yield LoanSummary(
                    ref=j["externalId"], core_id=str(j["id"]), borrower_ref=j.get("clientExternalId", ""),
                    product=self._product_codes.get(j["loanProductId"], str(j["loanProductId"])),
                    product_name=j.get("loanProductName", ""), principal=to_kobo(j["principal"]),
                    disbursed_on=_date(j.get("timeline", {}).get("actualDisbursementDate")), status=status,
                    outstanding=to_kobo(s.get("totalOutstanding", 0)), arrears=to_kobo(s.get("totalOverdue", 0)),
                    days_late=(bank_today() - since).days if since else 0,
                )


def _products() -> list[Product]:
    from repaydesk.synth.names import PRODUCTS

    return PRODUCTS
