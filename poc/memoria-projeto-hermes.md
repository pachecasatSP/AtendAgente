# Memória do Projeto — Bot de Atendimento WhatsApp (Hermes) / Ac Soluções

> Documento de contexto para retomar o projeto em outra conversa. Resume
> decisões, estado atual, credenciais (só localização, nunca valores) e
> pendências. Última atualização: 2026-07-24.

---

## Quem é / contexto

- **Empresa:** Ac Soluções em Software Ltda (razão social oficial; identificação
  Meta/business_id: `839821984960375`). Responsável: **Adolfo Sales Pacheco**,
  contato **(11) 95439-2987**.
- **Domínio:** `colocar-me.com.br` (registrado no Registro.br).
- **Objetivo:** operar bots de atendimento no WhatsApp — começou com um cliente
  (estúdio de yoga "Yogart") e evoluiu para virar produto/Tech Provider da Meta,
  com a Ac Soluções oferecendo "Agentes de Atendimento no WhatsApp" como serviço.

## O que é o "Hermes"

- **hermes-agent** (framework da Nous Research, MIT). Roda como gateway 24/7,
  chama LLM por API externa (não precisa de GPU).
- Gateway escuta porta **8090**, webhook path `/whatsapp/webhook`.
- Canal de produção: **WhatsApp Cloud API** (não Baileys — número em WABA não
  pareia por QR, e Cloud API é Python puro, mais leve e sem risco de ban).
- **Ferramentas restritas de propósito:** só `clarify` + `memory` ativas.
  Terminal/code/file/browser/computer_use DESLIGADAS (segurança contra prompt
  injection em canal aberto a desconhecidos). Whisper/STT mantido.

## Credenciais / IDs (WABA do Yogart, hoje usada pela Ac Soluções)

- **phone_number_id:** `448951754961208`
- **número de telefone:** +55 11 92008-1743 (ATENÇÃO: tem 8 dígitos após DDD —
  verificar se falta o 9, ficaria 55119920081743 no wa.me)
- **WABA_ID:** `428254857032810`
- **APP_ID:** `1337983741884069`
- Credenciais reais vivem no Secret `hermes-env` do K8s e no `.env`. NUNCA
  versionar valores.

---

## ESTADO ATUAL DA INFRAESTRUTURA (o que está no ar)

### Produção: Hetzner + K3s (ATIVO)
- **Máquina:** Hetzner **CPX22** (2 vCPU AMD / 4 GB / 80 GB), x86.
  (CX e CAX estavam indisponíveis; CPX escolhida — x86 elimina dúvida de ARM.)
- **Host:** `ubuntu-4gb-fsn1-1`, IP **2.28.15.6**, usuário `root`, Ubuntu 24.04.
- **Arquitetura:** K3s single-node, Traefik (embutido) + cert-manager.
  Namespace `hermes`. Um namespace por projeto (preparado para trazer Azure
  depois).
