# Finora — Digital Banking & Loan Processing System

Flask + Firebase Realtime Database (system of record) + Neo4j (graph mirror). Modules: Customers, Branches, Accounts, Transactions, Loans, Dashboard, Graph.

## Run locally
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                    # then edit the values
# put your Firebase service-account key at credentials/firebase-service-account.json
flask --app app seed-demo                               # optional: adds a demo branch
flask --app app run
```
Open http://127.0.0.1:5000. If Firebase isn't configured, a setup page explains what's missing.
Set `FLASK_DEBUG=1` in `.env` only for local development.

## Suggested order
Branches → Customers → Accounts → Transactions → Loans.

## Production
`gunicorn app:app` (set a strong `FLASK_SECRET_KEY`). Never commit `.env` or `credentials/`.

## Notes
- `docs/` holds the legacy MySQL schema and an unrelated Neo4j test script from the original project.
- Loan model is unchanged from the original: total due = principal + rate% applied once.

## Neo4j graph (optional)
Firebase stays the main database; every write is also mirrored into Neo4j as nodes/relationships.
1. Create a free Neo4j Aura instance (or run Neo4j locally) and fill the `NEO4J_*` values in `.env`.
2. `python check_neo4j.py` — verifies the connection.
3. Click **Sync from Firebase** on the Graph page (or `flask --app app sync-graph`) to copy existing data. New records sync automatically.

Model: `(Customer)-[:OWNS]->(Account)-[:HELD_AT]->(Branch)`, `(Account)-[:HAS_TRANSACTION]->(Transaction)`,
`(Customer)-[:APPLIED_FOR]->(LoanApplication)-[:SANCTIONED_AS]->(Loan)-[:REPAID_BY]->(LoanPayment)`.
Aadhaar, PAN and date of birth are intentionally not copied to Neo4j. If Neo4j is down the app keeps working;
run a sync afterwards to catch up.
