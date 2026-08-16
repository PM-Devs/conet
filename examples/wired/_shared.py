# Shared config for the wiring demo -- every script here (three agent
# wrappers + the orchestrator) points at the same control plane, so they
# form one colony instead of three isolated toy setups. Not part of the
# conet package itself, just constants for these example scripts.

import os

# Allow overriding via environment variables so users can point examples
# at whatever DB they prefer (defaults keep the demo behavior).
DB_PATH = os.environ.get('CONET_DB_PATH', 'demo_colony.db')
NATS_URL = os.environ.get('CONET_NATS_URL', 'nats://localhost:4222')
POLICY_SECRET = os.environ.get('CONET_POLICY_SECRET', 'demo-shared-secret-change-me')  # demo-only
POLICY_PATH = os.environ.get('CONET_POLICY_PATH', 'examples/wired/demo_policy.csv')
