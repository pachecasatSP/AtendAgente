# Melhorias no Onboarding — AtendPraGente

**Página analisada:** http://onboarding.atendpragente.com.br/signup

## Contexto

A página hoje é uma landing simples e direta, com foco em conversão (bonita, curta, emocional), mas **sem nenhuma preparação** para o que acontece depois do clique em "Conectar meu WhatsApp". Isso é o ponto crítico para um cliente leigo, porque o embedded signup da Meta joga o usuário direto numa tela de "Criar ou selecionar Portfólio Empresarial" sem contexto algum — e é exatamente aí que a maioria trava, desiste ou erra (cria portfólio duplicado, usa conta pessoal errada, etc.).

Como o link de convite é de **uso único**, qualquer travamento no meio do fluxo é mais grave do que em um cadastro comum: o cliente não pode simplesmente "tentar de novo" sozinho.

---

## Sugestões de conteúdo (em camadas)

### 1. Bloco "O que vai acontecer agora" — antes do botão

Curto, 3 passos, para baixar a ansiedade e evitar o "ué, por que tá pedindo Facebook?" no meio do fluxo:

1. Você vai fazer login com sua conta do Facebook
2. A Meta vai pedir pra você criar (ou escolher) o Portfólio Empresarial da sua empresa
3. Você confirma seu número de WhatsApp por SMS ou ligação

### 2. Aviso específico sobre o Portfólio Empresarial

Ponto que mais gera confusão em cliente leigo — evita duplicidade de Business Manager e problemas de verificação depois:

> ⚠️ Se sua empresa já tem um Portfólio Empresarial (Business Manager) no Facebook, selecione ele em vez de criar um novo — assim evitamos duplicidade e problemas de verificação depois.

### 3. Aviso sobre o número de telefone — antes de gerar frustração

Precisa ficar claro **antes** do fluxo, não depois. Muito cliente acha que vai continuar usando o WhatsApp normal (app) e o assistente ao mesmo tempo:

> Esse número vai passar a funcionar via WhatsApp Business API. Se ele já tem o WhatsApp normal (app) instalado, você precisará desinstalar o app desse número depois de conectar.

### 4. Estado de espera / confirmação pós-signup

Tela clara de "deu certo" ao final do embedded signup, contendo:
- O que foi conectado (nome da empresa, número)
- O que falta acontecer (ex.: verificação da Meta pode levar horas/dias)
- Um contato de suporte visível (essencial, já que o link é de uso único — se travar no meio, o cliente precisa de uma saída)

### 5. Mini FAQ / tooltip de apoio

Um atalho tipo "Já tenho um Portfólio Empresarial, mas não sei onde acessar", explicando a diferença entre:
- Conta pessoal do Facebook
- Página do Facebook
- Portfólio Empresarial (Business Manager)

Essas três coisas são praticamente sempre confundidas por quem nunca usou a plataforma.

---

## Pendências para fechar o texto final

- Confirmar se o fluxo tem uma tela de "sucesso" separada depois do embedded signup, ou se o modal da Meta simplesmente fecha e some (isso muda onde o item 4 deve ser implementado).
- Definir o tom exato do texto (mesmo estilo direto/informal da página atual) para os blocos acima.
