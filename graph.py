"""Neo4j graph mirror for Finora.

Firebase Realtime Database stays the system of record. Every write is also
mirrored into Neo4j as nodes and relationships so the data can be explored as
a graph. If Neo4j is not configured or is unreachable, the app keeps working;
failures are logged and `sync_all()` can repair any drift later.

Graph model
    (Customer)-[:OWNS]->(Account)-[:HELD_AT]->(Branch)
    (Account)-[:HAS_TRANSACTION]->(Transaction)
    (Customer)-[:APPLIED_FOR]->(LoanApplication)-[:SANCTIONED_AS]->(Loan)-[:REPAID_BY]->(LoanPayment)

Sensitive identifiers (Aadhaar, PAN, date of birth) are deliberately NOT copied.

Environment:
    NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD, NEO4J_DATABASE (default "neo4j")
"""

import logging
import os
import time

log = logging.getLogger("finora.graph")

_driver = None
_down_until = 0.0          # circuit breaker: skip Neo4j briefly after a failure
_schema_ready = False
_COOLDOWN = 30             # seconds

LABELS = {
    "Customer": "customerId", "Branch": "branchId", "Account": "accountNo",
    "Transaction": "transactionId", "LoanApplication": "applicationId",
    "Loan": "loanId", "LoanPayment": "paymentId",
}

# Cypher fragment: stable "Label:id" key for any node `n`
NODE_ID = ("coalesce(n.customerId, n.branchId, n.accountNo, n.transactionId, "
           "n.applicationId, n.loanId, n.paymentId)")


def settings():
    return {
        "uri": os.getenv("NEO4J_URI", "").strip(),
        "user": os.getenv("NEO4J_USERNAME", "").strip(),
        "password": os.getenv("NEO4J_PASSWORD", "").strip(),
        "database": os.getenv("NEO4J_DATABASE", "neo4j").strip() or "neo4j",
    }


def configured():
    s = settings()
    return bool(s["uri"] and s["user"] and s["password"])


def _get_driver():
    global _driver
    if _driver is None:
        from neo4j import GraphDatabase  # imported lazily so the app runs without the package
        s = settings()
        _driver = GraphDatabase.driver(s["uri"], auth=(s["user"], s["password"]),
                                       connection_timeout=5, max_transaction_retry_time=5)
    return _driver


def _session():
    return _get_driver().session(database=settings()["database"])


def _run(query, **params):
    """Run a write query. Returns True on success; never raises."""
    global _down_until
    if not configured() or time.time() < _down_until:
        return False
    try:
        ensure_schema()
        with _session() as session:
            session.run(query, **params).consume()
        return True
    except Exception as error:
        _down_until = time.time() + _COOLDOWN
        log.warning("Neo4j write skipped: %s", error)
        return False


def read(query, **params):
    """Run a read query and return a list of dicts. Raises on failure."""
    with _session() as session:
        return [record.data() for record in session.run(query, **params)]


def status():
    """('ok' | 'unconfigured' | 'error', message)."""
    if not configured():
        return "unconfigured", "Set NEO4J_URI, NEO4J_USERNAME and NEO4J_PASSWORD in .env."
    try:
        import neo4j  # noqa: F401
    except ImportError:
        return "error", "The neo4j package is not installed. Run: pip install -r requirements.txt"
    try:
        _get_driver().verify_connectivity()
        ensure_schema()
        return "ok", "Connected"
    except Exception as error:
        return "error", f"Could not connect to Neo4j: {error}"


def ensure_schema():
    """Unique constraints (also create indexes). Safe to call repeatedly."""
    global _schema_ready
    if _schema_ready:
        return
    with _session() as session:
        for label, key in LABELS.items():
            session.run(f"CREATE CONSTRAINT {label.lower()}_{key.lower()} IF NOT EXISTS "
                        f"FOR (n:{label}) REQUIRE n.{key} IS UNIQUE").consume()
    _schema_ready = True


