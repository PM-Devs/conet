# Changelog

## 0.2.8 - patch release
- Add auditable dashboard actions for account creation, policy changes,
  integration management, and team administration.
- Make audit-write failures visible instead of silently discarding them.
- Improve diagnostic logging when human-role policy persistence fails.

## 0.2.2 - patch release
- Ensure dashboard user DB schema is initialized at startup so the first
	account can be created (fixes `no such table: user`).
	- Also include `run_dashboard.py` convenience script and tests.

## 0.2.1 - patch release
- Minor fixes and dashboard DB initialization convenience script (`run_dashboard.py`).

## 0.2.3 - patch release
- Publish new artifact after PyPI duplicate-file conflict with 0.2.2;
	bump version and rebuild artifacts. No functional changes beyond
	packaging metadata.

## 0.2.4 - patch release
- Retry publishing with updated packaging metadata and small fixes:
	- Ensure DB initialization and single-DB opt-in are logged at startup.
	- Enable SQLite WAL mode and add busy timeout + useful indexes for
		better dashboard streaming under concurrent load.
	- Persist human role assignments (Casbin policy save) when possible.
	- Update examples to read DB/NATS configuration from environment.

## 0.2.5 - patch release
- Rebuild and republish package artifacts to resolve PyPI file conflicts;
	includes packaging metadata refresh only. No functional changes.

## 0.2.6 - patch release
- Bumped to 0.2.6 to publish a fresh set of artifacts after PyPI
	reported existing matching files for 0.2.5. Includes metadata sync
	(`__version__`) and packaging helper improvements.

## 0.2.0 - previous
- Initial public prototype release.
