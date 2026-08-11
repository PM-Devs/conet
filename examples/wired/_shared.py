# Shared config for the wiring demo -- every script here (three agent
# wrappers + the orchestrator) points at the same control plane, so they
# form one colony instead of three isolated toy setups. Not part of the
# conet package itself, just constants for these example scripts.

DB_PATH = "demo_colony.db"
NATS_URL = "nats://localhost:4222"
POLICY_SECRET = "demo-shared-secret-change-me"  # demo-only, not a real secret
POLICY_PATH = "examples/wired/demo_policy.csv"
