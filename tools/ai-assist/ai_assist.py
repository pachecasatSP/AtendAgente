"""Assistente de IA pra ajudar o cliente self-service a preencher os
campos difíceis de Configurações (tom de voz, serviços, como trabalhamos,
descrição do negócio, exemplos) — ver specs/assistente-ia-configuracoes.md.

Cópia canônica. tools/tenant-panel/ tem uma cópia idêntica deste arquivo
(ai_assist.py) porque o pod do painel só monta o próprio diretório via
hostPath — não enxerga tools/ai-assist/ por sys.path como onboarding-
service enxerga soul-generator/provision-tenant. Mesmo padrão já usado
pelos parsers _parse_pipe_lines/_format_pipe_lines, duplicados entre
onboarding-service e tenant-panel pelo mesmo motivo. Mudou aqui, muda lá
também.
"""
import json
import os
import urllib.error
import urllib.request

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Mais barato disponível hoje — constante fixa de propósito (não expor
# como env var ainda), troca aqui quando o preço mudar.
MODEL = "openai/gpt-5.6-luna"


class AiAssistError(RuntimeError):
    pass


_FIELD_INSTRUCTIONS = {
    "descricao_negocio": (
        "Escreva de 1 a 3 frases descrevendo o que o negócio faz, em "
        "português do Brasil, tom direto — vira a descrição do negócio "
        "pro assistente de atendimento (SOUL.md). Não invente serviço "
        "específico que não foi mencionado no contexto; se faltar "
        "informação, escreva de forma mais genérica em vez de chutar "
        "detalhe."
    ),
    "tom_descricao": (
        "Escreva uma única linha curta descrevendo o tom de voz que o "
        "assistente de atendimento deve usar (exemplo de estilo: "
        "\"Português do Brasil, tom cordial e direto.\"). Baseie-se no "
        "que a pessoa descreveu sobre o negócio/estilo dela no contexto."
    ),
    "servicos": (
        "Liste os serviços/produtos oferecidos, um por linha, no formato "
        "exato `Nome | Descrição curta | Público-alvo`. Use só o que foi "
        "mencionado no contexto ou na descrição — não invente serviço que "
        "não foi citado. Se não souber o público-alvo, escreva algo "
        "genérico como \"Quem precisa do serviço\" em vez de inventar um "
        "nicho específico."
    ),
    "como_trabalhamos": (
        "Liste fatos operacionais do negócio, um por linha, no formato "
        "exato `Rótulo: valor` (exemplo de formato: \"Horário de "
        "atendimento: seg a sáb, 9h às 19h\"). Nunca invente horário, "
        "prazo ou preço específico que não foi mencionado no contexto — "
        "se não souber o valor, escreva `[ajustar]` no lugar dele, nunca "
        "um número chutado."
    ),
    "exemplos": (
        "Escreva de 2 a 4 pares de pergunta-e-resposta que um cliente "
        "típico mandaria pra esse negócio no WhatsApp, um par por linha, "
        "no formato exato `Pergunta | Resposta`. As respostas devem soar "
        "como o próprio assistente de atendimento respondendo, no tom "
        "descrito no contexto — sem inventar preço, prazo ou dado "
        "concreto que não esteja lá."
    ),
}


def suggest_field(field: str, context: dict, hint: str = "") -> str:
    """Chama o OpenRouter e devolve o texto sugerido pro campo `field`, já
    no formato que o parser existente espera (pipe-lines, label:texto,
    etc.) — pronto pra colar na textarea. Levanta AiAssistError em
    qualquer falha (rede, formato, campo sem suporte) — quem chama decide
    como degradar, nunca deve travar o preenchimento manual do
    formulário."""
    instructions = _FIELD_INSTRUCTIONS.get(field)
    if not instructions:
        raise AiAssistError(f"Campo sem suporte a sugestão da IA: {field}")

    context_lines = [f"{k}: {v}" for k, v in (context or {}).items() if v]
    context_text = "\n".join(context_lines) or "(nada preenchido ainda)"

    user_content = f"Contexto já preenchido nos outros campos:\n{context_text}\n"
    if hint.strip():
        user_content += f"\nO que a pessoa descreveu agora: {hint.strip()}\n"
    user_content += (
        f'\nGere só o conteúdo do campo "{field}", sem comentário, título '
        "nem explicação — só o texto pronto pra colar no campo."
    )

    payload = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": 500,
    }).encode()

    req = urllib.request.Request(
        OPENROUTER_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise AiAssistError(
            f"OpenRouter recusou a chamada: HTTP {e.code}: {e.read().decode()}"
        ) from e
    except urllib.error.URLError as e:
        raise AiAssistError(f"Falha de rede ao chamar o OpenRouter: {e}") from e

    try:
        content = body["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as e:
        raise AiAssistError(f"Resposta inesperada do OpenRouter: {body}") from e
    if not content:
        raise AiAssistError("OpenRouter devolveu uma sugestão vazia")
    return content
