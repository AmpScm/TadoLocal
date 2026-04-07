# Changelog
All notable changes to this project will be documented in this file.

The format is based on https://keepachangelog.com/en/1.1.0/,
and this project adheres to Semantic Versioning.

---

## [1.1.2] – 2026-04-07
### Added
- Persistent HVAC mode support, the ability to toggle Smart Schedule for heating/cooling (this will trigger an API call). (#43)
- Full Smart AC Control support.
- New `/zones/{id}/set` parameter `heating_mode` (0 = off, 1 = HEAT, 2 = COOL).
- Ability to switch an entire zone on or off with a single API call via `/zones/set/` (as either an overlay or a persistent change).
- Automatic database history purge via CLI and manual purge via API (`/purgehistory/now/`).
- This changelog. (#44)

### Changed
- Improved synchronization logic between HomeKit state and cloud‑derived zone data (HomeKit device types now take precedence and names are stored correctly).
- `heating_enabled=true` now restores the last known active heating or cooling mode.
- Updated documentation to reflect persistent HVAC behavior and Smart AC Control support.
- Updated `index.html` to support Smart AC Control (COOL modes/colors) and persistent Smart Schedule switching.
- Updated unit tests to cover new and modified features.

### Fixed
- Corrected case‑sensitivity issues in UUID comparisons.
- Home Assistant add‑on now only moves the database when the public database does not already exist.
- Removed large top‑page spacing in `index.html`.

---

## [1.1.1] – 2026-03-05
### Added
- Home Assistant add‑on upgrade now supports standalone accessories.
- Containerization support including Dockerfile, entrypoint script, and installation documentation.
- Additional tests for multi‑pairing support and temperature sync.

### Changed
- Updated version to 1.1.1.
- Improved cloud sync behavior and humidity/temperature handling.
- Polling is now always enabled for Smart AC Control devices.
- General cleanup of duplicated imports and inline code.

### Fixed
- Fixed accessory ID collision in HomeKit change handler.
- Corrected multiple standalone accessory issues including window detection, NoneType sync, and crash conditions.

---

## [1.1.0] – 2026-02-26
### Added
- Standalone HomeKit accessory support (e.g., Smart AC Control V3+).
- Open‑window detection logic and corresponding unit tests.
- Dark theme for the UI.
- Additional open‑window detection when AC is active.

### Changed
- Version updated to 1.1.0.
- UI updates including index.html improvements.
- README and installation documentation updates.

### Fixed
- Resolved Windows/Linux line‑ending issues in tests.
- Fixed missing zone information (issue #19).
- Improved humidity sync using cloud API.

---

## [1.0.3] – 2026-02-07
### Added
- Home Assistant Add‑on.
- Additional tests and fixes for project scripts.
- Added pytest‑httpx to development requirements.

### Changed
- Cleanup of development dependencies and initial packaging support.
- Improved test coverage and ruff cleanup.

### Fixed
- Fixed broken unit tests.
- Resolved zeroconf test issues.
- Corrected bridge IP display regression.

---

## [1.0.2] – 2025-11-25
### Fixed
- Corrected zone leader update during synchronization.

---

## [1.0.1] – 2025-11-17 to 2025-11-24
### Added
- Improved default icons for heating devices.

### Changed
- Documentation and README updates.

### Fixed
- Removed invalid argument in code.
- Fixed command processing for non‑thermostat devices.
- Corrected fetching of zones and thermostats by ID.

---

## [1.0.0] – 2025-11-03 to 2025-11-21
### Added
- Bearer token support.
- User‑Agent header for outgoing requests.
- Domoticz plugin improvements and voice tag enhancements.
- SSE refresh improvements.
- Auto‑setup support for dzga/dzga‑flask.
- Optimistic update handling for integrations.
- Historic data exposure in the UI.
- Minimal web UI for diagnostics and setup.
- REST API consistency improvements.

### Changed
- Major logging cleanup and improvements.
- Improved shutdown sequence.
- Enhanced thermostat history visualization.
- Updated initial heartbeat and event logging.

### Fixed
- Multiple Domoticz plugin fixes.
- Device creation fixes.
- Resolved Python version confusion.

---

## [0.9.0] – 2025-10-30 to 2025-11-02
### Added
- Initial proxy code.
- Polling/eventing system groundwork.
- Zones, storage, and event system.
- Cloud data feeding for improved reporting.
- Ability to set heating per zone via REST.
- Preliminary project roadmap.

### Changed
- Major README enhancements.
- API cleanup and consistency improvements.
- Improved state reporting.

### Fixed
- Fixes for local Domoticz integration issues.

---

## [0.1.0] – 2025-10-30
### Added
- Initial working prototype.
- Basic REST API.
- Early event system.
- Initial project structure.