def _i(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------------
# Mirror writers: collection name -> (cypher, params-builder)
# --------------------------------------------------------------------------
def _customer(r):
    return ("MERGE (c:Customer {customerId: $id}) "
            "SET c.name = $name, c.email = $email, c.phone = $phone, c.gender = $gender, "
            "c.maritalStatus = $marital, c.createdAt = $created",
            dict(id=_i(r.get("CustomerID")), name=r.get("FullName"), email=r.get("Email"),
                 phone=r.get("PhoneNo"), gender=r.get("Gender") or None,
                 marital=r.get("MaritalStatus") or None, created=r.get("CreatedAt")))


def _branch(r):
    return ("MERGE (b:Branch {branchId: $id}) "
            "SET b.name = $name, b.city = $city, b.ifsc = $ifsc, b.address = $address, b.phone = $phone",
            dict(id=_i(r.get("BranchID")), name=r.get("BranchName"), city=r.get("City"),
                 ifsc=r.get("IFSCCode"), address=r.get("Address"), phone=r.get("PhoneNo")))


def _account(r):
    return ("MERGE (a:Account {accountNo: $id}) "
            "SET a.type = $type, a.status = $status, a.openDate = $open "
            "MERGE (c:Customer {customerId: $cid}) MERGE (b:Branch {branchId: $bid}) "
            "MERGE (c)-[:OWNS]->(a) MERGE (a)-[:HELD_AT]->(b)",
            dict(id=_i(r.get("AccountNo")), type=r.get("AccountType"), status=r.get("Status"),
                 open=r.get("OpenDate"), cid=_i(r.get("CustomerID")), bid=_i(r.get("BranchID"))))


def _transaction(r):
    return ("MERGE (t:Transaction {transactionId: $id}) "
            "SET t.type = $type, t.amount = $amount, t.mode = $mode, t.date = $date, t.description = $desc "
            "MERGE (a:Account {accountNo: $ano}) MERGE (a)-[:HAS_TRANSACTION]->(t)",
            dict(id=_i(r.get("TransactionID")), type=r.get("TransactionType"), amount=_f(r.get("Amount")),
                 mode=r.get("Mode"), date=r.get("TransactionDate"), desc=r.get("Description") or None,
                 ano=_i(r.get("AccountNo"))))


def _application(r):
    return ("MERGE (l:LoanApplication {applicationId: $id}) "
            "SET l.loanType = $type, l.requested = $amount, l.status = $status, "
            "l.date = $date, l.purpose = $purpose "
            "MERGE (c:Customer {customerId: $cid}) MERGE (c)-[:APPLIED_FOR]->(l)",
            dict(id=_i(r.get("ApplicationID")), type=r.get("LoanType"), amount=_f(r.get("RequestedAmount")),
                 status=r.get("Status"), date=r.get("ApplicationDate"), purpose=r.get("Purpose") or None,
                 cid=_i(r.get("CustomerID"))))


def _loan(r):
    return ("MERGE (l:Loan {loanId: $id}) "
            "SET l.loanType = $type, l.sanctioned = $amount, l.rate = $rate, "
            "l.tenureMonths = $tenure, l.status = $status, l.sanctionDate = $date "
            "MERGE (a:LoanApplication {applicationId: $aid}) MERGE (a)-[:SANCTIONED_AS]->(l)",
            dict(id=_i(r.get("LoanID")), type=r.get("LoanType"), amount=_f(r.get("SanctionAmount")),
                 rate=_f(r.get("InterestRate")), tenure=_i(r.get("TenureMonths")), status=r.get("Status"),
                 date=r.get("SanctionDate"), aid=_i(r.get("ApplicationID"))))


def _payment(r):
    return ("MERGE (p:LoanPayment {paymentId: $id}) "
            "SET p.amount = $amount, p.principal = $principal, p.interest = $interest, "
            "p.mode = $mode, p.date = $date, p.installmentNo = $inst, p.outstanding = $outstanding "
            "MERGE (l:Loan {loanId: $lid}) MERGE (l)-[:REPAID_BY]->(p)",
            dict(id=_i(r.get("PaymentID")), amount=_f(r.get("AmountPaid")), principal=_f(r.get("PrincipalAmount")),
                 interest=_f(r.get("InterestAmount")), mode=r.get("PaymentMode"), date=r.get("PaymentDate"),
                 inst=_i(r.get("InstallmentNo")), outstanding=_f(r.get("BalanceOutstanding")),
                 lid=_i(r.get("LoanID"))))


WRITERS = {
    "customers": _customer, "bank_branches": _branch, "accounts": _account,
    "transactions": _transaction, "loan_applications": _application,
    "loans": _loan, "loan_payments": _payment,
}
# order matters for sync_all so parents exist first (MERGE would create stubs anyway)
SYNC_ORDER = ["bank_branches", "customers", "accounts", "transactions",
              "loan_applications", "loans", "loan_payments"]


def mirror(collection, record):
    """Mirror one Firebase record into Neo4j. Safe to call from request handlers."""
    writer = WRITERS.get(collection)
    if not writer or not configured():
        return False
    query, params = writer(record)
    return _run(query, **params)


def sync_all(get_records):
    """Re-mirror everything from Firebase. Returns (records_synced, errors)."""
    global _down_until
    if not configured():
        raise RuntimeError("Neo4j is not configured.")
    _down_until = 0.0
    ensure_schema()
    synced = errors = 0
    with _session() as session:
        for collection in SYNC_ORDER:
            for record in get_records(collection):
                query, params = WRITERS[collection](record)
                try:
                    session.run(query, **params).consume()
                    synced += 1
                except Exception as error:
                    errors += 1
                    log.warning("Sync failed for %s: %s", collection, error)
    return synced, errors


# --------------------------------------------------------------------------
# Read side (Graph page)
# --------------------------------------------------------------------------
def counts():
    nodes = read("MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS total ORDER BY total DESC")
    rels = read("MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS total ORDER BY total DESC")
    return nodes, rels


def customer_network():
    return read(
        "MATCH (c:Customer) "
        "OPTIONAL MATCH (c)-[:OWNS]->(a:Account) "
        "WITH c, count(DISTINCT a) AS accounts "
        "OPTIONAL MATCH (c)-[:APPLIED_FOR]->(:LoanApplication)-[:SANCTIONED_AS]->(l:Loan) "
        "RETURN c.customerId AS id, c.name AS name, accounts, count(DISTINCT l) AS loans, "
        "coalesce(sum(l.sanctioned), 0) AS exposure "
        "ORDER BY accounts DESC, loans DESC, name LIMIT 10")


def branch_activity():
    return read(
        "MATCH (b:Branch) "
        "OPTIONAL MATCH (b)<-[:HELD_AT]-(a:Account) "
        "OPTIONAL MATCH (a)-[:HAS_TRANSACTION]->(t:Transaction) "
        "RETURN b.branchId AS id, b.name AS name, b.city AS city, count(DISTINCT a) AS accounts, "
        "count(t) AS transactions, "
        "sum(CASE t.type WHEN 'Deposit' THEN t.amount ELSE 0 END) AS deposits, "
        "sum(CASE t.type WHEN 'Withdrawal' THEN t.amount ELSE 0 END) AS withdrawals "
        "ORDER BY transactions DESC, name")


def _caption(label, props):
    pick = {
        "Customer": props.get("name"), "Branch": props.get("name"),
        "Account": f"Acct {props.get('accountNo')}", "Transaction": f"{props.get('type')} #{props.get('transactionId')}",
        "LoanApplication": f"App #{props.get('applicationId')}", "Loan": f"Loan #{props.get('loanId')}",
        "LoanPayment": f"Pay #{props.get('paymentId')}",
    }
    return pick.get(label) or label


def graph_data(limit=400):
    """Nodes + edges for the visualisation (capped for browser performance)."""
    rows = read(f"MATCH (n) RETURN labels(n)[0] AS label, {NODE_ID} AS id, properties(n) AS props LIMIT $limit",
                limit=limit)
    nodes, keys = [], set()
    for row in rows:
        key = f"{row['label']}:{row['id']}"
        keys.add(key)
        nodes.append({"id": key, "label": _caption(row["label"], row["props"]),
                      "group": row["label"], "title": "<br>".join(f"{k}: {v}" for k, v in row["props"].items())})
    edge_rows = read(
        "MATCH (n)-[r]->(m) "
        f"RETURN labels(n)[0] AS fl, {NODE_ID} AS fid, type(r) AS rel, "
        f"labels(m)[0] AS tl, {NODE_ID.replace('n.', 'm.')} AS tid LIMIT $limit", limit=limit * 2)
    edges = [{"from": f"{r['fl']}:{r['fid']}", "to": f"{r['tl']}:{r['tid']}", "label": r["rel"]}
             for r in edge_rows
             if f"{r['fl']}:{r['fid']}" in keys and f"{r['tl']}:{r['tid']}" in keys]
    return {"nodes": nodes, "edges": edges}
