# Changelog

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
