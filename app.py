"""Finora - Digital Banking & Loan Processing System.

Flask + Firebase Realtime Database.

Required .env entries (see .env.example):
    FIREBASE_DATABASE_URL=https://YOUR-PROJECT-default-rtdb.REGION.firebasedatabase.app
    FIREBASE_CREDENTIALS=credentials/firebase-service-account.json
    FLASK_SECRET_KEY=a-long-random-string
"""

import os
import re
import secrets
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from dotenv import load_dotenv
from flask import (Flask, flash, redirect, render_template, request, session,
                   url_for)
from werkzeug.exceptions import HTTPException

import graph

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or "dev-only-change-this"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

ACCOUNT_TYPES = ("Savings", "Current", "Salary", "Fixed Deposit")
PAYMENT_MODES = ("Cash", "UPI", "Bank Transfer", "Card")
LOAN_TYPES = ("Personal", "Home", "Education", "Vehicle", "Business")
HISTORY_LIMIT = 100

# --------------------------------------------------------------------------
# Firebase initialisation (lazy-friendly: a bad config shows a setup page
# instead of crashing the whole server)
# --------------------------------------------------------------------------
FIREBASE_ERROR = None
db = None


def _initialize_firebase():
    """Initialise the Admin SDK once. Returns an error message or None."""
    global db
    try:
        import firebase_admin
        from firebase_admin import credentials
        from firebase_admin import db as firebase_db
    except ImportError:
        return ("Python packages are missing. Run: pip install -r requirements.txt")

    database_url = os.getenv("FIREBASE_DATABASE_URL", "").strip()
    credentials_path = os.getenv(
        "FIREBASE_CREDENTIALS", "credentials/firebase-service-account.json"
    ).strip()

    if not database_url:
        return "FIREBASE_DATABASE_URL is missing from your .env file."
    if not os.path.isfile(credentials_path):
        return (f"Firebase service-account JSON not found at '{credentials_path}'. "
                "Check FIREBASE_CREDENTIALS in your .env file.")
    try:
        if not firebase_admin._apps:
            firebase_admin.initialize_app(
                credentials.Certificate(credentials_path),
                {"databaseURL": database_url},
            )
        db = firebase_db
    except Exception as error:  # invalid JSON, bad key, bad URL...
        return f"Firebase could not be initialised: {error}"
    return None


FIREBASE_ERROR = _initialize_firebase()


# --------------------------------------------------------------------------
# Data helpers
# --------------------------------------------------------------------------
def ref(path="/"):
    return db.reference(path)


def _items(value):
    """Realtime Database returns a *list* (not a dict) when keys are small
    sequential integers such as 1, 2, 3. Handle both shapes."""
    if isinstance(value, dict):
        return list(value.items())
    if isinstance(value, list):
        return [(str(i), v) for i, v in enumerate(value)]
    return []


def get_records(collection):
    records = []
    for key, record in _items(ref(collection).get()):
        if isinstance(record, dict):
            item = dict(record)
            item.pop("_key", None)
            records.append(item)
    return records


def get_record(collection, record_id, id_field):
    wanted = str(record_id)
    direct = ref(f"{collection}/{wanted}").get()
    if isinstance(direct, dict):
        return dict(direct)
    for item in get_records(collection):
        if str(item.get(id_field)) == wanted:
            return item
    return None


def next_id(collection):
    return int(ref(f"_counters/{collection}").transaction(lambda c: int(c or 0) + 1))


def add_record(collection, id_field, record):
    record_id = next_id(collection)
    payload = dict(record)
    payload[id_field] = record_id
    ref(f"{collection}/{record_id}").set(payload)
    graph.mirror(collection, payload)  # best-effort copy into Neo4j
    return payload


def update_record(collection, record_id, updates):
    path = f"{collection}/{record_id}"
    if not isinstance(ref(path).get(), dict):
        return False
    ref(path).update(updates)
    graph.mirror(collection, ref(path).get())
    return True


