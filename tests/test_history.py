"""Behavioral tests for independent, append-only JSONL session history."""

from __future__ import annotations

import builtins
import errno
import json
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))
import history


def _writer(root: str, session: str, group: str, count: int, start: object,
            results: object, same_key: bool = False) -> None:
    try:
        if not start.wait(10):
            raise RuntimeError("No start signal")
        events = []
        for number in range(count):
            key = "shared" if same_key else f"{group}-{number}"
            event = history.append_event(Path(root), history.DEFAULT_HISTORY, session,
                                         "work.logged", {"summary": key}, key, timeout=10)
            events.append(event)
        results.put(("ok", events))
    except BaseException as exc:
        results.put(("error", repr(exc)))


def _hold_lock(path: str, ready: object, release: object) -> None:
    import portalocker
    with portalocker.Lock(path, mode="ab", timeout=5):
        ready.set()
        release.wait(10)


class HistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="history acceptance ")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "repository with spaces"
        self.root.mkdir()
        self.session = str(uuid4())
        self.relative = history.DEFAULT_HISTORY
        self.directory = self.root / self.relative
        self.path = self.directory / f"{self.session}.jsonl"
        self.lock_path = self.directory / f"{self.session}.jsonl.lock"

    def append(self, key: str = "first", **kwargs: object) -> dict:
        options = {"root": self.root, "history": self.relative, "session": self.session,
                   "kind": "work.logged", "payload": {"summary": "Cambio revisado"},
                   "idempotency_key": key}
        options.update(kwargs)
        return history.append_event(**options)

    def row(self, **kwargs: object) -> dict:
        event = {"schema_version": 1, "event_id": str(uuid4()),
                 "idempotency_key": "fixture-1", "session_id": self.session,
                 "sequence": 1, "occurred_at": "2026-11-13T12:34:56.123456Z",
                 "kind": "work.logged", "source": "test", "payload": {"summary": "Uno"}}
        event.update(kwargs)
        return event

    @staticmethod
    def encode(*events: dict) -> bytes:
        return b"".join((json.dumps(event, ensure_ascii=True) + "\n").encode("utf-8")
                        for event in events)

    def snapshot(self) -> dict[str, bytes | None]:
        return {path.relative_to(self.root).as_posix(): path.read_bytes() if path.is_file() else None
                for path in self.root.rglob("*")}

    def test_appends_exact_fields_and_preserves_existing_prefix(self) -> None:
        first = self.append(payload={"summary": "Añadí documentación", "files": ["README.md"],
                                     "commands": ["python -B check.py"]})
        prefix = self.path.read_bytes()
        self.lock_path.write_bytes(b"persistent sidecar metadata\n")
        second = self.append("second", payload={"summary": "Verificación"})
        data = self.path.read_bytes()
        self.assertTrue(data.startswith(prefix))
        self.assertTrue(data.endswith(b"\n"))
        self.assertEqual(history.parse_segment(data, self.session), [first, second])
        self.assertEqual(set(first), {"schema_version", "event_id", "idempotency_key",
                                     "session_id", "sequence", "occurred_at", "kind",
                                     "source", "payload"})
        self.assertEqual([first["sequence"], second["sequence"]], [1, 2])
        self.assertNotEqual(first["event_id"], second["event_id"])
        self.assertIn("Añadí".encode(), prefix)
        self.assertEqual(self.lock_path.read_bytes(), b"persistent sidecar metadata\n")
        report = history.verify_history(self.root, self.relative)
        self.assertEqual(report["state"], "valid")
        self.assertEqual(report["event_count"], 2)
        self.assertEqual(report["semantic_review"], "not_attested")
        self.assertEqual(report["sessions"], [{"session_id": self.session,
                         "path": self.path.relative_to(self.root).as_posix(),
                         "state": "open", "event_count": 2, "last_sequence": 2}])

    def test_existing_noncanonical_json_bytes_are_not_reserialized(self) -> None:
        self.directory.mkdir(parents=True)
        prefix = self.encode(self.row())
        self.path.write_bytes(prefix)
        self.append()
        self.assertTrue(self.path.read_bytes().startswith(prefix))

    def test_identical_retry_returns_original_event_and_resyncs(self) -> None:
        first = self.append(payload={"a": 1, "b": {"nested": True}})
        before = self.path.read_bytes()
        with patch.object(history.os, "fsync", wraps=os.fsync) as sync:
            retry = self.append(payload={"b": {"nested": True}, "a": 1})
        self.assertEqual(retry, first)
        self.assertEqual(sync.call_count, 1)
        self.assertEqual(self.path.read_bytes(), before)

    def test_reusing_a_key_with_different_content_never_appends(self) -> None:
        self.append(payload={"value": 1})
        before = self.path.read_bytes()
        for changes in ({"payload": {"value": 2}}, {"payload": {"value": True}},
                        {"payload": {"value": 1.0}}, {"kind": "docs.checked"},
                        {"source": "another-writer"}):
            options = {"payload": {"value": 1}, **changes}
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "Conflicto"):
                self.append(**options)
            self.assertEqual(self.path.read_bytes(), before)

    def test_closed_is_terminal_but_old_retries_still_succeed(self) -> None:
        first = self.append()
        closed = self.append("close", kind="session.closed", payload={"summary": "Fin"})
        before = self.path.read_bytes()
        self.assertEqual(self.append(), first)
        self.assertEqual(self.append("close", kind="session.closed", payload={"summary": "Fin"}), closed)
        with self.assertRaisesRegex(ValueError, "cerrada"):
            self.append("late")
        with self.assertRaisesRegex(ValueError, "Conflicto"):
            self.append("close", kind="session.closed", payload={"summary": "Otro cierre"})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(history.verify_history(self.root, self.relative)["sessions"][0]["state"], "closed")
        extra = self.row(sequence=3, idempotency_key="after-close")
        with self.assertRaisesRegex(ValueError, "cerrado"):
            history.parse_segment(before + self.encode(extra), self.session)

    def test_fsync_failure_is_unacknowledged_and_same_key_retry_is_safe(self) -> None:
        self.append()
        prefix = self.path.read_bytes()
        with patch.object(history.os, "fsync", side_effect=OSError(errno.EIO, "injected fsync failure")):
            with self.assertRaisesRegex(OSError, "injected fsync failure"):
                self.append("uncertain", payload={"summary": "Persistencia incierta"})
        after_failure = self.path.read_bytes()
        self.assertTrue(after_failure.startswith(prefix))
        written = history.parse_segment(after_failure, self.session)
        self.assertEqual(len(written), 2)
        with patch.object(history.os, "fsync", wraps=os.fsync) as sync:
            retry = self.append("uncertain", payload={"summary": "Persistencia incierta"})
        self.assertEqual(sync.call_count, 1)
        self.assertEqual(retry, written[-1])
        self.assertEqual(self.path.read_bytes(), after_failure)

    def test_complete_bytes_are_flushed_before_fsync_and_ack(self) -> None:
        actual_sync = os.fsync
        observed: list[list[dict]] = []

        def record(descriptor: int) -> None:
            observed.append(history.parse_segment(self.path.read_bytes(), self.session))
            actual_sync(descriptor)

        with patch.object(history.os, "fsync", side_effect=record):
            event = self.append()
        self.assertEqual(observed, [[event]])

    def test_short_write_preserves_prefix_leaves_visible_tail_and_never_repairs(self) -> None:
        self.append()
        prefix = self.path.read_bytes()
        actual_fdopen = os.fdopen

        class ShortWriter:
            def __init__(self, stream: object) -> None:
                self.stream = stream

            def __getattr__(self, name: str) -> object:
                return getattr(self.stream, name)

            def __enter__(self) -> object:
                self.stream.__enter__()
                return self

            def __exit__(self, *arguments: object) -> object:
                return self.stream.__exit__(*arguments)

            def write(self, data: bytes) -> int:
                return self.stream.write(data[:len(data) // 2])

        def partially_writing(descriptor: int, mode: str) -> object:
            return ShortWriter(actual_fdopen(descriptor, mode))

        with patch.object(history.os, "fdopen", side_effect=partially_writing):
            with self.assertRaisesRegex(OSError, "Escritura incompleta"):
                self.append("partial")
        damaged = self.path.read_bytes()
        self.assertTrue(damaged.startswith(prefix))
        self.assertGreater(len(damaged), len(prefix))
        self.assertFalse(damaged.endswith(b"\n"))
        with self.assertRaisesRegex(ValueError, "cola incompleta"):
            self.append("partial")
        self.assertEqual(self.path.read_bytes(), damaged)

    def test_incomplete_and_corrupt_segments_fail_without_changing_bytes(self) -> None:
        self.append()
        prefix = self.path.read_bytes()
        corruptions = [prefix + b'{"payload":', prefix + b"not json\n",
                       prefix + b"\n", prefix + b'{"x": 1, "x": 2}\n',
                       prefix[:-1], prefix + b"\xff\n"]
        for data in corruptions:
            with self.subTest(data=data[-30:]):
                self.path.write_bytes(data)
                for operation in (lambda: self.append("new"),
                                  lambda: history.verify_history(self.root, self.relative),
                                  lambda: history.render_history(self.root, self.relative)):
                    with self.assertRaises(ValueError):
                        operation()
                    self.assertEqual(self.path.read_bytes(), data)

    def test_parser_rejects_bad_contract_values(self) -> None:
        invalid = [self.row(schema_version=True), self.row(schema_version=2),
                   self.row(sequence=True), self.row(sequence=0), self.row(sequence=2),
                   self.row(sequence=1.0), self.row(event_id="not-a-uuid"),
                   self.row(session_id=self.session.upper()), self.row(idempotency_key=""),
                   self.row(source="\ncli"), self.row(kind="Invalid kind"),
                   self.row(occurred_at="2026-02-30T12:00:00Z"),
                   self.row(occurred_at="2026-11-13T12:00:00+00:00"),
                   self.row(payload=[]), self.row(payload={"x": "\ud800"}),
                   self.row(extra="unknown field")]
        missing = self.row()
        del missing["source"]
        invalid.append(missing)
        for row in invalid:
            with self.subTest(row=row), self.assertRaises(ValueError):
                history.parse_segment(self.encode(row), self.session)
        for data in (b"[]\n", b"{}\n", b"null\n", b"\xef\xbb\xbf{}\n", b"\xff\n"):
            with self.subTest(data=data), self.assertRaises(ValueError):
                history.parse_segment(data, self.session)

    def test_parser_rejects_duplicate_keys_and_nonfinite_numbers_at_any_depth(self) -> None:
        baseline = self.encode(self.row(payload={"x": 0}))
        replacements = [b'{"x": 0, "x": 1}', b'{"nested": {"a": 1, "a": 2}}',
                        b'{"x": NaN}', b'{"x": Infinity}', b'{"x": -Infinity}',
                        b'{"x": 1e999}']
        for replacement in replacements:
            data = baseline.replace(b'{"x": 0}', replacement)
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                history.parse_segment(data, self.session)
        duplicated = baseline.replace(b'"schema_version": 1', b'"schema_version": 1, "schema_version": 1')
        with self.assertRaisesRegex(ValueError, "duplicada"):
            history.parse_segment(duplicated, self.session)

    def test_parser_rejects_duplicate_ids_keys_mixed_sessions_and_gaps(self) -> None:
        first = self.row()
        second = self.row(sequence=2, idempotency_key="second")
        for changes in ({"event_id": first["event_id"]}, {"idempotency_key": first["idempotency_key"]},
                        {"session_id": str(uuid4())}, {"sequence": 3}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                history.parse_segment(self.encode(first, {**second, **changes}), self.session)
        with self.assertRaisesRegex(ValueError, "no coincide"):
            history.parse_segment(self.encode(first), str(uuid4()))

    def test_bad_append_arguments_create_no_files_or_directories(self) -> None:
        invalid = [{"session": "../escape"}, {"session": self.session.upper()},
                   {"idempotency_key": ""}, {"kind": "Bad kind"}, {"source": "\x00"},
                   {"payload": []}, {"payload": {1: "number key"}},
                   {"payload": {"x": float("nan")}}, {"payload": {"x": float("inf")}},
                   {"payload": {"x": (1, 2)}}, {"payload": {"x": "\udfff"}},
                   {"timeout": True}, {"timeout": -1}, {"timeout": float("inf")},
                   {"history": "/tmp/outside"}, {"history": "../outside"},
                   {"history": "docs/../outside"}, {"history": "."},
                   {"history": "docs//history"}, {"history": "docs/history/"},
                   {"history": "C:/history"}, {"history": "docs\\history"}]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.append(**changes)
            self.assertEqual(list(self.root.iterdir()), [])

    def test_depth_and_size_limits_are_enforced_without_truncation(self) -> None:
        nested: dict = {}
        for _ in range(history.MAX_JSON_DEPTH + 2):
            nested = {"x": nested}
        with self.assertRaisesRegex(ValueError, "profundidad"):
            self.append(payload=nested)
        with patch.object(history, "MAX_EVENT_BYTES", 100):
            with self.assertRaises(ValueError):
                self.append(payload={"x": "x" * 200})
        self.assertFalse(self.directory.exists())
        self.append()
        prefix = self.path.read_bytes()
        with patch.object(history, "MAX_SEGMENT_BYTES", len(prefix) + 1):
            with self.assertRaises(ValueError):
                self.append("overflow")
        self.assertEqual(self.path.read_bytes(), prefix)
        with patch.object(history, "MAX_EVENT_BYTES", 32), self.assertRaises(ValueError):
            history.parse_segment(prefix, self.session)

    def test_readers_create_nothing_and_ignore_operational_sidecars(self) -> None:
        self.append(payload={"summary": "Texto con ``` y <script>", "commands": ["echo hola"]})
        self.lock_path.unlink()
        before = self.snapshot()
        real_open = os.open

        def read_only(path: object, flags: int, *args: object, **kwargs: object) -> int:
            self.assertEqual(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND), 0)
            return real_open(path, flags, *args, **kwargs)

        with patch.object(history.os, "open", side_effect=read_only), \
                patch.object(history.os, "mkdir", side_effect=AssertionError("reader mkdir")), \
                patch.object(history, "_sync", side_effect=AssertionError("reader fsync")):
            report = history.verify_history(self.root, self.relative)
            rendered = history.render_history(self.root, self.relative)
        self.assertEqual(report["event_count"], 1)
        self.assertIn("not_attested", rendered)
        self.assertIn("````json", rendered)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.lock_path.exists())

    def test_trifecta_entry_template_is_adapted_with_escaped_text_and_complete_payload(self) -> None:
        payload = {"summary": "Cambio <script>\n# falso título **negrita**",
                   "files": ["README.md", "[enlace](https://example.invalid)"],
                   "commands": ["echo `texto`"], "additional": {"value": 42}}
        event = self.append(payload=payload)
        prefix = self.path.read_bytes()
        rendered = history.render_history(self.root, self.relative)
        self.assertIn("- **Summary**: Cambio &lt;script&gt;", rendered)
        self.assertIn("- **Files**: README\\.md, \\[enlace\\]", rendered)
        self.assertIn("- **Commands**: echo \\`texto\\`", rendered)
        self.assertNotIn("\n# falso título", rendered)
        fenced_json = rendered.split("```json\n", 1)[1].split("\n```", 1)[0]
        self.assertEqual(json.loads(fenced_json), payload)
        self.assertEqual(history.parse_segment(prefix)[0], event)
        self.assertEqual(self.path.read_bytes(), prefix)

    def test_missing_history_is_an_error_and_empty_existing_history_is_valid(self) -> None:
        for operation in (history.verify_history, history.render_history):
            with self.assertRaises(FileNotFoundError):
                operation(self.root, self.relative)
        self.assertEqual(list(self.root.iterdir()), [])
        self.directory.mkdir(parents=True)
        report = history.verify_history(self.root, self.relative)
        self.assertEqual(report["sessions"], [])
        self.assertEqual(report["event_count"], 0)
        self.path.write_bytes(b"")
        self.assertEqual(history.parse_segment(b"", self.session), [])
        self.assertEqual(history.verify_history(self.root, self.relative)["event_count"], 0)
        self.assertEqual(self.append()["sequence"], 1)

    def test_selected_session_is_scoped_and_global_duplicate_ids_fail(self) -> None:
        first = self.append()
        another = str(uuid4())
        other_path = self.directory / f"{another}.jsonl"
        other_path.write_bytes(self.encode({**first, "session_id": another}))
        with self.assertRaisesRegex(ValueError, "entre sesiones"):
            history.verify_history(self.root, self.relative)
        self.assertEqual(history.verify_history(self.root, self.relative, self.session)["event_count"], 1)
        # Aislar el nombre inválido del error de duplicación anterior: el orden
        # lexicográfico de los UUID no debe decidir qué excepción se observa.
        other_path.write_bytes(self.encode({**first, "session_id": another,
                                           "event_id": str(uuid4())}))
        self.assertEqual(history.verify_history(self.root, self.relative)["event_count"], 2)
        (self.directory / "bad-name.jsonl").write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "UUID"):
            history.verify_history(self.root, self.relative)

    def test_symlink_segment_and_parents_are_rejected_without_touching_targets(self) -> None:
        outside = self.base / "outside.jsonl"
        original = self.encode(self.row())
        outside.write_bytes(original)
        self.directory.mkdir(parents=True)
        self.path.symlink_to(outside)
        for operation in (lambda: self.append(),
                          lambda: history.verify_history(self.root, self.relative),
                          lambda: history.render_history(self.root, self.relative)):
            with self.assertRaisesRegex(ValueError, "simbólico"):
                operation()
        self.assertEqual(outside.read_bytes(), original)
        self.assertFalse(self.lock_path.exists())
        linked_root = self.base / "linked root"
        linked_root.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "simbólico"):
            self.append(root=linked_root)
        outside_directory = self.base / "outside history"
        outside_directory.mkdir()
        link = self.root / "linked-history"
        link.symlink_to(outside_directory, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "simbólico"):
            self.append(history="linked-history/child")
        self.assertEqual(list(outside_directory.iterdir()), [])

    def test_symlink_or_hardlink_lock_is_rejected_without_writing(self) -> None:
        self.append()
        prefix = self.path.read_bytes()
        target = self.base / "outside lock"
        target.write_bytes(b"keep lock contents\n")
        self.lock_path.unlink()
        self.lock_path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "simbólico"):
            self.append("later")
        self.assertEqual(self.path.read_bytes(), prefix)
        self.lock_path.unlink()
        os.link(target, self.lock_path)
        with self.assertRaisesRegex(ValueError, "duros"):
            self.append("later")
        self.assertEqual(target.read_bytes(), b"keep lock contents\n")
        self.assertEqual(self.path.read_bytes(), prefix)

    def test_hardlinked_and_nonregular_segments_are_rejected(self) -> None:
        self.append()
        prefix = self.path.read_bytes()
        alias = self.base / "alias.jsonl"
        os.link(self.path, alias)
        for operation in (lambda: self.append("later"),
                          lambda: history.verify_history(self.root, self.relative)):
            with self.assertRaisesRegex(ValueError, "duros"):
                operation()
        self.assertEqual(alias.read_bytes(), prefix)
        self.path.unlink()
        self.path.mkdir()
        with self.assertRaisesRegex(ValueError, "regular"):
            self.append("later")
        self.path.rmdir()
        if hasattr(os, "mkfifo"):
            os.mkfifo(self.path)
            with self.assertRaisesRegex(ValueError, "regular"):
                self.append("later")

    def test_optional_writer_dependency_failure_does_not_create_storage(self) -> None:
        importer = builtins.__import__

        def without_portalocker(name: str, *args: object, **kwargs: object) -> object:
            if name == "portalocker":
                raise ImportError("injected optional dependency absence")
            return importer(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=without_portalocker):
            with self.assertRaisesRegex(OSError, "portalocker==4.4.2"):
                self.append()
        self.assertFalse(self.directory.exists())

    def _processes(self, workers: int, count: int, same_key: bool = False) -> list[dict]:
        context = multiprocessing.get_context("spawn")
        start = context.Event()
        results = context.Queue()
        processes = [context.Process(target=_writer, args=(str(self.root), self.session,
                     f"writer-{index}", count, start, results, same_key)) for index in range(workers)]
        try:
            for process in processes:
                process.start()
            start.set()
            events = []
            for _ in processes:
                state, result = results.get(timeout=20)
                self.assertEqual(state, "ok", result)
                events.extend(result)
            for process in processes:
                process.join(timeout=10)
                self.assertEqual(process.exitcode, 0)
            return events
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
            results.close()
            results.join_thread()

    def test_multiprocess_append_has_exact_count_no_losses_and_preserved_prefix(self) -> None:
        self.append("seed")
        prefix = self.path.read_bytes()
        workers, count = 6, 6
        acknowledgements = self._processes(workers, count)
        data = self.path.read_bytes()
        events = history.parse_segment(data, self.session)
        self.assertTrue(data.startswith(prefix))
        self.assertEqual(len(events), 1 + workers * count)
        self.assertEqual([event["sequence"] for event in events], list(range(1, len(events) + 1)))
        self.assertEqual(len({event["event_id"] for event in events}), len(events))
        expected = {f"writer-{worker}-{number}" for worker in range(workers) for number in range(count)}
        self.assertEqual({event["idempotency_key"] for event in events[1:]}, expected)
        self.assertEqual({event["event_id"] for event in acknowledgements},
                         {event["event_id"] for event in events[1:]})

    def test_multiprocess_same_key_is_written_exactly_once(self) -> None:
        acknowledgements = self._processes(workers=5, count=1, same_key=True)
        events = history.parse_segment(self.path.read_bytes(), self.session)
        self.assertEqual(len(events), 1)
        self.assertTrue(all(event == events[0] for event in acknowledgements))

    def test_real_lock_contention_times_out_without_appending_or_deleting_sidecar(self) -> None:
        self.append()
        prefix = self.path.read_bytes()
        lock_identity = self.lock_path.stat().st_ino
        context = multiprocessing.get_context("spawn")
        ready, release = context.Event(), context.Event()
        holder = context.Process(target=_hold_lock, args=(str(self.lock_path), ready, release))
        holder.start()
        try:
            self.assertTrue(ready.wait(timeout=10))
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                self.append("blocked", timeout=0.15)
            self.assertLess(time.monotonic() - started, 3)
            self.assertEqual(self.path.read_bytes(), prefix)
            self.assertEqual(self.lock_path.stat().st_ino, lock_identity)
        finally:
            release.set()
            holder.join(timeout=10)
            if holder.is_alive():
                holder.terminate()
                holder.join(timeout=5)
        self.assertEqual(holder.exitcode, 0)
        self.assertEqual(self.append("unblocked")["sequence"], 2)


if __name__ == "__main__":
    unittest.main()
