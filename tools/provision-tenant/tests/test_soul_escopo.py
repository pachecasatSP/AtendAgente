"""O SOUL de todo tenant precisa trazer, de forma explicita, a regra de escopo:
nunca falar de politica nem de nada fora do que o negocio oferece, e isso NAO e motivo de encaminhar
(a frase de encaminhamento avisa o dono de verdade)."""
import sys
from pathlib import Path

import pytest
import yaml

GERADOR = Path(__file__).resolve().parents[2] / "soul-generator"
sys.path.insert(0, str(GERADOR))
import generate_soul  # noqa: E402

TENANTS = sorted((GERADOR / "tenants").glob("*.yaml"))


def _soul(caminho: Path) -> str:
    return generate_soul.render(yaml.safe_load(caminho.read_text(encoding="utf-8")))


def _secao_nao_faco(soul: str) -> str:
    inicio = soul.index("# O que eu NÃO faço")
    fim = soul.index("\n# ", inicio + 1)
    return soul[inicio:fim]


def test_ha_tenants_de_exemplo():
    assert TENANTS, "sem YAML de exemplo para renderizar"


@pytest.mark.parametrize("caminho", TENANTS, ids=lambda p: p.stem)
def test_soul_proibe_politica_e_assunto_fora_do_escopo(caminho):
    soul = _soul(caminho)
    nome = yaml.safe_load(caminho.read_text(encoding="utf-8"))["nome_negocio"]
    secao = _secao_nao_faco(soul)
    assert "nunca falo de política" in secao
    assert "Política eu nunca discuto" in secao
    assert "qualquer assunto que não tenha a ver com o que " + nome + " oferece" in secao
    assert "$" not in secao.replace("R$", ""), "sobrou placeholder sem substituir"


@pytest.mark.parametrize("caminho", TENANTS, ids=lambda p: p.stem)
def test_fora_do_escopo_nao_dispara_o_encaminhamento(caminho):
    cfg = yaml.safe_load(caminho.read_text(encoding="utf-8"))
    secao = _secao_nao_faco(_soul(caminho))
    assert "Assunto fora do\nescopo não é caso de encaminhar" in secao
    # a resposta-modelo para assunto fora do escopo nao pode conter a frase que o sistema detecta
    modelo = secao.split('"Esse assunto foge do que eu', 1)[1].split('"', 1)[0]
    assert "chamar" not in modelo
    assert cfg["escalacao"]["nome"] not in modelo
