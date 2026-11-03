/* Comportamento comum das telas de acesso. Sem JS tudo funciona igual: o botão de mostrar senha nasce `hidden`. */
(function () {
  document.querySelectorAll('[data-ver-senha]').forEach(function (btn) {
    var campo = document.getElementById(btn.getAttribute('data-ver-senha'));
    if (!campo) return;
    btn.hidden = false;
    btn.addEventListener('click', function () {
      var mostrar = campo.type === 'password';
      campo.type = mostrar ? 'text' : 'password';
      btn.textContent = mostrar ? 'Ocultar' : 'Mostrar';
      btn.setAttribute('aria-pressed', mostrar ? 'true' : 'false');
    });
  });

  document.querySelectorAll('input[data-confirma]').forEach(function (conf) {
    var base = document.getElementById(conf.getAttribute('data-confirma'));
    if (!base) return;
    function checar() { conf.setCustomValidity(conf.value && conf.value !== base.value ? 'As senhas não são iguais.' : ''); }
    conf.addEventListener('input', checar);
    base.addEventListener('input', checar);
  });

  document.querySelectorAll('form[data-envio]').forEach(function (form) {
    var botao = form.querySelector('button[type=submit]');
    if (!botao) return;
    var texto = botao.textContent;
    form.addEventListener('submit', function () { botao.disabled = true; botao.textContent = form.getAttribute('data-envio'); });
    window.addEventListener('pageshow', function () { botao.disabled = false; botao.textContent = texto; });
  });
})();
