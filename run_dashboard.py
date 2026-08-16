#!/usr/bin/env python3
import asyncio
import uvicorn

from conet.dashboard.services import build_services
from conet.dashboard.app import create_dashboard_app


import argparse
import asyncio
import os
import uvicorn

from conet.dashboard.services import build_services
from conet.dashboard.app import create_dashboard_app


def main() -> None:
    parser = argparse.ArgumentParser(description='Run CoNET dashboard')
    parser.add_argument('--db-path', help='Control DB path (sqlite file)', default=os.environ.get('CONET_DB_PATH', 'conet.db'))
    parser.add_argument('--users-db-path', help='Users DB path (sqlite file). If omitted and not using --single-db, users DB will not be created.', default=os.environ.get('CONET_USERS_DB_PATH'))
    parser.add_argument('--single-db', dest='single_db', action='store_true', help='Use a single DB for control + users (overrides users-db-path)')
    parser.add_argument('--no-single-db', dest='single_db', action='store_false', help='Use separate control and users DBs')
    # Default to single-DB for local/demo runs unless explicitly disabled
    if os.environ.get('CONET_SINGLE_DB') is None:
        default_single = True
    else:
        default_single = (os.environ.get('CONET_SINGLE_DB') == '1')
    parser.set_defaults(single_db=default_single)
    parser.add_argument('--port', type=int, default=int(os.environ.get('CONET_PORT', '8000')), help='Port to serve the dashboard on')
    parser.add_argument('--init-db', dest='init_db', action='store_true', help='Create DB tables for users (runs migrations).')
    parser.set_defaults(init_db=False)
    parser.add_argument('--dry-run', dest='dry_run', action='store_true', help='Print planned DB actions without creating files or starting the server.')
    parser.set_defaults(dry_run=False)
    args = parser.parse_args()

    # Export CONET_SINGLE_DB so other components reading env see consistent mode
    os.environ['CONET_SINGLE_DB'] = '1' if args.single_db else '0'

    services = build_services(db_path=args.db_path, users_db_path=(args.users_db_path if not args.single_db else None), use_single_db=args.single_db)

    # If dry-run requested, print planned DB actions and exit without mutating files
    if args.dry_run:
        print("DRY-RUN: Planned actions:")
        try:
            print(f"- Control DB file: {args.db_path}")
        except Exception:
            print("- Control DB file: <unknown>")
        users_db_display = args.db_path if args.single_db else (args.users_db_path or '<not set>')
        print(f"- Users DB (would be): {users_db_display} (single-db={args.single_db})")
        if args.init_db:
            print("- Would initialize users DB tables (pass --init-db to actually run)")
        else:
            print("- Would skip users DB initialization (no --init-db passed)")
        print("Dry-run complete. Exiting without starting server.")
        return

    # Optionally initialize the users DB/tables if requested. This avoids
    # unexpected side-effects (creating files) when operators only want to
    # run the server without mutating storage.
    if args.init_db:
        try:
            asyncio.run(services.auth.create_db_and_tables())
            print(f"Users DB initialized: {getattr(services.auth.engine.url, 'database', services.auth.engine.url)}")
        except Exception as exc:
            print("Failed to initialize users DB:", exc)
    else:
        print("Skipping users DB initialization (pass --init-db to create tables)")

    # Print resolved DB paths to help debug misconfiguration
    try:
        print(f"Control DB path: {services.store._db_path}")
    except Exception:
        print("Control DB path: <unknown>")
    try:
        print(f"Users DB URL: {services.auth.engine.url}")
        if args.single_db:
            print('Running in SINGLE-DB mode. Users and control data share the same SQLite file.')
    except Exception:
        print("Users DB URL: <unknown>")

    app = create_dashboard_app(services)
    print(f"Starting CoNET dashboard on http://0.0.0.0:{args.port}")
    uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
