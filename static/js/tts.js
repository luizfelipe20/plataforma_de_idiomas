/*
 * Player de áudio dos trechos.
 *
 * Motor principal: MP3 gerado no servidor (<audio>), que funciona em qualquer
 * navegador. Se o servidor não gerar o áudio (ou estiver desativado), usa a
 * Web Speech API do navegador para aquele trecho.
 *
 * Controles: reproduzir (e seguir para os próximos trechos), pausar/retomar,
 * repetir o trecho, anterior/próximo, parar, velocidade e reprodução de um
 * trecho individual (botões [data-play-chunk]).
 * Os textos vêm de <script type="application/json">, então o modo "somente
 * áudio" funciona mesmo com o texto oculto na interface.
 */
(function () {
  "use strict";

  function readJSON(id) {
    const el = id && document.getElementById(id);
    return el ? JSON.parse(el.textContent) : [];
  }

  function Player(root) {
    this.root = root;
    this.chunks = readJSON(root.getAttribute("data-chunks"));
    this.urls = readJSON(root.getAttribute("data-audio-urls"));
    this.locale = root.getAttribute("data-locale");
    this.index = 0;
    this.playingAll = false;
    this.engine = null;  // "server" | "browser" | null
    this.status = root.querySelector("[data-status]");
    this.rateInput = root.querySelector("[data-rate]");
    this.rateLabel = root.querySelector("[data-rate-label]");
    this.audio = new Audio();
    this.audio.preload = "auto";
    this.speechSupported = "speechSynthesis" in window && "SpeechSynthesisUtterance" in window;
    this.voice = null;
    if (!this.urls.length && !this.speechSupported) {
      this.setStatus("Áudio indisponível: ative TTS_PROVIDER=gtts no servidor ou use Chrome, Edge ou Safari.");
      root.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
      return;
    }
    if (this.speechSupported) {
      this.pickVoice();
      window.speechSynthesis.onvoiceschanged = this.pickVoice.bind(this);
    }
    this.bind();
    this.setStatus("Pronto · trecho 1 de " + this.chunks.length);
  }

  Player.prototype.pickVoice = function () {
    const voices = window.speechSynthesis.getVoices();
    const lang = this.locale.toLowerCase(), base = lang.split("-")[0];
    this.voice = voices.find(function (v) { return v.lang.toLowerCase() === lang; }) ||
      voices.find(function (v) { return v.lang.toLowerCase().indexOf(base) === 0; }) || null;
  };

  Player.prototype.rate = function () {
    return this.rateInput ? parseFloat(this.rateInput.value) : 1;
  };

  Player.prototype.bind = function () {
    const self = this;
    this.root.addEventListener("click", function (ev) {
      const btn = ev.target.closest("[data-action]");
      if (!btn) return;
      const action = btn.getAttribute("data-action");
      if (action === "play") self.playFrom(self.index, true);
      if (action === "pause") self.togglePause();
      if (action === "stop") self.stop();
      if (action === "repeat") self.playFrom(self.index, false);
      if (action === "prev") self.playFrom(Math.max(0, self.index - 1), false);
      if (action === "next") self.playFrom(Math.min(self.chunks.length - 1, self.index + 1), false);
    });
    document.addEventListener("click", function (ev) {
      const btn = ev.target.closest("[data-play-chunk]");
      if (btn) self.playFrom(parseInt(btn.getAttribute("data-play-chunk"), 10), false);
    });
    if (this.rateInput) {
      this.rateInput.addEventListener("input", function () {
        self.rateLabel.textContent = self.rate().toFixed(2) + "×";
        self.rateInput.setAttribute("aria-valuetext", self.rate().toFixed(2).replace(".", ",") + "×");
        self.audio.playbackRate = self.rate();
      });
    }
    this.audio.addEventListener("ended", function () { self.finished(self.index); });
    window.addEventListener("pagehide", function () { self.stop(); });
  };

  Player.prototype.setStatus = function (msg) { if (this.status) this.status.textContent = msg; };

  Player.prototype.highlight = function () {
    const index = this.index;
    document.querySelectorAll("[data-chunk-index]").forEach(function (el) {
      el.classList.toggle("speaking", parseInt(el.getAttribute("data-chunk-index"), 10) === index);
    });
    document.dispatchEvent(new CustomEvent("tts:chunk", { detail: { index: index } }));
  };

  Player.prototype.isPlaying = function () {
    if (this.engine === "server") return !this.audio.paused && !this.audio.ended;
    if (this.engine === "browser") return window.speechSynthesis.speaking && !window.speechSynthesis.paused;
    return false;
  };

  Player.prototype.halt = function () {
    this.audio.pause();
    if (this.speechSupported) window.speechSynthesis.cancel();
  };

  Player.prototype.playFrom = function (index, continueAll) {
    this.halt();
    this.index = index;
    this.playingAll = continueAll;
    this.highlight();
    if (this.urls.length) this.playServer(index);
    else this.playBrowser(index);
  };

  Player.prototype.playServer = function (index) {
    const self = this;
    this.engine = "server";
    this.setStatus("Carregando áudio do trecho " + (index + 1) + "…");
    this.audio.src = this.urls[index];
    this.audio.playbackRate = this.rate();
    this.audio.play().then(function () {
      self.setStatus("Reproduzindo trecho " + (index + 1) + " de " + self.chunks.length);
      self.prefetch(index + 1);
    }).catch(function (err) {
      if (self.index !== index || (err && err.name === "AbortError")) return;
      if (self.speechSupported) {
        self.setStatus("Áudio do servidor indisponível; usando a voz do navegador.");
        self.playBrowser(index);
      } else {
        self.setStatus("Não foi possível reproduzir o áudio deste trecho.");
      }
    });
  };

  /* Pede ao servidor o áudio do próximo trecho para que ele já esteja pronto (cache). */
  Player.prototype.prefetch = function (index) {
    this.prefetched = this.prefetched || {};
    if (index >= this.urls.length || this.prefetched[index]) return;
    this.prefetched[index] = true;
    fetch(this.urls[index], { credentials: "same-origin" }).catch(function () {});
  };

  Player.prototype.playBrowser = function (index) {
    const self = this;
    this.engine = "browser";
    const utter = new SpeechSynthesisUtterance(this.chunks[index]);
    utter.lang = this.locale;
    if (this.voice) utter.voice = this.voice;
    utter.rate = this.rate();
    utter.onstart = function () { self.setStatus("Reproduzindo trecho " + (index + 1) + " de " + self.chunks.length + " (voz do navegador)"); };
    utter.onend = function () { self.finished(index); };
    utter.onerror = function (e) {
      if (e.error !== "interrupted" && e.error !== "canceled") self.setStatus("Erro no áudio: " + e.error);
    };
    window.speechSynthesis.speak(utter);
  };

  Player.prototype.finished = function (index) {
    if (this.index !== index) return;  // substituído por outra reprodução
    if (this.playingAll && index + 1 < this.chunks.length) {
      this.playFrom(index + 1, true);
    } else {
      this.engine = null;
      this.setStatus("Fim da reprodução · trecho " + (index + 1) + " de " + this.chunks.length);
    }
  };

  Player.prototype.togglePause = function () {
    if (this.engine === "server") {
      if (this.audio.paused) { this.audio.play(); this.setStatus("Retomado · trecho " + (this.index + 1)); }
      else { this.audio.pause(); this.setStatus("Pausado · trecho " + (this.index + 1)); }
    } else if (this.engine === "browser") {
      const synth = window.speechSynthesis;
      if (synth.paused) { synth.resume(); this.setStatus("Retomado · trecho " + (this.index + 1)); }
      else if (synth.speaking) { synth.pause(); this.setStatus("Pausado · trecho " + (this.index + 1)); }
    }
  };

  Player.prototype.stop = function () {
    this.playingAll = false;
    this.halt();
    this.engine = null;
    this.setStatus("Parado · trecho " + (this.index + 1) + " de " + this.chunks.length);
  };

  /* Trechos avulsos (perguntas de compreensão auditiva) */
  // Se o MP3 do servidor falhar, mostra o botão que usa a voz do navegador.
  document.querySelectorAll("audio[data-question-audio]").forEach(function (audio) {
    audio.addEventListener("error", function () {
      const fallback = audio.parentElement.querySelector("[data-speak]");
      if (fallback && "speechSynthesis" in window) { audio.classList.add("hidden"); fallback.classList.remove("hidden"); }
    });
  });
  document.addEventListener("click", function (ev) {
    const btn = ev.target.closest("[data-speak]");
    if (!btn || !("speechSynthesis" in window)) return;
    window.speechSynthesis.cancel();
    const utter = new SpeechSynthesisUtterance(readJSON(btn.getAttribute("data-speak")));
    utter.lang = btn.getAttribute("data-locale") || "en-US";
    window.speechSynthesis.speak(utter);
  });

  const players = [];
  document.querySelectorAll("[data-tts-player]").forEach(function (el) {
    el.ttsPlayer = new Player(el);
    players.push(el.ttsPlayer);
  });
  // Usado pelo app.js: áudio tocando conta como atividade de estudo.
  window.LinguaLab = window.LinguaLab || {};
  window.LinguaLab.audioPlaying = function () {
    const clip = Array.prototype.some.call(document.querySelectorAll("audio[data-question-audio]"), function (a) {
      return !a.paused && !a.ended;
    });
    return clip || players.some(function (p) { return p.isPlaying && p.isPlaying(); });
  };
})();
