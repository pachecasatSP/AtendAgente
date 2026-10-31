"""Troca, na imagem do Hermes, os textos de "primeiro contato" que o agente repassa ao cliente final.

O Hermes foi feito para um humano na CLI: na primeira mensagem de todas, ele injeta uma nota mandando o agente "mencionar que /help mostra os
comandos" e oferecer "montar um perfil". Num atendimento por WhatsApp isso vaza mecanismo interno para o cliente (spec infraestrutura-aws, secao 11).
Rodado no `docker build` da imagem atendpragente-hermes (infra-aws/atendpragente/docker/Dockerfile.hermes), sobre a base travada por digest.

Cada troca exige que o trecho antigo exista; se a base mudar e o trecho sumir, o build FALHA em vez de publicar uma imagem sem o patch.
Rodar de novo e inofensivo (trecho ja trocado = ok).

  python patch_textos_hermes.py /opt/hermes
"""
import sys
from pathlib import Path

# (arquivo relativo a raiz, trecho antigo, trecho novo) — trechos de UMA linha, para nao depender de indentacao
TROCAS = [
    (
        "gateway/run_turn.py",
        '"Briefly introduce yourself and mention that /help shows available commands. "',
        '"Greet them warmly in their own language, in one or two short sentences, as the attendant described in your '
        'persona. Never mention commands, slash commands, /help, setup or configuration. "',
    ),
    # nota de "montar perfil" (so roda se onboarding.profile_build nao estiver "off"): mesma mencao ao /help, em duas linhas do codigo
    (
        "agent/onboarding.py",
        'After a one-sentence introduction (mention /help "',
        'After a one-sentence introduction (never mention /help or commands "',
    ),
    (
        "agent/onboarding.py",
        '"shows commands), OFFER',
        '"or setup), OFFER',
    ),
]


def aplicar(raiz: Path) -> list[str]:
    """Aplica as trocas sob `raiz`; devolve o que foi feito. Levanta SystemExit se algum trecho nao existe nem ja foi trocado."""
    feitas = []
    for rel, antigo, novo in TROCAS:
        caminho = raiz / rel
        texto = caminho.read_text(encoding="utf-8")
        if texto.count(antigo) == 1:
            caminho.write_text(texto.replace(antigo, novo), encoding="utf-8")
            feitas.append(f"{rel}: trocado")
        elif texto.count(antigo) == 0 and texto.count(novo) == 1:
            feitas.append(f"{rel}: ja estava trocado")
        else:
            raise SystemExit(
                f"patch_textos_hermes: trecho esperado nao encontrado exatamente uma vez em {rel} ({texto.count(antigo)} ocorrencias): "
                f"{antigo[:70]!r}. A base do Hermes mudou? Revalide o patch antes de publicar a imagem."
            )
    return feitas


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("uso: python patch_textos_hermes.py <raiz do hermes, ex.: /opt/hermes>")
    for linha in aplicar(Path(sys.argv[1])):
        print(linha)
