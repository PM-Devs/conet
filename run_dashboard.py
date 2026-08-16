#!/usr/bin/env python3
import asyncio
import uvicorn

from conet.dashboard.services import build_services
from conet.dashboard.app import create_dashboard_app


def main() -> None:
    services = build_services()
    # Ensure the users DB and tables exist before serving the dashboard
    try:
        asyncio.run(services.auth.create_db_and_tables())
        print(f"Users DB initialized: {getattr(services.auth.engine.url, 'database', services.auth.engine.url)}")
    except Exception as exc:
        print("Failed to initialize users DB:", exc)

    # Print resolved DB paths to help debug misconfiguration
    try:
        print(f"Control DB path: {services.store._db_path}")
    except Exception:
        print("Control DB path: <unknown>")
    try:
        print(f"Users DB URL: {services.auth.engine.url}")
        # Detect single-DB mode from env so operators know both services use same file
        import os
        if os.environ.get('CONET_SINGLE_DB') == '1':
            print('Warning: running in SINGLE-DB mode (CONET_SINGLE_DB=1). Users and control data share the same SQLite file.')
    except Exception:
        print("Users DB URL: <unknown>")

    app = create_dashboard_app(services)
    print("Starting CoNET dashboard on http://0.0.0.0:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