def as_float(value, default=0.0):
    try:
        return float(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return float(default)


def as_int(value, default=0):
    try:
        return int(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return int(default)


def to_decimal(value):
    return Decimal(str(value if value not in (None, "") else "0"))


def parse_amount(text, label="amount"):
    """Parse a positive money value with at most 2 decimals."""
    try:
        amount = to_decimal(text)
    except InvalidOperation:
        raise ValueError(f"Enter a valid {label}.")
    if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2:
        raise ValueError(f"Enter a valid {label} greater than zero (max 2 decimal places).")
    return amount


def now_iso():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def index_by(records, field):
    return {as_int(r.get(field)): r for r in records}


def sort_desc(records, field):
    return sorted(records, key=lambda r: as_int(r.get(field)), reverse=True)


def balances_for(transactions):
    """Single pass: {AccountNo: Decimal balance}."""
    balances = {}
    for t in transactions:
        no = as_int(t.get("AccountNo"))
        amount = to_decimal(t.get("Amount"))
        kind = t.get("TransactionType")
        if kind == "Deposit":
            balances[no] = balances.get(no, Decimal("0")) + amount
        elif kind == "Withdrawal":
            balances[no] = balances.get(no, Decimal("0")) - amount
    return balances


def loan_position(loan, payments):
    """Total due / paid / outstanding for one loan.

    Keeps the original academic-demo model: principal + simple interest
    (rate % applied once to the sanctioned amount)."""
    principal = to_decimal(loan.get("SanctionAmount"))
    rate = to_decimal(loan.get("InterestRate"))
    total_due = principal * (Decimal("1") + rate / Decimal("100"))
    total_paid = sum((to_decimal(p.get("AmountPaid")) for p in payments), Decimal("0"))
    outstanding = max(Decimal("0"), total_due - total_paid)
    percent = int(min(100, (total_paid / total_due * 100))) if total_due > 0 else 0
    return {"principal": principal, "total_due": total_due,
            "total_paid": total_paid, "outstanding": outstanding, "percent": percent}


# --------------------------------------------------------------------------
# Template filters / context
# --------------------------------------------------------------------------
@app.template_filter("inr")
def inr(value):
    """Format as Indian Rupees with lakh/crore grouping: ₹12,34,567.00"""
    try:
        amount = Decimal(str(value if value not in (None, "") else 0))
    except InvalidOperation:
        amount = Decimal("0")
    sign = "-" if amount < 0 else ""
    whole, frac = f"{abs(amount):.2f}".split(".")
    if len(whole) > 3:
        head, tail, parts = whole[:-3], whole[-3:], []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return f"{sign}₹{whole}.{frac}"


@app.template_filter("dt")
def format_datetime(value):
    if not value:
        return "—"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", ""))
        return parsed.strftime("%d %b %Y, %H:%M")
    except ValueError:
        return str(value)


@app.template_filter("d")
def format_date(value):
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(str(value).replace("Z", "")).strftime("%d %b %Y")
    except ValueError:
        return str(value)


@app.template_filter("badge")
def badge_class(status):
    return {
        "Active": "ok", "Approved": "ok", "Paid": "ok", "Deposit": "ok", "Connected": "ok",
        "Pending": "warn", "Withdrawal": "bad", "Rejected": "bad", "Closed": "muted",
    }.get(str(status), "muted")


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token  # visible inside imported macros too


@app.context_processor
def inject_globals():
    return {"today": date.today().isoformat(), "current_year": date.today().year}


@app.before_request
def guard_request():
    if request.endpoint == "static":
        return None
    if FIREBASE_ERROR:
        return render_template("setup.html", error=FIREBASE_ERROR), 503
    if request.method == "POST":
        sent = request.form.get("_csrf", "")
        if not sent or not secrets.compare_digest(sent, session.get("_csrf", "")):
            flash("Your session expired. Please try again.", "error")
            return redirect(request.referrer or url_for("home"))
    return None


@app.after_request
def security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?[0-9]{7,15}$")
PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
AADHAAR_RE = re.compile(r"^[0-9]{12}$")
IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")


def clean_customer_form(form):
    """Return (values, error)."""
    values = {
        "FullName": form.get("FullName", "").strip(),
        "DateOfBirth": form.get("DateOfBirth", "").strip(),
        "Gender": form.get("Gender", "").strip(),
        "Address": form.get("Address", "").strip(),
        "PhoneNo": re.sub(r"[\s-]", "", form.get("PhoneNo", "")),
        "Email": form.get("Email", "").strip().lower(),
        "AadhaarNo": re.sub(r"[\s-]", "", form.get("AadhaarNo", "")),
        "PANNo": form.get("PANNo", "").strip().upper(),
        "MaritalStatus": form.get("MaritalStatus", "").strip(),
    }
    if not values["FullName"] or not values["PhoneNo"] or not values["Email"]:
        return values, "Name, phone number and email are required."
    if not EMAIL_RE.match(values["Email"]):
        return values, "Enter a valid email address."
    if not PHONE_RE.match(values["PhoneNo"]):
        return values, "Enter a valid phone number (7–15 digits, optional leading +)."
    if values["AadhaarNo"] and not AADHAAR_RE.match(values["AadhaarNo"]):
        return values, "Aadhaar number must be exactly 12 digits."
    if values["PANNo"] and not PAN_RE.match(values["PANNo"]):
        return values, "PAN must look like ABCDE1234F."
    if values["DateOfBirth"]:
        try:
            if date.fromisoformat(values["DateOfBirth"]) > date.today():
                return values, "Date of birth cannot be in the future."
        except ValueError:
            return values, "Enter a valid date of birth."
    return values, None


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@app.route("/")
def home():
    summary = {"customers": 0, "accounts": 0, "branches": 0}
    db_status = "Connected"
    try:
        summary["customers"] = len(get_records("customers"))
        summary["accounts"] = len(get_records("accounts"))
        summary["branches"] = len(get_records("bank_branches"))
    except Exception as error:
        app.logger.warning("Firebase unreachable (%s). Check your internet/DNS: curl -I https://oauth2.googleapis.com",
                           type(error).__name__)
        db_status = "Not connected"
    return render_template("index.html", db_status=db_status, summary=summary)


@app.route("/dashboard")
def dashboard():
    stats = {"customers": 0, "accounts": 0, "active_accounts": 0, "transactions": 0,
             "deposits": 0.0, "withdrawals": 0.0, "loans": 0.0, "outstanding": 0.0,
             "pending_applications": 0, "branches": 0}
    recent_transactions, recent_applications = [], []
    try:
        customers = get_records("customers")
        accounts = get_records("accounts")
        transactions = get_records("transactions")
        loans = get_records("loans")
        applications = get_records("loan_applications")
        payments = get_records("loan_payments")

        stats["customers"] = len(customers)
        stats["accounts"] = len(accounts)
        stats["branches"] = len(get_records("bank_branches"))
        stats["active_accounts"] = sum(1 for a in accounts if a.get("Status") == "Active")
        stats["transactions"] = len(transactions)
        stats["deposits"] = sum(as_float(t.get("Amount")) for t in transactions if t.get("TransactionType") == "Deposit")
        stats["withdrawals"] = sum(as_float(t.get("Amount")) for t in transactions if t.get("TransactionType") == "Withdrawal")
        stats["loans"] = sum(as_float(l.get("SanctionAmount")) for l in loans if l.get("Status") in ("Active", "Paid"))
        stats["pending_applications"] = sum(1 for a in applications if a.get("Status") == "Pending")

        by_loan = {}
        for p in payments:
            by_loan.setdefault(as_int(p.get("LoanID")), []).append(p)
        stats["outstanding"] = float(sum(
            (loan_position(l, by_loan.get(as_int(l.get("LoanID")), []))["outstanding"]
             for l in loans if l.get("Status") == "Active"), Decimal("0")))

        customer_map = index_by(customers, "CustomerID")
        recent_transactions = sort_desc(transactions, "TransactionID")[:6]
        recent_applications = sort_desc(applications, "ApplicationID")[:6]
        for a in recent_applications:
            a["FullName"] = customer_map.get(as_int(a.get("CustomerID")), {}).get("FullName", "Unknown customer")
    except Exception:
        app.logger.exception("Dashboard Firebase error")
        flash("Some dashboard data could not be loaded. Check the server log for details.", "error")

    flow_total = stats["deposits"] + stats["withdrawals"]
    stats["deposit_pct"] = round(stats["deposits"] / flow_total * 100) if flow_total else 0
    stats["withdrawal_pct"] = 100 - stats["deposit_pct"] if flow_total else 0
    return render_template("dashboard.html", stats=stats,
                           recent_transactions=recent_transactions,
                           recent_applications=recent_applications)


@app.route("/customers", methods=["GET", "POST"])
def customers():
    form_values = {}
    if request.method == "POST":
        values, error = clean_customer_form(request.form)
        if not error:
            existing = get_records("customers")
            labels = {"PhoneNo": "phone number", "Email": "email", "AadhaarNo": "Aadhaar number", "PANNo": "PAN"}
            for field, label in labels.items():
                value = values[field]
                if value and any(str(c.get(field, "")).casefold() == value.casefold() for c in existing):
                    error = f"A customer with that {label} already exists."
                    break
        if error:
            flash(error, "error")
            form_values = values
        else:
            try:
                created = add_record("customers", "CustomerID", {**values, "CreatedAt": now_iso()})
                flash(f"Customer #{created['CustomerID']} ({values['FullName']}) registered successfully.", "success")
                return redirect(url_for("customers"))
            except Exception:
                app.logger.exception("Customer save error")
                flash("Could not save the customer. Check the server log for details.", "error")
                form_values = values

    customer_list = sort_desc(get_records("customers"), "CustomerID")
    return render_template("customers.html", customers=customer_list, form=form_values)


@app.route("/branches", methods=["GET", "POST"])
def branches():
    form_values = {}
    if request.method == "POST":
        values = {
            "BranchName": request.form.get("BranchName", "").strip(),
            "Address": request.form.get("Address", "").strip(),
            "City": request.form.get("City", "").strip(),
            "IFSCCode": request.form.get("IFSCCode", "").strip().upper(),
            "PhoneNo": re.sub(r"[\s-]", "", request.form.get("PhoneNo", "")),
        }
        error = None
        if not values["BranchName"] or not values["City"] or not values["IFSCCode"]:
            error = "Branch name, city and IFSC code are required."
        elif not IFSC_RE.match(values["IFSCCode"]):
            error = "IFSC must be 11 characters: 4 letters, a 0, then 6 letters/digits (e.g. DEMO0000001)."
        elif values["PhoneNo"] and not PHONE_RE.match(values["PhoneNo"]):
            error = "Enter a valid branch phone number."
        elif any(str(b.get("IFSCCode", "")).upper() == values["IFSCCode"] for b in get_records("bank_branches")):
            error = "A branch with that IFSC code already exists."
        if error:
            flash(error, "error")
            form_values = values
        else:
            try:
                created = add_record("bank_branches", "BranchID", values)
                flash(f"Branch “{values['BranchName']}” added (ID {created['BranchID']}).", "success")
                return redirect(url_for("branches"))
            except Exception:
                app.logger.exception("Branch save error")
                flash("Could not save the branch. Check the server log for details.", "error")
                form_values = values

    branch_list = sorted(get_records("bank_branches"), key=lambda b: as_int(b.get("BranchID")))
    counts = {}
    for a in get_records("accounts"):
        counts[as_int(a.get("BranchID"))] = counts.get(as_int(a.get("BranchID")), 0) + 1
    for b in branch_list:
        b["AccountCount"] = counts.get(as_int(b.get("BranchID")), 0)
    return render_template("branches.html", branches=branch_list, form=form_values)


@app.route("/accounts", methods=["GET", "POST"])
def accounts():
    if request.method == "POST":
        customer_id = request.form.get("CustomerID", "").strip()
        branch_id = request.form.get("BranchID", "").strip()
        account_type = request.form.get("AccountType", "").strip()
        if not customer_id or not branch_id or account_type not in ACCOUNT_TYPES:
            flash("Please complete all required account fields.", "error")
        elif not get_record("customers", customer_id, "CustomerID"):
            flash("The selected customer does not exist.", "error")
        elif not get_record("bank_branches", branch_id, "BranchID"):
            flash("The selected branch does not exist.", "error")
        else:
            try:
                created = add_record("accounts", "AccountNo", {
                    "CustomerID": as_int(customer_id), "BranchID": as_int(branch_id),
                    "AccountType": account_type, "OpenDate": date.today().isoformat(),
                    "Status": "Active",
                })
                flash(f"Account {created['AccountNo']} created successfully.", "success")
            except Exception:
                app.logger.exception("Account save error")
                flash("Could not create the account. Check the server log.", "error")
        return redirect(url_for("accounts"))

    customer_list = sorted(get_records("customers"), key=lambda c: str(c.get("FullName", "")).casefold())
    branch_list = sorted(get_records("bank_branches"), key=lambda b: str(b.get("BranchName", "")).casefold())
    customer_map, branch_map = index_by(customer_list, "CustomerID"), index_by(branch_list, "BranchID")
    balances = balances_for(get_records("transactions"))
    account_list = sort_desc(get_records("accounts"), "AccountNo")
    for a in account_list:
        a["FullName"] = customer_map.get(as_int(a.get("CustomerID")), {}).get("FullName", "Unknown customer")
        branch = branch_map.get(as_int(a.get("BranchID")), {})
        a["BranchName"] = branch.get("BranchName", "Unknown branch")
        a["IFSCCode"] = branch.get("IFSCCode", "—")
        a["Balance"] = float(balances.get(as_int(a.get("AccountNo")), Decimal("0")))
    return render_template("accounts.html", customers=customer_list, branches=branch_list,
                           accounts=account_list,
                           pre_customer=request.args.get("customer", ""),
                           pre_branch=request.args.get("branch", ""))


@app.route("/transactions", methods=["GET", "POST"])
def transactions():
    if request.method == "POST":
        transaction_type = request.form.get("TransactionType", "").strip()
        mode = request.form.get("Mode", "").strip()
        description = request.form.get("Description", "").strip()[:255]
        try:
            if transaction_type not in ("Deposit", "Withdrawal"):
                raise ValueError("Please select Deposit or Withdrawal.")
            if mode not in PAYMENT_MODES:
                raise ValueError("Please select a valid payment mode.")
            amount = parse_amount(request.form.get("Amount", ""))
            try:
                account_no = int(request.form.get("AccountNo", ""))
            except ValueError:
                raise ValueError("Please select a valid account.")
            account = get_record("accounts", account_no, "AccountNo")
            if not account or account.get("Status") != "Active":
                raise ValueError("The account does not exist or is not active.")
            balance = balances_for([t for t in get_records("transactions")
                                    if as_int(t.get("AccountNo")) == account_no]).get(account_no, Decimal("0"))
            if transaction_type == "Withdrawal" and amount > balance:
                raise ValueError(f"Insufficient balance. Available balance: {inr(balance)}")
            add_record("transactions", "TransactionID", {
                "AccountNo": account_no,
                "TransactionDate": datetime.now().isoformat(timespec="seconds"),
                "TransactionType": transaction_type, "Amount": float(amount),
                "Mode": mode, "Description": description,
            })
            flash(f"{transaction_type} of {inr(amount)} recorded for account {account_no}.", "success")
        except ValueError as error:
            flash(str(error), "error")
        except Exception:
            app.logger.exception("Transaction save error")
            flash("Could not record the transaction. Check the server log.", "error")
        return redirect(url_for("transactions"))

    all_transactions = get_records("transactions")
    balances = balances_for(all_transactions)
    customer_map = index_by(get_records("customers"), "CustomerID")
    account_list = [a for a in get_records("accounts") if a.get("Status") == "Active"]
    account_list.sort(key=lambda a: as_int(a.get("AccountNo")))
    for a in account_list:
        a["Balance"] = float(balances.get(as_int(a.get("AccountNo")), Decimal("0")))
        a["FullName"] = customer_map.get(as_int(a.get("CustomerID")), {}).get("FullName", "Unknown customer")
    history = sort_desc(all_transactions, "TransactionID")
    return render_template("transactions.html", accounts=account_list,
                           transactions=history[:HISTORY_LIMIT], total_transactions=len(history),
                           limit=HISTORY_LIMIT, pre_account=request.args.get("account", ""))


@app.route("/loans", methods=["GET", "POST"])
def loans():
    if request.method == "POST":
        action = request.form.get("action", "").strip()
        try:
            if action == "apply":
                customer_id = as_int(request.form.get("CustomerID"))
                loan_type = request.form.get("LoanType", "").strip()
                purpose = request.form.get("Purpose", "").strip()[:255]
                amount = parse_amount(request.form.get("RequestedAmount"), "requested amount")
                if customer_id <= 0 or loan_type not in LOAN_TYPES:
                    raise ValueError("Enter valid loan application details.")
                if not get_record("customers", customer_id, "CustomerID"):
                    raise ValueError("Please select an existing customer.")
                created = add_record("loan_applications", "ApplicationID", {
                    "CustomerID": customer_id, "ApplicationDate": date.today().isoformat(),
                    "LoanType": loan_type, "RequestedAmount": float(amount),
                    "Purpose": purpose, "Status": "Pending",
                })
                flash(f"Loan application #{created['ApplicationID']} submitted for review.", "success")

            elif action in ("approve", "reject"):
                application_id = as_int(request.form.get("ApplicationID"))
                application = get_record("loan_applications", application_id, "ApplicationID")
                if not application:
                    raise ValueError("Loan application not found.")
                if application.get("Status") != "Pending":
                    raise ValueError("This application has already been processed.")
                if action == "reject":
                    update_record("loan_applications", application_id, {"Status": "Rejected"})
                    flash(f"Application #{application_id} rejected.", "success")
                else:
                    rate = to_decimal(request.form.get("InterestRate", "10"))
                    tenure = as_int(request.form.get("TenureMonths", "12"))
                    if (not rate.is_finite() or rate < 0 or rate > 100
                            or rate.as_tuple().exponent < -2 or not 1 <= tenure <= 360):
                        raise ValueError("Enter a valid interest rate (0–100%) and tenure (1–360 months).")
                    loan = add_record("loans", "LoanID", {
                        "ApplicationID": application_id, "LoanType": application.get("LoanType"),
                        "SanctionDate": date.today().isoformat(),
                        "SanctionAmount": as_float(application.get("RequestedAmount")),
                        "InterestRate": float(rate), "TenureMonths": tenure, "Status": "Active",
                    })
                    update_record("loan_applications", application_id,
                                  {"Status": "Approved", "LoanID": loan["LoanID"]})
                    flash(f"Application #{application_id} approved — loan #{loan['LoanID']} created.", "success")

            elif action == "repay":
                loan_id = as_int(request.form.get("LoanID"))
                amount_paid = parse_amount(request.form.get("AmountPaid"), "repayment amount")
                payment_mode = request.form.get("PaymentMode", "").strip()
                if payment_mode not in PAYMENT_MODES:
                    raise ValueError("Select a valid payment mode.")
                loan = get_record("loans", loan_id, "LoanID")
                if not loan or loan.get("Status") != "Active":
                    raise ValueError("Active loan not found.")
                existing = [p for p in get_records("loan_payments") if as_int(p.get("LoanID")) == loan_id]
                position = loan_position(loan, existing)
                if amount_paid > position["outstanding"]:
                    raise ValueError(f"Repayment exceeds the outstanding amount of {inr(position['outstanding'])}.")
                principal_remaining = max(Decimal("0"), position["principal"] - position["total_paid"])
                principal_paid = min(amount_paid, principal_remaining)
                new_outstanding = position["outstanding"] - amount_paid
                add_record("loan_payments", "PaymentID", {
                    "LoanID": loan_id, "PaymentDate": date.today().isoformat(),
                    "InstallmentNo": len(existing) + 1,
                    "AmountPaid": float(amount_paid), "PrincipalAmount": float(principal_paid),
                    "InterestAmount": float(amount_paid - principal_paid),
                    "PaymentMode": payment_mode, "BalanceOutstanding": float(new_outstanding),
                })
                if new_outstanding == 0:
                    update_record("loans", loan_id, {"Status": "Paid"})
                    flash(f"Loan #{loan_id} is now fully repaid.", "success")
                else:
                    flash(f"Repayment of {inr(amount_paid)} recorded. Outstanding: {inr(new_outstanding)}.", "success")
            else:
                raise ValueError("Unknown loan action.")
        except (ValueError, InvalidOperation) as error:
            flash(str(error) or "Please check the entered values.", "error")
        except Exception:
            app.logger.exception("Loan processing error")
            flash("Could not process the loan request. Check the server log.", "error")
        return redirect(url_for("loans"))

    customer_list = sorted(get_records("customers"), key=lambda c: str(c.get("FullName", "")).casefold())
    customer_map = index_by(customer_list, "CustomerID")
    applications = sort_desc(get_records("loan_applications"), "ApplicationID")
    application_map = index_by(applications, "ApplicationID")
    for a in applications:
        a["FullName"] = customer_map.get(as_int(a.get("CustomerID")), {}).get("FullName", "Unknown customer")
    payments = sort_desc(get_records("loan_payments"), "PaymentID")
    by_loan = {}
    for p in payments:
        by_loan.setdefault(as_int(p.get("LoanID")), []).append(p)
    loan_list = sort_desc(get_records("loans"), "LoanID")
    for l in loan_list:
        app_row = application_map.get(as_int(l.get("ApplicationID")), {})
        l["FullName"] = customer_map.get(as_int(app_row.get("CustomerID")), {}).get("FullName", "Unknown customer")
        pos = loan_position(l, by_loan.get(as_int(l.get("LoanID")), []))
        l.update({"TotalPaid": float(pos["total_paid"]), "TotalDue": float(pos["total_due"]),
                  "Outstanding": float(pos["outstanding"]), "Percent": pos["percent"]})
    return render_template("loans.html", customers=customer_list, applications=applications,
                           loans=loan_list, payments=payments, loan_types=LOAN_TYPES,
                           pre_customer=request.args.get("customer", ""))


# --------------------------------------------------------------------------
# Neo4j graph view
# --------------------------------------------------------------------------
@app.route("/graph")
def graph_page():
    state, message = graph.status()
    ctx = {"state": state, "message": message, "node_counts": [], "rel_counts": [],
           "customers": [], "branch_rows": []}
    if state == "ok":
        try:
            ctx["node_counts"], ctx["rel_counts"] = graph.counts()
            ctx["customers"] = graph.customer_network()
            ctx["branch_rows"] = graph.branch_activity()
        except Exception as error:
            app.logger.exception("Neo4j read error")
            ctx["state"], ctx["message"] = "error", f"Neo4j query failed: {error}"
    return render_template("graph.html", **ctx)


@app.route("/graph/data")
def graph_data():
    try:
        return graph.graph_data()
    except Exception as error:
        app.logger.exception("Neo4j graph data error")
        return {"error": str(error), "nodes": [], "edges": []}, 503


@app.route("/graph/sync", methods=["POST"])
def graph_sync():
    try:
        synced, errors = graph.sync_all(get_records)
        if errors:
            flash(f"Synced {synced} records to Neo4j; {errors} failed (see server log).", "error")
        else:
            flash(f"Synced {synced} records from Firebase to Neo4j.", "success")
    except Exception as error:
        app.logger.exception("Neo4j sync error")
        flash(f"Neo4j sync failed: {error}", "error")
    return redirect(url_for("graph_page"))


@app.route("/health")
def health():
    """Read-only connectivity check (the old route wrote to the database on GET)."""
    try:
        ref("_counters").get()
        return {"status": "ok", "database": "connected"}
    except Exception as error:
        app.logger.exception("Health check failed")
        return {"status": "error", "database": str(error)}, 503


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------
@app.errorhandler(HTTPException)
def http_error(error):
    return render_template("error.html", code=error.code, title=error.name,
                           message=error.description), error.code


@app.errorhandler(Exception)
def unexpected_error(error):
    app.logger.exception("Unhandled error")
    return render_template("error.html", code=500, title="Something went wrong",
                           message="An unexpected error occurred. Please try again."), 500


# --------------------------------------------------------------------------
# CLI:  flask --app app seed-demo
# --------------------------------------------------------------------------
@app.cli.command("seed-demo")
def seed_demo():
    """Add a fictional demo branch if none exists."""
    if FIREBASE_ERROR:
        raise SystemExit(FIREBASE_ERROR)
    if get_records("bank_branches"):
        print("A branch already exists - nothing to do.")
        return
    created = add_record("bank_branches", "BranchID", {
        "BranchName": "Demo Main Branch", "Address": "10 Sample Road", "City": "Vellore",
        "IFSCCode": "DEMO0000001", "PhoneNo": "9000000001"})
    print(f"Created demo branch #{created['BranchID']}.")


@app.cli.command("sync-graph")
def sync_graph():
    """Copy all Firebase data into Neo4j:  flask --app app sync-graph"""
    if FIREBASE_ERROR:
        raise SystemExit(FIREBASE_ERROR)
    synced, errors = graph.sync_all(get_records)
    print(f"Synced {synced} records to Neo4j ({errors} errors).")


if __name__ == "__main__":
    # Port 5000 is taken by AirPlay Receiver on macOS, so default to 5001.
    app.run(port=int(os.getenv("PORT", "5001")), debug=os.getenv("FLASK_DEBUG", "0") == "1")