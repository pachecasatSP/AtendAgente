#!/usr/bin/env python3
"""Gera um SOUL.md a partir de um YAML de configuração de tenant.

Uso:
    python3 generate_soul.py tenants/exemplo.yaml > SOUL.md
    python3 generate_soul.py tenants/exemplo.yaml -o SOUL.md

Sem dependências além da stdlib (PyYAML é a única externa, já disponível
em qualquer imagem Python padrão) — roda em qualquer servidor sem setup.
"""
import argparse
import string
import sys
from pathlib import Path

import yaml

TEMPLATE_PATH = Path(__file__).parent / "SOUL.template.md"


def build_services_block(services: list[dict]) -> str:
    parts = []
    for svc in services:
        parts.append(f"\n## {svc['nome']}\n{svc['descricao'].strip()}\n")
        if svc.get("publico_alvo"):
            parts.append(f"**{svc['publico_alvo'].strip()}**\n")
    return "".join(parts) + "\n"


def build_how_we_work_block(how_we_work: dict) -> str:
    if not how_we_work:
        return "\n"
    parts = ["\n"]
    for label, text in how_we_work.items():
        parts.append(f"**{label}:** {text.strip()}\n\n")
    return "".join(parts)


def build_unknown_gaps_block(gaps: list[str]) -> str:
    if not gaps:
        return "\n(nenhuma lacuna conhecida no momento)\n\n"
    parts = ["\n"]
    for gap in gaps:
        parts.append(f"[{gap.upper()}]\n")
    return "".join(parts) + "\n"


DEFAULT_TRIGGERS = [
    "É sobre valor, orçamento ou proposta",
    "É sobre fechar contrato, prazo ou início",
    "A pessoa pede para falar com alguém",
    "Há reclamação ou insatisfação de qualquer tipo",
    "Eu não sei responder e a informação importa para a decisão dela",
]


def build_escalation_triggers_block(custom_triggers: list[str]) -> str:
    triggers = DEFAULT_TRIGGERS + list(custom_triggers or [])
    return "".join(f"- {t}\n" for t in triggers) + "\n"


def build_examples_block(examples: list[dict]) -> str:
    if not examples:
        return "\n(sem exemplos cadastrados ainda — adicionar após as primeiras conversas reais)\n"
    parts = ["\n"]
    for ex in examples:
        parts.append(
            f"**Contato:** {ex['pergunta'].strip()}\n"
            f"**Eu:** {ex['resposta'].strip()}\n\n"
        )
    return "".join(parts)


def render(config: dict) -> str:
    template = string.Template(TEMPLATE_PATH.read_text(encoding="utf-8"))

    escalation = config["escalacao"]
    tone = config.get("tom", {})
    assistant_name = (config.get("nome_atendente") or "").strip()

    values = {
        "assistant_intro": f"Meu nome é **{assistant_name}**. " if assistant_name else "",
        "assistant_label": f"{assistant_name}, " if assistant_name else "",
        "business_name": config["nome_negocio"],
        "business_article": config.get("artigo_negocio", "a"),
        "business_description": config["descricao_negocio"].strip(),
        "audience_description": config.get("descricao_publico", "").strip(),
        "tone_description": tone.get(
            "descricao",
            "Português do Brasil, tom cordial e direto.",
        ).strip(),
        "emoji_policy": tone.get("emoji", "com moderação — no máximo um, e nem sempre"),
        "services_block": build_services_block(config.get("servicos", [])),
        "how_we_work_block": build_how_we_work_block(config.get("como_trabalhamos", {})),
        "unknown_gaps_block": build_unknown_gaps_block(config.get("lacunas_conhecidas", [])),
        "escalation_name": escalation["nome"],
        "escalation_phone": escalation["telefone"],
        "escalation_pronoun": escalation.get("pronome", "ele"),
        "escalation_triggers_block": build_escalation_triggers_block(
            escalation.get("gatilhos_extra", [])
        ),
        "examples_block": build_examples_block(config.get("exemplos", [])),
    }

    return template.substitute(values)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tenant_yaml", type=Path, help="YAML de config do tenant")
    parser.add_argument("-o", "--output", type=Path, help="Caminho de saída (default: stdout)")
    args = parser.parse_args()

    config = yaml.safe_load(args.tenant_yaml.read_text(encoding="utf-8"))
    soul = render(config)

    if args.output:
        args.output.write_text(soul, encoding="utf-8")
        print(f"SOUL gerado em {args.output}", file=sys.stderr)
    else:
        print(soul)


if __name__ == "__main__":
    main()
