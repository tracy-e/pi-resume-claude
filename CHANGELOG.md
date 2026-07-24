# Changelog

## 0.1.5

### UI
- The session picker is searchable: a filter input sits above the list and
  narrows it live as you type (fuzzy, matching both the title and the session
  id). `/resume-claude <words>` now opens that picker pre-filtered instead of
  failing outright, so a typo is editable rather than fatal.

### Performance
- Discovery reads a bounded head/tail window of each transcript instead of
  parsing it in full. On a 1.3 GB session store a listing went from ~6.7s to
  ~0.8s.

### Fixed
- Sessions started in a subdirectory — or in a parent directory and later moved
  into the current one — are now listed, matching `claude --resume`. Discovery
  scans the cwd's own project slug plus descendant and ancestor slugs, and keeps
  a session when any recorded cwd is inside the current directory.
- Discovery no longer silently drops a session whose transcript is larger than
  the head/tail window, or whose window holds a single oversized record.
- The content-based cwd pre-filter is gone; it dropped legitimate sessions when
  an early record carried a different cwd.

### Reader
- Failures are reported through a structured JSON error channel under `--json`
  (`ambiguous_reference` carrying candidate summaries, `reader_error`
  otherwise), so the extension routes an ambiguous reference straight to the
  picker instead of scraping the message text.
- `--max-text-chars` (default 2000) caps each recovered message's text, keeping
  a handoff payload bounded on long sessions; truncation is surfaced as a
  `message_text_truncated` warning.
- `--limit N` lists only the N most-recent sessions (default `0`, no cap).

### Removed
- Dropped the unreachable Codex and Cursor reader paths (~1150 lines). The
  package is Claude-only, matching its name and documentation.

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
