# Changelog

All notable changes to Murphy's Bench are recorded here, newest first.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/); versions are the
tags cut by `scripts/release.sh` and deployed by `scripts/update.sh`.

## v0.4.41 — 2026-07-19

### Fixed
- **Install docs**: `INSTALL.md` no longer runs the full pytest suite as part of
  "Initialize the Application" — running hundreds of tests isn't part of bringing the
  app up, it's an optional health check. Moved to its own "verify the install" note.
- **Register (Light POS)**: the register's search screen only ever showed a work order
  if you typed a search term — a walk-in or unnamed-client job had no way to be found
  short of guessing its exact client name (e.g. the system "Unsorted / Unverified"
  bucket). It now lists the most recently completed work orders by default, so any
  finished job can be found by browsing instead of searching blind.
