import contextlib
import io
import json
import os
import threading
import time
from datetime import datetime

import pandas as pd

import qa.history as history
from qa.history import (
    aggregate_error_types,
    build_history_tree,
    delete_classification,
    error_type_monthly_trend,
    export_history_csv,
    export_history_excel,
    find_regression,
    load_classified_history,
    load_history,
    load_sequence_history,
    migrate_legacy_jsonl_history,
    prune_history,
    record_run,
    rewrite_classification,
)


def _seed_raw_rows(path, rows):
    """Insere des lignes directement en base (plutot que via record_run, qui
    timestampe toujours avec datetime.now()) - necessaire pour les tests de
    prune_history, qui doivent controler precisement le timestamp de chaque
    entree. rows: liste de dicts avec au moins timestamp/sheet/mode/total/
    ok/ko/score, et optionnellement country/trigger/sequence."""
    with contextlib.closing(history._connect(path)) as conn:
        conn.executescript(history._SCHEMA)
        for r in rows:
            conn.execute(
                "INSERT INTO runs (timestamp, sheet, mode, total, ok, ko, score, "
                "country, trigger_name, sequence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    r["timestamp"], r.get("sheet", "SEQ 1"), r.get("mode", "wording"),
                    r.get("total", 10), r.get("ok", 10), r.get("ko", 0), r.get("score", 100.0),
                    r.get("country"), r.get("trigger"), r.get("sequence"),
                ),
            )
        conn.commit()


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

    def test_corrupt_database_file_is_treated_as_empty_history(self, tmp_path):
        # load_history ne doit jamais planter l'app si le fichier existe
        # mais n'est pas une base SQLite valide (ex: fichier tronque/corrompu),
        # et qu'aucune sauvegarde .bak n'existe non plus pour s'y rabattre.
        path = tmp_path / "history.db"
        path.write_text("this is not a sqlite database", encoding="utf-8")

        assert load_history(path=str(path)) == []

    def test_falls_back_to_backup_when_primary_file_becomes_corrupted(self, tmp_path):
        # Simule une synchronisation OneDrive interrompue en plein ecriture
        # (fichier principal tronque/corrompu APRES un enregistrement
        # reussi) : la sauvegarde .bak, rafraichie juste apres cette
        # ecriture, doit permettre de retrouver l'historique malgre tout,
        # plutot que de le traiter comme perdu en bloc.
        path = str(tmp_path / "history.db")
        record_run("SEQ 1", "wording", total=10, ok=8, ko=2, score=80.0, path=path)
        assert os.path.exists(path + ".bak")

        with open(path, "wb") as f:
            f.write(b"not a sqlite database anymore")

        entries = load_history(path=path)
        assert len(entries) == 1
        assert entries[0]["score"] == 80.0

    def test_truncated_primary_db_falls_back_to_backup_and_does_not_lose_data_on_next_write(self, tmp_path):
        # Bug reproduit : une synchronisation OneDrive interrompue peut
        # tronquer history.db a 0 octet. sqlite3.connect() + CREATE TABLE
        # IF NOT EXISTS reussissent silencieusement sur un fichier vide (une
        # base SQLite valide, juste vide), ce qui est indistinguable d'une
        # base valide qui se trouve juste vide si on ne verifie rien de
        # plus : une lecture y verrait [] plutot que de retomber sur .bak,
        # et un record_run qui suivrait ecraserait .bak (mutates=True) avec
        # cette base quasi-vide, detruisant l'historique reel de facon
        # permanente.
        path = str(tmp_path / "history.db")
        record_run("SEQ 1", "wording", 10, 8, 2, 80.0, path=path,
                   country="Germany", trigger="T", sequence="SEQ 1")
        record_run("SEQ 1", "wording", 10, 9, 1, 90.0, path=path,
                   country="Germany", trigger="T", sequence="SEQ 1")
        record_run("SEQ 1", "wording", 10, 10, 0, 100.0, path=path,
                   country="Germany", trigger="T", sequence="SEQ 1")
        assert os.path.exists(path + ".bak")

        # Simule la troncature a 0 octet (fichier existant, mais vide).
        with open(path, "wb"):
            pass
        assert os.path.getsize(path) == 0

        # Une lecture doit toujours retrouver l'historique reel via .bak,
        # pas un historique vide silencieusement accepte comme valide.
        entries = load_history(path=path)
        assert [e["score"] for e in entries] == [80.0, 90.0, 100.0]

        # Un nouvel enregistrement ne doit ni se perdre, ni ecraser .bak
        # avec moins de donnees que ce qu'il contenait avant la troncature.
        record_run("SEQ 1", "wording", 10, 10, 0, 95.0, path=path,
                   country="Germany", trigger="T", sequence="SEQ 1")

        entries_after = load_history(path=path)
        assert [e["score"] for e in entries_after] == [80.0, 90.0, 100.0, 95.0]

        backup_entries = load_history(path=path + ".bak")
        assert len(backup_entries) == 4

    def test_returns_empty_list_when_backup_is_also_corrupted(self, tmp_path):
        path = tmp_path / "history.db"
        backup = tmp_path / "history.db.bak"
        path.write_text("garbage", encoding="utf-8")
        backup.write_text("also garbage", encoding="utf-8")

        assert load_history(path=str(path)) == []

    def test_tested_by_stored_only_when_provided(self, tmp_path):
        path = str(tmp_path / "history.db")

        record_run("SEQ 1", "wording", total=10, ok=10, ko=0, score=100.0, path=path)
        record_run("SEQ 1", "wording", total=10, ok=10, ko=0, score=100.0, path=path, tested_by="mbonhomme")

        entries = load_history(path=path)
        assert "tested_by" not in entries[0]
        assert entries[1]["tested_by"] == "mbonhomme"

    def test_record_run_never_raises_on_unwritable_path(self):
        # Directory that cannot exist as a valid path component (a file
        # used as a directory) - should be swallowed, not raised.
        record_run("SEQ 1", "wording", total=1, ok=1, ko=0, score=100.0, path="/dev/null/impossible/history.jsonl")

    def test_write_and_read_functions_never_raise_on_oserror_from_connect(self, tmp_path, monkeypatch):
        # _connect() peut lever un OSError independamment de tout probleme
        # SQLite (ex: os.makedirs qui echoue parce que le repertoire est
        # verrouille par une synchronisation OneDrive) - avant le correctif,
        # seul record_run le rattrapait ; load_history/rewrite_classification/
        # prune_history/delete_classification ne rattrapaient que
        # sqlite3.DatabaseError et auraient laisse l'OSError remonter.
        path = str(tmp_path / "history.db")
        record_run("SEQ 1", "wording", total=1, ok=1, ko=0, score=100.0,
                    country="Germany", trigger="Old", sequence="SEQ 1", path=path)

        def _boom(_path):
            raise OSError("repertoire verrouille par une synchronisation OneDrive")

        monkeypatch.setattr(history, "_connect", _boom)

        assert load_history(path=path) == []
        rewrite_classification("Germany", "Old", "SEQ 1", "Germany", "New", "SEQ 1", path=path)
        prune_history(path=path)
        delete_classification("Germany", "New", "SEQ 1", path=path)

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

    def test_build_history_tree_uses_provided_entries_without_reloading(self, tmp_path, monkeypatch):
        # pages/1_Historique.py charge une seule fois via load_classified_history
        # et passe le resultat a build_history_tree(entries=...) pour eviter
        # une deuxieme lecture complete de la base a chaque rerun - ce test
        # verifie que build_history_tree n'appelle plus load_classified_history
        # du tout quand `entries` est fourni (path pointe volontairement vers
        # un fichier inexistant : si build_history_tree l'ignorait et relisait
        # quand meme, le test suivant l'aurait detecte par un arbre vide).
        path = str(tmp_path / "does_not_exist.jsonl")
        entries = [
            {"timestamp": "2026-01-05 10:00:00", "sheet": "SEQ 1", "mode": "wording",
             "total": 10, "ok": 8, "ko": 2, "score": 80.0,
             "country": "Germany", "trigger": "Revamp Inac", "sequence": "SEQ 1"},
        ]

        monkeypatch.setattr(history, "load_classified_history", lambda path=None: (_ for _ in ()).throw(
            AssertionError("build_history_tree ne doit pas relire la base quand entries est fourni")
        ))

        tree = build_history_tree(path=path, entries=entries)

        assert tree == {"Germany": {"Revamp Inac": {"SEQ 1": entries}}}


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

    def test_delete_preserves_unclassified_entries(self, tmp_path):
        path = str(tmp_path / "history.db")
        record_run("raw", "wording", total=1, ok=1, ko=0, score=100.0, path=path)  # non classe
        record_run(
            "SEQ 1", "wording", total=1, ok=1, ko=0, score=100.0, path=path,
            country="Germany", trigger="T", sequence="SEQ 1"
        )

        delete_classification("Germany", "T", "SEQ 1", path=path)

        remaining = load_history(path=path)
        assert len(remaining) == 1
        assert remaining[0]["sheet"] == "raw"
        assert "country" not in remaining[0]

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
        _seed_raw_rows(path, entries)

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

    def test_preserves_unclassified_entries(self, tmp_path):
        path = str(tmp_path / "history.jsonl")
        self._write(path, [
            {"timestamp": "2015-01-01 00:00:00", "sheet": "raw", "mode": "wording",
             "total": 1, "ok": 1, "ko": 0, "score": 100.0},  # non classe
            self._entry("2015-01-01 00:00:00"),
            self._entry("2015-06-01 00:00:00"),
        ])

        prune_history(path=path, now=self.NOW)

        remaining = load_history(path=path)
        assert any(e["sheet"] == "raw" and "country" not in e for e in remaining)
        # Sequence classee, 0 entree recente -> les 2 dernieres (donc les deux) sont gardees.
        assert sum(1 for e in remaining if e.get("sequence") == "SEQ 1") == 2

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


