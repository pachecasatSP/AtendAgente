# Painel por tenant (Fase 5 UI)

App FastAPI pequena, provisionada **por tenant** (não um painel central)
como parte de `tools/provision-tenant/provision_tenant.py` desde a
Fase 4. Lê `TENANT_ID` do ambiente e consulta o MongoDB compartilhado
(`sessions`/`messages`, sincronizados pelo sidecar `mongo-sync` da Fase
5), sempre filtrado por esse `tenant_id`.

Fica em `https://<tenant>.atendpragente.com.br/painel`, na mesma
Ingress do webhook do tenant (regra de path separada, não subdomínio
próprio). Protegido por **HTTP Basic Auth** — usuário/senha gerados no
provisionamento (`PANEL_USER`/`PANEL_PASSWORD` no Secret do tenant).

## Rodar localmente

```bash
export TENANT_ID=teste
export MONGO_URI="mongodb://user:pass@localhost:27017/atendagente?authSource=admin"
export PANEL_USER=teste-admin
export PANEL_PASSWORD=senha-local
pip install -r requirements.txt
uvicorn app:app --reload
```

## Deploy

Não tem script de setup próprio — é gerado e aplicado automaticamente
por `provision_tenant.py` (`build_infra_manifest`) pra cada tenant, sem
imagem Docker/registry: `python:3.12-slim` + `pip install` no startup,
código montado via `hostPath` de `/root/atendagente-tools/tenant-panel/`
(mesmo padrão do sidecar `mongo-sync`).
