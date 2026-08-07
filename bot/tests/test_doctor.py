"""Tests du diagnostic d'installation."""

from __future__ import annotations

import sys
import types

import pytest

from smcbot.cli import main
from smcbot.doctor import Check, Diagnostic, run_diagnostics


class FauxSymbole:
    def __init__(self, name="XAUUSD"):
        self.name = name
        self.digits = 2
        self.point = 0.01
        self.spread = 28
        self.trade_tick_value = 1.0
        self.trade_tick_size = 0.01


class FauxMt5(types.ModuleType):
    """Terminal MT5 simulé, pour éprouver le diagnostic hors Windows."""

    ACCOUNT_TRADE_MODE_REAL = 2

    def __init__(
        self,
        *,
        init_ok=True,
        connected=True,
        symbole_ok=True,
        bougies=5000,
        devise="USD",
        reel=False,
        code_erreur=-10005,
    ):
        super().__init__("MetaTrader5")
        self.__version__ = "5.0.6090"
        self._init_ok = init_ok
        self._connected = connected
        self._symbole_ok = symbole_ok
        self._bougies = bougies
        self._devise = devise
        self._reel = reel
        self._code_erreur = code_erreur
        self.options = None
        self.arrete = False
        self.TIMEFRAME_M1 = 1

    def initialize(self, **options):
        self.options = options
        return self._init_ok

    def last_error(self):
        messages = {-6: "Terminal: Authorization failed", -10005: "IPC timeout"}
        return (self._code_erreur, messages.get(self._code_erreur, "erreur"))

    def shutdown(self):
        self.arrete = True

    def terminal_info(self):
        return types.SimpleNamespace(
            name="MetaTrader 5", connected=self._connected, trade_allowed=True
        )

    def account_info(self):
        return types.SimpleNamespace(
            trade_mode=2 if self._reel else 0, currency=self._devise
        )

    def symbol_select(self, name, enable=True):
        return self._symbole_ok

    def symbol_info(self, name):
        return FauxSymbole(name)

    def symbols_get(self, motif=""):
        return [FauxSymbole("XAUUSD.a"), FauxSymbole("GOLD")]

    def copy_rates_from_pos(self, symbol, tf, start, count):
        return list(range(self._bougies)) if self._bougies else None


@pytest.fixture
def installer_mt5(monkeypatch):
    def _installer(**kwargs):
        faux = FauxMt5(**kwargs)
        monkeypatch.setitem(sys.modules, "MetaTrader5", faux)
        return faux

    return _installer


def motif(diag: Diagnostic, prefixe: str) -> Check:
    for check in diag.checks:
        if check.name.startswith(prefixe):
            return check
    raise AssertionError(f"aucune vérification « {prefixe} » dans {diag.to_text()}")


# ------------------------------------------------------------------- absence


def test_paquet_absent(monkeypatch):
    """Sur une machine sans MT5, le diagnostic s'arrête proprement."""
    monkeypatch.setitem(sys.modules, "MetaTrader5", None)
    monkeypatch.delitem(sys.modules, "MetaTrader5")

    diag = run_diagnostics()
    paquet = motif(diag, "Paquet")
    if paquet.ok is False:  # cas Linux, celui de l'intégration continue
        assert "absent" in paquet.detail
        assert not diag.ok
        assert "ExportBars" in paquet.hint or "pip install" in paquet.hint


def test_python_verifie():
    diag = run_diagnostics()
    check = motif(diag, "Python")
    assert "64 bits" in check.detail or "32 bits" in check.detail


# ---------------------------------------------------------------- nominal


def test_chaine_complete_ok(installer_mt5):
    faux = installer_mt5()
    diag = run_diagnostics("XAUUSD", "M1")

    assert motif(diag, "Paquet").ok is True
    assert motif(diag, "Terminal").ok is True
    assert motif(diag, "Symbole").ok is True
    assert motif(diag, "Historique").ok is True
    assert diag.ok
    assert faux.arrete, "le terminal doit être refermé après diagnostic"


def test_valeur_du_point_affichee(installer_mt5):
    """La valeur qui casse tout le dimensionnement doit être visible."""
    installer_mt5()
    diag = run_diagnostics("XAUUSD", "M1")
    assert "1.0000 par point" in motif(diag, "Symbole").detail


# ------------------------------------------------------------------ pannes


def test_terminal_injoignable(installer_mt5):
    installer_mt5(init_ok=False)
    diag = run_diagnostics()

    check = motif(diag, "Connexion au terminal")
    assert check.ok is False
    assert "ne répond pas" in check.detail
    assert "Ferme complètement MT5" in check.hint
    assert not diag.ok


