#!/usr/bin/env python3
"""Standard-library unit tests for session_reader.

Run with:  python3 -m unittest discover -s tests -p 'test_*.py'
       or:  npm test
"""

from __future__ import annotations

import io
import itertools
import json
import os
import sqlite3
import sys
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

_SCRIPTS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "scripts",
)
sys.path.insert(0, _SCRIPTS_DIR)

import session_reader as sr  # noqa: E402

FAKE_CWD = "/home/tester/fake-project"


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def _claude_records(title: str, cwd: str = FAKE_CWD) -> list[dict]:
    return [
        {
            "type": "user",
            "uuid": "u1",
            "parentUuid": None,
            "timestamp": "2026-07-20T10:00:00Z",
            "cwd": cwd,
            "gitBranch": "main",
            "message": {"role": "user", "content": [{"type": "text", "text": title}]},
        },
        {
            "type": "assistant",
            "uuid": "a1",
            "parentUuid": "u1",
            "timestamp": "2026-07-20T10:00:05Z",
            "cwd": cwd,
            "gitBranch": "main",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "text": "secret private reasoning"},
                    {"type": "text", "text": "Sure, editing now"},
                    {"type": "tool_use", "id": "t1", "name": "Edit", "input": {"file": "a.py"}},
                ],
            },
        },
        {
            "type": "user",
            "uuid": "u2",
            "parentUuid": "a1",
            "timestamp": "2026-07-20T10:00:06Z",
            "cwd": cwd,
            "gitBranch": "main",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "ok done", "is_error": False}
                ],
            },
        },
    ]


