import os
import time

import pytest

import qa.archive as archive
from conftest import fake_incrementing_datetime


@pytest.fixture(autouse=True)
def isolated_output(tmp_path, monkeypatch):
    """Redirige OUTPUT_DIR vers un dossier temporaire pour isoler les tests
    du vrai output/ du projet."""
    monkeypatch.setattr(archive, "OUTPUT_DIR", str(tmp_path / "output"))
    return tmp_path


def _make_report(tmp_path, name="report.xlsx"):
    path = tmp_path / name
    path.write_bytes(b"fake xlsx content")
    return str(path)


class TestSafeFolderName:
    def test_sanitizes_forbidden_characters(self):
        assert archive.safe_folder_name('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"

    def test_blank_falls_back_to_unclassified_label(self):
        assert archive.safe_folder_name("") == archive.UNCLASSIFIED_LABEL
        assert archive.safe_folder_name("   ") == archive.UNCLASSIFIED_LABEL
        assert archive.safe_folder_name(None) == archive.UNCLASSIFIED_LABEL

    def test_leaves_normal_names_unchanged(self):
        assert archive.safe_folder_name("SEQ 1 NON LOYAL") == "SEQ 1 NON LOYAL"


class TestSequenceDir:
    def test_builds_nested_sanitized_path(self, isolated_output):
        result = archive.sequence_dir("Germany", "Revamp Inac", "SEQ 1 NON LOYAL")
        assert result == os.path.join(archive.OUTPUT_DIR, "Germany", "Revamp Inac", "SEQ 1 NON LOYAL")


class TestArchiveReportAndListReports:
    def test_archives_under_type_prefixed_filename(self, tmp_path, isolated_output):
        report = _make_report(tmp_path)
        dest = archive.archive_report(report, "Germany", "Revamp Inac", "SEQ 1 NON LOYAL", mode="wording")

        assert os.path.exists(dest)
        assert os.path.basename(dest).startswith("QA_wording_")
        assert dest != report

    def test_list_reports_filters_by_mode(self, tmp_path, isolated_output, monkeypatch):
        fake_incrementing_datetime(monkeypatch)
        report = _make_report(tmp_path)

        archive.archive_report(report, "Germany", "Revamp Inac", "SEQ 1", mode="wording")
        archive.archive_report(report, "Germany", "Revamp Inac", "SEQ 1", mode="url")

        assert len(archive.list_reports("Germany", "Revamp Inac", "SEQ 1", mode="wording")) == 1
        assert len(archive.list_reports("Germany", "Revamp Inac", "SEQ 1", mode="url")) == 1
        assert len(archive.list_reports("Germany", "Revamp Inac", "SEQ 1")) == 2

    def test_list_reports_empty_for_unknown_sequence(self, isolated_output):
        assert archive.list_reports("Germany", "Revamp Inac", "SEQ 1") == []

    def test_rotation_keeps_only_last_two_per_type(self, tmp_path, isolated_output, monkeypatch):
        fake_incrementing_datetime(monkeypatch)
        report = _make_report(tmp_path)

        # archive_report() trie par mtime reel du fichier (pas par le
        # timestamp du nom de fichier) pour decider quoi purger : sur un
        # fichier factice minuscule, 4 appels consecutifs peuvent finir avec
        # des mtimes indiscernables. On force un ordre deterministe en
        # antidatant chaque fichier juste apres sa creation (l'appel suivant
        # le voit alors comme "plus ancien" que le sien, cree a l'instant).
        backdate_base = time.time() - 60
        paths = []
        for i in range(4):
            p = archive.archive_report(report, "Germany", "Revamp Inac", "SEQ 1", mode="wording")
            os.utime(p, (backdate_base + i, backdate_base + i))
            paths.append(p)

        reports = archive.list_reports("Germany", "Revamp Inac", "SEQ 1", mode="wording")
        assert len(reports) == archive.KEEP_REPORTS
        assert not os.path.exists(paths[0])
        assert not os.path.exists(paths[1])
        assert os.path.exists(paths[2])
        assert os.path.exists(paths[3])

    def test_rotation_does_not_affect_other_types(self, tmp_path, isolated_output, monkeypatch):
        fake_incrementing_datetime(monkeypatch)
        report = _make_report(tmp_path)

        for _ in range(3):
            archive.archive_report(report, "Germany", "Revamp Inac", "SEQ 1", mode="wording")
        archive.archive_report(report, "Germany", "Revamp Inac", "SEQ 1", mode="url")

        assert len(archive.list_reports("Germany", "Revamp Inac", "SEQ 1", mode="wording")) == 2
        assert len(archive.list_reports("Germany", "Revamp Inac", "SEQ 1", mode="url")) == 1


class TestDeleteSequenceDir:
    def test_removes_the_whole_sequence_folder(self, tmp_path, isolated_output):
        report = _make_report(tmp_path)
        archive.archive_report(report, "Germany", "Revamp Inac", "SEQ 1", mode="wording")

        seq_dir = archive.sequence_dir("Germany", "Revamp Inac", "SEQ 1")
        assert os.path.isdir(seq_dir)

        archive.delete_sequence_dir("Germany", "Revamp Inac", "SEQ 1")
        assert not os.path.exists(seq_dir)

    def test_missing_sequence_is_a_no_op(self, isolated_output):
        archive.delete_sequence_dir("Germany", "Revamp Inac", "Does Not Exist")  # ne doit pas lever


class TestMoveSequenceDir:
    def test_renames_sequence_folder(self, tmp_path, isolated_output, monkeypatch):
        fake_incrementing_datetime(monkeypatch)
        report = _make_report(tmp_path)
        archive.archive_report(report, "Germany", "Revamp Inac", "SEQ 1 NON FID", mode="url")

        archive.move_sequence_dir(
            "Germany", "Revamp Inac", "SEQ 1 NON FID",
            "Germany", "Revamp Inac", "SEQ 1 NON LOYAL"
        )

        assert not os.path.isdir(archive.sequence_dir("Germany", "Revamp Inac", "SEQ 1 NON FID"))
        moved = archive.list_reports("Germany", "Revamp Inac", "SEQ 1 NON LOYAL", mode="url")
        assert len(moved) == 1

    def test_merges_into_existing_destination_without_overwriting(self, tmp_path, isolated_output, monkeypatch):
        fake_incrementing_datetime(monkeypatch)
        report_a = _make_report(tmp_path, "a.xlsx")
        report_b = _make_report(tmp_path, "b.xlsx")

        archive.archive_report(report_a, "Germany", "Revamp Inac", "SEQ 1 NON FID", mode="wording")
        archive.archive_report(report_b, "Germany", "Revamp Inac", "SEQ 1 NON LOYAL", mode="wording")

        archive.move_sequence_dir(
            "Germany", "Revamp Inac", "SEQ 1 NON FID",
            "Germany", "Revamp Inac", "SEQ 1 NON LOYAL"
        )

        merged = archive.list_reports("Germany", "Revamp Inac", "SEQ 1 NON LOYAL", mode="wording")
        # Rotation reappliquee apres fusion : au plus KEEP_REPORTS conserves.
        assert 1 <= len(merged) <= archive.KEEP_REPORTS

    def test_missing_source_is_a_no_op(self, isolated_output):
        archive.move_sequence_dir(
            "Germany", "Revamp Inac", "Does Not Exist",
            "Germany", "Revamp Inac", "SEQ 1"
        )  # ne doit pas lever
        assert not os.path.isdir(archive.sequence_dir("Germany", "Revamp Inac", "SEQ 1"))

    def test_filename_collision_is_suffixed_instead_of_overwritten(self, isolated_output):
        # Cas non couvert par les tests precedents (noms de fichiers
        # distincts par construction, voir archive_report) : source ET
        # destination contiennent chacune un fichier de MEME nom - le
        # fichier deplace doit etre suffixe "_fusionne", jamais ecraser
        # celui deja present dans le dossier de destination.
        src_dir = archive.sequence_dir("Germany", "Revamp Inac", "SEQ 1 NON FID")
        dst_dir = archive.sequence_dir("Germany", "Revamp Inac", "SEQ 1 NON LOYAL")
        os.makedirs(src_dir)
        os.makedirs(dst_dir)
        with open(os.path.join(src_dir, "same_name.xlsx"), "wb") as f:
            f.write(b"source content")
        with open(os.path.join(dst_dir, "same_name.xlsx"), "wb") as f:
            f.write(b"destination content")

        archive.move_sequence_dir(
            "Germany", "Revamp Inac", "SEQ 1 NON FID",
            "Germany", "Revamp Inac", "SEQ 1 NON LOYAL"
        )

        with open(os.path.join(dst_dir, "same_name.xlsx"), "rb") as f:
            assert f.read() == b"destination content"
        with open(os.path.join(dst_dir, "same_name_fusionne.xlsx"), "rb") as f:
            assert f.read() == b"source content"


def _touch_root_file(name, mtime_offset_seconds=0):
    """Cree un fichier .xlsx factice a la racine de OUTPUT_DIR (jamais dans
    un sous-dossier Pays/Trigger/Sequence) - simule un rapport genere par
    qa.report.save_report/save_consolidated_report avant tout archivage."""
    os.makedirs(archive.OUTPUT_DIR, exist_ok=True)
    path = os.path.join(archive.OUTPUT_DIR, name)
    with open(path, "wb") as f:
        f.write(b"fake xlsx content")
    if mtime_offset_seconds:
        t = time.time() + mtime_offset_seconds
        os.utime(path, (t, t))
    return path


class TestListAndPruneRootReports:
    """
    prune_root_reports applique la MEME regle KEEP_REPORTS que
    archive_report, mais aux rapports "bruts" generes directement a la
    racine de OUTPUT_DIR (un par comparaison, avant tout archivage) - voir
    qa.report.save_report/save_consolidated_report. Ne doit jamais toucher
    aux rapports deja archives dans un sous-dossier Pays/Trigger/Sequence.
    """

    def test_list_root_reports_ignores_archived_subfolder_reports(self, tmp_path, isolated_output):
        _touch_root_file("QA_SEQ_1_20260101_000000.xlsx")
        report = _make_report(tmp_path)
        archive.archive_report(report, "Germany", "Revamp Inac", "SEQ 1", mode="wording")

        assert len(archive.list_root_reports()) == 1

    def test_prune_root_reports_keeps_only_the_n_most_recent(self, isolated_output):
        paths = [_touch_root_file(f"QA_ROOT_{i}.xlsx", mtime_offset_seconds=i) for i in range(4)]

        archive.prune_root_reports(keep=2)

        assert not os.path.exists(paths[0])
        assert not os.path.exists(paths[1])
        assert os.path.exists(paths[2])
        assert os.path.exists(paths[3])

    def test_prune_root_reports_defaults_to_keep_reports(self, isolated_output):
        for i in range(5):
            _touch_root_file(f"QA_ROOT_{i}.xlsx", mtime_offset_seconds=i)

        archive.prune_root_reports()

        assert len(archive.list_root_reports()) == archive.KEEP_REPORTS

    def test_prune_root_reports_never_touches_archived_subfolder_reports(self, tmp_path, isolated_output, monkeypatch):
        fake_incrementing_datetime(monkeypatch)
        report = _make_report(tmp_path)
        for _ in range(5):
            archive.archive_report(report, "Germany", "Revamp Inac", "SEQ 1", mode="wording")
        for i in range(5):
            _touch_root_file(f"QA_ROOT_{i}.xlsx", mtime_offset_seconds=i)

        archive.prune_root_reports(keep=2)

        # Deja rabote a KEEP_REPORTS par archive_report lui-meme - doit le rester.
        assert len(archive.list_reports("Germany", "Revamp Inac", "SEQ 1", mode="wording")) == archive.KEEP_REPORTS
        assert len(archive.list_root_reports()) == 2

    def test_no_root_reports_is_a_no_op(self, isolated_output):
        archive.prune_root_reports()  # ne doit pas lever
        assert archive.list_root_reports() == []
