"""O onboarding-service grava `signups.plano` como CHAVE ("comecando"); o
backoffice precisa achar o preço de tabela a partir dela. Antes
`PRECO_POR_PLANO.get("comecando")` devolvia sempre None e nenhuma
comissão era atribuída."""
import pytest

from app import store


@pytest.mark.parametrize(
    "plano, nome, preco",
    [
        ("entrada", "Entrada", 84.90),
        ("comecando", "Começando", 157.90),
        ("crescendo", "Crescendo", 717.90),
        ("Entrada", "Entrada", 84.90),
        ("Começando", "Começando", 157.90),
        ("começando", "Começando", 157.90),
        ("CRESCENDO", "Crescendo", 717.90),
        ("  comecando  ", "Começando", 157.90),
    ],
)
def test_plano_com_preco_de_tabela(plano, nome, preco):
    assert store.nome_do_plano(plano) == nome
    assert store.preco_do_plano(plano) == preco


@pytest.mark.parametrize("plano", [None, "", "gratuito", "trial", "Gratuito (convite)", "Sem limite", "sem-limite", "inexistente"])
def test_plano_sem_preco_de_tabela(plano):
    assert store.nome_do_plano(plano) is None
    assert store.preco_do_plano(plano) is None


def test_chaves_do_onboarding_cobrem_todos_os_planos_com_preco():
    """Se o onboarding ganhar um plano com valor fixo, o backoffice tem de conhecê-lo."""
    from pathlib import Path

    fonte = (Path(__file__).resolve().parents[2] / "onboarding-service" / "app" / "main.py").read_text(encoding="utf-8")
    inicio = fonte.index("PLANOS = {")
    bloco = fonte[inicio : fonte.index("\n}\n", inicio)]
    chaves_com_valor = [
        linha.split('"')[1]
        for linha in bloco.splitlines()
        if linha.strip().startswith('"') and '"valor":' in linha and '"valor": 0' not in linha
    ]
    assert chaves_com_valor, "não achei os planos no onboarding-service (formato mudou?)"
    for chave in chaves_com_valor:
        assert store.preco_do_plano(chave) is not None, f"plano '{chave}' do onboarding sem preço no backoffice"
