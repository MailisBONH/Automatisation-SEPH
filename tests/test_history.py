import json

from qa.history import load_history, record_run


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
