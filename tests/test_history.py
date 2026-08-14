import json

from qa.history import (
    build_history_tree,
    delete_classification,
    load_classified_history,
    load_history,
    load_sequence_history,
    record_run,
    rewrite_classification,
)


class TestRecordAndLoadHistory:
    def test_records_and_reloads_a_run(self, tmp_path):
        path = str(tmp_path / "history.jsonl")

        record_run("SEQ 1 NON LOYAL", "wording", total=50, ok=30, ko=20, score=60.0, path=path)

        entries = load_history(path=path)
        assert len(entries) == 1
        assert entries[0]["sheet"] == "SEQ 1 NON LOYAL"
        assert entries[0]["score"] == 60.0
        assert "timestamp" in entries[0]

    def test_filters_by_sheet(self, tmp_path):
        path = str(tmp_path / "history.jsonl")

        record_run("SEQ 1", "wording", total=10, ok=5, ko=5, score=50.0, path=path)
        record_run("SEQ 2", "wording", total=10, ok=8, ko=2, score=80.0, path=path)
        record_run("SEQ 1", "wording", total=10, ok=9, ko=1, score=90.0, path=path)

        seq1_entries = load_history(sheet="SEQ 1", path=path)
        assert [e["score"] for e in seq1_entries] == [50.0, 90.0]

    def test_missing_file_returns_empty_list(self, tmp_path):
        path = str(tmp_path / "does_not_exist.jsonl")
        assert load_history(path=path) == []

    def test_corrupt_line_is_skipped(self, tmp_path):
        path = tmp_path / "history.jsonl"
        path.write_text('not valid json\n{"sheet": "SEQ 1", "score": 42}\n', encoding="utf-8")

        entries = load_history(path=str(path))
        assert len(entries) == 1
        assert entries[0]["score"] == 42

    def test_record_run_never_raises_on_unwritable_path(self):
        # Directory that cannot exist as a valid path component (a file
        # used as a directory) - should be swallowed, not raised.
        record_run("SEQ 1", "wording", total=1, ok=1, ko=0, score=100.0, path="/dev/null/impossible/history.jsonl")

    def test_country_trigger_sequence_stored_only_when_provided(self, tmp_path):
        path = str(tmp_path / "history.jsonl")

        record_run("SEQ 1", "wording", total=10, ok=10, ko=0, score=100.0, path=path)
        record_run(
            "SEQ 1", "wording", total=10, ok=10, ko=0, score=100.0,
            country="Germany", trigger="Revamp Inac", sequence="SEQ 1 NON LOYAL", path=path
        )

        entries = load_history(path=path)
        assert "country" not in entries[0]
        assert entries[1]["country"] == "Germany"
        assert entries[1]["trigger"] == "Revamp Inac"
        assert entries[1]["sequence"] == "SEQ 1 NON LOYAL"


class TestClassifiedHistory:
    def _seed(self, path):
        record_run("SEQ 1", "wording", 10, 10, 0, 100.0, path=path)  # non classe, ignore partout
        record_run(
            "SEQ 1 NON LOYAL", "wording", 61, 17, 44, 27.87, path=path,
            country="Germany", trigger="Revamp Inac", sequence="SEQ 1 NON LOYAL"
        )
        record_run(
            "UTM TRIGGER", "url", 21, 19, 2, 90.48, path=path,
            country="Germany", trigger="Revamp Inac", sequence="SEQ 1 NON LOYAL"
        )
        record_run(
            "SEQ 1 NON LOYAL", "combined", 82, 36, 46, 43.9, path=path,
            country="Germany", trigger="Revamp Inac", sequence="SEQ 1 NON LOYAL"
        )
        record_run(
            "SEQ 2 LOYAL", "wording", 30, 30, 0, 100.0, path=path,
            country="Germany", trigger="Revamp Inac", sequence="SEQ 2 LOYAL"
        )
        record_run(
            "SEQ 1 NON LOYAL", "wording", 20, 10, 10, 50.0, path=path,
            country="France", trigger="Revamp Inac", sequence="SEQ 1 NON LOYAL"
        )

    def test_load_classified_history_excludes_unclassified_entries(self, tmp_path):
        path = str(tmp_path / "history.jsonl")
        self._seed(path)

        entries = load_classified_history(path=path)
        assert len(entries) == 5
        assert all(e.get("country") and e.get("trigger") and e.get("sequence") for e in entries)

    def test_build_history_tree_groups_by_country_trigger_sequence(self, tmp_path):
        path = str(tmp_path / "history.jsonl")
        self._seed(path)

        tree = build_history_tree(path=path)

        assert set(tree.keys()) == {"Germany", "France"}
        assert set(tree["Germany"]["Revamp Inac"].keys()) == {"SEQ 1 NON LOYAL", "SEQ 2 LOYAL"}
        assert len(tree["Germany"]["Revamp Inac"]["SEQ 1 NON LOYAL"]) == 3
        assert len(tree["Germany"]["Revamp Inac"]["SEQ 2 LOYAL"]) == 1
        assert len(tree["France"]["Revamp Inac"]["SEQ 1 NON LOYAL"]) == 1

    def test_load_sequence_history_is_scoped_to_one_countrytriggersequence(self, tmp_path):
        path = str(tmp_path / "history.jsonl")
        self._seed(path)

        germany_entries = load_sequence_history("Germany", "Revamp Inac", "SEQ 1 NON LOYAL", path=path)
        assert len(germany_entries) == 3
        assert {e["mode"] for e in germany_entries} == {"wording", "url", "combined"}

        france_entries = load_sequence_history("France", "Revamp Inac", "SEQ 1 NON LOYAL", path=path)
        assert len(france_entries) == 1

    def test_empty_history_returns_empty_tree(self, tmp_path):
        path = str(tmp_path / "does_not_exist.jsonl")
        assert build_history_tree(path=path) == {}
        assert load_sequence_history("Germany", "Revamp Inac", "SEQ 1", path=path) == []


