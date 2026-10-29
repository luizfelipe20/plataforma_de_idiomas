/* LinguaLab — vídeos: upload em blocos (retomável), situação do processamento e legendas sincronizadas. */
(function () {
  "use strict";

  function getCookie(name) {
    const match = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
    return match ? decodeURIComponent(match[1]) : null;
  }
  function readJSON(id) {
    const el = document.getElementById(id);
    return el ? JSON.parse(el.textContent) : null;
  }
  function formatBytes(n) {
    const units = ["B", "KB", "MB", "GB"];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return n.toFixed(i ? 1 : 0) + " " + units[i];
  }
  function wait(ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); }

  /* ------------------------------------------------------------------ */
  /* Upload                                                              */
  /* ------------------------------------------------------------------ */
  // Duração lida pelo navegador antes do envio (o servidor confirma depois com ffprobe).
  function readDuration(file) {
    return new Promise(function (resolve) {
      const el = document.createElement("video");
      const url = URL.createObjectURL(file);
      const done = function (value) { URL.revokeObjectURL(url); resolve(value); };
      el.preload = "metadata";
      el.onloadedmetadata = function () { done(isFinite(el.duration) ? el.duration : null); };
      el.onerror = function () { done(null); };
      setTimeout(function () { done(null); }, 10000);
      el.src = url;
    });
  }

  const uploadBox = document.querySelector("[data-upload]");
  const config = readJSON("upload-config");
  if (uploadBox && config) {
    const form = uploadBox.querySelector("[data-upload-form]");
    const fileInput = form.querySelector("input[type=file]");
    const submit = form.querySelector("[data-upload-submit]");
    const pauseBtn = uploadBox.querySelector("[data-upload-cancel]");
    const box = uploadBox.querySelector("[data-upload-progress]");
    const bar = uploadBox.querySelector("[data-upload-bar]");
    const percent = uploadBox.querySelector("[data-upload-percent]");
    const detail = uploadBox.querySelector("[data-upload-detail]");
    const errorBox = uploadBox.querySelector("[data-upload-error]");
    const resuming = uploadBox.hasAttribute("data-upload-resume");
    let paused = false, uploading = false;

    function showError(msg) {
      errorBox.textContent = msg;
      errorBox.classList.toggle("hidden", !msg);
    }
    function setProgress(sent, total, rate) {
      const pct = total ? Math.floor((100 * sent) / total) : 0;
      bar.style.width = pct + "%";
      bar.parentElement.setAttribute("aria-valuenow", pct);
      percent.textContent = pct + "%";
      let text = formatBytes(sent) + " de " + formatBytes(total);
      if (rate > 0) {
        const eta = Math.max(0, (total - sent) / rate);
        text += " · " + formatBytes(rate) + "/s · faltam ~" + Math.ceil(eta / 60) + " min";
      }
      detail.textContent = text;
    }

    function sendChunk(url, blob, offset) {
      return fetch(url + "?offset=" + offset, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/octet-stream",
          "X-CSRFToken": getCookie("csrftoken") || "",
          "X-Requested-With": "fetch",
          "X-Chunk-Length": String(blob.size),
        },
        body: blob,
      }).then(function (resp) {
        if (resp.status === 401) { window.location.reload(); throw new Error("expired"); }
        return resp.json().then(function (data) { return { status: resp.status, data: data }; });
      });
    }

    // Envia o arquivo a partir de `offset`; erros de rede são repetidos com espera crescente.
    async function sendFile(file, upload) {
      let offset = upload.received_bytes || 0;
      const started = Date.now(), startOffset = offset;
      while (offset < file.size) {
        if (paused) throw new Error("paused");
        const blob = file.slice(offset, Math.min(file.size, offset + upload.chunk_size));
        let result = null;
        for (let attempt = 0; attempt < 5 && !result; attempt++) {
          try {
            result = await sendChunk(upload.chunk_url, blob, offset);
          } catch (err) {
            if (err.message === "expired") throw err;
            detail.textContent = "Conexão instável, tentando de novo…";
            await wait(1000 * Math.pow(2, attempt));
          }
        }
        if (!result) throw new Error("Não foi possível enviar o arquivo. Verifique a conexão e tente continuar.");
        if (result.status === 409 && typeof result.data.received_bytes === "number" && result.data.status === "uploading") {
          offset = result.data.received_bytes;  // o servidor indica de onde continuar
          continue;
        }
        if (result.status !== 200) throw new Error(result.data.error || "Falha no envio.");
        offset = result.data.received_bytes;
        const elapsed = (Date.now() - started) / 1000;
        setProgress(offset, file.size, elapsed > 1 ? (offset - startOffset) / elapsed : 0);
        if (result.data.status !== "uploading") return result.data;
      }
      return null;
    }

    form.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      if (uploading) return;
      showError("");
      const file = fileInput.files[0];
      if (!file) { showError("Selecione um arquivo de vídeo."); return; }
      const ext = "." + (file.name.split(".").pop() || "").toLowerCase();
      let upload;
      try {
        uploading = true; paused = false;
        submit.disabled = true;
        pauseBtn.classList.remove("hidden");
        box.classList.remove("hidden");
        if (resuming) {
          if (file.name !== config.filename || file.size !== config.size_bytes) {
            throw new Error("Selecione o mesmo arquivo enviado antes: " + config.filename + " (" + formatBytes(config.size_bytes) + ").");
          }
          upload = { chunk_url: config.chunk_url, chunk_size: config.chunk_size, received_bytes: config.received_bytes, detail_url: config.detail_url };
        } else {
          if (config.extensions.indexOf(ext) === -1) throw new Error("Formato não suportado: " + ext);
          if (file.size > config.max_bytes) throw new Error("O arquivo passa do limite de " + formatBytes(config.max_bytes) + ".");
          detail.textContent = "Lendo a duração do vídeo…";
          const duration = await readDuration(file);
          if (duration && duration > config.max_duration + 1) {
            throw new Error("O vídeo tem " + (duration / 3600).toFixed(1) + " h; o limite é " + (config.max_duration / 3600) + " h.");
          }
          const data = new FormData(form);
          const resp = await fetch(config.start_url, {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", "X-CSRFToken": getCookie("csrftoken") || "", "X-Requested-With": "fetch" },
            body: JSON.stringify({
              title: data.get("title"), language: data.get("language"), target_language: data.get("target_language"),
              filename: file.name, size: file.size, duration: duration,
            }),
          });
          const body = await resp.json();
          if (!resp.ok) throw new Error(body.error || "Não foi possível iniciar o envio.");
          upload = body;
        }
        setProgress(upload.received_bytes || 0, file.size, 0);
        await sendFile(file, upload);
        detail.textContent = "Envio concluído. Abrindo o vídeo…";
        window.location.href = upload.detail_url;
      } catch (err) {
        if (err.message === "paused") {
          detail.textContent = "Envio pausado. Clique em “" + submit.textContent.trim() + "” para continuar.";
          if (upload && !resuming) {
            // Retomada pela página do vídeo (o arquivo é escolhido de novo lá).
            window.location.href = upload.detail_url;
          }
        } else if (err.message !== "expired") {
          showError(err.message);
        }
      } finally {
        uploading = false;
        submit.disabled = false;
        pauseBtn.classList.add("hidden");
      }
    });
    pauseBtn.addEventListener("click", function () { paused = true; });
    window.addEventListener("beforeunload", function (ev) {
      if (uploading) { ev.preventDefault(); ev.returnValue = ""; }
    });
  }

  /* ------------------------------------------------------------------ */
  /* Situação do processamento (consulta periódica)                      */
  /* ------------------------------------------------------------------ */
  const watched = document.querySelectorAll("[data-media-watch]");
  const statusUrl = watched.length ? watched[0].getAttribute("data-status-url") : null;
  if (watched.length && statusUrl) {
    const rows = {};
    watched.forEach(function (row) { rows[row.getAttribute("data-media-row")] = row; });
    const initial = {};
    Object.keys(rows).forEach(function (id) {
      const label = rows[id].querySelector("[data-status-label]");
      initial[id] = label ? label.textContent.trim() : "";
    });
    const poll = function () {
      if (document.visibilityState !== "visible") return;
      fetch(statusUrl + "?ids=" + Object.keys(rows).join(","), { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
        .then(function (resp) { return resp.ok ? resp.json() : { items: [] }; })
        .then(function (data) {
          data.items.forEach(function (item) {
            const row = rows[item.id];
            if (!row) return;
            // Mudou de situação (ex.: processando → pronto): recarrega para mostrar os resultados.
            if (item.status_label !== initial[item.id] && item.status !== "processing" && item.status !== "queued") {
              window.location.reload();
              return;
            }
            const barEl = row.querySelector("[data-status-bar]");
            if (barEl) barEl.style.width = item.progress + "%";
            const pctEl = row.querySelector("[data-status-percent]");
            if (pctEl) pctEl.textContent = item.progress + "%";
            const detailEl = row.querySelector("[data-status-detail]");
            if (detailEl) detailEl.textContent = item.status === "uploading" ? item.upload_percent + "% enviado" : item.current_step_label;
            const labelEl = row.querySelector("[data-status-label]");
            if (labelEl) labelEl.textContent = item.status_label;
          });
        })
        .catch(function () {});
    };
    setInterval(poll, 4000);
  }

  /* ------------------------------------------------------------------ */
  /* Legendas sincronizadas com o áudio do segmento                      */
  /* ------------------------------------------------------------------ */
  const audio = document.querySelector("[data-segment-audio]");
  const cues = readJSON("cue-data");
  if (audio && cues) {
    const items = Array.prototype.slice.call(document.querySelectorAll("[data-cue]"));
    const list = document.querySelector("[data-cue-list]");
    let active = -1;
    audio.addEventListener("timeupdate", function () {
      const t = audio.currentTime;
      let idx = -1;
      for (let i = 0; i < cues.length; i++) { if (t >= cues[i].start && t < cues[i].end + 0.05) { idx = i; break; } }
      if (idx === active) return;
      if (active >= 0 && items[active]) items[active].firstElementChild.classList.remove("active");
      active = idx;
      if (idx >= 0 && items[idx]) {
        const el = items[idx].firstElementChild;
        el.classList.add("active");
        if (list) {
          const top = el.offsetTop - list.offsetTop;
          if (top < list.scrollTop || top > list.scrollTop + list.clientHeight - el.offsetHeight) list.scrollTop = top - list.clientHeight / 3;
        }
      }
    });
    document.addEventListener("click", function (ev) {
      const seek = ev.target.closest("[data-seek]");
      if (!seek) return;
      const cue = cues[parseInt(seek.getAttribute("data-seek"), 10)];
      if (!cue) return;
      audio.currentTime = cue.start;
      audio.play().catch(function () {});
    });
  }
})();