class TestExportHistory:
    def _entries(self):
        return [
            {
                "timestamp": "2026-01-05 10:00:00", "country": "Germany", "trigger": "Revamp Inac",
                "sequence": "SEQ 1", "mode": "wording", "total": 10, "ok": 8, "ko": 2, "score": 80.0,
                "error_types": {"Minor typo": 1, "Content mismatch": 1},
            },
            {
                "timestamp": "2026-02-01 10:00:00", "country": "France", "trigger": "Revamp Inac",
                "sequence": "SEQ 2", "mode": "url", "total": 5, "ok": 5, "ko": 0, "score": 100.0,
            },
        ]

    def test_csv_export_round_trips_the_core_columns(self):
        csv_bytes = export_history_csv(self._entries())

        df = pd.read_csv(io.BytesIO(csv_bytes), encoding="utf-8-sig")

        assert list(df["country"]) == ["Germany", "France"]
        assert list(df["sequence"]) == ["SEQ 1", "SEQ 2"]
        assert list(df["score"]) == [80.0, 100.0]

    def test_csv_export_uses_a_bom_for_excel_compatibility(self):
        csv_bytes = export_history_csv(self._entries())
        assert csv_bytes.startswith(b"\xef\xbb\xbf")

    def test_csv_export_serializes_error_types_as_json(self):
        csv_bytes = export_history_csv(self._entries())
        df = pd.read_csv(io.BytesIO(csv_bytes), encoding="utf-8-sig")

        assert json.loads(df["error_types"].iloc[0]) == {"Minor typo": 1, "Content mismatch": 1}
        # Entree sans error_types du tout -> objet JSON vide, pas une valeur manquante.
        assert json.loads(df["error_types"].iloc[1]) == {}

    def test_excel_export_is_readable_and_matches_the_csv_content(self):
        excel_bytes = export_history_excel(self._entries())

        df = pd.read_excel(io.BytesIO(excel_bytes), sheet_name="Historique", engine="openpyxl")

        assert list(df["country"]) == ["Germany", "France"]
        assert list(df["score"]) == [80.0, 100.0]

    def test_empty_history_still_produces_a_valid_header_only_export(self):
        csv_bytes = export_history_csv([])
        df = pd.read_csv(io.BytesIO(csv_bytes), encoding="utf-8-sig")
        assert list(df.columns) == [
            "timestamp", "country", "trigger", "sequence", "mode", "total", "ok", "ko", "score",
            "error_types", "tested_by",
        ]
        assert len(df) == 0


