import json
from datetime import datetime

from qa.history import (
    aggregate_error_types,
    build_history_tree,
    delete_classification,
    error_type_monthly_trend,
    find_regression,
    load_classified_history,
    load_history,
    load_sequence_history,
    prune_history,
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

    def test_error_types_stored_only_when_non_empty(self, tmp_path):
        path = str(tmp_path / "history.jsonl")

        record_run("SEQ 1", "wording", total=10, ok=8, ko=2, score=80.0, path=path)
        record_run(
            "SEQ 1", "wording", total=10, ok=8, ko=2, score=80.0, path=path,
            error_types={"Minor typo": 1, "Content mismatch": 1}
        )
        record_run("SEQ 1", "wording", total=10, ok=8, ko=2, score=80.0, path=path, error_types={})

        entries = load_history(path=path)
        assert "error_types" not in entries[0]
        assert entries[1]["error_types"] == {"Minor typo": 1, "Content mismatch": 1}
        assert "error_types" not in entries[2]


class TestAggregateErrorTypes:
    def test_sums_across_entries(self):
        entries = [
            {"error_types": {"Minor typo": 2, "Content mismatch": 1}},
            {"error_types": {"Minor typo": 1}},
        ]
        assert aggregate_error_types(entries) == {"Minor typo": 3, "Content mismatch": 1}

    def test_entries_without_error_types_are_ignored(self):
        entries = [{"score": 50.0}, {"error_types": {}}, {"error_types": {"Minor typo": 1}}]
        assert aggregate_error_types(entries) == {"Minor typo": 1}

    def test_empty_input_returns_empty_dict(self):
        assert aggregate_error_types([]) == {}


class TestErrorTypeMonthlyTrend:
    def test_groups_by_calendar_month(self):
        entries = [
            {"timestamp": "2026-01-05 10:00:00", "error_types": {"Minor typo": 2}},
            {"timestamp": "2026-01-20 10:00:00", "error_types": {"Minor typo": 1, "Content mismatch": 1}},
            {"timestamp": "2026-02-01 10:00:00", "error_types": {"Minor typo": 5}},
        ]

        trend = error_type_monthly_trend(entries)

        by_period = {(r["period"], r["error_type"]): r["count"] for r in trend}
        assert by_period[("2026-01", "Minor typo")] == 3
        assert by_period[("2026-01", "Content mismatch")] == 1
        assert by_period[("2026-02", "Minor typo")] == 5
        # Triee chronologiquement par period.
        assert [r["period"] for r in trend] == sorted(r["period"] for r in trend)

    def test_entries_without_error_types_are_skipped(self):
        entries = [{"timestamp": "2026-01-05 10:00:00"}, {"timestamp": "2026-01-05 10:00:00", "error_types": {}}]
        assert error_type_monthly_trend(entries) == []


class TestFindRegression:
    def test_no_prior_history_returns_none(self, tmp_path):
        path = str(tmp_path / "history.jsonl")
        assert find_regression("Germany", "T", "SEQ 1", "wording", 80.0, path=path) is None

    def test_score_drop_above_threshold_is_flagged(self, tmp_path):
        path = str(tmp_path / "history.jsonl")
        record_run("SEQ 1", "wording", 10, 9, 1, 90.0, path=path, country="Germany", trigger="T", sequence="SEQ 1")

        result = find_regression("Germany", "T", "SEQ 1", "wording", 80.0, threshold=5, path=path)
        assert result == {"previous_score": 90.0, "current_score": 80.0, "delta": -10.0}

    def test_score_drop_below_threshold_is_not_flagged(self, tmp_path):
        path = str(tmp_path / "history.jsonl")
        record_run("SEQ 1", "wording", 10, 9, 1, 90.0, path=path, country="Germany", trigger="T", sequence="SEQ 1")

        assert find_regression("Germany", "T", "SEQ 1", "wording", 87.0, threshold=5, path=path) is None

    def test_score_improvement_is_not_flagged(self, tmp_path):
        path = str(tmp_path / "history.jsonl")
        record_run("SEQ 1", "wording", 10, 5, 5, 50.0, path=path, country="Germany", trigger="T", sequence="SEQ 1")

        assert find_regression("Germany", "T", "SEQ 1", "wording", 90.0, threshold=5, path=path) is None

    def test_filters_by_mode(self, tmp_path):
        path = str(tmp_path / "history.jsonl")
        record_run("SEQ 1", "url", 10, 9, 1, 90.0, path=path, country="Germany", trigger="T", sequence="SEQ 1")

        # Aucun historique "wording" pour cette sequence, seulement "url" -
        # ne doit pas comparer un score wording a un score url.
        assert find_regression("Germany", "T", "SEQ 1", "wording", 10.0, threshold=5, path=path) is None

    def test_compares_against_the_last_entry_only(self, tmp_path):
        path = str(tmp_path / "history.jsonl")
        record_run("SEQ 1", "wording", 10, 3, 7, 30.0, path=path, country="Germany", trigger="T", sequence="SEQ 1")
        record_run("SEQ 1", "wording", 10, 9, 1, 90.0, path=path, country="Germany", trigger="T", sequence="SEQ 1")

        # Le dernier run (90.0) sert de reference, pas le premier (30.0).
        assert find_regression("Germany", "T", "SEQ 1", "wording", 89.0, threshold=5, path=path) is None
        result = find_regression("Germany", "T", "SEQ 1", "wording", 80.0, threshold=5, path=path)
        assert result["previous_score"] == 90.0


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


class TestPruneHistory:
    NOW = datetime(2026, 8, 14)

    def _entry(self, timestamp, sequence="SEQ 1", country="Germany", trigger="T"):
        return {
            "timestamp": timestamp, "sheet": sequence, "mode": "wording",
            "total": 10, "ok": 10, "ko": 0, "score": 100.0,
            "country": country, "trigger": trigger, "sequence": sequence,
        }

    def _write(self, path, entries):
        with open(path, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

    def test_keeps_all_recent_entries_and_drops_old_ones(self, tmp_path):
        path = str(tmp_path / "history.jsonl")
        self._write(path, [
            self._entry("2020-01-01 00:00:00"),
            self._entry("2020-06-01 00:00:00"),
            self._entry("2026-01-01 00:00:00"),
            self._entry("2026-06-01 00:00:00"),
        ])

        prune_history(path=path, now=self.NOW)

        remaining = load_history(path=path)
        assert [e["timestamp"] for e in remaining] == ["2026-01-01 00:00:00", "2026-06-01 00:00:00"]

    def test_keeps_previous_entry_when_only_one_is_recent(self, tmp_path):
        path = str(tmp_path / "history.jsonl")
        self._write(path, [
            self._entry("2018-01-01 00:00:00"),
            self._entry("2020-01-01 00:00:00"),
            self._entry("2026-06-01 00:00:00"),
        ])

        prune_history(path=path, now=self.NOW)

        remaining = load_history(path=path)
        assert [e["timestamp"] for e in remaining] == ["2020-01-01 00:00:00", "2026-06-01 00:00:00"]

    def test_keeps_last_two_entries_when_sequence_is_dormant(self, tmp_path):
        path = str(tmp_path / "history.jsonl")
        self._write(path, [
            self._entry("2015-01-01 00:00:00"),
            self._entry("2018-01-01 00:00:00"),
            self._entry("2020-01-01 00:00:00"),
        ])

        prune_history(path=path, now=self.NOW)

        remaining = load_history(path=path)
        assert [e["timestamp"] for e in remaining] == ["2018-01-01 00:00:00", "2020-01-01 00:00:00"]

    def test_sequences_are_pruned_independently(self, tmp_path):
        path = str(tmp_path / "history.jsonl")
        self._write(path, [
            self._entry("2015-01-01 00:00:00", sequence="SEQ 1"),
            self._entry("2015-06-01 00:00:00", sequence="SEQ 1"),
            self._entry("2015-01-01 00:00:00", sequence="SEQ 2"),
            self._entry("2026-01-01 00:00:00", sequence="SEQ 2"),
            self._entry("2026-06-01 00:00:00", sequence="SEQ 2"),
        ])

        prune_history(path=path, now=self.NOW)

        remaining = load_history(path=path)
        seq1 = [e["timestamp"] for e in remaining if e["sequence"] == "SEQ 1"]
        seq2 = [e["timestamp"] for e in remaining if e["sequence"] == "SEQ 2"]
        assert seq1 == ["2015-01-01 00:00:00", "2015-06-01 00:00:00"]  # dormante -> 2 dernieres
        assert seq2 == ["2026-01-01 00:00:00", "2026-06-01 00:00:00"]  # 2 recentes -> la vieille est droppee

    def test_preserves_unclassified_and_corrupt_lines(self, tmp_path):
        path = tmp_path / "history.jsonl"
        path.write_text(
            "not valid json\n"
            + json.dumps({"sheet": "raw", "mode": "wording", "total": 1, "ok": 1, "ko": 0,
                          "score": 100.0, "timestamp": "2015-01-01 00:00:00"}) + "\n"
            + json.dumps(self._entry("2015-01-01 00:00:00")) + "\n"
            + json.dumps(self._entry("2015-06-01 00:00:00")) + "\n",
            encoding="utf-8"
        )

        prune_history(path=str(path), now=self.NOW)

        remaining = path.read_text(encoding="utf-8").splitlines()
        assert "not valid json" in remaining
        assert any('"sheet": "raw"' in line for line in remaining)
        # Sequence classee, 0 entree recente -> les 2 dernieres (donc les deux) sont gardees.
        assert sum(1 for line in remaining if '"sequence": "SEQ 1"' in line) == 2

    def test_missing_file_is_a_no_op(self, tmp_path):
        path = str(tmp_path / "does_not_exist.jsonl")
        prune_history(path=path, now=self.NOW)  # ne doit pas lever

    def test_record_run_prunes_automatically(self, tmp_path):
        path = str(tmp_path / "history.jsonl")
        self._write(path, [
            self._entry("2015-01-01 00:00:00"),
            self._entry("2015-06-01 00:00:00"),
            self._entry("2016-01-01 00:00:00"),
        ])

        record_run("SEQ 1", "wording", 10, 10, 0, 100.0, path=path,
                   country="Germany", trigger="T", sequence="SEQ 1")

        remaining = load_sequence_history("Germany", "T", "SEQ 1", path=path)
        # Avant ce run, la sequence etait dormante. Ce nouveau run devient
        # l'unique entree recente -> on la garde plus la derniere d'avant
        # (2016-01-01), les deux plus vieilles sont supprimees.
        assert len(remaining) == 2
        assert remaining[0]["timestamp"] == "2016-01-01 00:00:00"