- **Webhook:** `https://bot.colocar-me.com.br/whatsapp/webhook` — URL FIXA com
  HTTPS válido (Let's Encrypt prod). Eliminou o cloudflared frágil.
- **DNS:** registro A `bot.colocar-me.com.br` → 2.28.15.6 (Registro.br).
- **Provedor LLM:** **OpenRouter** (API key), modelo
  `meta-llama/llama-3.3-70b-instruct`. Config no `config.yaml`:
  `provider: openrouter`, `base_url: https://openrouter.ai/api/v1`.
- **Volume persistente:** PVC `hermes-data` (local-path, 5Gi), montado em
  `/opt/data` — guarda SOUL.md, config, memória, sessões.
- **Health confirmado:** `accepted:1, rejected_signature:0, ffmpeg_present:true`.
  Bot responde corretamente pela Hetzner.
- **Manifestos versionados:** repositório `hermes-k3s/` (cluster/, hermes/,
  scripts/setup-k3s.sh, README). Deployment usa `replicas:1` +
  `strategy:Recreate` (duas instâncias brigariam pela sessão).

### Aposentada: Oracle Cloud (a desligar)
- Oracle Linux 9.8, ~498 MB RAM, IP 163.176.33.136, usuário `opc`.
- Era o PoC original. Sofria de OOM, SELinux, cloudflared frágil.
- **Ação pendente:** backup do `~/.hermes/`, parar serviços
  (`systemctl stop hermes cloudflared-hermes`), destruir instância após dias de
  Hetzner estável.

### Site institucional: Netlify (ATIVO)
- Landing + política de privacidade da Ac Soluções, hospedados na **Netlify**
  (CDN, sem IP fixo).
- URL: **solucoes-re.colocar-me.com.br** (política em `/privacidade`).
- Arquivos: `index.html` + `privacidade.html` (identidade verde-folha/âmbar).
- Botões "Fale conosco" (4: nav, hero, contato, rodapé) linkam para
  `https://wa.me/5511920081743?text=Quero%20informações%20sobre%20bots%20de%20atendimento`
  — apontam para a WABA (demonstração ao vivo do produto).

---

## App Review da Meta — APROVADO ✓

- Ac Soluções é **Tech Provider aprovado**. Business Verification ✓, Access
  Verification ✓, App Review ✓.
- Permissões concedidas: `whatsapp_business_messaging` +
  `whatsapp_business_management` (+ public_profile automático).
  Removida `business_management` (desnecessária, enxugou a submissão).
- Vídeos de evidência: (1) cURL enviando mensagem via Cloud API;
  (2) criação de template no WhatsApp Manager. Ambos com legenda em inglês.
- **Verificar:** se o app já está em modo **Live** (não Development). Em dev há
  limite de 5 destinatários. Live remove o limite (requer o App Review, que já
  passou).
- Isso habilita **Embedded Signup** → base para o multi-tenant.

---

## SOULs (personas)

### SOUL da Ac Soluções (ATIVO no número hoje)
- Arquivo: `SOUL-ac-solucoes.md`. Tom carioca informal-competente.
- 4 serviços: Desenvolvimento Sob Medida, Head Count, Squads as a Service,
  **Agentes de Atendimento no WhatsApp** (este último aponta que a própria
  conversa é a demonstração — casa com a mensagem que vem do site).
- Encaminha para o Adolfo (11) 95439-2987. Salvaguardas: não inventa, não fala
  valor, não dá opinião técnica definitiva, não promete o que não cumpre.
- PENDENTE no SOUL: cases/portfólio, disponibilidade atual.
- REVISAR: padronizar "AC Soluções" → "Ac Soluções em Software"; confirmar
  número; a frase "não me chamo Hermes" pode disparar o filtro de prompt
  injection do Hermes (se o SOUL não carregar, é o 1º suspeito — reescrever de
  forma afirmativa).

### SOUL do Yogart (INATIVO — sobrescrito)
- Arquivo: `SOUL-yogart-v2.md` (~300 linhas). Estúdio de yoga na Vila Andrade,
  SP (R. Dep. João Sussumu Hirata, 662-3, CEP 05715-010, estacionamento grátis).
- Grade de 21 aulas/semana, 9 modalidades descritas, regra de nível iniciante
  (Vinyasa Flow=avançado, Kuruntas=desafio), regra do 2º domingo (aula no Parque
  Burle Marx), Experiência de Chegada (3 aulas R$140, ou R$185 com tapete).
  Encaminha para Maria Miranda (11) 97084-0601.
- **CONFLITO EM ABERTO:** o número tem só 1 SOUL ativo por vez. Ao ativar o SOUL
  da Ac Soluções, o Yogart ficou SEM atendimento nesse número. Decisão pendente:
  Yogart pausa, ou precisa de número próprio? É o que o multi-tenant resolve.

---

## PENDÊNCIAS EM ABERTO (por prioridade)

1. **[SEGURANÇA - URGENTE] Rotacionar a chave do OpenRouter.** A chave
   `sk-or-v1-820954...` apareceu inteira em conversa — comprometida. Gerar nova,
   revogar antiga, atualizar Secret. CUIDADO: a chave teve o prefixo `sk-or-`
   DUPLICADO uma vez (79 chars em vez de 73) — colar UMA vez só, confirmar com
   `wc -c` = 73/74.

2. **[INFRA] Aposentar a Oracle** (163.176.33.136): backup + stop serviços +
   destruir instância após dias de Hetzner estável.

3. **[PRODUTO] Definir Yogart vs Ac Soluções no número único.** Um número, um
   SOUL. Se Yogart precisa atender, precisa de 2º número OU acelerar
   multi-tenant. Decisão de negócio pendente.

4. **[PRODUTO] Multi-tenant / Embedded Signup.** Habilitado pela aprovação Tech
   Provider. É a próxima fase: cada negócio com seu número/SOUL, onboarding via
   Embedded Signup. Exige construir: multi-tenancy no Hermes (hoje single-tenant),
   painel, billing, SOUL como configuração (não arquivo editado à mão),
   isolamento de dados.

5. **[INFRA - FUTURO] Migração do Kubernetes do Azure** para o mesmo K3s da
   Hetzner (cada projeto num namespace). Adiado — dimensionar quando for (a
   CPX22 de 4GB pode não bastar; avaliar upgrade). Pontos de tradução AKS→K3s:
   StorageClass Azure→local-path/Longhorn, LoadBalancer→ServiceLB/MetalLB,
   Ingress→Traefik, Key Vault→Secret/External Secrets.

6. **[SOUL] Verificar número da WABA no site** (8 vs 9 dígitos) e completar SOUL
   da Ac Soluções (cases, disponibilidade).

---

## APRENDIZADOS NÃO ÓBVIOS (para não repetir os erros)

- **System prompt fica em cache por sessão:** editar o SOUL exige `/new` no
  chat, não só restart do serviço.
- **Secret do K8s não recarrega em pod rodando:** após recriar o Secret, é
  obrigatório `kubectl rollout restart deploy/hermes`. Variáveis só são lidas na
  subida do container. (Sintoma que enganou: verify token continuava `PREENCHER`
  mesmo com Secret novo.)
- **Secret criado do arquivo EXEMPLO:** cuidado para criar o Secret do `.env`
  real (`--from-env-file=.env`), não do `02-secret.EXAMPLE.yaml` (que tem
  `PREENCHER`).
- **CRLF do Windows** quebra tokens de forma intermitente: rodar
  `tr -d '\r' < .env > .env.clean` antes de criar Secret.
- **Prefixo duplicado em chave** (`sk-or-sk-or-v1-...`): só `od -c` revelou.
  Verificar tamanho sempre.
- **Número em Cloud API não abre no WhatsApp Web/app comum** — ele vira endpoint
  de API. Destinatário de teste tem que ser outro número.
- **Janela de 24h da Meta:** texto livre só para quem escreveu nas últimas 24h.
  Fora disso, só template pré-aprovado (Hermes não implementa → `graph error
  131047`). Por isso disparo ativo (lembretes, prospecção) exige template +
  disparador próprio, fora do Hermes puro.
- **host key changed no SSH** ao recriar servidor com mesmo IP: normal,
  `ssh-keygen -R <ip>` resolve.
- **cert-manager: testar em staging antes de prod** (rate limit de prod: ~5
  certs/semana). Trocar issuer staging→prod e deletar o secret TLS para reemitir.
- **DNS tem que apontar ANTES de aplicar o Ingress** — o desafio HTTP-01 do
  Let's Encrypt precisa alcançar o servidor pelo domínio.
- **Firewall Hetzner: abrir 22, 80 e 443.** 80 é obrigatória para o Let's
  Encrypt, não só a 443.

---

## ARQUIVOS DO PROJETO (gerados nas conversas)

- `SOUL-ac-solucoes.md` — persona ativa (Ac Soluções, 4 serviços)
- `SOUL-yogart-v2.md` — persona do Yogart (inativa)
- `spec-agente-whatsapp-yogart.md` — spec técnica completa
- `guia-comercializacao-bot.md` — guia de validação para comercializar
- `index.html` + `privacidade.html` — site institucional (na Netlify)
- `hermes-k3s/` (+ `.tar.gz`) — manifestos K3s versionados
- `setup-oracle-linux.sh` / `setup-oracle.sh` — bootstrap Oracle (legado)