class TestMigrateLegacyJsonlHistory:
    """Migration unique de l'ancien historique JSONL (avant le passage a
    SQLite) vers la nouvelle base - voir qa.history.migrate_legacy_jsonl_history.
    Doit rester silencieuse et jamais destructive : l'ancien fichier n'est
    jamais supprime, seulement renomme apres import reussi."""

    def _write_legacy_jsonl(self, path, entries):
        with open(path, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

    def test_migrates_every_field_including_error_types(self, tmp_path):
        legacy_path = str(tmp_path / "history.jsonl")
        db_path = str(tmp_path / "history.db")
        self._write_legacy_jsonl(legacy_path, [
            {
                "timestamp": "2026-01-05 10:00:00", "sheet": "SEQ 1", "mode": "wording",
                "total": 10, "ok": 8, "ko": 2, "score": 80.0,
                "country": "Germany", "trigger": "Revamp Inac", "sequence": "SEQ 1",
                "error_types": {"Minor typo": 2},
            },
            {"timestamp": "2026-01-06 10:00:00", "sheet": "raw", "mode": "wording",
             "total": 1, "ok": 1, "ko": 0, "score": 100.0},  # non classe, sans error_types
        ])

        migrated = migrate_legacy_jsonl_history(legacy_path=legacy_path, path=db_path)

        assert migrated is True
        entries = load_history(path=db_path)
        assert len(entries) == 2
        assert entries[0]["country"] == "Germany"
        assert entries[0]["error_types"] == {"Minor typo": 2}
        assert "country" not in entries[1]
        assert "tested_by" not in entries[0]  # colonne inexistante avant la migration

    def test_renames_the_legacy_file_after_a_successful_migration(self, tmp_path):
        legacy_path = str(tmp_path / "history.jsonl")
        db_path = str(tmp_path / "history.db")
        self._write_legacy_jsonl(legacy_path, [
            {"timestamp": "2026-01-05 10:00:00", "sheet": "SEQ 1", "mode": "wording",
             "total": 1, "ok": 1, "ko": 0, "score": 100.0},
        ])

        migrate_legacy_jsonl_history(legacy_path=legacy_path, path=db_path)

        assert not os.path.exists(legacy_path)
        assert os.path.exists(legacy_path + ".migrated")

    def test_is_idempotent_and_does_not_duplicate_entries(self, tmp_path):
        legacy_path = str(tmp_path / "history.jsonl")
        db_path = str(tmp_path / "history.db")
        self._write_legacy_jsonl(legacy_path, [
            {"timestamp": "2026-01-05 10:00:00", "sheet": "SEQ 1", "mode": "wording",
             "total": 1, "ok": 1, "ko": 0, "score": 100.0},
        ])

        first = migrate_legacy_jsonl_history(legacy_path=legacy_path, path=db_path)
        # Le fichier legacy a ete renomme par le premier appel : le rappeler
        # avec le meme legacy_path (qui n'existe plus) simule un redemarrage
        # de l'app apres une migration deja effectuee.
        second = migrate_legacy_jsonl_history(legacy_path=legacy_path, path=db_path)

        assert first is True
        assert second is False
        assert len(load_history(path=db_path)) == 1

    def test_no_legacy_file_is_a_silent_noop(self, tmp_path):
        legacy_path = str(tmp_path / "does_not_exist.jsonl")
        db_path = str(tmp_path / "history.db")

        assert migrate_legacy_jsonl_history(legacy_path=legacy_path, path=db_path) is False
        assert not os.path.exists(db_path)

    def test_does_not_overwrite_an_already_migrated_database(self, tmp_path):
        # Le fichier legacy existe TOUJOURS (ex: laisse volontairement de
        # cote par l'utilisateur) mais la base cible existe deja - ne doit
        # jamais reimporter par-dessus un historique deja en place.
        legacy_path = str(tmp_path / "history.jsonl")
        db_path = str(tmp_path / "history.db")
        self._write_legacy_jsonl(legacy_path, [
            {"timestamp": "2026-01-05 10:00:00", "sheet": "SEQ 1", "mode": "wording",
             "total": 1, "ok": 1, "ko": 0, "score": 100.0},
        ])
        record_run("SEQ 2", "wording", total=1, ok=1, ko=0, score=100.0, path=db_path)

        assert migrate_legacy_jsonl_history(legacy_path=legacy_path, path=db_path) is False
        assert os.path.exists(legacy_path)  # pas touche
        entries = load_history(path=db_path)
        assert len(entries) == 1 and entries[0]["sheet"] == "SEQ 2"

    def test_corrupt_lines_in_the_legacy_file_are_skipped(self, tmp_path):
        legacy_path = tmp_path / "history.jsonl"
        db_path = str(tmp_path / "history.db")
        legacy_path.write_text(
            "not valid json\n"
            + json.dumps({"timestamp": "2026-01-05 10:00:00", "sheet": "SEQ 1", "mode": "wording",
                          "total": 1, "ok": 1, "ko": 0, "score": 100.0}) + "\n",
            encoding="utf-8"
        )

        migrate_legacy_jsonl_history(legacy_path=str(legacy_path), path=db_path)

        assert len(load_history(path=db_path)) == 1

    def test_concurrent_calls_do_not_duplicate_entries(self, tmp_path, monkeypatch):
        """Reproduit le scenario a l'origine du bug corrige : deux sessions
        Streamlit (deux threads du meme process) qui appelleraient la
        migration au meme moment, juste apres une mise a jour, avant que
        `path` n'existe encore. Le correctif verifie et ecrit sous le meme
        `_LOCK` : le second thread doit rester bloque jusqu'a la fin du
        premier, puis constater que `path` existe deja, plutot que
        d'importer les entrees une seconde fois."""
        legacy_path = str(tmp_path / "history.jsonl")
        db_path = str(tmp_path / "history.db")
        self._write_legacy_jsonl(legacy_path, [
            {"timestamp": "2026-01-05 10:00:00", "sheet": "SEQ 1", "mode": "wording",
             "total": 1, "ok": 1, "ko": 0, "score": 100.0},
        ])

        original_load = history._load_legacy_jsonl
        entered_critical_section = threading.Event()

        def slow_load(path):
            entered_critical_section.set()
            time.sleep(0.1)
            return original_load(path)

        monkeypatch.setattr(history, "_load_legacy_jsonl", slow_load)

        results = []

        def run():
            results.append(migrate_legacy_jsonl_history(legacy_path=legacy_path, path=db_path))

        first = threading.Thread(target=run)
        first.start()
        assert entered_critical_section.wait(timeout=2), "le premier thread n'a jamais atteint sa section critique"

        second = threading.Thread(target=run)
        second.start()
        first.join()
        second.join()

        assert sorted(results) == [False, True]
        assert len(load_history(path=db_path)) == 1
