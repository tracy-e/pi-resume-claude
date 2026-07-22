# Changelog

## 0.1.4

### UI
- Session picker uses a fixed-height `SelectList` (10 visible rows) so long
  lists scroll in place instead of expanding the TUI and jumping to the bottom.

### Fixed
- Session titles now follow Claude Code's resume list: `customTitle` → `aiTitle`
  → `lastPrompt` → summary → last recoverable user text (including slash-command
  XML → `/cmd args`). Broken parent chains no longer collapse to `(untitled)`.

## 0.1.3

### Extension
- Headless runs with more than one match now print the session ids instead of
  only asking for one, so a session can be resumed by id without a picker.
- `continue` and `-c` are accepted as aliases for `latest`, matching Claude
  Code's own "continue most recent" spelling.

### Performance
- The reader runs asynchronously so a scan no longer blocks (freezes) the TUI,
  and a status line shows while sessions are read.
- Discovery no longer reads each transcript in full to reject a foreign one: the
  cwd pre-filter stops after the first few records (cwd rides on every record's
  envelope). On a large session store this cut a listing from ~10s to ~2.5s.

## 0.1.2

### Fixed
- Session discovery no longer drops a Claude session whose leaf-chain cwd
  matches the current directory when an earlier record in the transcript
  carries a different cwd (a regression from the 0.1.1 discovery pre-filter).
- The published npm tarball no longer bundles Python bytecode
  (`__pycache__/*.pyc`): `files` now lists exact paths and the test run skips
  bytecode generation.

## 0.1.1

### Performance
- Faster session listing: discovery pre-filters transcripts by cwd before the
  full parent-chain parse, instead of parsing every transcript on disk.

### Extension
- Free-text title matching mirrors the reader (lower-cased, whitespace-normalized).
- Headless runs with more than one match now ask for an explicit session id
  instead of silently resuming an arbitrary session.
- Fixed the relative-time label so sessions roll over to "days" at 24h.

### Packaging & tooling
- Ship the `NOTICE` attribution file.
- Added a standard-library unit-test suite under `tests/` plus an `npm test`
  script.
- The publish workflow runs the tests and verifies the release tag matches the
  `package.json` version.
- Narrowed dependency version ranges.
