---
name: resume-claude
description: >
  Resume or continue work from a recent Claude Code session. Use when the user
  switched from Claude Code, says "continue from Claude" or "resume my Claude
  session", or names a Claude session by description, path, or native ID.
  Prefer the /resume-claude command when available.
---

Set `TOOL=claude`. Set `SKILL_DIR` to this skill's directory (the folder that
contains this `SKILL.md`).

Read and follow `references/CORE.md`, using any text after this skill block as
the optional session reference (`latest` when empty).

When the `/resume-claude` extension command is available, prefer it: it runs the
reader and injects inert session JSON directly.