class TestRewriteAndDeleteClassification:
    def test_rewrite_moves_matching_entries_to_new_classification(self, tmp_path):
        path = str(tmp_path / "history.jsonl")
        record_run(
            "SEQ 1 NON FID", "url", 21, 19, 2, 90.48, path=path,
            country="Germany", trigger="Revamp Inac", sequence="SEQ 1 NON FID"
        )
        record_run(
            "SEQ 2 LOYAL", "wording", 30, 30, 0, 100.0, path=path,
            country="Germany", trigger="Revamp Inac", sequence="SEQ 2 LOYAL"
        )

        rewrite_classification(
            "Germany", "Revamp Inac", "SEQ 1 NON FID",
            "Germany", "Revamp Inac", "SEQ 1 NON LOYAL",
            path=path
        )

        tree = build_history_tree(path=path)
        assert "SEQ 1 NON FID" not in tree["Germany"]["Revamp Inac"]
        assert len(tree["Germany"]["Revamp Inac"]["SEQ 1 NON LOYAL"]) == 1
        # L'autre sequence n'est pas affectee.
        assert len(tree["Germany"]["Revamp Inac"]["SEQ 2 LOYAL"]) == 1

    def test_rewrite_merges_into_an_existing_sequence(self, tmp_path):
        path = str(tmp_path / "history.jsonl")
        record_run(
            "A", "wording", 10, 10, 0, 100.0, path=path,
            country="Germany", trigger="Revamp Inac", sequence="SEQ 1 NON FID"
        )
        record_run(
            "B", "url", 10, 5, 5, 50.0, path=path,
            country="Germany", trigger="Revamp Inac", sequence="SEQ 1 NON LOYAL"
        )

        rewrite_classification(
            "Germany", "Revamp Inac", "SEQ 1 NON FID",
            "Germany", "Revamp Inac", "SEQ 1 NON LOYAL",
            path=path
        )

        merged = load_sequence_history("Germany", "Revamp Inac", "SEQ 1 NON LOYAL", path=path)
        assert len(merged) == 2

    def test_rewrite_on_missing_file_is_a_no_op(self, tmp_path):
        path = str(tmp_path / "does_not_exist.jsonl")
        rewrite_classification("Germany", "T", "SEQ 1", "Germany", "T", "SEQ 2", path=path)  # ne doit pas lever

    def test_delete_removes_only_matching_entries(self, tmp_path):
        path = str(tmp_path / "history.jsonl")
        record_run(
            "SEQ 1", "wording", 10, 10, 0, 100.0, path=path,
            country="Germany", trigger="Revamp Inac", sequence="SEQ 1"
        )
        record_run(
            "SEQ 2", "wording", 10, 10, 0, 100.0, path=path,
            country="Germany", trigger="Revamp Inac", sequence="SEQ 2"
        )

        delete_classification("Germany", "Revamp Inac", "SEQ 1", path=path)

        tree = build_history_tree(path=path)
        assert "SEQ 1" not in tree["Germany"]["Revamp Inac"]
        assert "SEQ 2" in tree["Germany"]["Revamp Inac"]

    def test_delete_preserves_unclassified_and_corrupt_lines(self, tmp_path):
        path = tmp_path / "history.jsonl"
        path.write_text(
            'not valid json\n'
            '{"sheet": "raw", "mode": "wording", "total": 1, "ok": 1, "ko": 0, "score": 100.0}\n'
            + json.dumps({
                "sheet": "SEQ 1", "mode": "wording", "total": 1, "ok": 1, "ko": 0, "score": 100.0,
                "country": "Germany", "trigger": "T", "sequence": "SEQ 1"
            }) + "\n",
            encoding="utf-8"
        )

        delete_classification("Germany", "T", "SEQ 1", path=str(path))

        remaining = path.read_text(encoding="utf-8").splitlines()
        assert "not valid json" in remaining
        assert any('"sheet": "raw"' in line for line in remaining)
        assert not any('"sequence": "SEQ 1"' in line for line in remaining)

    def test_delete_on_missing_file_is_a_no_op(self, tmp_path):
        path = str(tmp_path / "does_not_exist.jsonl")
        delete_classification("Germany", "T", "SEQ 1", path=path)  # ne doit pas lever
