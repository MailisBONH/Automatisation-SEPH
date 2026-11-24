"""
Tests de qa.config._env_flag : parsing partage des variables d'environnement
booleennes (DEBUG_MODE). Regression tenue a distance : une valeur
EXPLICITEMENT VIDE (ex. `NAME=` dans un .env) ne doit pas etre traitee comme
"desactive", au meme titre que "0"/"false", mais retomber sur le defaut comme
le fait deja une variable totalement absente.
"""
import qa.config as config
from qa.config import _env_flag


class TestEnvFlag:
    def test_missing_variable_falls_back_to_default(self, monkeypatch):
        monkeypatch.delenv("SOME_FLAG", raising=False)
        assert _env_flag("SOME_FLAG", default_on=True) is True
        assert _env_flag("SOME_FLAG", default_on=False) is False

    def test_explicitly_empty_variable_falls_back_to_default_like_a_missing_one(self, monkeypatch):
        # Le piege corrige : os.environ.get("NAME", "1") ne retourne "1" que
        # si la cle est ABSENTE, jamais si elle vaut "" (la cle existe,
        # juste vide) - sans le garde-fou explicite dans _env_flag, ce cas
        # retombait a tort sur "desactive".
        monkeypatch.setenv("SOME_FLAG", "")
        assert _env_flag("SOME_FLAG", default_on=True) is True
        assert _env_flag("SOME_FLAG", default_on=False) is False

    def test_whitespace_only_variable_is_treated_as_empty(self, monkeypatch):
        monkeypatch.setenv("SOME_FLAG", "   ")
        assert _env_flag("SOME_FLAG", default_on=True) is True

    def test_explicit_0_or_false_disable_regardless_of_default(self, monkeypatch):
        for value in ("0", "false", "FALSE", "False"):
            monkeypatch.setenv("SOME_FLAG", value)
            assert _env_flag("SOME_FLAG", default_on=True) is False

    def test_any_other_non_empty_value_enables_regardless_of_default(self, monkeypatch):
        for value in ("1", "true", "yes", "on"):
            monkeypatch.setenv("SOME_FLAG", value)
            assert _env_flag("SOME_FLAG", default_on=False) is True

    def test_debug_mode_is_built_on_env_flag(self):
        # Verrouille l'usage effectif du helper : une regression qui
        # reintroduirait un parsing ad-hoc casserait ce test.
        import inspect
        source = inspect.getsource(config)
        assert 'DEBUG_MODE = _env_flag("QA_DEBUG_MODE"' in source
