---
name: resume-claude
description: >
  Resume or continue work from a recent Claude Code session. Use when the user
  switched from Claude Code, says "continue from Claude" or "resume my Claude
  session", or names a Claude session by description, path, or native ID.
  Prefer the /resume-claude command when available.
---

Set `TOOL=claude`. Set `PACKAGE_ROOT` to the package root (the directory that
contains `scripts/session_reader.py` and `references/CORE.md`), which is this
skill directory's parent's parent.

Read and follow `references/CORE.md` at `${PACKAGE_ROOT}/references/CORE.md`,
using any text after this skill block as the optional session reference
(`latest` when empty).

When the `/resume-claude` extension command is available, prefer it: it runs the
reader and injects inert session JSON directly.
