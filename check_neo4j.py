"""Quick connectivity check:  python check_neo4j.py"""
from dotenv import load_dotenv
load_dotenv()
import graph

state, message = graph.status()
print(f"Neo4j: {state} - {message}")
raise SystemExit(0 if state == "ok" else 1)
