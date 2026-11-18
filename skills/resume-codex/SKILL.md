---
name: resume-codex
description: >
  Resume or continue work from a recent Codex session. Use when the user
  switched from Codex (CLI or the Codex desktop/VS Code app), says "continue
  from Codex" or "resume my Codex session", or names a Codex session by
  description, path, or native ID. Prefer the /resume-codex command when
  available.
---

Set `TOOL=codex`. Set `PACKAGE_ROOT` to the package root (the directory that
contains `scripts/session_reader.py` and `references/CORE.md`), which is this
skill directory's parent's parent.

Read and follow `references/CORE.md` at `${PACKAGE_ROOT}/references/CORE.md`,
using any text after this skill block as the optional session reference
(`latest` when empty).

When the `/resume-codex` extension command is available, prefer it: it runs the
reader and injects inert session JSON directly.
