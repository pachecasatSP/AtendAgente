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


def build_catalog_block(config: dict) -> str:
    """Vazio até o tenant cadastrar o primeiro item do catálogo no
    painel (config.catalogo_ativo, ver tools/tenant-panel/app.py). O CSV
    em si é publicado à parte, sem restart — ver publish_catalog em
    tools/provision-tenant/provision_tenant.py."""
    if not config.get("catalogo_ativo"):
        return ""
    dominio = config.get("dominio", "")
    vitrine_url = f"https://{dominio}/vitrine" if dominio else "/vitrine"
    return (
        "\n## Catálogo de produtos\n"
        "Você tem acesso a um catálogo de produtos/serviços em "
        "`/opt/data/catalogo.csv` (nome, preço, categoria, descrição — "
        "um por linha). Use a ferramenta de leitura de arquivo pra "
        "consultar preços e detalhes quando o cliente perguntar sobre "
        "algo específico. **Releia o arquivo toda vez que o assunto for "
        "produto/preço, mesmo que já tenha lido antes na mesma conversa** "
        "— o catálogo pode mudar a qualquer momento (item novo, preço "
        "atualizado), e responder com uma leitura antiga é o mesmo erro "
        "que inventar informação. Não invente item nem preço que não "
        "esteja lá — se não achar, diga que vai confirmar. Pra mostrar o "
        f"catálogo inteiro, pode mandar o link da vitrine: `{vitrine_url}`.\n"
    )


def build_agenda_block(config: dict) -> str:
    """Vazio até o tenant configurar+testar a Google Agenda no painel
    (config.agendamento_ativo, ver /painel/api/agenda em
    tools/tenant-panel/app.py). A ferramenta `criar_agendamento` já fica
    registrada no Hermes desde o provisionamento (ver enable_calendar_mcp
    em tools/provision-tenant/provision_tenant.py) — esse bloco só
    autoriza o bot a usá-la."""
    if not config.get("agendamento_ativo"):
        return ""
    duracao = config.get("agendamento_duracao_minutos") or 30
    return (
        "\n## Agendamento\n"
        "Você pode marcar horário na nossa agenda usando a ferramenta "
        f"`criar_agendamento`. Duração padrão: {duracao} minutos. Antes "
        "de chamar a ferramenta, confirme com a pessoa o assunto e o "
        "horário que ela quer. Se a ferramenta disser que o horário está "
        "ocupado, avise e peça outro horário — nunca insista no mesmo "
        "nem invente disponibilidade. Depois de agendar com sucesso, "
        "mande o link do evento e, se vierem preenchidos, o do Google "
        "Meet e o arquivo de convite (.ics) — esse último funciona em "
        "qualquer app de calendário, não só Google, então sempre mande "
        "junto quando disponível.\n"
    )


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
        "catalog_block": build_catalog_block(config),
        "agenda_block": build_agenda_block(config),
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