def test_authorization_failed_est_expliquee(installer_mt5):
    """Code -6 : le cas réellement rencontré. Le message doit être spécifique."""
    installer_mt5(init_ok=False, code_erreur=-6)
    diag = run_diagnostics()

    check = motif(diag, "Connexion au terminal")
    assert check.ok is False
    assert "authentification" in check.detail
    assert "aucun compte n'y est connecté" in check.hint
    assert "--mt5-path" in check.hint
    # La solution de repli par variables d'environnement doit être proposée
    assert "MT5_LOGIN" in check.hint


def test_trading_algo_desactive(installer_mt5):
    installer_mt5(init_ok=False, code_erreur=-8)
    check = motif(run_diagnostics(), "Connexion au terminal")
    assert "algorithmique" in check.detail
    assert "Expert Advisors" in check.hint


def test_mt5_path_transmis_a_initialize(installer_mt5):
    faux = installer_mt5()
    run_diagnostics("XAUUSD", "M1", path=r"C:\MT5\terminal64.exe")
    assert faux.options.get("path") == r"C:\MT5\terminal64.exe"


def test_identifiants_lus_dans_l_environnement(installer_mt5, monkeypatch):
    """Jamais en ligne de commande : uniquement par variables d'environnement."""
    monkeypatch.setenv("MT5_LOGIN", "123456")
    monkeypatch.setenv("MT5_PASSWORD", "secret")
    monkeypatch.setenv("MT5_SERVER", "Broker-Demo")
    faux = installer_mt5()

    diag = run_diagnostics()
    assert faux.options["login"] == 123456
    assert faux.options["server"] == "Broker-Demo"
    # Et surtout : le mot de passe ne doit apparaître nulle part dans la sortie
    assert "secret" not in diag.to_text()


def test_login_non_numerique_refuse(installer_mt5, monkeypatch):
    monkeypatch.setenv("MT5_LOGIN", "mon-compte")
    monkeypatch.setenv("MT5_PASSWORD", "x")
    monkeypatch.setenv("MT5_SERVER", "y")
    installer_mt5()

    check = motif(run_diagnostics(), "Connexion au terminal")
    assert check.ok is False
    assert "numéro de compte" in check.detail


def test_terminal_hors_ligne(installer_mt5):
    installer_mt5(connected=False)
    diag = run_diagnostics()
    assert motif(diag, "Terminal").ok is False


def test_symbole_introuvable_propose_des_noms(installer_mt5):
    """Le cas le plus courant : le courtier n'appelle pas l'or « XAUUSD »."""
    installer_mt5(symbole_ok=False)
    diag = run_diagnostics("XAUUSD", "M1")

    check = motif(diag, "Symbole")
    assert check.ok is False
    assert "XAUUSD.a" in check.hint and "GOLD" in check.hint
    assert not diag.ok


def test_historique_vide(installer_mt5):
    installer_mt5(bougies=0)
    diag = run_diagnostics("XAUUSD", "M1")

    check = motif(diag, "Historique")
    assert check.ok is False
    assert "défiler" in check.hint


def test_unite_de_temps_inconnue(installer_mt5):
    installer_mt5()
    diag = run_diagnostics("XAUUSD", "M7")
    assert motif(diag, "Historique").ok is False


def test_devise_non_usd_signalee(installer_mt5):
    """Un compte en euros change la valeur du point : il faut le dire."""
    installer_mt5(devise="EUR")
    diag = run_diagnostics()

    check = motif(diag, "Compte")
    assert "EUR" in check.detail
    assert "value_per_point_per_lot" in check.hint


def test_compte_reel_signale(installer_mt5):
    installer_mt5(reel=True)
    assert "RÉEL" in motif(run_diagnostics(), "Compte").detail


def test_aucune_donnee_sensible_affichee(installer_mt5):
    """La sortie doit pouvoir être collée dans une conversation sans risque."""
    installer_mt5()
    texte = run_diagnostics().to_text().lower()
    for interdit in ("login", "password", "mot de passe", "solde", "balance"):
        assert interdit not in texte


# --------------------------------------------------------------------- CLI


def test_commande_doctor(installer_mt5, capsys):
    installer_mt5()
    assert main(["doctor", "--symbol", "XAUUSD", "--timeframe", "M1"]) == 0
    assert "Diagnostic smcbot" in capsys.readouterr().out


def test_commande_doctor_renvoie_1_si_panne(installer_mt5, capsys):
    installer_mt5(symbole_ok=False)
    assert main(["doctor"]) == 1
    assert "ECHEC" in capsys.readouterr().out
