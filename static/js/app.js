/* LinguaLab — comportamento global: CSRF, cronômetro, heartbeat, inatividade e tempo de estudo. */
(function () {
  "use strict";

  function getCookie(name) {
    const match = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
    return match ? decodeURIComponent(match[1]) : null;
  }

  function postJSON(url, data, options) {
    return fetch(url, Object.assign({
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCookie("csrftoken") || "",
        "X-Requested-With": "fetch",
      },
      body: JSON.stringify(data || {}),
    }, options || {})).then(function (resp) {
      if (resp.status === 401) {
        return resp.json().then(function (body) {
          window.location.href = (body && body.login_url) || "/contas/entrar/";
          throw new Error("expired");
        });
      }
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      return resp.json();
    });
  }
  window.LinguaLab = { postJSON: postJSON };

  function pad(n) { return String(n).padStart(2, "0"); }
  function formatHMS(totalSeconds) {
    const s = Math.max(0, Math.floor(totalSeconds));
    return pad(Math.floor(s / 3600)) + ":" + pad(Math.floor((s % 3600) / 60)) + ":" + pad(s % 60);
  }
  window.LinguaLab.formatHMS = formatHMS;

  /* ------------------------------------------------------------------ */
  /* Menu em gaveta (celular/tablet)                                     */
  /* ------------------------------------------------------------------ */
  const menuToggle = document.querySelector("[data-menu-toggle]");
  const sidebar = document.getElementById("sidebar");
  const backdrop = document.querySelector(".nav-backdrop");

  function setMenu(open) {
    if (!menuToggle || !sidebar) return;
    document.body.classList.toggle("nav-open", open);
    menuToggle.setAttribute("aria-expanded", String(open));
    menuToggle.setAttribute("aria-label", open ? "Fechar menu" : "Abrir menu");
    if (backdrop) backdrop.classList.toggle("hidden", !open);
    const main = document.querySelector(".main");
    if (main) main.inert = open;  // o foco do teclado fica dentro do menu aberto
    if (open) {
      const first = sidebar.querySelector(".nav a.active") || sidebar.querySelector("a, button");
      if (first) first.focus();
    }
  }
  document.addEventListener("click", function (ev) {
    if (ev.target.closest("[data-menu-toggle]")) { setMenu(!document.body.classList.contains("nav-open")); return; }
    if (ev.target.closest("[data-menu-close]")) { setMenu(false); menuToggle.focus(); return; }
    if (document.body.classList.contains("nav-open") && ev.target.closest(".sidebar .nav a")) setMenu(false);
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape" && document.body.classList.contains("nav-open")) { setMenu(false); menuToggle.focus(); }
  });
  // Ao passar para o layout de desktop o menu fica fixo: limpa o estado de gaveta aberta.
  if (window.matchMedia) {
    window.matchMedia("(min-width: 64em)").addEventListener("change", function (mq) { if (mq.matches) setMenu(false); });
  }

  /* ------------------------------------------------------------------ */
  /* Painéis de filtro: recolhidos no celular quando não há filtro ativo  */
  /* ------------------------------------------------------------------ */
  const panels = document.querySelectorAll("details[data-collapse-mobile]");
  if (panels.length && window.matchMedia) {
    const wide = window.matchMedia("(min-width: 40em)");
    panels.forEach(function (d) { if (!wide.matches && !d.hasAttribute("data-has-filters")) d.open = false; });
    wide.addEventListener("change", function (mq) { if (mq.matches) panels.forEach(function (d) { d.open = true; }); });
  }

  /* ------------------------------------------------------------------ */
  /* Tabelas empilhadas: cada célula recebe o rótulo da sua coluna       */
  /* ------------------------------------------------------------------ */
  document.querySelectorAll("table.table-stack").forEach(function (table) {
    const labels = Array.prototype.map.call(table.querySelectorAll("thead th"), function (th) {
      return th.getAttribute("data-label") || th.textContent.trim();
    });
    table.querySelectorAll("tbody tr").forEach(function (tr) {
      let col = 0;
      Array.prototype.forEach.call(tr.children, function (td) {
        const span = parseInt(td.getAttribute("colspan") || "1", 10);
        if (span === 1 && labels[col] && !td.hasAttribute("data-label")) td.setAttribute("data-label", labels[col]);
        col += span;
      });
    });
    table.setAttribute("data-stack-ready", "");
  });

  /* Botões que disparam operações demoradas (IA) */
  document.addEventListener("submit", function (ev) {
    const form = ev.target;
    const confirmMsg = form.getAttribute("data-confirm");
    if (confirmMsg && !window.confirm(confirmMsg)) { ev.preventDefault(); return; }
    const btn = ev.submitter && ev.submitter.hasAttribute("data-loading-text") ? ev.submitter : form.querySelector("[data-loading-text]");
    if (btn) {
      btn.classList.add("loading");
      btn.textContent = btn.getAttribute("data-loading-text");
    }
  });

  /* Edição inline de tags */
  document.addEventListener("click", function (ev) {
    const btn = ev.target.closest("[data-edit-toggle]");
    if (!btn) return;
    const row = btn.closest("[data-edit-row]");
    row.querySelectorAll("[data-edit-view], [data-edit-form]").forEach(function (el) { el.classList.toggle("hidden"); });
    const input = row.querySelector("[data-edit-form] input[type=text]");
    if (input && !input.closest(".hidden")) input.focus();
  });

  /* Modo foco: esconde menu, cabeçalhos e ações secundárias; a escolha fica neste navegador */
  const focusButtons = Array.prototype.slice.call(document.querySelectorAll("[data-focus-toggle]"));
  if (focusButtons.length) {
    const setFocusMode = function (on, moveFocus) {
      document.body.classList.toggle("focus-mode", on);
      try { localStorage.setItem("lingualab-focus", on ? "1" : "0"); } catch (e) { /* armazenamento bloqueado */ }
      if (!moveFocus) return;
      const visible = focusButtons.find(function (b) { return b.offsetParent !== null; });
      if (visible) visible.focus();
    };
    let savedFocus = false;
    try { savedFocus = localStorage.getItem("lingualab-focus") === "1"; } catch (e) { /* armazenamento bloqueado */ }
    if (savedFocus) setFocusMode(true, false);
    focusButtons.forEach(function (btn) {
      btn.addEventListener("click", function () {
        setFocusMode(!document.body.classList.contains("focus-mode"), true);
      });
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key !== "Escape" || !document.body.classList.contains("focus-mode")) return;
      const popover = document.querySelector("[data-term-popover]");
      if (popover && !popover.classList.contains("hidden")) return;  // o Esc fecha o popover, não o foco
      setFocusMode(false, true);
    });
  }

  /* Mostrar/ocultar o texto de referência durante o quiz */
  document.addEventListener("click", function (ev) {
    const btn = ev.target.closest("[data-toggle-reference]");
    if (!btn) return;
    const target = document.getElementById(btn.getAttribute("aria-controls"));
    if (!target) return;
    const hidden = target.classList.toggle("hidden");
    btn.setAttribute("aria-expanded", String(!hidden));
    btn.textContent = hidden ? "Mostrar texto" : "Ocultar texto";
    const note = document.querySelector("[data-reference-note]");
    if (note) note.classList.toggle("hidden", !hidden);
  });

  /* Contador de caracteres e trechos */
  document.querySelectorAll("[data-char-count]").forEach(function (el) {
    const target = document.getElementById(el.getAttribute("data-char-count"));
    const max = parseInt(el.getAttribute("data-chunk-size"), 10) || 800;
    function update() {
      const len = target.value.length;
      el.textContent = len + " caracteres · ~" + Math.max(1, Math.ceil(len / max)) + " trecho(s) de até " + max;
    }
    target.addEventListener("input", update);
    update();
  });

  /* ------------------------------------------------------------------ */
  /* Termos para reforçar: destaque e marcação por seleção               */
  /* ------------------------------------------------------------------ */
  const termsEl = document.getElementById("difficult-terms");
  let difficultTerms = termsEl ? JSON.parse(termsEl.textContent) : [];

  function escapeRegExp(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

  // Reescreve o texto do elemento envolvendo os termos marcados em links para o detalhe do termo.
  function highlightTerms(root) {
    const plain = root.textContent;
    if (!difficultTerms.length) { root.textContent = plain; return; }
    const byTerm = {};
    difficultTerms.forEach(function (t) { byTerm[t.term.toLowerCase()] = t; });
    const alternatives = difficultTerms.map(function (t) { return escapeRegExp(t.term); }).join("|");
    // Idiomas sem espaços entre palavras não têm limite de palavra.
    const bounded = !/^(ja|zh|ko)/.test(root.getAttribute("lang") || "");
    const pattern = bounded ? "(?<![\\p{L}\\p{N}])(" + alternatives + ")(?![\\p{L}\\p{N}])" : "(" + alternatives + ")";
    const re = new RegExp(pattern, "giu");
    const frag = document.createDocumentFragment();
    let last = 0;
    plain.replace(re, function (match, _g, offset) {
      if (offset > last) frag.appendChild(document.createTextNode(plain.slice(last, offset)));
      const term = byTerm[match.toLowerCase()] || {};
      const mark = document.createElement(term.url ? "a" : "mark");
      mark.className = "term-mark";
      mark.textContent = match;
      if (term.url) {
        mark.href = term.url;
        mark.draggable = false;  // não atrapalha a seleção de trechos que começam no termo
      }
      mark.title = (term.meaning ? term.meaning + " · " : "") + "Clique para ver o termo";
      frag.appendChild(mark);
      last = offset + match.length;
      return match;
    });
    frag.appendChild(document.createTextNode(plain.slice(last)));
    root.textContent = "";
    root.appendChild(frag);
  }
  window.LinguaLab.highlightTerms = highlightTerms;

  const termSources = function () { return document.querySelectorAll("[data-term-source]"); };
  termSources().forEach(highlightTerms);

  const popover = document.querySelector("[data-term-popover]");
  if (popover) {
    document.body.appendChild(popover);  // posicionado em relação à página
    const markBtn = popover.querySelector("[data-term-mark]");
    const markLabel = markBtn.textContent;
    let pending = null;
    // No toque, a seleção pode sumir antes do clique no botão: não esconde enquanto ele é pressionado.
    let pressing = false;
    popover.addEventListener("pointerdown", function () { pressing = true; });

    function hidePopover() { popover.classList.add("hidden"); pending = null; pressing = false; }

    // Frase em volta da seleção, usada como contexto do termo.
    function sentenceAround(plain, start, end) {
      let from = start, to = end;
      while (from > 0 && !/[.!?。！？\n]/.test(plain[from - 1])) from--;
      while (to < plain.length && !/[.!?。！？\n]/.test(plain[to])) to++;
      return plain.slice(from, Math.min(plain.length, to + 1)).trim();
    }

    function readSelection() {
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
      const range = sel.getRangeAt(0);
      const node = range.commonAncestorContainer;
      const root = (node.nodeType === 1 ? node : node.parentElement).closest("[data-term-source]");
      const term = sel.toString().replace(/\s+/g, " ").trim();
      if (!root || !term || term.length > 200) return null;
      const before = document.createRange();
      before.setStart(root, 0);
      before.setEnd(range.startContainer, range.startOffset);
      const start = before.toString().length;
      return {
        root: root,
        rect: range.getBoundingClientRect(),
        data: {
          text_id: root.getAttribute("data-term-source"),
          term: term,
          context: sentenceAround(root.textContent, start, start + sel.toString().length),
        },
      };
    }

    let selTimer = null;
    document.addEventListener("selectionchange", function () {
      clearTimeout(selTimer);
      selTimer = setTimeout(function () {
        const found = readSelection();
        if (!found) { if (!pressing) hidePopover(); return; }
        pending = found;
        markBtn.disabled = false;
        markBtn.textContent = markLabel;
        popover.classList.remove("hidden");
        // Abaixo da seleção: no celular, o menu nativo de copiar fica acima dela.
        const width = popover.offsetWidth;
        const left = Math.min(Math.max(8, found.rect.left + found.rect.width / 2 - width / 2), window.innerWidth - width - 8);
        popover.style.left = (left + window.scrollX) + "px";
        popover.style.top = (found.rect.bottom + window.scrollY + 8) + "px";
      }, 200);
    });

    // Mantém a seleção ao tocar no botão.
    markBtn.addEventListener("mousedown", function (ev) { ev.preventDefault(); });
    markBtn.addEventListener("click", function () {
      if (!pending) return;
      const data = pending.data;
      pressing = false;
      markBtn.disabled = true;
      postJSON(popover.getAttribute("data-term-url"), data).then(function (term) {
        if (!difficultTerms.some(function (t) { return t.term.toLowerCase() === term.term.toLowerCase(); })) {
          difficultTerms.push({ term: term.term, meaning: term.meaning, url: term.url });
          difficultTerms.sort(function (a, b) { return b.term.length - a.term.length; });
        }
        termSources().forEach(highlightTerms);
        window.getSelection().removeAllRanges();
        markBtn.textContent = term.created ? "✓ Adicionado à lista" : "✓ Marcado " + term.times_marked + "×";
        setTimeout(hidePopover, 1200);
      }).catch(function () {
        markBtn.disabled = false;
        markBtn.textContent = "Erro — tentar de novo";
      });
    });
  }

  /* ------------------------------------------------------------------ */
  /* Cronômetro e sessão                                                 */
  /* ------------------------------------------------------------------ */
  const cfgEl = document.getElementById("timer-config");
  if (!cfgEl) return;
  const cfg = JSON.parse(cfgEl.textContent);
  const timerEl = document.querySelector("[data-timer]");
  const timerValue = timerEl && timerEl.querySelector("[data-timer-value]");

  // Diferença entre o relógio do servidor e o do navegador: o servidor é a referência.
  let skew = cfg.server_now_ms - Date.now();
  let startedAt = cfg.started_at_ms;
  let serverLastActivity = cfg.last_activity_ms;
  let lastInteraction = Date.now();
  let interactedSinceBeat = false;
  let warningOpen = false;
  let expiryChecked = false;

  function serverNow() { return Date.now() + skew; }

  function markActivity() {
    lastInteraction = Date.now();
    interactedSinceBeat = true;
    if (warningOpen) closeWarning();
  }
  ["mousedown", "keydown", "touchstart", "scroll", "wheel"].forEach(function (evt) {
    window.addEventListener(evt, markActivity, { passive: true, capture: true });
  });

  function mediaPlaying() {
    return !!(window.LinguaLab.audioPlaying && window.LinguaLab.audioPlaying());
  }
  window.LinguaLab.isActive = function () {
    return document.visibilityState === "visible" && (Date.now() - lastInteraction < 120000 || mediaPlaying());
  };

  function render() {
    if (timerValue) timerValue.textContent = formatHMS((serverNow() - startedAt) / 1000);
    if (mediaPlaying()) { lastInteraction = Date.now(); interactedSinceBeat = true; }
    const idleFor = (serverNow() - Math.max(serverLastActivity, lastInteraction + skew)) / 1000;
    if (timerEl) timerEl.classList.toggle("idle", idleFor > 120);
    const remaining = cfg.inactivity_timeout - idleFor;
    if (remaining <= cfg.inactivity_warning && !warningOpen) openWarning();
    if (warningOpen) {
      const counter = document.querySelector("[data-idle-countdown]");
      if (counter) counter.textContent = formatHMS(Math.max(0, remaining));
      if (remaining <= 0 && !expiryChecked) { expiryChecked = true; beat(false); }  // o servidor decide
    }
  }

  function beat(forceActive) {
    const active = forceActive || interactedSinceBeat;
    interactedSinceBeat = false;
    return postJSON(cfg.heartbeat_url, { active: active }).then(function (state) {
      skew = state.server_now_ms - Date.now();
      startedAt = state.started_at_ms;
      serverLastActivity = state.last_activity_ms;
    }).catch(function () { /* rede instável: tenta no próximo ciclo */ });
  }

  const modal = document.getElementById("idle-modal");
  function openWarning() {
    if (!modal) return;
    warningOpen = true;
    modal.classList.remove("hidden");
  }
  function closeWarning() {
    if (!modal) return;
    warningOpen = false;
    expiryChecked = false;
    modal.classList.add("hidden");
  }
  if (modal) {
    modal.querySelector("[data-idle-continue]").addEventListener("click", function () {
      markActivity();
      beat(true);
    });
  }

  render();
  setInterval(render, 1000);
  setInterval(function () { beat(false); }, cfg.heartbeat * 1000);
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") beat(false);
  });

  /* ------------------------------------------------------------------ */
  /* Tempo de estudo (leitura, áudio, quiz)                              */
  /* ------------------------------------------------------------------ */
  const studyEl = document.querySelector("[data-study-activity]");
  if (!studyEl) return;
  let beatUrl = null;
  const studyTimer = document.querySelector("[data-study-timer]");
  let creditedSeconds = 0;

  postJSON(cfg.study_start_url, {
    activity: studyEl.getAttribute("data-study-activity"),
    text_id: studyEl.getAttribute("data-study-text") || null,
  }).then(function (data) { beatUrl = data.beat_url; }).catch(function () {});

  function studyBeat(end) {
    if (!beatUrl) return;
    postJSON(beatUrl, { active: window.LinguaLab.isActive(), end: !!end }, end ? { keepalive: true } : undefined)
      .then(function (data) {
        creditedSeconds = data.duration_seconds;
        if (studyTimer) studyTimer.textContent = formatHMS(creditedSeconds);
      }).catch(function () {});
  }
  setInterval(function () { studyBeat(false); }, cfg.study_heartbeat * 1000);
  window.addEventListener("pagehide", function () { studyBeat(true); });
})();
