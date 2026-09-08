"""
Tests de qa.digest : qa.digest.send_teams_notification (liaison PROPRE au
module, voir home.py pour la meme precaution documentee ailleurs dans ce
projet) est monkeypatche partout ici - aucune requete reseau reelle.
"""
import qa.digest as digest
from qa.digest import build_digest_text, send_digest
from qa.history import record_run


class TestBuildDigestText:
    def test_empty_entries_returns_empty_string(self):
        assert build_digest_text([]) == ""

    def test_no_regression_reports_a_clean_bill_of_health(self, tmp_path):
        path = str(tmp_path / "history.db")
        record_run("SEQ 1", "wording", 10, 9, 1, 90.0, path=path, country="France", trigger="T", sequence="SEQ 1")

        from qa.history import load_classified_history
        text = build_digest_text(load_classified_history(path=path))

        assert "1** séquence" in text
        assert "Aucune régression détectée" in text
        assert "régression(s) détectée" not in text

    def test_lists_regressions_worst_first_with_scores(self, tmp_path):
        path = str(tmp_path / "history.db")
        record_run("SEQ 1", "wording", 10, 9, 1, 90.0, path=path, country="France", trigger="T", sequence="SEQ 1")
        record_run("SEQ 1", "wording", 10, 8, 2, 80.0, path=path, country="France", trigger="T", sequence="SEQ 1")  # -10
        record_run("SEQ 2", "wording", 10, 9, 1, 90.0, path=path, country="France", trigger="T", sequence="SEQ 2")
        record_run("SEQ 2", "wording", 10, 4, 6, 40.0, path=path, country="France", trigger="T", sequence="SEQ 2")  # -50

        from qa.history import load_classified_history
        text = build_digest_text(load_classified_history(path=path), threshold=5)

        assert "2 régression(s) détectée(s)" in text
        seq2_line = next(line for line in text.splitlines() if "SEQ 2" in line)
        seq1_line = next(line for line in text.splitlines() if "SEQ 1" in line)
        # SEQ 2 (pire regression, -50) doit apparaitre avant SEQ 1 (-10).
        assert text.index(seq2_line) < text.index(seq1_line)
        assert "90.0% → 40.0% (-50.0 pts)" in seq2_line

    def test_below_threshold_deltas_are_not_reported_as_regressions(self, tmp_path):
        path = str(tmp_path / "history.db")
        record_run("SEQ 1", "wording", 10, 9, 1, 90.0, path=path, country="France", trigger="T", sequence="SEQ 1")
        record_run("SEQ 1", "wording", 10, 9, 1, 88.0, path=path, country="France", trigger="T", sequence="SEQ 1")  # -2

        from qa.history import load_classified_history
        text = build_digest_text(load_classified_history(path=path), threshold=5)

        assert "Aucune régression détectée" in text

    def test_truncates_beyond_max_regressions_listed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(digest, "MAX_REGRESSIONS_LISTED", 2)
        path = str(tmp_path / "history.db")
        for i in range(3):
            seq = f"SEQ {i}"
            record_run(seq, "wording", 10, 9, 1, 90.0, path=path, country="France", trigger="T", sequence=seq)
            record_run(seq, "wording", 10, 3, 7, 30.0, path=path, country="France", trigger="T", sequence=seq)

        from qa.history import load_classified_history
        text = build_digest_text(load_classified_history(path=path), threshold=5)

        assert "3 régression(s) détectée(s)" in text
        assert "et 1 de plus" in text

    def test_escapes_markdown_special_characters_in_classification_fields(self, tmp_path):
        # Meme protection que home._do_archive (voir
        # qa.notifications.escape_teams_markdown) : pays/trigger/sequence
        # viennent du contenu Excel, semi-fiable - un nom contenant de la
        # syntaxe markdown ne doit jamais produire un lien cliquable ou un
        # formatage trompeur dans le message poste sur Teams.
        path = str(tmp_path / "history.db")
        record_run(
            "SEQ 1", "wording", 10, 9, 1, 90.0, path=path,
            country="France", trigger="T", sequence="[Cliquez ici](https://phishing.example)",
        )
        record_run(
            "SEQ 1", "wording", 10, 3, 7, 30.0, path=path,
            country="France", trigger="T", sequence="[Cliquez ici](https://phishing.example)",
        )

        from qa.history import load_classified_history
        text = build_digest_text(load_classified_history(path=path), threshold=5)

        assert "](https://phishing.example)" not in text


class TestSendDigest:
    def test_no_classified_history_does_not_call_teams(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(digest, "send_teams_notification", lambda *a, **k: calls.append((a, k)) or True)

        result = send_digest(path=str(tmp_path / "does_not_exist.db"))

        assert result is False
        assert calls == []

    def test_sends_the_built_text_with_a_digest_title(self, tmp_path, monkeypatch):
        path = str(tmp_path / "history.db")
        record_run("SEQ 1", "wording", 10, 9, 1, 90.0, path=path, country="France", trigger="T", sequence="SEQ 1")

        captured = {}

        def _fake_send(title, text):
            captured["title"] = title
            captured["text"] = text
            return True

        monkeypatch.setattr(digest, "send_teams_notification", _fake_send)

        result = send_digest(path=path)

        assert result is True
        assert "Digest" in captured["title"]
        assert "1** séquence" in captured["text"]

    def test_propagates_a_failed_send(self, tmp_path, monkeypatch):
        path = str(tmp_path / "history.db")
        record_run("SEQ 1", "wording", 10, 9, 1, 90.0, path=path, country="France", trigger="T", sequence="SEQ 1")
        monkeypatch.setattr(digest, "send_teams_notification", lambda *a, **k: False)

        assert send_digest(path=path) is False


class TestMainCli:
    def test_prints_success_and_returns_0_when_sent(self, tmp_path, monkeypatch, capsys):
        path = str(tmp_path / "history.db")
        record_run("SEQ 1", "wording", 10, 9, 1, 90.0, path=path, country="France", trigger="T", sequence="SEQ 1")
        monkeypatch.setattr(digest, "HISTORY_PATH", path)
        monkeypatch.setattr(digest, "send_teams_notification", lambda *a, **k: True)

        assert digest.main() == 0
        assert "envoyé" in capsys.readouterr().out

    def test_prints_explanation_and_returns_1_when_not_sent(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(digest, "HISTORY_PATH", str(tmp_path / "does_not_exist.db"))

        assert digest.main() == 1
        assert "non envoyé" in capsys.readouterr().out
