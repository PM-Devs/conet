#!/usr/bin/env python3
import asyncio
import uvicorn

from conet.dashboard.services import build_services
from conet.dashboard.app import create_dashboard_app


def main() -> None:
    services = build_services()
    # Ensure the users DB and tables exist before serving the dashboard
    asyncio.run(services.auth.create_db_and_tables())

    app = create_dashboard_app(services)
    print("Starting CoNET dashboard on http://0.0.0.0:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
