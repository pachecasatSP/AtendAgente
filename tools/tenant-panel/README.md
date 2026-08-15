# Painel por tenant (Fase 5 UI)

App FastAPI pequena, provisionada **por tenant** (não um painel central)
como parte de `tools/provision-tenant/provision_tenant.py` desde a
Fase 4. Lê `TENANT_ID` do ambiente e consulta o MongoDB compartilhado
(`sessions`/`messages`, sincronizados pelo sidecar `mongo-sync` da Fase
5), sempre filtrado por esse `tenant_id`.

Fica em `https://<tenant>.atendpragente.com.br/painel`, na mesma
Ingress do webhook do tenant (regra de path separada, não subdomínio
próprio). Login por formulário com sessão em cookie assinado — o
cliente cadastra o próprio usuário/senha na primeira visita, via um
link de configuração de uso único (`/painel/setup?token=...`, o token
vem do provisionamento em `PANEL_SETUP_TOKEN` no Secret do tenant;
`PANEL_SESSION_SECRET` assina o cookie de sessão). Credenciais do
cliente ficam na coleção `panel_auth` do Mongo compartilhado (hash
PBKDF2, nunca a senha em texto puro).

## Handoff manual (operador assume a conversa)

Cada sessão tem um campo `handoff` (bool) na collection `sessions`.
Com `handoff: true`, o painel mostra uma caixa de mensagem e o
operador pode responder direto pelo WhatsApp (`POST
/painel/api/messages/{session_id}/send`, chama a Cloud API com
`WHATSAPP_CLOUD_ACCESS_TOKEN`/`WHATSAPP_CLOUD_PHONE_NUMBER_ID` — já
disponíveis no container via o mesmo Secret `{tenant}-env` do Hermes,
nenhuma env var nova precisa ser provisionada). Mandar uma mensagem já
liga `handoff` sozinho; existe também um toggle manual
(`POST /painel/api/sessions/{session_id}/handoff`).

**Importante:** por enquanto isso só controla o que o painel mostra e
permite enviar — o processo Hermes do tenant (que recebe o webhook da
Meta direto, sem passar pelo painel) ainda **não checa** esse campo, ou
seja, o bot continua respondendo normalmente mesmo com `handoff: true`.
Silenciar o bot de fato requer um patch em
`/opt/hermes/plugins/platforms/whatsapp/adapter.py` no servidor (fora
deste repo) — ver `specs/roadmap-multi-tenant.md` seção "Operação"/
"Painel". Essa é a Fase B, ainda não feita.

## Rodar localmente

```bash
export TENANT_ID=teste
export MONGO_URI="mongodb://user:pass@localhost:27017/atendagente?authSource=admin"
export PANEL_SETUP_TOKEN=token-local-qualquer
export PANEL_SESSION_SECRET=segredo-local-qualquer
pip install -r requirements.txt
uvicorn app:app --reload
# depois acesse http://localhost:8000/painel/setup?token=token-local-qualquer
```

## Deploy

Não tem script de setup próprio — é gerado e aplicado automaticamente
por `provision_tenant.py` (`build_infra_manifest`) pra cada tenant, sem
imagem Docker/registry: `python:3.12-slim` + `pip install` no startup,
código montado via `hostPath` de `/root/atendagente-tools/tenant-panel/`
(mesmo padrão do sidecar `mongo-sync`).
