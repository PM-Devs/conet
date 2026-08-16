# Changelog

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

## 0.2.0 - previous
- Initial public prototype release.