class PureHelperTests(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(sr.slugify("/home/tester/my.project"), "-home-tester-my-project")

    def test_safe_text_strips_control_chars_but_keeps_ws(self):
        self.assertEqual(sr._safe_text("a\x00b"), "a\ufffdb")
        self.assertEqual(sr._safe_text("a\nb\tc"), "a\nb\tc")
        self.assertEqual(sr._safe_text(None), "")

    def test_one_line_collapses_and_truncates(self):
        self.assertEqual(sr._one_line("a\n  b   c", 100), "a b c")
        self.assertEqual(sr._one_line("abcdef", 3), "abc...")

    def test_timestamp_to_millis(self):
        self.assertEqual(
            sr._iso_from_millis(sr._timestamp_to_millis("2026-07-20T10:00:00Z")),
            "2026-07-20T10:00:00+00:00",
        )
        self.assertEqual(sr._timestamp_to_millis(1_700_000_000), 1_700_000_000_000)
        self.assertEqual(sr._timestamp_to_millis(1_700_000_000_000), 1_700_000_000_000)
        self.assertIsNone(sr._timestamp_to_millis(True))
        self.assertIsNone(sr._timestamp_to_millis("not-a-date"))

    def test_generated_meta_detection(self):
        self.assertTrue(sr._is_generated_meta_text("<system-reminder>hi</system-reminder>"))
        self.assertTrue(sr._is_generated_meta_text("[Request interrupted by user]"))
        self.assertFalse(sr._is_generated_meta_text("a normal sentence"))

    def test_extract_command_display(self):
        raw = (
            "<command-message>code-review</command-message>\n"
            "<command-name>/code-review</command-name>\n"
            "<command-args>--fix a500e37</command-args>"
        )
        self.assertEqual(sr._extract_command_display(raw), "/code-review --fix a500e37")
        self.assertEqual(
            sr._extract_command_display("<command-name>/clear</command-name>"),
            "/clear",
        )
        self.assertIsNone(sr._extract_command_display("plain user text"))


class ClaudeParseTests(unittest.TestCase):
    def test_basic_chain(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.jsonl"
            _write_jsonl(path, _claude_records("Please add a feature"))
            result = sr.read_claude_session(path)

        self.assertEqual(result["tool"], "claude")
        self.assertEqual(result["cwd"], FAKE_CWD)
        self.assertEqual(result["branch"], "main")
        self.assertEqual(len(result["turns"]), 3)

        user, assistant, tool_turn = result["turns"]
        self.assertEqual(user["role"], "user")
        self.assertEqual(user["text"], "Please add a feature")
        self.assertTrue(user["inert"])
        self.assertEqual(assistant["text"], "Sure, editing now")
        self.assertEqual(assistant["tool_calls"][0]["name"], "Edit")
        self.assertTrue(assistant["tool_calls"][0]["inert"])
        self.assertEqual(tool_turn["tool_results"][0]["content"], "ok done")

        self.assertEqual(result["title"], "Please add a feature")
        self.assertEqual(result["last_user_request"], "Please add a feature")
        self.assertEqual(result["last_assistant_action"], "Sure, editing now")
        # Thinking blocks must never leak into the inert payload.
        self.assertNotIn("secret private reasoning", json.dumps(result))

    def test_title_prefers_custom_then_ai_then_last_prompt(self):
        """Claude Code resume list: customTitle > aiTitle > lastPrompt."""
        base = _claude_records("first user message")
        # Leaf-chain-only fallback would see the first user text; named/ persisted
        # title records must win instead.
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "named.jsonl"
            _write_jsonl(
                path,
                base
                + [
                    {"type": "last-prompt", "lastPrompt": "later prompt", "sessionId": "s"},
                    {"type": "ai-title", "aiTitle": "ai-slug", "sessionId": "s"},
                    {"type": "custom-title", "customTitle": "my-name", "sessionId": "s"},
                ],
            )
            self.assertEqual(sr.read_claude_session(path)["title"], "my-name")

            path = Path(tmp) / "ai.jsonl"
            _write_jsonl(
                path,
                base
                + [
                    {"type": "last-prompt", "lastPrompt": "later prompt", "sessionId": "s"},
                    {"type": "ai-title", "aiTitle": "ai-slug", "sessionId": "s"},
                ],
            )
            self.assertEqual(sr.read_claude_session(path)["title"], "ai-slug")

            path = Path(tmp) / "last.jsonl"
            _write_jsonl(
                path,
                base + [{"type": "last-prompt", "lastPrompt": "later prompt", "sessionId": "s"}],
            )
            self.assertEqual(sr.read_claude_session(path)["title"], "later prompt")

    def test_title_from_last_prompt_when_leaf_chain_drops_first_user(self):
        """Broken parent links must not yield (untitled) if last-prompt exists."""
        records = [
            {
                "type": "user",
                "uuid": "orphan-user",
                "parentUuid": None,
                "timestamp": "2026-07-20T10:00:00Z",
                "cwd": FAKE_CWD,
                "message": {
                    "role": "user",
                    "content": "Resize emoji cache thumbs to 100x100",
                },
            },
            # mid-chain records whose parent is missing from the file → leaf
            # walk never reaches orphan-user; title must still resolve.
            {
                "type": "assistant",
                "uuid": "a-mid",
                "parentUuid": "missing-parent",
                "timestamp": "2026-07-20T10:01:00Z",
                "cwd": FAKE_CWD,
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "ok"}],
                },
            },
            {
                "type": "user",
                "uuid": "u-tool",
                "parentUuid": "a-mid",
                "timestamp": "2026-07-20T10:01:01Z",
                "cwd": FAKE_CWD,
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t1",
                            "content": "ok",
                        }
                    ],
                },
            },
            {
                "type": "last-prompt",
                "lastPrompt": "Resize emoji cache thumbs to 100x100",
                "sessionId": "s",
            },
        ]
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.jsonl"
            _write_jsonl(path, records)
            result = sr.read_claude_session(path)
        self.assertEqual(result["title"], "Resize emoji cache thumbs to 100x100")

    def test_title_from_command_xml_when_last_prompt_empty(self):
        records = [
            {
                "type": "user",
                "uuid": "u1",
                "parentUuid": None,
                "timestamp": "2026-07-20T10:00:00Z",
                "cwd": FAKE_CWD,
                "message": {
                    "role": "user",
                    "content": (
                        "<command-message>clear</command-message>\n"
                        "<command-name>/clear</command-name>\n"
                        "<command-args></command-args>"
                    ),
                },
            },
            {
                "type": "last-prompt",
                "lastPrompt": None,
                "sessionId": "s",
            },
        ]
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "cmd.jsonl"
            _write_jsonl(path, records)
            result = sr.read_claude_session(path)
        self.assertEqual(result["title"], "/clear")
        # The command is the user's request: keep it as a turn, not just a title.
        self.assertEqual(result["turns"][0]["text"], "/clear")
        self.assertEqual(result["last_user_request"], "/clear")

    def test_title_ignores_sidechain_user_records(self):
        """A sub-agent's internal prompt (isSidechain) must not become the title."""
        records = [
            {
                "type": "user",
                "uuid": "u1",
                "parentUuid": None,
                "timestamp": "2026-07-20T10:00:00Z",
                "cwd": FAKE_CWD,
                "message": {"role": "user", "content": "Fix the login bug in auth.py"},
            },
            {
                "type": "assistant",
                "uuid": "a1",
                "parentUuid": "u1",
                "timestamp": "2026-07-20T10:00:05Z",
                "cwd": FAKE_CWD,
                "message": {"role": "assistant", "content": [{"type": "text", "text": "on it"}]},
            },
            # Task sub-agent record, appended later; must be skipped for the title.
            {
                "type": "user",
                "uuid": "s1",
                "parentUuid": None,
                "isSidechain": True,
                "timestamp": "2026-07-20T10:01:00Z",
                "cwd": FAKE_CWD,
                "message": {"role": "user", "content": "You are a search agent. Grep for TODOs."},
            },
        ]
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "side.jsonl"
            _write_jsonl(path, records)
            result = sr.read_claude_session(path)
        self.assertEqual(result["title"], "Fix the login bug in auth.py")

    def test_title_keeps_explicit_title_starting_with_angle_bracket(self):
        """`_is_generated_meta_text` must not drop a real custom title like `<wip> auth`."""
        base = _claude_records("do the thing")
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "named.jsonl"
            _write_jsonl(
                path,
                base + [{"type": "custom-title", "customTitle": "<wip> auth", "sessionId": "s"}],
            )
            self.assertEqual(sr.read_claude_session(path)["title"], "<wip> auth")

            path = Path(tmp) / "prompt.jsonl"
            _write_jsonl(
                path,
                base + [{"type": "last-prompt", "lastPrompt": "<div> is not rendering", "sessionId": "s"}],
            )
            self.assertEqual(sr.read_claude_session(path)["title"], "<div> is not rendering")

    def test_malformed_records_warned(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.jsonl"
            _write_jsonl(path, _claude_records("Task"))
            with path.open("a", encoding="utf-8") as handle:
                handle.write("this is not json{\n")
            result = sr.read_claude_session(path)
        codes = {w["code"] for w in result["warnings"]}
        self.assertIn("malformed_records_skipped", codes)

    def test_unknown_records_warned(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.jsonl"
            records = _claude_records("Task")
            records.append({"type": "totally-unknown-type", "uuid": "x1"})
            _write_jsonl(path, records)
            result = sr.read_claude_session(path)
        codes = {w["code"] for w in result["warnings"]}
        self.assertIn("unknown_records_skipped", codes)

    def test_hard_compaction_drops_pre_boundary_history(self):
        records = [
            {
                "type": "user",
                "uuid": "old",
                "parentUuid": None,
                "timestamp": "2026-07-20T09:00:00Z",
                "cwd": FAKE_CWD,
                "message": {"role": "user", "content": [{"type": "text", "text": "old stuff"}]},
            },
            {
                "type": "system",
                "subtype": "compact_boundary",
                "uuid": "boundary",
                "parentUuid": "old",
                "timestamp": "2026-07-20T09:30:00Z",
            },
            {
                "type": "user",
                "uuid": "new",
                "parentUuid": None,
                "timestamp": "2026-07-20T10:00:00Z",
                "cwd": FAKE_CWD,
                "message": {"role": "user", "content": [{"type": "text", "text": "new task"}]},
            },
            {
                "type": "assistant",
                "uuid": "newa",
                "parentUuid": "new",
                "timestamp": "2026-07-20T10:00:05Z",
                "cwd": FAKE_CWD,
                "message": {"role": "assistant", "content": [{"type": "text", "text": "working"}]},
            },
        ]
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.jsonl"
            _write_jsonl(path, records)
            result = sr.read_claude_session(path)
        self.assertEqual(result["last_user_request"], "new task")
        self.assertNotIn("old stuff", json.dumps(result))

    def test_message_text_truncated_with_warning(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.jsonl"
            _write_jsonl(path, _claude_records("x" * 5000))
            result = sr.read_claude_session(path, max_text_chars=100)
        user_turn = next(t for t in result["turns"] if t["role"] == "user" and t["text"])
        self.assertIn("truncated", user_turn["text"])
        # Stored text (content + marker) must respect the cap, not overshoot it.
        self.assertLessEqual(len(user_turn["text"]), 100)
        self.assertIn(
            "message_text_truncated", [w["code"] for w in result["warnings"]]
        )

    def test_message_text_tiny_cap_still_respects_bound(self):
        # A cap smaller than the truncation marker must hard-cut, never overshoot.
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.jsonl"
            _write_jsonl(path, _claude_records("x" * 5000))
            result = sr.read_claude_session(path, max_text_chars=5)
        for turn in result["turns"]:
            self.assertLessEqual(len(turn["text"]), 5)

    def test_message_text_untouched_under_cap(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.jsonl"
            _write_jsonl(path, _claude_records("Short prompt"))
            result = sr.read_claude_session(path)  # default cap far exceeds text
        self.assertNotIn(
            "message_text_truncated", [w["code"] for w in result["warnings"]]
        )

    def test_parent_chain_survives_interleaved_harness_records(self):
        """Claude interleaves `attachment`/`progress` records into the
        parentUuid chain. Dropping them (as non-conversational) severs the leaf
        walk and leaves only the last turn or two."""
        records = [
            {
                "type": "user",
                "uuid": "u1",
                "parentUuid": None,
                "timestamp": "2026-07-20T10:00:00Z",
                "cwd": FAKE_CWD,
                "message": {"role": "user", "content": "start the task"},
            },
            {
                "type": "attachment",
                "uuid": "att1",
                "parentUuid": "u1",
                "timestamp": "2026-07-20T10:00:01Z",
                "cwd": FAKE_CWD,
            },
            {
                "type": "assistant",
                "uuid": "a1",
                "parentUuid": "att1",
                "timestamp": "2026-07-20T10:00:02Z",
                "cwd": FAKE_CWD,
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "working on it"}],
                },
            },
        ]
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.jsonl"
            _write_jsonl(path, records)
            result = sr.read_claude_session(path)
        self.assertEqual([turn["role"] for turn in result["turns"]], ["user", "assistant"])
        self.assertEqual(result["last_user_request"], "start the task")

    def test_malformed_message_and_type_do_not_crash(self):
        """Foreign JSON is untrusted: a non-dict `message` or a non-string
        `type` must degrade, not raise and lose the whole session."""
        records = [
            {
                "type": "user",
                "uuid": "u1",
                "parentUuid": None,
                "timestamp": "2026-07-20T10:00:00Z",
                "cwd": FAKE_CWD,
                "message": {"role": "user", "content": "hello"},
            },
            {
                "type": "assistant",
                "uuid": "a1",
                "parentUuid": "u1",
                "timestamp": "2026-07-20T10:00:01Z",
                "cwd": FAKE_CWD,
                "message": "not-a-dict",
            },
            {
                "type": [],
                "uuid": "bad",
                "parentUuid": None,
                "timestamp": "2026-07-20T10:00:02Z",
                "cwd": FAKE_CWD,
                "message": {"role": [], "content": [{"type": {}, "text": "x"}]},
            },
        ]
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.jsonl"
            _write_jsonl(path, records)
            result = sr.read_claude_session(path)
        self.assertIn("hello", json.dumps(result))

    def test_harness_metadata_types_are_not_reported_unknown(self):
        records = _claude_records("Task")
        known = [
            "attachment",
            "api-request",
            "api-request-blob",
            "permission-mode",
            "atis-latch",
            "tools-used",
        ]
        for index, record_type in enumerate(known):
            records.append(
                {
                    "type": record_type,
                    "uuid": f"harness-{index}",
                    "parentUuid": None,
                    "timestamp": f"2026-07-20T10:01:0{index}Z",
                    "cwd": FAKE_CWD,
                }
            )
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.jsonl"
            _write_jsonl(path, records)
            result = sr.read_claude_session(path)
        self.assertNotIn(
            "unknown_records_skipped", [w["code"] for w in result["warnings"]]
        )


class DiscoverAndResolveTests(unittest.TestCase):
    def _build_store(self, tmp: str) -> None:
        projects = Path(tmp) / "projects" / sr.slugify(FAKE_CWD)
        projects.mkdir(parents=True)
        self.id_a = "11111111-1111-4111-8111-111111111111"
        self.id_b = "22222222-2222-4222-8222-222222222222"
        path_a = projects / f"{self.id_a}.jsonl"
        path_b = projects / f"{self.id_b}.jsonl"
        _write_jsonl(path_a, _claude_records("Alpha feature work"))
        _write_jsonl(path_b, _claude_records("Beta bugfix"))
        # A is newer than B so "latest" resolves to A.
        os.utime(path_b, (1_000_000, 1_000_000))
        os.utime(path_a, (2_000_000, 2_000_000))

    def test_discover_and_resolve(self):
        with TemporaryDirectory() as tmp:
            self._build_store(tmp)
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": tmp}):
                sessions = sr.discover_sessions("claude", FAKE_CWD)
                self.assertEqual(len(sessions), 2)
                self.assertEqual(sessions[0]["session_id"], self.id_a)

                self.assertEqual(
                    sr.resolve_session("claude", "latest", FAKE_CWD)["session_id"], self.id_a
                )
                # `continue` / `-c` are aliases for the newest session.
                for alias in ("continue", "--continue", "-c", "CONTINUE"):
                    self.assertEqual(
                        sr.resolve_session("claude", alias, FAKE_CWD)["session_id"],
                        self.id_a,
                    )
                self.assertEqual(
                    sr.resolve_session("claude", self.id_b, FAKE_CWD)["session_id"], self.id_b
                )
                self.assertEqual(
                    sr.resolve_session("claude", "beta", FAKE_CWD)["session_id"], self.id_b
                )
                # A short id fragment matches too: filtering covers the id, not
                # just the title (the TUI picker's haystack does the same).
                self.assertEqual(
                    sr.resolve_session("claude", self.id_b[:8], FAKE_CWD)["session_id"],
                    self.id_b,
                )
                with self.assertRaises(sr.AmbiguousReference):
                    sr.resolve_session("claude", "e", FAKE_CWD)

    def test_prefilter_filters_foreign_sessions(self):
        with TemporaryDirectory() as tmp:
            projects = Path(tmp) / "projects" / sr.slugify(FAKE_CWD)
            projects.mkdir(parents=True)
            good = projects / "11111111-1111-4111-8111-111111111111.jsonl"
            foreign = projects / "22222222-2222-4222-8222-222222222222.jsonl"
            _write_jsonl(good, _claude_records("Mine"))
            _write_jsonl(foreign, _claude_records("Theirs", cwd="/some/other/place"))
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": tmp}):
                sessions = sr.discover_sessions("claude", FAKE_CWD)
            self.assertEqual([s["title"] for s in sessions], ["Mine"])

    def test_prefilter_keeps_session_with_leading_foreign_cwd(self):
        # Regression: a leading foreign cwd must not veto a target-cwd leaf chain.
        uid = "33333333-3333-4333-8333-333333333333"
        records = [
            {
                "type": "user",
                "uuid": "orphan",
                "parentUuid": None,
                "timestamp": "2026-07-20T08:00:00Z",
                "cwd": "/some/other/place",
                "message": {"role": "user", "content": [{"type": "text", "text": "old branch"}]},
            },
            {
                "type": "user",
                "uuid": "u1",
                "parentUuid": None,
                "timestamp": "2026-07-20T10:00:00Z",
                "cwd": FAKE_CWD,
                "gitBranch": "main",
                "message": {"role": "user", "content": [{"type": "text", "text": "real work"}]},
            },
            {
                "type": "assistant",
                "uuid": "a1",
                "parentUuid": "u1",
                "timestamp": "2026-07-20T10:00:05Z",
                "cwd": FAKE_CWD,
                "gitBranch": "main",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "on it"}]},
            },
        ]
        with TemporaryDirectory() as tmp:
            projects = Path(tmp) / "projects" / sr.slugify(FAKE_CWD)
            projects.mkdir(parents=True)
            _write_jsonl(projects / f"{uid}.jsonl", records)
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": tmp}):
                sessions = sr.discover_sessions("claude", FAKE_CWD)
        self.assertEqual([s["session_id"] for s in sessions], [uid])

    def test_leading_foreign_cwd_records_do_not_veto_match(self):
        # A session that opens with a run of foreign-cwd orphans before reaching
        # the real target-cwd chain must still be discovered: the cwd check is
        # "any record is within the target", not "the first/leaf record matches".
        uid = "44444444-4444-4444-8444-444444444444"
        records = [
            {
                "type": "user",
                "uuid": f"orphan{i}",
                "parentUuid": None,
                "timestamp": f"2026-07-20T08:{i:02d}:00Z",
                "cwd": "/some/other/place",
                "message": {"role": "user", "content": [{"type": "text", "text": f"old {i}"}]},
            }
            for i in range(10)
        ]
        records += [
            {
                "type": "user",
                "uuid": "u1",
                "parentUuid": None,
                "timestamp": "2026-07-20T10:00:00Z",
                "cwd": FAKE_CWD,
                "gitBranch": "main",
                "message": {"role": "user", "content": [{"type": "text", "text": "real work"}]},
            },
            {
                "type": "assistant",
                "uuid": "a1",
                "parentUuid": "u1",
                "timestamp": "2026-07-20T10:00:05Z",
                "cwd": FAKE_CWD,
                "gitBranch": "main",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "on it"}]},
            },
        ]
        with TemporaryDirectory() as tmp:
            projects = Path(tmp) / "projects" / sr.slugify(FAKE_CWD)
            projects.mkdir(parents=True)
            _write_jsonl(projects / f"{uid}.jsonl", records)
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": tmp}):
                sessions = sr.discover_sessions("claude", FAKE_CWD)
        self.assertEqual([s["session_id"] for s in sessions], [uid])

    def test_discovers_subdirectory_sessions(self):
        # Claude Code treats a session run in a subdirectory as part of the repo,
        # so resuming from the root must surface it (its own slug dir is a
        # descendant of the cwd's slug dir).
        sub_cwd = FAKE_CWD + "/backend"
        uid = "55555555-5555-4555-8555-555555555555"
        with TemporaryDirectory() as tmp:
            sub_dir = Path(tmp) / "projects" / sr.slugify(sub_cwd)
            sub_dir.mkdir(parents=True)
            _write_jsonl(sub_dir / f"{uid}.jsonl", _claude_records("backend work", cwd=sub_cwd))
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": tmp}):
                ids = [s["session_id"] for s in sr.discover_sessions("claude", FAKE_CWD)]
        self.assertIn(uid, ids)

    def test_discovers_ancestor_slug_session_that_entered_this_cwd(self):
        # Launched at the monorepo root (so it lives under the *root* slug dir)
        # and later cd'd into this package: resuming from the package must find it.
        sub_cwd = FAKE_CWD + "/backend"
        uid = "77777777-7777-4777-8777-777777777777"
        records = _claude_records("root session that moved")
        records[-1]["cwd"] = sub_cwd
        with TemporaryDirectory() as tmp:
            root_dir = Path(tmp) / "projects" / sr.slugify(FAKE_CWD)
            root_dir.mkdir(parents=True)
            _write_jsonl(root_dir / f"{uid}.jsonl", records)
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": tmp}):
                ids = [s["session_id"] for s in sr.discover_sessions("claude", sub_cwd)]
        self.assertIn(uid, ids)

    def test_pure_ancestor_session_does_not_leak_into_subdir(self):
        # A root session that never entered the package must NOT show up there,
        # even though the root slug dir is now scanned.
        sub_cwd = FAKE_CWD + "/backend"
        uid = "88888888-8888-4888-8888-888888888888"
        with TemporaryDirectory() as tmp:
            root_dir = Path(tmp) / "projects" / sr.slugify(FAKE_CWD)
            root_dir.mkdir(parents=True)
            _write_jsonl(root_dir / f"{uid}.jsonl", _claude_records("root only"))
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": tmp}):
                ids = [s["session_id"] for s in sr.discover_sessions("claude", sub_cwd)]
        self.assertNotIn(uid, ids)

    def test_own_dir_session_kept_when_light_window_truncated(self):
        # The matching cwd sits in the unread middle: a "no match" verdict from a
        # truncated light read must not drop an own-slug session.
        uid = "99999999-9999-4999-8999-999999999999"
        foreign = {
            "type": "user",
            "parentUuid": None,
            "cwd": "/some/other/place",
            "message": {"role": "user", "content": [{"type": "text", "text": "x" * 120}]},
        }
        records = []
        for i in range(12):
            record = dict(foreign, uuid=f"f{i}", timestamp=f"2026-07-20T08:{i:02d}:00Z")
            if i == 6:  # only the middle record carries the matching cwd
                record = dict(record, cwd=FAKE_CWD)
            records.append(record)
        with TemporaryDirectory() as tmp:
            projects = Path(tmp) / "projects" / sr.slugify(FAKE_CWD)
            projects.mkdir(parents=True)
            _write_jsonl(projects / f"{uid}.jsonl", records)
            # Shrink the light window so the middle really is skipped.
            with (
                mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": tmp}),
                mock.patch.object(sr, "_LIGHT_HEAD_BYTES", 200),
                mock.patch.object(sr, "_LIGHT_TAIL_BYTES", 200),
            ):
                ids = [s["session_id"] for s in sr.discover_sessions("claude", FAKE_CWD)]
        self.assertIn(uid, ids)

    def test_excludes_sibling_repo_sessions(self):
        # A sibling repo's slug shares the cwd-slug prefix, so the cheap dir
        # pre-select would let it in -- the content is-within check must reject it.
        sibling_cwd = FAKE_CWD + "-other"
        uid = "66666666-6666-4666-8666-666666666666"
        with TemporaryDirectory() as tmp:
            sib_dir = Path(tmp) / "projects" / sr.slugify(sibling_cwd)
            sib_dir.mkdir(parents=True)
            _write_jsonl(sib_dir / f"{uid}.jsonl", _claude_records("sibling", cwd=sibling_cwd))
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": tmp}):
                ids = [s["session_id"] for s in sr.discover_sessions("claude", FAKE_CWD)]
        self.assertNotIn(uid, ids)

    def test_limit_keeps_most_recent(self):
        with TemporaryDirectory() as tmp:
            projects = Path(tmp) / "projects" / sr.slugify(FAKE_CWD)
            projects.mkdir(parents=True)
            ids = []
            for i in range(4):
                uid = f"{i}{i}{i}{i}0000-0000-4000-8000-000000000000"
                ids.append(uid)
                path = projects / f"{uid}.jsonl"
                _write_jsonl(path, _claude_records(f"session {i}"))
                os.utime(path, (1_000_000 + i, 1_000_000 + i))  # i=3 is newest
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": tmp}):
                limited = sr.discover_sessions("claude", FAKE_CWD, limit=2)
                unlimited = sr.discover_sessions("claude", FAKE_CWD)
        self.assertEqual(len(unlimited), 4)
        # Only the two most recent, newest first.
        self.assertEqual([s["session_id"] for s in limited], [ids[3], ids[2]])


