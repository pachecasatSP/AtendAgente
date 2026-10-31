"""patch_textos_hermes: o texto de primeiro contato do Hermes nao pode mandar o cliente final usar /help."""
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import patch_textos_hermes as p  # noqa: E402

# Trechos copiados do Hermes v0.21.5 (gateway/run_turn.py e agent/onboarding.py), com a indentacao original.
RUN_TURN = '''\
            _intro_note = (
                "[System note: This is the user's very first message ever. "
                "Briefly introduce yourself and mention that /help shows available commands. "
                "Keep the introduction concise -- one or two sentences max.]"
            )
'''
ONBOARDING = '''\
    return (
        "\\n\\n"
        "[System note: This is the user's very first message ever. After a one-sentence introduction (mention /help "
        "shows commands), OFFER — do not assume — to build a short profile of them so you can be more useful, and "
        "explain they can decline or do it later.]"
    )
'''


@pytest.fixture
def raiz(tmp_path):
    (tmp_path / "gateway").mkdir()
    (tmp_path / "agent").mkdir()
    (tmp_path / "gateway" / "run_turn.py").write_text(RUN_TURN, encoding="utf-8")
    (tmp_path / "agent" / "onboarding.py").write_text(ONBOARDING, encoding="utf-8")
    return tmp_path


def _ler(raiz, rel):
    return (raiz / rel).read_text(encoding="utf-8")


def test_nenhum_texto_manda_o_cliente_usar_help(raiz):
    p.aplicar(raiz)
    for rel in ("gateway/run_turn.py", "agent/onboarding.py"):
        texto = _ler(raiz, rel)
        assert "(mention /help" not in texto and "mention that /help" not in texto and "shows commands" not in texto
    assert "Never mention commands" in _ler(raiz, "gateway/run_turn.py")
    assert "never mention /help" in _ler(raiz, "agent/onboarding.py")


def test_o_resto_do_arquivo_fica_intacto(raiz):
    p.aplicar(raiz)
    assert "Keep the introduction concise -- one or two sentences max.]" in _ler(raiz, "gateway/run_turn.py")
    assert "OFFER — do not assume — to build a short profile" in _ler(raiz, "agent/onboarding.py")


def test_python_continua_valido_depois_do_patch(raiz):
    p.aplicar(raiz)
    compile(textwrap.dedent(_ler(raiz, "gateway/run_turn.py")), "run_turn.py", "exec")
    compile(textwrap.dedent(_ler(raiz, "agent/onboarding.py")).replace("return (", "x = (", 1), "onboarding.py", "exec")


def test_rodar_duas_vezes_e_inofensivo(raiz):
    p.aplicar(raiz)
    antes = {r: _ler(raiz, r) for r in ("gateway/run_turn.py", "agent/onboarding.py")}
    feitas = p.aplicar(raiz)
    assert all("ja estava trocado" in f for f in feitas)
    assert {r: _ler(raiz, r) for r in antes} == antes


def test_base_que_mudou_derruba_o_build(raiz):
    (raiz / "gateway" / "run_turn.py").write_text(RUN_TURN.replace("/help", "/ajuda"), encoding="utf-8")
    with pytest.raises(SystemExit, match="Revalide o patch"):
        p.aplicar(raiz)


def test_trecho_duplicado_tambem_derruba_o_build(raiz):
    (raiz / "gateway" / "run_turn.py").write_text(RUN_TURN * 2, encoding="utf-8")
    with pytest.raises(SystemExit):
        p.aplicar(raiz)
