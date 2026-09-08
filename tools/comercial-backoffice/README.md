# Backoffice comercial

CRM de leads + apuração de comissão pra vendedores/parceiros externos
do AtendPraGente. Regras de negócio completas em
`specs/comissao-vendedores.md` — este README só cobre o que é
específico de rodar o código.

## Onde roda

Servidor `2.28.15.6` (o mesmo da Duda), namespace `hermes` — **não** o
`atendagente` do onboarding-service. Não existe rede privada Hetzner
entre os dois servidores (confirmado por SSH), então este serviço tem
Mongo próprio (não o compartilhado) e fala com tenant só via a API
HTTP pública do onboarding-service (`app/onboarding_client.py`).

## Local (dev)

```
pip install -r requirements.txt
export MONGO_URI="mongodb://localhost:27017/backoffice"
export BACKOFFICE_SESSION_SECRET="dev-secret-troque-em-producao"
export ONBOARDING_ADMIN_KEY="mesma-chave-do-onboarding-service-env"
export ADMIN_PIN="0000"
uvicorn app.main:app --reload --port 8000
```

Primeiro vendedor admin precisa ser criado direto no Mongo (não existe
signup público) — ex.:

```python
from app import store
store.create_vendedor("Admin", "admin@atendpragente.com.br", "", "admin", "senha-forte-aqui", role="admin")
```

## Deploy

`python3 setup_backoffice.py` — ver docstring do próprio script.