FAKE_CODEX_ID = "0f0f0f0f-0f0f-4f0f-8f0f-0f0f0f0f0f0f"


_CODEX_USER_IDS = itertools.count(1)


def _codex_user(text: str) -> dict:
    # Deterministic ids: hash() is salted per process, which would make the
    # fixtures differ between runs.
    return {
        "type": "response_item",
        "timestamp": "2026-07-20T10:00:01Z",
        "payload": {
            "type": "message",
            "id": f"msg-user-{next(_CODEX_USER_IDS)}",
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        },
    }


def _codex_records(prompt: str, cwd: str = FAKE_CWD, session_id: str = FAKE_CODEX_ID) -> list[dict]:
    return [
        {
            "timestamp": "2026-07-20T10:00:00Z",
            "type": "session_meta",
            "payload": {
                "id": session_id,
                "session_id": session_id,
                "timestamp": "2026-07-20T10:00:00Z",
                "cwd": cwd,
                "source": "cli",
                "git": {"branch": "main", "commit_hash": "abc"},
                "base_instructions": {"text": "You are Codex. SECRET BASE INSTRUCTIONS"},
            },
        },
        # Harness-injected user turn: never a user request.
        _codex_user("# AGENTS.md instructions for the repo\n<INSTRUCTIONS>be nice</INSTRUCTIONS>"),
        {
            "type": "turn_context",
            "timestamp": "2026-07-20T10:00:01Z",
            "payload": {"cwd": cwd, "approval_policy": "on-request", "model": "gpt-6"},
        },
        _codex_user(prompt),
        {
            "type": "response_item",
            "timestamp": "2026-07-20T10:00:02Z",
            "payload": {
                "type": "reasoning",
                "id": "rs1",
                "summary": [],
                "encrypted_content": "gAAAAsecretreasoning",
            },
        },
        {
            "type": "response_item",
            "timestamp": "2026-07-20T10:00:03Z",
            "payload": {
                "type": "custom_tool_call",
                "id": "ctc1",
                "call_id": "call_1",
                "name": "exec",
                "status": "completed",
                "input": "await tools.exec_command({cmd:'pytest -q'})",
            },
        },
        {
            "type": "response_item",
            "timestamp": "2026-07-20T10:00:04Z",
            "payload": {
                "type": "custom_tool_call_output",
                "id": "ctco1",
                "call_id": "call_1",
                "output": [
                    {"type": "input_text", "text": "Script completed\nOutput:\n"},
                    {"type": "input_text", "text": "3 passed"},
                ],
            },
        },
        {
            "type": "response_item",
            "timestamp": "2026-07-20T10:00:05Z",
            "payload": {
                "type": "message",
                "id": "msg-a1",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Tests pass."}],
            },
        },
        # Stale duplicate of the same turn in the event stream: must not render twice.
        {
            "type": "event_msg",
            "timestamp": "2026-07-20T10:00:05Z",
            "payload": {
                "type": "item_completed",
                "item": {"type": "AgentMessage", "content": [{"type": "Text", "text": "Tests pass."}]},
            },
        },
    ]


