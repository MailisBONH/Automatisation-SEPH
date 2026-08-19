import os
import time

import qa.cleanup as cleanup
from qa.cleanup import cleanup_old_temp_files, cleanup_old_temp_files_throttled


def _touch(path, age_hours=0):
    path.write_text("content", encoding="utf-8")
    if age_hours:
        old_time = time.time() - age_hours * 3600
        os.utime(path, (old_time, old_time))


class TestCleanupOldTempFiles:
    def test_removes_files_older_than_max_age(self, tmp_path):
        old_file = tmp_path / "old_input.xlsx"
        _touch(old_file, age_hours=48)

        removed = cleanup_old_temp_files(max_age_hours=24, temp_dir=str(tmp_path))

        assert removed == 1
        assert not old_file.exists()

    def test_keeps_recent_files(self, tmp_path):
        recent_file = tmp_path / "recent_input.xlsx"
        _touch(recent_file, age_hours=1)

        removed = cleanup_old_temp_files(max_age_hours=24, temp_dir=str(tmp_path))

        assert removed == 0
        assert recent_file.exists()

    def test_ignores_hidden_marker_files(self, tmp_path):
        marker = tmp_path / ".last_cleanup"
        _touch(marker, age_hours=48)

        removed = cleanup_old_temp_files(max_age_hours=24, temp_dir=str(tmp_path))

        assert removed == 0
        assert marker.exists()

    def test_missing_directory_returns_zero(self, tmp_path):
        assert cleanup_old_temp_files(temp_dir=str(tmp_path / "does_not_exist")) == 0

    def test_monkeypatching_temp_dir_and_max_age_affects_a_call_with_no_explicit_args(self, tmp_path, monkeypatch):
        # Regression : TEMP_DIR/MAX_AGE_HOURS doivent etre relus a l'appel
        # (pas figes comme defaut de parametre a l'import du module), sinon
        # monkeypatcher qa.cleanup.TEMP_DIR n'aurait aucun effet sur un appel
        # sans temp_dir= explicite - piege silencieux pour un futur test.
        monkeypatch.setattr(cleanup, "TEMP_DIR", str(tmp_path))
        monkeypatch.setattr(cleanup, "MAX_AGE_HOURS", 24)
        old_file = tmp_path / "old_input.xlsx"
        _touch(old_file, age_hours=48)

        removed = cleanup_old_temp_files()

        assert removed == 1
        assert not old_file.exists()

    def test_mixed_old_and_recent_files(self, tmp_path):
        old1 = tmp_path / "old1.xlsx"
        old2 = tmp_path / "old2.html"
        recent = tmp_path / "recent.xlsx"
        _touch(old1, age_hours=30)
        _touch(old2, age_hours=25)
        _touch(recent, age_hours=2)

        removed = cleanup_old_temp_files(max_age_hours=24, temp_dir=str(tmp_path))

        assert removed == 2
        assert not old1.exists()
        assert not old2.exists()
        assert recent.exists()


class TestCleanupThrottled:
    def test_first_call_performs_cleanup_and_writes_marker(self, tmp_path):
        old_file = tmp_path / "old_input.xlsx"
        _touch(old_file, age_hours=48)

        cleanup_old_temp_files_throttled(max_age_hours=24, temp_dir=str(tmp_path))

        assert not old_file.exists()
        assert (tmp_path / ".last_cleanup").exists()

    def test_second_call_within_throttle_window_is_a_no_op(self, tmp_path):
        cleanup_old_temp_files_throttled(max_age_hours=24, temp_dir=str(tmp_path))

        old_file = tmp_path / "old_input.xlsx"
        _touch(old_file, age_hours=48)

        # Le marqueur vient d'etre pose : ce second appel ne doit pas rescanner.
        cleanup_old_temp_files_throttled(max_age_hours=24, temp_dir=str(tmp_path))

        assert old_file.exists()
