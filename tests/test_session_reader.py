#!/usr/bin/env python3
"""Standard-library unit tests for session_reader.

Run with:  python3 -m unittest discover -s tests -p 'test_*.py'
       or:  npm test
"""

from __future__ import annotations

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

_SCRIPTS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "skills",
    "resume-claude",
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
                self.assertEqual(
                    sr.resolve_session("claude", self.id_b, FAKE_CWD)["session_id"], self.id_b
                )
                self.assertEqual(
                    sr.resolve_session("claude", "beta", FAKE_CWD)["session_id"], self.id_b
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

    def test_cli_ambiguous_exits_two(self):
        with TemporaryDirectory() as tmp:
            projects = Path(tmp) / "projects" / sr.slugify(FAKE_CWD)
            projects.mkdir(parents=True)
            _write_jsonl(projects / "11111111-1111-4111-8111-111111111111.jsonl", _claude_records("Edge case"))
            _write_jsonl(projects / "22222222-2222-4222-8222-222222222222.jsonl", _claude_records("Elder scroll"))
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": tmp}):
                code, _, err = self._run(["claude", "show", "e", "--cwd", FAKE_CWD, "--json"])
        self.assertEqual(code, 2)
        self.assertIn("matched", err)


if __name__ == "__main__":
    unittest.main()
