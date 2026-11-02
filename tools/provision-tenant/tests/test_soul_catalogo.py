"""Com o catalogo ativo, o SOUL tem que mandar ler o catalogo.csv e enviar o link da vitrine antes de qualquer
"nao tenho" ou encaminhamento (caso real: a Aninha, da Linda Ana, respondia "nao tenho o catalogo, vou chamar o
dono" sem chamar nenhuma ferramenta)."""
import sys
from pathlib import Path

import pytest
import yaml

GERADOR = Path(__file__).resolve().parents[2] / "soul-generator"
sys.path.insert(0, str(GERADOR))
import generate_soul  # noqa: E402

TENANTS = sorted((GERADOR / "tenants").glob("*.yaml"))
DOMINIO = "loja-teste.atendpragente.com.br"


def _config(caminho: Path, **extra) -> dict:
    return {**yaml.safe_load(caminho.read_text(encoding="utf-8")), **extra}


def _secao_catalogo(soul: str) -> str:
    inicio = soul.index("## Catálogo")
    fim = soul.find("\n#", inicio + 1)
    return soul[inicio:] if fim == -1 else soul[inicio:fim]


@pytest.mark.parametrize("caminho", TENANTS, ids=lambda p: p.stem)
def test_catalogo_ativo_manda_ler_o_arquivo_e_enviar_a_vitrine(caminho):
    secao = _secao_catalogo(generate_soul.render(_config(caminho, catalogo_ativo=True, dominio=DOMINIO)))
    assert "nunca é caso de \"não tenho\" nem de encaminhar" in secao
    assert "leia o arquivo primeiro" in secao
    assert f"https://{DOMINIO}/vitrine" in secao
    assert secao.count(f"https://{DOMINIO}/vitrine") == 2  # a regra antiga e a nova apontam para a mesma URL
    assert "esta seção vale mais que \"Não invento\"" in secao
    assert "aquilo estava errado: corrija e mande agora" in secao


@pytest.mark.parametrize("caminho", TENANTS, ids=lambda p: p.stem)
def test_sem_catalogo_ativo_nao_ha_secao_de_catalogo(caminho):
    soul = generate_soul.render(_config(caminho, catalogo_ativo=False))
    assert "catalogo.csv" not in soul
    assert "vitrine" not in soul
