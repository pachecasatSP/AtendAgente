"""Resumo do provisionamento: segredos só no terminal da CLI, nunca no log do onboarding."""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import provision_tenant as pt  # noqa: E402

CRED = {"verify_token": "VERIFY-SECRETO-0123456789", "panel_setup_token": "SETUP-SECRETO-0123456789"}


def test_padrao_do_provision_nao_mostra_segredos():
    assert inspect.signature(pt.provision).parameters["mostrar_segredos"].default is False


def test_sem_mostrar_nenhum_segredo_sai(capsys):
    pt.imprimir_resumo("loja", "loja.exemplo.com.br", CRED, mostrar_segredos=False)
    saida = capsys.readouterr().out
    assert "no ar em https://loja.exemplo.com.br/whatsapp/webhook" in saida
    assert "SECRETO" not in saida and "loja-hermes-env" in saida


def test_cli_mostra_para_o_operador(capsys):
    pt.imprimir_resumo("loja", "loja.exemplo.com.br", CRED, mostrar_segredos=True)
    saida = capsys.readouterr().out
    assert "VERIFY-SECRETO-0123456789" in saida and "SETUP-SECRETO-0123456789" in saida


def test_cli_passa_mostrar_segredos():
    src = Path(pt.__file__).read_text(encoding="utf-8")
    assert "mostrar_segredos=True" in src.split("def main()")[1]


def test_onboarding_nao_pede_para_mostrar():
    onboarding = Path(pt.__file__).resolve().parents[2] / "onboarding-service" / "app" / "main.py"
    assert "mostrar_segredos" not in onboarding.read_text(encoding="utf-8")