class CodexParseTests(unittest.TestCase):
    def test_basic_rollout(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / f"rollout-2026-07-20T10-00-00-{FAKE_CODEX_ID}.jsonl"
            _write_jsonl(path, _codex_records("Add a feature"))
            result = sr.read_codex_session(path)

        self.assertEqual(result["tool"], "codex")
        self.assertEqual(result["source"], "codex-cli")
        self.assertEqual(result["session_id"], FAKE_CODEX_ID)
        self.assertEqual(result["cwd"], FAKE_CWD)
        self.assertEqual(result["branch"], "main")
        # injected AGENTS.md turn, reasoning, and the duplicate event are gone
        roles = [turn["role"] for turn in result["turns"]]
        self.assertEqual(roles, ["user", "assistant", "tool", "assistant"])
        self.assertEqual(result["turns"][0]["text"], "Add a feature")
        self.assertEqual(result["turns"][1]["tool_calls"][0]["name"], "exec")
        self.assertEqual(result["turns"][2]["tool_results"][0]["content"], "Script completed Output: 3 passed")
        self.assertEqual(result["title"], "Add a feature")
        self.assertEqual(result["last_user_request"], "Add a feature")
        self.assertEqual(result["last_assistant_action"], "Tests pass.")
        blob = json.dumps(result)
        self.assertNotIn("SECRET BASE INSTRUCTIONS", blob)
        self.assertNotIn("secretreasoning", blob)
        self.assertNotIn("be nice", blob)
        self.assertIn("unsafe_records_skipped", [w["code"] for w in result["warnings"]])

    def test_compaction_uses_replacement_history(self):
        records = _codex_records("fresh prompt after compaction")
        # A pre-boundary turn that the compaction dropped: only the stored
        # replacement history describes it afterwards.
        records.insert(2, _codex_user("stale pre-compaction prompt"))
        records.insert(
            3,
            {
                "type": "compacted",
                "timestamp": "2026-07-20T09:59:59Z",
                "payload": {
                    "message": "",
                    "replacement_history": [_codex_user("older prompt kept by compaction")["payload"]],
                },
            },
        )
        # Only the records after the last boundary are live on top of it.
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.jsonl"
            _write_jsonl(path, records)
            result = sr.read_codex_session(path)
        texts = [turn["text"] for turn in result["turns"] if turn["role"] == "user"]
        self.assertEqual(texts, ["older prompt kept by compaction", "fresh prompt after compaction"])
        self.assertIn("compacted_history_replaced", [w["code"] for w in result["warnings"]])

    def test_pre_compaction_turns_are_dropped(self):
        records = _codex_records("after")
        records.insert(1, _codex_user("before compaction"))
        # No `compacted` record: everything renders.
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.jsonl"
            _write_jsonl(path, records)
            self.assertEqual(
                [t["text"] for t in sr.read_codex_session(path)["turns"] if t["role"] == "user"],
                ["before compaction", "after"],
            )
            # With one, only the replacement history plus post-boundary turns remain.
            records.insert(
                3,
                {
                    "type": "compacted",
                    "payload": {"replacement_history": [], "message": ""},
                },
            )
            path = Path(tmp) / "t.jsonl"
            _write_jsonl(path, records)
            self.assertEqual(
                [t["text"] for t in sr.read_codex_session(path)["turns"] if t["role"] == "user"],
                ["after"],
            )

    def test_thread_rollback_drops_user_turns(self):
        records = [
            _codex_records("first")[0],
            _codex_user("first"),
            _codex_user("second"),
            {
                "type": "event_msg",
                "payload": {"type": "thread_rolled_back", "num_turns": 1},
            },
        ]
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.jsonl"
            _write_jsonl(path, records)
            result = sr.read_codex_session(path)
        self.assertEqual([t["text"] for t in result["turns"]], ["first"])

    def test_turn_cap_keeps_head_and_tail(self):
        records = [_codex_records("first")[0]]
        records += [_codex_user(f"prompt {i}") for i in range(40)]
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.jsonl"
            _write_jsonl(path, records)
            capped = sr.read_codex_session(path, max_turns=8)
            uncapped = sr.read_codex_session(path, max_turns=0)
        self.assertEqual(len(uncapped["turns"]), 40)
        self.assertEqual(len(capped["turns"]), 8)
        self.assertEqual(capped["turns"][0]["text"], "prompt 0")
        self.assertEqual(capped["turns"][-1]["text"], "prompt 39")
        self.assertIn("turns_elided", [w["code"] for w in capped["warnings"]])
        self.assertNotIn("turns_elided", [w["code"] for w in uncapped["warnings"]])

    def test_turn_cap_one_keeps_only_the_stopping_point(self):
        records = [_codex_records("first")[0]]
        records += [_codex_user(f"prompt {i}") for i in range(4)]
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.jsonl"
            _write_jsonl(path, records)
            capped = sr.read_codex_session(path, max_turns=1)
        self.assertEqual([t["text"] for t in capped["turns"]], ["prompt 3"])
        self.assertIn("turns_elided", [w["code"] for w in capped["warnings"]])

    def test_title_matches_the_indexed_list_label(self):
        # `show` must reuse the picker's label (name -> title -> opening prompt),
        # or the row the user picked and the handoff disagree.
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            sessions = home / "sessions" / "2026" / "07" / "20"
            sessions.mkdir(parents=True)
            path = sessions / f"rollout-2026-07-20T10-00-00-{FAKE_CODEX_ID}.jsonl"
            _write_jsonl(path, _codex_records("opening real prompt"))
            con = sqlite3.connect(home / "state_5.sqlite")
            con.execute(
                "CREATE TABLE threads (id TEXT, rollout_path TEXT, updated_at_ms INTEGER, "
                "source TEXT, cwd TEXT, archived INTEGER, title TEXT, "
                "first_user_message TEXT, name TEXT)"
            )
            con.execute(
                "INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    FAKE_CODEX_ID,
                    str(path),
                    2_000_000,
                    "cli",
                    FAKE_CWD,
                    0,
                    "generated title",
                    "opening real prompt",
                    "replace-r50",
                ),
            )
            con.commit()
            con.close()
            with mock.patch.dict(os.environ, {"CODEX_HOME": tmp}):
                result = sr.read_codex_session(path)
                listed = sr.discover_sessions("codex", FAKE_CWD)
        self.assertEqual(result["title"], "replace-r50")
        self.assertEqual(result["title"], listed[0]["title"])

    def test_malformed_item_type_does_not_crash(self):
        # Untrusted rollout: a non-string item type must not raise TypeError.
        records = _codex_records("real prompt")
        records.append({"type": "response_item", "payload": {"type": []}})
        records.append({"type": [], "payload": {}})
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.jsonl"
            _write_jsonl(path, records)
            result = sr.read_codex_session(path)
        self.assertEqual(result["turns"][0]["text"], "real prompt")

    def test_message_text_cap(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.jsonl"
            _write_jsonl(path, _codex_records("x" * 5000))
            result = sr.read_codex_session(path, max_text_chars=50)
        self.assertLessEqual(max(len(t["text"]) for t in result["turns"]), 50)
        self.assertIn("message_text_truncated", [w["code"] for w in result["warnings"]])

    def test_title_skips_injected_turns(self):
        # Codex names a session after its opening real prompt; the leading
        # AGENTS.md turn in _codex_records must not become the title.
        records = _codex_records("real prompt")[:-1]
        records.append(_codex_user("<environment_context>\n  <cwd>x</cwd>\n</environment_context>"))
        self.assertEqual(sr._codex_first_prompt(records), "real prompt")
        self.assertIsNone(sr._codex_first_prompt(records[1:2]))


class CodexDiscoveryTests(unittest.TestCase):
    def _build_store(self, tmp: str) -> None:
        """Two Codex rollouts under a fake CODEX_HOME, newest first by mtime."""
        self.id_a = "11111111-1111-4111-8111-111111111111"
        self.id_b = "22222222-2222-4222-8222-222222222222"
        sessions = Path(tmp) / "sessions" / "2026" / "07" / "20"
        sessions.mkdir(parents=True)
        path_a = sessions / f"rollout-2026-07-20T11-00-00-{self.id_a}.jsonl"
        path_b = sessions / f"rollout-2026-07-20T10-00-00-{self.id_b}.jsonl"
        _write_jsonl(path_a, _codex_records("Alpha feature work", session_id=self.id_a))
        _write_jsonl(path_b, _codex_records("Beta bugfix", session_id=self.id_b))
        # A stub thread: opened, never used (only session_meta).
        stub = sessions / "rollout-2026-07-20T12-00-00-33333333-3333-4333-8333-333333333333.jsonl"
        _write_jsonl(stub, _codex_records("unused", session_id="33333333-3333-4333-8333-333333333333")[:1])
        os.utime(path_b, (1_000_000, 1_000_000))
        os.utime(path_a, (2_000_000, 2_000_000))
        os.utime(stub, (3_000_000, 3_000_000))

    def test_discover_and_resolve(self):
        with TemporaryDirectory() as tmp:
            self._build_store(tmp)
            with mock.patch.dict(os.environ, {"CODEX_HOME": tmp}):
                sessions = sr.discover_sessions("codex", FAKE_CWD)
                # The unused stub is not a resumable session.
                self.assertEqual(
                    [s["session_id"] for s in sessions], [self.id_a, self.id_b]
                )
                self.assertEqual(
                    sr.resolve_session("codex", "latest", FAKE_CWD)["session_id"], self.id_a
                )
                for alias in ("continue", "--continue", "-c", "CONTINUE"):
                    self.assertEqual(
                        sr.resolve_session("codex", alias, FAKE_CWD)["session_id"], self.id_a
                    )
                self.assertEqual(
                    sr.resolve_session("codex", self.id_b, FAKE_CWD)["session_id"], self.id_b
                )
                self.assertEqual(
                    sr.resolve_session("codex", "beta", FAKE_CWD)["session_id"], self.id_b
                )

    def test_subdirectory_sessions_are_listed_from_parent(self):
        sub_cwd = FAKE_CWD + "/apps/ios"
        uid = "44444444-4444-4444-8444-444444444444"
        with TemporaryDirectory() as tmp:
            sessions = Path(tmp) / "sessions" / "2026" / "07" / "20"
            sessions.mkdir(parents=True)
            _write_jsonl(
                sessions / f"rollout-2026-07-20T10-00-00-{uid}.jsonl",
                _codex_records("ios work", cwd=sub_cwd, session_id=uid),
            )
            with mock.patch.dict(os.environ, {"CODEX_HOME": tmp}):
                sessions = sr.discover_sessions("codex", FAKE_CWD)
                ids = [s["session_id"] for s in sessions]
                # ... but not from a sibling directory.
                sibling = [s["session_id"] for s in sr.discover_sessions("codex", FAKE_CWD + "/apps/agent")]
        self.assertIn(uid, ids)
        # The handoff reports where the session actually ran, not the listing cwd.
        self.assertEqual([s["cwd"] for s in sessions], [sub_cwd])
        self.assertNotIn(uid, sibling)

    def test_foreign_cwd_is_not_listed(self):
        with TemporaryDirectory() as tmp:
            sessions = Path(tmp) / "sessions" / "2026" / "07" / "20"
            sessions.mkdir(parents=True)
            _write_jsonl(
                sessions / "rollout-2026-07-20T10-00-00-55555555-5555-4555-8555-555555555555.jsonl",
                _codex_records("theirs", cwd="/some/other/place"),
            )
            with mock.patch.dict(os.environ, {"CODEX_HOME": tmp}):
                self.assertEqual(sr.discover_sessions("codex", FAKE_CWD), [])

    def test_state_database_drives_discovery(self):
        """The threads index is the primary source; only interactive threads count."""
        uid = "66666666-6666-4666-8666-666666666666"
        sub_uid = "77777777-7777-4777-8777-777777777777"
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            sessions = home / "sessions" / "2026" / "07" / "20"
            sessions.mkdir(parents=True)
            rollout = sessions / f"rollout-2026-07-20T10-00-00-{uid}.jsonl"
            _write_jsonl(rollout, _codex_records("indexed", session_id=uid))
            sub_rollout = sessions / f"rollout-2026-07-20T10-00-00-{sub_uid}.jsonl"
            _write_jsonl(
                sub_rollout,
                _codex_records("indexed subdir", cwd=FAKE_CWD + "/apps/ios", session_id=sub_uid),
            )
            database = home / "state_5.sqlite"
            con = sqlite3.connect(database)
            con.execute(
                "CREATE TABLE threads (id TEXT, rollout_path TEXT, updated_at_ms INTEGER, "
                "source TEXT, cwd TEXT, archived INTEGER, title TEXT, "
                "first_user_message TEXT, git_branch TEXT, name TEXT, thread_source TEXT)"
            )
            rows = [
                (uid, str(rollout), 2_000_000, "cli", FAKE_CWD, 0, "indexed", "", "main", None, "user"),
                (sub_uid, str(sub_rollout), 1_000_000, "vscode", FAKE_CWD + "/apps/ios", 0, "", "sub", None, None, "user"),
                # Harness-spawned thread: same cwd, not a user conversation.
                ("88888888-8888-4888-8888-888888888888", str(rollout), 3_000_000,
                 '{"subagent":{"other":"guardian"}}', FAKE_CWD, 0, "Guardian review", "", None, None,
                 "guardian_review"),
                # Archived thread.
                ("99999999-9999-4999-8999-999999999999", str(rollout), 4_000_000, "cli", FAKE_CWD, 1,
                 "archived", "", None, None, "user"),
            ]
            con.executemany("INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
            con.commit()
            con.close()
            with mock.patch.dict(os.environ, {"CODEX_HOME": tmp}):
                sessions = sr.discover_sessions("codex", FAKE_CWD)
                ids = [s["session_id"] for s in sessions]
                self.assertEqual(ids, [uid, sub_uid])
                self.assertEqual(sessions[0]["title"], "indexed")
                self.assertEqual(sessions[0]["branch"], "main")
                self.assertEqual(sessions[1]["title"], "sub")
                # Native id resolution goes through the index too.
                self.assertEqual(
                    sr.resolve_session("codex", sub_uid, FAKE_CWD)["session_id"], sub_uid
                )

    def test_unusable_index_falls_back_to_file_scan(self):
        """A corrupt/locked thread index must degrade to scanning sessions/."""
        uid = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        with TemporaryDirectory() as tmp:
            home = Path(tmp)
            sessions = home / "sessions" / "2026" / "07" / "20"
            sessions.mkdir(parents=True)
            _write_jsonl(
                sessions / f"rollout-2026-07-20T10-00-00-{uid}.jsonl",
                _codex_records("scanned", session_id=uid),
            )
            # Not a SQLite database: opening it raises. Discovery must not fail.
            (home / "state_5.sqlite").write_bytes(b"not a database at all")
            with mock.patch.dict(os.environ, {"CODEX_HOME": tmp}):
                ids = [s["session_id"] for s in sr.discover_sessions("codex", FAKE_CWD)]
        self.assertEqual(ids, [uid])

    def test_zstd_rollout_is_readable(self):
        try:
            from compression import zstd  # noqa: F401
        except ImportError:
            self.skipTest("stdlib compression.zstd requires Python 3.14+")
        uid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        with TemporaryDirectory() as tmp:
            sessions = Path(tmp) / "sessions" / "2026" / "07" / "20"
            sessions.mkdir(parents=True)
            path = sessions / f"rollout-2026-07-20T10-00-00-{uid}.jsonl.zst"
            body = "\n".join(json.dumps(r) for r in _codex_records("archived work", session_id=uid))
            path.write_bytes(zstd.compress(body.encode("utf-8")))
            with mock.patch.dict(os.environ, {"CODEX_HOME": tmp}):
                ids = [s["session_id"] for s in sr.discover_sessions("codex", FAKE_CWD)]
                result = sr.read_codex_session(path)
        self.assertIn(uid, ids)
        self.assertEqual(result["turns"][0]["text"], "archived work")


class CliTests(unittest.TestCase):
    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sr.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_cli_list_and_show(self):
        with TemporaryDirectory() as tmp:
            projects = Path(tmp) / "projects" / sr.slugify(FAKE_CWD)
            projects.mkdir(parents=True)
            uid = "11111111-1111-4111-8111-111111111111"
            _write_jsonl(projects / f"{uid}.jsonl", _claude_records("Only one"))
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": tmp}):
                code, out, _ = self._run(["claude", "list", "--cwd", FAKE_CWD, "--json"])
                self.assertEqual(code, 0)
                self.assertEqual(len(json.loads(out)["sessions"]), 1)

                code, out, _ = self._run(["claude", "show", "latest", "--cwd", FAKE_CWD, "--json"])
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(out)["session_id"], uid)

                # `continue` is an alias for the newest session (leading-dash forms
                # like `-c` are normalized to `latest` by the extension, not argv).
                code, out, _ = self._run(["claude", "show", "continue", "--cwd", FAKE_CWD, "--json"])
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(out)["session_id"], uid)

    def _build_ambiguous_store(self, tmp: str) -> None:
        projects = Path(tmp) / "projects" / sr.slugify(FAKE_CWD)
        projects.mkdir(parents=True)
        _write_jsonl(projects / "11111111-1111-4111-8111-111111111111.jsonl", _claude_records("Edge case"))
        _write_jsonl(projects / "22222222-2222-4222-8222-222222222222.jsonl", _claude_records("Elder scroll"))

    def test_claude_turn_cap_keeps_head_and_tail(self):
        records = []
        for i in range(30):
            records.append(
                {
                    "type": "user",
                    "uuid": f"u{i}",
                    "parentUuid": None if i == 0 else f"u{i - 1}",
                    "timestamp": f"2026-07-20T10:{i:02d}:00Z",
                    "cwd": FAKE_CWD,
                    "message": {"role": "user", "content": [{"type": "text", "text": f"p{i}"}]},
                }
            )
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.jsonl"
            _write_jsonl(path, records)
            result = sr.read_claude_session(path, max_turns=8)
        self.assertEqual(len(result["turns"]), 8)
        self.assertEqual(result["turns"][0]["text"], "p0")
        self.assertEqual(result["turns"][-1]["text"], "p29")

    def test_cli_ambiguous_json_returns_structured_matches(self):
        with TemporaryDirectory() as tmp:
            self._build_ambiguous_store(tmp)
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": tmp}):
                code, out, err = self._run(["claude", "show", "e", "--cwd", FAKE_CWD, "--json"])
        self.assertEqual(code, 2)
        # Structured mode: machine-readable payload on stdout, nothing on stderr.
        self.assertEqual(err, "")
        payload = json.loads(out)
        self.assertEqual(payload["error"], "ambiguous_reference")
        self.assertEqual(payload["reference"], "e")
        self.assertGreater(len(payload["matches"]), 1)
        first = payload["matches"][0]
        self.assertIn("session_id", first)
        self.assertIn("title", first)

    def test_cli_ambiguous_human_lists_matches_on_stderr(self):
        with TemporaryDirectory() as tmp:
            self._build_ambiguous_store(tmp)
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": tmp}):
                code, out, err = self._run(["claude", "show", "e", "--cwd", FAKE_CWD])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("matched", err)

    def test_cli_codex_list_and_show(self):
        uid = "12121212-1212-4212-8212-121212121212"
        with TemporaryDirectory() as tmp:
            sessions = Path(tmp) / "sessions" / "2026" / "07" / "20"
            sessions.mkdir(parents=True)
            _write_jsonl(
                sessions / f"rollout-2026-07-20T10-00-00-{uid}.jsonl",
                _codex_records("Only one codex session", session_id=uid),
            )
            with mock.patch.dict(os.environ, {"CODEX_HOME": tmp}):
                code, out, _ = self._run(["codex", "list", "--cwd", FAKE_CWD, "--json"])
                self.assertEqual(code, 0)
                self.assertEqual(len(json.loads(out)["sessions"]), 1)

                code, out, _ = self._run(["codex", "show", "latest", "--cwd", FAKE_CWD, "--json"])
                self.assertEqual(code, 0)
                payload = json.loads(out)
                self.assertEqual(payload["tool"], "codex")
                self.assertEqual(payload["session_id"], uid)
                self.assertEqual(payload["turns"][0]["text"], "Only one codex session")

    def test_cli_codex_list_warns_when_index_is_unusable(self):
        uid = "34343434-3434-4434-8434-343434343434"
        with TemporaryDirectory() as tmp:
            sessions = Path(tmp) / "sessions" / "2026" / "07" / "20"
            sessions.mkdir(parents=True)
            _write_jsonl(
                sessions / f"rollout-2026-07-20T10-00-00-{uid}.jsonl",
                _codex_records("scanned", session_id=uid),
            )
            # A corrupt index must degrade to the file scan *and* say so, instead
            # of reading as an empty store.
            (Path(tmp) / "state_5.sqlite").write_bytes(b"not a database at all")
            with mock.patch.dict(os.environ, {"CODEX_HOME": tmp}):
                code, out, _ = self._run(["codex", "list", "--cwd", FAKE_CWD, "--json"])
        payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual([s["session_id"] for s in payload["sessions"]], [uid])
        self.assertEqual(
            [w["code"] for w in payload["warnings"]], ["codex_index_unusable"]
        )

    def test_cli_show_accepts_a_dashed_reference_after_separator(self):
        # The extension passes `-- <ref>` so a leading dash becomes a literal
        # reference (readable "no match") instead of an argparse usage dump.
        with TemporaryDirectory() as tmp:
            projects = Path(tmp) / "projects" / sr.slugify(FAKE_CWD)
            projects.mkdir(parents=True)
            _write_jsonl(
                projects / "11111111-1111-4111-8111-111111111111.jsonl",
                _claude_records("Only one"),
            )
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": tmp}):
                code, out, err = self._run(
                    ["claude", "show", "--cwd", FAKE_CWD, "--json", "--", "--foo"]
                )
        self.assertEqual(code, 2)
        self.assertEqual(err, "")
        payload = json.loads(out)
        self.assertEqual(payload["error"], "reader_error")
        self.assertIn("--foo", payload["message"])

    def test_cli_reader_error_json_is_structured(self):
        # A non-ambiguous failure under --json must still be parseable JSON on
        # stdout (not human text on stderr), so the extension never shows a raw
        # blob as the error message.
        with TemporaryDirectory() as tmp:
            projects = Path(tmp) / "projects" / sr.slugify(FAKE_CWD)
            projects.mkdir(parents=True)
            _write_jsonl(projects / "11111111-1111-4111-8111-111111111111.jsonl", _claude_records("Only one"))
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": tmp}):
                code, out, err = self._run(["claude", "show", "nomatch-zzz", "--cwd", FAKE_CWD, "--json"])
        self.assertEqual(code, 2)
        self.assertEqual(err, "")
        payload = json.loads(out)
        self.assertEqual(payload["error"], "reader_error")
        self.assertIn("message", payload)


if __name__ == "__main__":
    unittest.main()
