/* Painel (/painel): lista de conversas, conversa, assumir/devolver ao bot, envio de texto e imagem.
   Rotas e chaves de localStorage são as mesmas de sempre (painel-seen-<session_id>). */
(function () {
  'use strict';
  var POLL_MS = 5000;
  var $ = function (id) { return document.getElementById(id); };
  var convListEl = $('conv-list'), threadAreaEl = $('thread-area'), threadHeaderEl = $('thread-header'), threadTitleEl = $('thread-title');
  var threadStatusEl = $('thread-status'), threadAvatarEl = $('thread-avatar'), vazioEl = $('vazio-conversa');
  var handoffBtnEl = $('handoff-btn'), deleteBtnEl = $('delete-btn'), backBtnEl = $('back-btn');
  var operatorAlertEl = $('operator-alert'), operatorTextEl = $('operator-alert-text'), resolveBtnEl = $('resolve-btn');
  var composeFormEl = $('compose-form'), composeInputEl = $('compose-input'), avisoBotEl = $('aviso-bot');
  var attachBtnEl = $('attach-btn'), imageInputEl = $('image-input'), previaEl = $('previa');
  var previaImgEl = $('previa-img'), previaNomeEl = $('previa-nome'), previaRemoverEl = $('previa-remover');
  var avisoEl = $('aviso'), dlgEl = $('dlg-excluir'), dlgTextoEl = $('dlg-excluir-texto');

  var activeSessionId = null, lastMessagesSignature = null, lastListSignature = null, sessionsById = {}, pendingImageFile = null, avisoTimer = null;
  var TZ = 'America/Sao_Paulo';
  var fmtDia = new Intl.DateTimeFormat('en-CA', { timeZone: TZ });

  var HANDOFF_MOTIVO_LABEL = {
    'bot (auto-escalação)': 'o bot identificou que precisa de uma pessoa',
    'pedido (rastreamento manual)': 'cliente perguntou sobre um pedido sem rastreio cadastrado',
    'agenda (remarcação)': 'cliente pediu pra remarcar um horário',
    'entrega falhou': 'uma mensagem não foi entregue (provavelmente janela de 24h fechada)'
  };

  function seenKey(id) { return 'painel-seen-' + id; }
  function lerSeen(id) { try { return parseInt(localStorage.getItem(seenKey(id)) || '0', 10) || 0; } catch (e) { return 0; } }
  function gravarSeen(id, n) { try { localStorage.setItem(seenKey(id), String(n)); } catch (e) { /* sem storage: só perde o contador de não lidas */ } }

  function escapeHtml(str) { var d = document.createElement('div'); d.textContent = str == null ? '' : String(str); return d.innerHTML; }
  function icone(nome, classe) { return '<svg class="icone ' + (classe || '') + '" aria-hidden="true"><use href="#i-' + nome + '"/></svg>'; }

  function handoffMotivoTexto(by) {
    if (!by) return 'O bot sinalizou que essa conversa precisa de um humano assumindo.';
    var label = HANDOFF_MOTIVO_LABEL[by];
    return label ? 'Motivo: ' + label + '.' : 'Motivo: ' + by;
  }

  /* ---------- datas: o servidor manda "YYYY-MM-DD HH:MM" em horário de São Paulo ---------- */
  function hojeSP(deslocDias) { return fmtDia.format(new Date(Date.now() + (deslocDias || 0) * 864e5)); }
  function partes(fmt) { var m = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}:\d{2})$/.exec(fmt || ''); return m ? { dia: m[1] + '-' + m[2] + '-' + m[3], d: m[3], m: m[2], y: m[1], hora: m[4] } : null; }
  function rotuloDia(p) { return p.dia === hojeSP() ? 'Hoje' : p.dia === hojeSP(-1) ? 'Ontem' : p.d + '/' + p.m + '/' + p.y; }
  function horaLista(fmt) { var p = partes(fmt); if (!p) return fmt || ''; return p.dia === hojeSP() ? p.hora : p.dia === hojeSP(-1) ? 'Ontem' : p.d + '/' + p.m; }

  /* ---------- nomes ---------- */
  function nomeExibido(s) {
    var n = s.display_name || s.chat_id || s.session_id || '';
    var d = /^\d{12,13}$/.test(n) ? n : '';
    if (d && d.indexOf('55') === 0) {
      var r = d.slice(4);
      return '+55 ' + d.slice(2, 4) + ' ' + r.slice(0, r.length - 4) + '-' + r.slice(-4);
    }
    return n;
  }
  function avatarHtml(nome) {
    var palavras = String(nome).split(/\s+/).filter(function (p) { return /\p{L}/u.test(p); });
    if (!palavras.length) return '<span class="avatar" aria-hidden="true">' + icone('pessoa') + '</span>';
    var ini = palavras.slice(0, 2).map(function (p) { return p.match(/\p{L}/u)[0]; }).join('').toUpperCase();
    return '<span class="avatar" aria-hidden="true">' + escapeHtml(ini) + '</span>';
  }

  function mostrarAviso(msg) {
    avisoEl.textContent = msg; avisoEl.hidden = false;
    clearTimeout(avisoTimer); avisoTimer = setTimeout(function () { avisoEl.hidden = true; }, 6000);
  }
  function sessaoExpirada(resp) { if (resp.status === 401) { window.location.href = '/painel/login'; return true; } return false; }

  /* ---------- lista ---------- */
  async function loadSessions() {
    var resp = await fetch('/painel/api/sessions');
    if (sessaoExpirada(resp) || !resp.ok) return;
    var sessions = await resp.json();
    sessionsById = {};
    var itens = sessions.map(function (s) {
      sessionsById[s.session_id] = s;
      return { id: s.session_id, nome: nomeExibido(s), hora: horaLista(s.last_activity_fmt), precisa: !!s.needs_operator, manual: !!s.handoff,
               naoLidas: Math.max(0, (s.message_count || 0) - lerSeen(s.session_id)), ativa: s.session_id === activeSessionId, motivo: s.handoff_by };
    });
    var assinatura = JSON.stringify(itens);
    if (assinatura !== lastListSignature) {            // sem mudança, não mexe no DOM (preserva foco e rolagem a cada 5 s)
      lastListSignature = assinatura;
      var focado = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.sid : null;
      if (!itens.length) {
        convListEl.innerHTML = '<li class="vazio-lista">Nenhuma conversa ainda. Quando um cliente escrever no WhatsApp, ela aparece aqui.</li>';
      } else {
        convListEl.innerHTML = itens.map(function (i) {
          var estado = i.precisa ? '<span class="conv-estado precisa">' + icone('alerta') + 'Precisa de você</span>'
                     : i.manual ? '<span class="conv-estado">Atendimento manual</span>' : '<span class="conv-estado"></span>';
          var badge = i.naoLidas > 0 ? '<span class="badge">' + i.naoLidas + '<span class="so-leitor"> não lidas</span></span>' : '';
          return '<li><button type="button" class="conv' + (i.precisa ? ' precisa' : '') + '" data-sid="' + escapeHtml(i.id) + '"' +
            (i.ativa ? ' aria-current="true"' : '') + (i.precisa ? ' title="' + escapeHtml(handoffMotivoTexto(i.motivo)) + '"' : '') + '>' +
            avatarHtml(i.nome) + '<span class="conv-corpo"><span class="conv-linha"><span class="conv-nome">' + escapeHtml(i.nome) + '</span>' +
            '<time class="conv-hora">' + escapeHtml(i.hora) + '</time></span><span class="conv-linha">' + estado + badge + '</span></span></button></li>';
        }).join('');
        if (focado) { var el = convListEl.querySelector('[data-sid="' + CSS.escape(focado) + '"]'); if (el) el.focus(); }
      }
    }
    if (activeSessionId) updateHeader(activeSessionId);
  }

  convListEl.addEventListener('click', function (ev) {
    var b = ev.target.closest('button[data-sid]');
    if (!b) return;
    var s = sessionsById[b.dataset.sid];
    openThread(b.dataset.sid, s ? s.message_count : 0);
  });

  /* ---------- cabeçalho da conversa ---------- */
  function updateHeader(id) {
    var s = sessionsById[id];
    if (!s) return;
    var nome = nomeExibido(s);
    threadHeaderEl.hidden = false; vazioEl.hidden = true;
    threadTitleEl.textContent = nome;
    threadAvatarEl.innerHTML = avatarHtml(nome);
    threadStatusEl.textContent = s.handoff ? 'Atendimento manual' : 'Bot respondendo';
    handoffBtnEl.textContent = s.handoff ? 'Devolver ao bot' : 'Assumir conversa';
    handoffBtnEl.className = 'btn' + (s.handoff ? ' contorno' : '');
    composeFormEl.hidden = !s.handoff;
    avisoBotEl.hidden = !!s.handoff;
    operatorAlertEl.hidden = !s.needs_operator;
    if (s.needs_operator) operatorTextEl.textContent = handoffMotivoTexto(s.handoff_by);
  }

  /* ---------- mensagens ---------- */
  function bolha(m) {
    var kind = m.role === 'assistant' ? 'assistant' : (m.role === 'operator' ? 'operator' : 'user');
    var falhou = m.delivery_status === 'failed';
    var p = partes(m.timestamp_fmt);
    var autor = kind === 'user' ? '' : '<span class="autor">' + (kind === 'operator' ? 'Você' : 'Bot') + '</span>';
    var nota = falhou ? '<div class="falha" title="' + escapeHtml(m.delivery_error || '') + '">' + icone('alerta') + '<span>Não entregue' + (m.delivery_error ? ' — ' + escapeHtml(m.delivery_error) : '') + '</span></div>' : '';
    return '<div class="linha ' + kind + '"><div class="msg ' + kind + (falhou ? ' falhou' : '') + '">' + autor +
      '<p class="texto">' + escapeHtml(m.content) + '</p><div class="msg-rodape"><time>' + escapeHtml(p ? p.hora : (m.timestamp_fmt || '')) + '</time></div>' + nota + '</div></div>';
  }

  async function renderThread(id, opts) {
    var silent = opts && opts.silent;
    var resp = await fetch('/painel/api/messages/' + encodeURIComponent(id));
    if (!resp.ok) return;
    var messages = await resp.json();
    var assinatura = JSON.stringify(messages);
    if (silent && assinatura === lastMessagesSignature) return;
    var noFim = threadAreaEl.scrollHeight - threadAreaEl.scrollTop - threadAreaEl.clientHeight < 80;
    var primeira = lastMessagesSignature === null;
    lastMessagesSignature = assinatura;
    var diaAnterior = null;
    threadAreaEl.innerHTML = messages.map(function (m) {
      var p = partes(m.timestamp_fmt), sep = '';
      if (p && p.dia !== diaAnterior) { sep = '<div class="dia">' + rotuloDia(p) + '</div>'; diaAnterior = p.dia; }
      return sep + bolha(m);
    }).join('');
    if (primeira || noFim) threadAreaEl.scrollTop = threadAreaEl.scrollHeight;   // quem subiu pra reler não é puxado pro fim a cada 5 s
    gravarSeen(id, messages.length);
  }

  async function openThread(id, count) {
    activeSessionId = id; lastMessagesSignature = null;
    gravarSeen(id, count || 0);
    threadAreaEl.hidden = false;
    updateHeader(id);
    await renderThread(id);
    lastListSignature = null;
    await loadSessions();
    var estreito = window.matchMedia('(max-width: 768px)').matches;
    document.body.classList.add('thread-open');
    if (estreito) {
      if (!(history.state && history.state.thread)) history.pushState({ thread: true }, '');   // o botão "voltar" do celular volta pra lista
      threadTitleEl.focus();
    }
  }

  function closeThread() {
    activeSessionId = null; lastMessagesSignature = null; lastListSignature = null;
    threadHeaderEl.hidden = true; operatorAlertEl.hidden = true; composeFormEl.hidden = true; avisoBotEl.hidden = true;
    threadAreaEl.hidden = true; threadAreaEl.innerHTML = ''; vazioEl.hidden = false;
    document.body.classList.remove('thread-open');
  }

  function voltarParaLista() {
    if (history.state && history.state.thread) history.back(); else document.body.classList.remove('thread-open');
    var ativa = convListEl.querySelector('[aria-current="true"]'); if (ativa) ativa.focus();
  }
  backBtnEl.addEventListener('click', voltarParaLista);
  window.addEventListener('popstate', function () { document.body.classList.remove('thread-open'); });

  /* ---------- ações ---------- */
  handoffBtnEl.addEventListener('click', async function () {
    if (!activeSessionId) return;
    var s = sessionsById[activeSessionId];
    var next = !(s && s.handoff);
    handoffBtnEl.disabled = true;
    try {
      var resp = await fetch('/painel/api/sessions/' + encodeURIComponent(activeSessionId) + '/handoff', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ active: next })
      });
      if (sessaoExpirada(resp)) return;
      if (!resp.ok) mostrarAviso('Não foi possível mudar o atendimento. Tente de novo.');
      await loadSessions();
      if (next && !composeFormEl.hidden) composeInputEl.focus();
    } finally { handoffBtnEl.disabled = false; }
  });

  resolveBtnEl.addEventListener('click', async function () {
    if (!activeSessionId) return;
    resolveBtnEl.disabled = true;
    try {
      var resp = await fetch('/painel/api/sessions/' + encodeURIComponent(activeSessionId) + '/resolve', { method: 'POST' });
      if (sessaoExpirada(resp)) return;
      if (!resp.ok) mostrarAviso('Não foi possível marcar como resolvido. Tente de novo.');
      await loadSessions();
    } finally { resolveBtnEl.disabled = false; }
  });

  deleteBtnEl.addEventListener('click', function () {
    if (!activeSessionId) return;
    var s = sessionsById[activeSessionId];
    dlgTextoEl.textContent = 'A conversa com "' + (s ? nomeExibido(s) : activeSessionId) + '" sai só da lista daqui. O bot continua lembrando desse contato e, se ele escrever de novo, a conversa volta a aparecer.';
    dlgEl.showModal();
  });
  dlgEl.addEventListener('close', async function () {
    if (dlgEl.returnValue !== 'ok' || !activeSessionId) return;
    deleteBtnEl.disabled = true;
    try {
      var resp = await fetch('/painel/api/sessions/' + encodeURIComponent(activeSessionId), { method: 'DELETE' });
      if (sessaoExpirada(resp)) return;
      if (!resp.ok) { mostrarAviso('Não foi possível excluir a conversa.'); return; }
      closeThread();
      await loadSessions();
    } finally { deleteBtnEl.disabled = false; }
  });

  /* ---------- envio ---------- */
  function clearPendingImage() {
    pendingImageFile = null; imageInputEl.value = ''; previaEl.hidden = true;
    if (previaImgEl.src) URL.revokeObjectURL(previaImgEl.src);
    previaImgEl.removeAttribute('src');
  }
  attachBtnEl.addEventListener('click', function () { imageInputEl.click(); });
  imageInputEl.addEventListener('change', function () {
    var file = imageInputEl.files[0];
    if (!file) return;
    if (!file.type.startsWith('image/')) { mostrarAviso('Só é possível anexar imagens.'); clearPendingImage(); return; }
    if (file.size > 5 * 1024 * 1024) { mostrarAviso('Imagem maior que 5 MB, o limite da API do WhatsApp.'); clearPendingImage(); return; }
    pendingImageFile = file; previaImgEl.src = URL.createObjectURL(file); previaNomeEl.textContent = file.name; previaEl.hidden = false;
  });
  previaRemoverEl.addEventListener('click', clearPendingImage);

  composeFormEl.addEventListener('submit', async function (ev) {
    ev.preventDefault();
    if (!activeSessionId) return;
    var content = composeInputEl.value.trim();
    if (!content && !pendingImageFile) return;
    var submitBtn = composeFormEl.querySelector('button[type=submit]');
    composeInputEl.disabled = true; submitBtn.disabled = true; attachBtnEl.disabled = true;
    try {
      var resp;
      if (pendingImageFile) {
        var fd = new FormData(); fd.append('file', pendingImageFile); fd.append('caption', content);
        resp = await fetch('/painel/api/messages/' + encodeURIComponent(activeSessionId) + '/send-image', { method: 'POST', body: fd });
      } else {
        resp = await fetch('/painel/api/messages/' + encodeURIComponent(activeSessionId) + '/send', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content: content })
        });
      }
      if (sessaoExpirada(resp)) return;
      if (!resp.ok) {
        var err = await resp.json().catch(function () { return {}; });
        mostrarAviso(err.detail || 'Não foi possível enviar a mensagem.');
        return;
      }
      composeInputEl.value = ''; clearPendingImage(); lastMessagesSignature = null;
      await renderThread(activeSessionId); await loadSessions();
    } finally {
      composeInputEl.disabled = false; submitBtn.disabled = false; attachBtnEl.disabled = false; composeInputEl.focus();
    }
  });

  /* ---------- uso do plano ---------- */
  async function loadUsage() {
    var resp = await fetch('/painel/api/usage');
    if (!resp.ok) return;
    var u = await resp.json();
    var barEl = $('usage-bar');
    if (!u.plano || !u.usage_limite) { barEl.hidden = true; return; }
    barEl.hidden = false;
    var pct = Math.min(100, Math.round((u.usage_pct || 0) * 100));
    $('usage-text').textContent = u.usage_current_month + ' de ' + u.usage_limite + ' conversas esse mês';
    var fill = $('usage-bar-fill');
    fill.style.width = pct + '%';
    fill.className = u.usage_status === 'over' ? 'over' : u.usage_status === 'warning' ? 'warning' : '';
    $('usage-track').setAttribute('aria-valuenow', String(pct));
  }

  async function poll() { await loadSessions(); if (activeSessionId) await renderThread(activeSessionId, { silent: true }); }

  loadSessions(); loadUsage();
  setInterval(poll, POLL_MS);
})();
