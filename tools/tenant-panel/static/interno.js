/* Aviso e confirmação das telas internas (no lugar de alert() e confirm()).
   avisar('texto')                      -> faixa vermelha embaixo da tela, some em 6 s (role="alert")
   await confirmar({titulo, texto, ok}) -> true/false, num <dialog> (foco em "Cancelar", Esc cancela) */
(function () {
  'use strict';
  var timer = null;

  window.avisar = function (mensagem) {
    var el = document.getElementById('aviso');
    if (!el) {
      el = document.createElement('p');
      el.id = 'aviso'; el.className = 'toast'; el.setAttribute('role', 'alert'); el.hidden = true;
      document.body.appendChild(el);
    }
    el.textContent = mensagem; el.hidden = false;
    clearTimeout(timer); timer = setTimeout(function () { el.hidden = true; }, 6000);
  };

  window.confirmar = function (opcoes) {
    return new Promise(function (resolve) {
      var dlg = document.createElement('dialog');
      dlg.className = 'dialogo';
      dlg.setAttribute('aria-labelledby', 'dlg-conf-titulo');
      var form = document.createElement('form'); form.method = 'dialog';
      var h = document.createElement('h2'); h.id = 'dlg-conf-titulo'; h.textContent = opcoes.titulo || 'Confirmar?';
      var p = document.createElement('p'); p.textContent = opcoes.texto || '';
      var acoes = document.createElement('div'); acoes.className = 'acoes';
      var cancelar = document.createElement('button'); cancelar.value = 'cancelar'; cancelar.className = 'btn contorno'; cancelar.textContent = 'Cancelar'; cancelar.autofocus = true;
      var ok = document.createElement('button'); ok.value = 'ok'; ok.className = 'btn' + (opcoes.perigo ? ' perigo' : ''); ok.textContent = opcoes.ok || 'Confirmar';
      acoes.appendChild(cancelar); acoes.appendChild(ok); form.appendChild(h); form.appendChild(p); form.appendChild(acoes); dlg.appendChild(form);
      dlg.addEventListener('close', function () { var r = dlg.returnValue === 'ok'; dlg.remove(); resolve(r); });
      document.body.appendChild(dlg);
      dlg.showModal();
    });
  };
})();
