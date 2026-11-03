/* Formulário de cadastro em passos (/signup/{id}/form). Mesma lógica de sempre: passos, validação por passo, sugestão de endereço (slug),
   busca de CEP (ViaCEP), ajuda de IA por campo e desistência. O servidor valida de novo no POST; nada daqui é a validação que vale. */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var signupId = document.querySelector('[data-signup-id]').dataset.signupId;
  var form = $('onboarding-form');
  var gratuito = form.dataset.gratuito === 'true';

  /* ---------- desistir ---------- */
  var desistirConfirm = $('desistir-confirm');
  $('desistir-link').addEventListener('click', function () { desistirConfirm.hidden = false; $('desistir-voltar').focus(); });
  $('desistir-voltar').addEventListener('click', function () { desistirConfirm.hidden = true; $('desistir-link').focus(); });
  desistirConfirm.addEventListener('keydown', function (e) { if (e.key === 'Escape') { desistirConfirm.hidden = true; $('desistir-link').focus(); } });
  $('desistir-confirmar').addEventListener('click', function (e) {
    var btn = e.currentTarget, erro = $('desistir-erro');
    erro.hidden = true; btn.disabled = true; btn.textContent = 'Desconectando…';
    fetch('/signup/' + encodeURIComponent(signupId) + '/desistir', { method: 'POST' })
      .then(function (r) { if (!r.ok) throw new Error('falhou'); return r.json(); })
      .then(function (d) { window.location.href = d.next; })
      .catch(function () { btn.disabled = false; btn.textContent = 'Sim, desistir'; erro.hidden = false; });
  });

  /* ---------- passos ---------- */
  var steps = Array.from(document.querySelectorAll('.passo'));
  var dots = Array.from(document.querySelectorAll('.progresso .dot'));
  var progressLabel = $('progress-label'), btnBack = $('btn-back'), btnNext = $('btn-next'), btnSubmit = $('btn-submit');
  // Convite gratuito não escolhe plano: o passo marcado com data-skip-if-gratuito some. `current` é a posição na lista visível, não o data-step bruto.
  var visibleStepNumbers = steps.filter(function (s) { return !(gratuito && s.hasAttribute('data-skip-if-gratuito')); }).map(function (s) { return Number(s.dataset.step); });
  var current = 1, total = visibleStepNumbers.length, primeiraVez = true;

  function render() {
    var ativo = visibleStepNumbers[current - 1];
    steps.forEach(function (s) { s.classList.toggle('active', Number(s.dataset.step) === ativo); });
    dots.forEach(function (d) {
      var pos = visibleStepNumbers.indexOf(Number(d.dataset.i)) + 1;     // posição visível desse ponto, ou 0 se oculto
      d.classList.toggle('done', pos > 0 && pos < current);
      d.classList.toggle('current', pos === current);
    });
    progressLabel.textContent = 'Passo ' + current + ' de ' + total;
    btnBack.disabled = current === 1;
    var ultimo = current === total;
    btnNext.hidden = ultimo; btnSubmit.hidden = !ultimo;
    if (!primeiraVez) {      // na carga da página não rouba o foco (no celular abria o teclado de cara)
      var passoAtivo = steps[ativo - 1];
      var campo = passoAtivo.querySelector('input[type=radio]:checked') || passoAtivo.querySelector('input, textarea');   // nos planos, o foco vai para o marcado
      if (campo) campo.focus({ preventScroll: true });
      window.scrollTo({ top: Math.max(0, document.querySelector('.cartao').offsetTop - 20), behavior: 'smooth' });
    }
    primeiraVez = false;
  }

  function parseFirst(field, index) { return ((field || '').split('|')[index] || '').trim(); }

  function suggestExamples() {
    var nome = document.querySelector('[name=nome_negocio]').value.trim() || 'a gente';
    var servicos = document.querySelector('[name=servicos]').value.split('\n').map(function (l) { return l.trim(); }).filter(Boolean);
    var escalNome = document.querySelector('[name=escalacao_nome]').value.trim();
    var escalTel = document.querySelector('[name=escalacao_telefone]').value.trim();
    var contato = escalNome ? escalNome + (escalTel ? ' (' + escalTel + ')' : '') : 'a gente';
    var s = [], s1 = parseFirst(servicos[0], 0), s2 = parseFirst(servicos[1], 0);
    if (s1) {
      s.push('Vocês têm horário pra ' + s1 + '? | Temos sim! Me conta que dia funciona melhor pra você que eu já vejo a disponibilidade.');
      s.push('Quanto custa ' + s1 + '? | Isso eu confirmo certinho com ' + contato + ' — mas já te adianto que vale a pena perguntar.');
    } else {
      s.push('Vocês estão abertos hoje? | Aqui é ' + nome + ', estamos sim! Me conta o que você precisa que eu já te ajudo.');
    }
    if (s2) s.push('Vocês também fazem ' + s2 + '? | Fazemos sim! Quer que eu te explique como funciona?');
    s.push('Posso falar com uma pessoa? | Claro! Chamando ' + contato + ' aqui pra te ajudar.');
    return s.join('\n');
  }
  function maybeSuggestExamples() { var f = $('exemplos-field'); if (f && !f.value.trim()) f.value = suggestExamples(); }

  function currentStepValid() {
    var campos = steps[visibleStepNumbers[current - 1] - 1].querySelectorAll('input, textarea');
    for (var i = 0; i < campos.length; i++) { if (!campos[i].reportValidity()) return false; }
    if (current === 1 && slugTaken) {
      tenantIdField.setCustomValidity('Esse endereço já está em uso — escolha outro.');
      tenantIdField.reportValidity(); tenantIdField.setCustomValidity('');
      return false;
    }
    return true;
  }

  /* ---------- sugestão de endereço (slug) a partir do nome do negócio ---------- */
  var nomeField = $('nome_negocio-field'), tenantIdField = $('tenant_id-field'), slugStatus = $('tenant_id-status');
  var tenantIdEdited = false, slugTaken = false, slugCheckTimer = null;
  function slugify(t) { return t.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 32); }
  function statusSlug(texto, classe) { slugStatus.textContent = texto; slugStatus.className = 'status-campo' + (classe ? ' ' + classe : ''); }

  async function checkSlug() {
    var valor = tenantIdField.value.trim().toLowerCase();
    if (!/^[a-z][a-z0-9-]{1,30}[a-z0-9]$/.test(valor)) { statusSlug(''); slugTaken = false; return; }
    statusSlug('Verificando disponibilidade…', 'checking');
    try {
      var resp = await fetch('/api/signup/check-slug?tenant_id=' + encodeURIComponent(valor));
      var data = await resp.json();
      if (tenantIdField.value.trim().toLowerCase() !== valor) return;     // digitou mais enquanto esperava
      if (data.disponivel) { statusSlug(valor + '.atendpragente.com.br está disponível', 'ok'); slugTaken = false; }
      else { statusSlug('Esse endereço já está em uso. Escolha outro.', 'taken'); slugTaken = true; }
    } catch (e) { statusSlug(''); slugTaken = false; }
  }
  function scheduleSlugCheck() { clearTimeout(slugCheckTimer); slugCheckTimer = setTimeout(checkSlug, 450); }
  nomeField.addEventListener('input', function () { if (tenantIdEdited) return; tenantIdField.value = slugify(nomeField.value); scheduleSlugCheck(); });
  tenantIdField.addEventListener('input', function () { tenantIdEdited = true; tenantIdField.value = slugify(tenantIdField.value); scheduleSlugCheck(); });

  /* ---------- CEP (ViaCEP) preenche endereço e bairro ---------- */
  var cepField = $('cep-field'), cepStatus = $('cep-status'), enderecoField = $('endereco-field'), bairroField = $('bairro-field'), cepTimer = null;
  function statusCep(texto, classe) { cepStatus.textContent = texto; cepStatus.className = 'status-campo' + (classe ? ' ' + classe : ''); }
  async function lookupCep() {
    var digits = cepField.value.replace(/\D/g, '');
    if (digits.length !== 8) { statusCep(''); return; }
    statusCep('Buscando endereço…', 'checking');
    try {
      var resp = await fetch('https://viacep.com.br/ws/' + digits + '/json/');
      var data = await resp.json();
      if (cepField.value.replace(/\D/g, '') !== digits) return;           // CEP mudou enquanto buscava
      if (data.erro) { statusCep('CEP não encontrado', 'taken'); return; }
      if (data.logradouro) enderecoField.value = data.logradouro;
      if (data.bairro) bairroField.value = data.bairro;
      statusCep('Endereço preenchido', 'ok');
    } catch (e) { statusCep(''); }
  }
  cepField.addEventListener('input', function () {
    var d = cepField.value.replace(/\D/g, '').slice(0, 8);
    cepField.value = d.length > 5 ? d.slice(0, 5) + '-' + d.slice(5) : d;
    clearTimeout(cepTimer); cepTimer = setTimeout(lookupCep, 400);
  });

  btnNext.addEventListener('click', function () {
    if (!currentStepValid()) return;
    current = Math.min(total, current + 1);
    // as sugestões de exemplos aparecem ao CHEGAR no passo que tem o campo (antes só no último passo, depois de a pessoa já ter visto o campo vazio)
    if (steps[visibleStepNumbers[current - 1] - 1].contains($('exemplos-field'))) maybeSuggestExamples();
    render();
  });
  btnBack.addEventListener('click', function () { current = Math.max(1, current - 1); render(); });

  // Ativar provisiona pod/DNS/certificado de verdade e demora (até ~1-2 min): o submit segue nativo, só dá retorno visual na hora.
  form.addEventListener('submit', function () {
    btnSubmit.disabled = true;
    btnSubmit.textContent = gratuito ? 'Ativando seu atendimento gratuito…' : 'Ativando seu atendimento…';
    btnBack.disabled = true;
  });
  render();

  /* ---------- ajuda de IA nos campos difíceis (specs/assistente-ia-configuracoes.md): um listener só para os 5 campos ---------- */
  document.addEventListener('click', async function (e) {
    var abrir = e.target.closest('.ai-assist-btn');
    if (abrir) { var b = document.querySelector('.ai-assist-box[data-for="' + abrir.dataset.field + '"]'); if (b) b.hidden = !b.hidden; return; }

    var gerar = e.target.closest('.ai-assist-gerar');
    if (gerar) {
      var box = gerar.closest('.ai-assist-box'), field = box.dataset.for, hint = box.querySelector('.ai-assist-hint').value;
      var erroEl = box.querySelector('.ai-assist-erro'), resultadoEl = box.querySelector('.ai-assist-resultado');
      erroEl.hidden = true; resultadoEl.hidden = true; gerar.disabled = true; gerar.textContent = 'Pensando…';
      try {
        var context = {};      // manda o form inteiro como contexto: os campos de passos anteriores continuam no DOM
        new FormData(form).forEach(function (v, k) { if (typeof v === 'string' && v.trim()) context[k] = v; });
        var resp = await fetch('/signup/' + encodeURIComponent(signupId) + '/ai-assist', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ field: field, hint: hint, context: context }) });
        if (!resp.ok) { var err = await resp.json().catch(function () { return {}; }); throw new Error(err.detail || 'Não consegui gerar a sugestão.'); }
        var data = await resp.json();
        box.querySelector('.ai-assist-texto').textContent = data.suggestion; resultadoEl.hidden = false;
      } catch (err) { erroEl.textContent = err.message; erroEl.hidden = false; }
      finally { gerar.disabled = false; gerar.textContent = 'Gerar sugestão'; }
      return;
    }

    var usar = e.target.closest('.ai-assist-usar');
    if (usar) {
      var box2 = usar.closest('.ai-assist-box'), alvo = document.querySelector('[name="' + box2.dataset.for + '"]');
      if (alvo) { alvo.value = box2.querySelector('.ai-assist-texto').textContent; alvo.focus(); }
      box2.hidden = true; return;
    }
    var descartar = e.target.closest('.ai-assist-descartar');
    if (descartar) descartar.closest('.ai-assist-resultado').hidden = true;
  });
})();
