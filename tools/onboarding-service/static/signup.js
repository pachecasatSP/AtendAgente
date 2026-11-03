/* Página inicial do cadastro (/signup): Embedded Signup da Meta + convite de teste escondido.
   app_id e config_id vêm de data-* no <body> (o servidor os injeta). Rotas: POST /api/signup/callback e POST /api/easter-egg/free-token. */
(function () {
  'use strict';
  var dados = document.body.dataset;
  var $ = function (id) { return document.getElementById(id); };

  window.fbAsyncInit = function () {
    window.FB.init({ appId: dados.appId, autoLogAppEvents: true, xfbml: true, version: 'v21.0' });
  };

  // Atalho escondido: digitar "duda" em qualquer lugar da página abre o painel de convite de teste. A defesa real é o PIN, checado no backend.
  (function () {
    var painel = $('egg-panel'), pin = $('egg-pin'), nota = $('egg-nota'), erro = $('egg-erro'), resultado = $('egg-resultado'), link = $('egg-link'), buffer = '';
    window.addEventListener('keydown', function (e) {
      if (!painel.hidden || e.key.length !== 1) return;
      buffer = (buffer + e.key.toLowerCase()).slice(-4);
      if (buffer === 'duda') { painel.hidden = false; pin.focus(); }
    });
    function fechar() { painel.hidden = true; pin.value = ''; erro.hidden = true; resultado.hidden = true; }
    $('egg-close').addEventListener('click', fechar);
    painel.addEventListener('keydown', function (e) { if (e.key === 'Escape') fechar(); });
    $('egg-gerar').addEventListener('click', function (e) {
      var btn = e.currentTarget;
      erro.hidden = true; resultado.hidden = true; btn.disabled = true;
      fetch('/api/easter-egg/free-token', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pin: pin.value, nota: nota.value }) })
        .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { if (!r.ok) throw new Error(d.detail || 'falhou'); return d; }); })
        .then(function (d) { link.value = d.link; resultado.hidden = false; link.select(); })
        .catch(function (err) { erro.textContent = err.message || 'Não consegui gerar o convite.'; erro.hidden = false; })
        .finally(function () { btn.disabled = false; });
    });
  })();

  var wabaId = null, phoneNumberId = null;
  window.addEventListener('message', function (event) {
    if (!event.origin || !event.origin.endsWith('facebook.com')) return;
    try {
      var d = JSON.parse(event.data);
      if (d.type === 'WA_EMBEDDED_SIGNUP' && d.event === 'FINISH') { wabaId = d.data.waba_id; phoneNumberId = d.data.phone_number_id; }
    } catch (e) { /* mensagens que não são JSON do embedded signup, ignora */ }
  });

  var btn = $('signup-btn'), status = $('status');
  function mostrar(texto, falha) { status.textContent = texto; status.className = falha ? 'erro-texto' : ''; }

  btn.addEventListener('click', function () {
    if (!window.FB) { mostrar('O login da Meta ainda está carregando. Tenta de novo em alguns segundos.', true); return; }   // antes: erro de JS se clicasse cedo
    mostrar('');
    btn.disabled = true;
    window.FB.login(function (response) {
      if (response.authResponse && response.authResponse.code) {
        mostrar('Conectando…');
        var params = new URLSearchParams(window.location.search);
        fetch('/api/signup/callback', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code: response.authResponse.code, waba_id: wabaId, phone_number_id: phoneNumberId, invite: params.get('invite') || '', vendedor: params.get('vendedor') || '' })
        })
          .then(function (r) { if (!r.ok) throw new Error('callback falhou'); return r.json(); })
          .then(function (d) { window.location.href = d.next; })
          .catch(function () { mostrar('Erro ao conectar — tente novamente.', true); btn.disabled = false; });
      } else {
        mostrar('Conexão cancelada.', true);
        btn.disabled = false;
      }
    }, { config_id: dados.configId, response_type: 'code', override_default_response_type: true });
  });
})();
