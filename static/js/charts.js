/*
 * Gráficos SVG leves, sem dependências.
 *
 * Uso: <div class="chart" data-chart="line|bar" data-source="id-do-json"
 *           data-series='[{"key":"avg_score","label":"Aproveitamento","color":"--series-1"}]'
 *           data-max="100" data-unit="%"></div>
 * O JSON (gerado com |json_script) deve conter "labels" e as chaves das séries.
 *
 * O desenho usa a largura real do contêiner (em px), então o texto dos eixos
 * mantém o tamanho legível em qualquer tela; o gráfico é redesenhado quando o
 * contêiner muda de tamanho.
 */
(function () {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const FONT = 12;

  function el(name, attrs, parent) {
    const node = document.createElementNS(NS, name);
    Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    if (parent) parent.appendChild(node);
    return node;
  }
  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#4f46e5";
  }
  function niceMax(v) {
    if (v <= 0) return 1;
    const mag = Math.pow(10, Math.floor(Math.log10(v)));
    const n = v / mag;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * mag;
  }
  function fmt(v, unit) {
    const n = Math.round(v * 10) / 10;
    return n.toLocaleString("pt-BR") + (unit || "");
  }

  function draw(container) {
    const data = JSON.parse(document.getElementById(container.getAttribute("data-source")).textContent);
    const series = JSON.parse(container.getAttribute("data-series"));
    const type = container.getAttribute("data-chart");
    const unit = container.getAttribute("data-unit") || "";
    const labels = data.labels || [];
    container.innerHTML = "";

    if (!labels.length) {
      const empty = document.createElement("div");
      empty.className = "chart-empty";
      empty.textContent = container.getAttribute("data-empty") || "Sem dados no período.";
      container.appendChild(empty);
      return;
    }

    let max = parseFloat(container.getAttribute("data-max"));
    if (!max) {
      const all = [];
      series.forEach(function (s) { all.push.apply(all, data[s.key] || []); });
      max = niceMax(Math.max.apply(null, all.concat([0])));
    }

    const W = Math.max(240, Math.round(container.clientWidth || 640));
    const H = Math.round(Math.min(280, Math.max(200, W * 0.5)));
    const yLabelWidth = Math.max.apply(null, [0, 1, 2, 3, 4].map(function (t) { return fmt((max / 4) * t, unit).length; })) * FONT * 0.6;
    const PAD = { top: 12, right: 10, bottom: FONT + 18, left: Math.ceil(yLabelWidth) + 10 };
    const innerW = W - PAD.left - PAD.right, innerH = H - PAD.top - PAD.bottom;
    const svg = el("svg", { viewBox: "0 0 " + W + " " + H, width: W, height: H, role: "img",
      "aria-label": container.getAttribute("aria-label") || "" }, container);
    const y = function (v) { return PAD.top + innerH - (v / max) * innerH; };
    const step = innerW / labels.length;
    const xCenter = function (i) { return PAD.left + step * i + step / 2; };

    // Grade e eixo Y (recessivos)
    const grid = el("g", { class: "grid" }, svg), axis = el("g", { class: "axis" }, svg);
    const ticks = innerH < 150 ? 2 : 4;
    for (let t = 0; t <= ticks; t++) {
      const v = (max / ticks) * t, yy = y(v);
      el("line", { x1: PAD.left, x2: W - PAD.right, y1: yy, y2: yy }, grid);
      el("text", { x: PAD.left - 6, y: yy + 4, "text-anchor": "end" }, axis).textContent = fmt(v, unit);
    }
    // Rótulos do eixo X: quantos couberem sem se sobrepor
    const labelWidth = Math.max.apply(null, labels.map(function (l) { return String(l).length; })) * FONT * 0.6 + 10;
    const every = Math.max(1, Math.ceil(labels.length / Math.max(1, Math.floor(innerW / labelWidth))));
    labels.forEach(function (lab, i) {
      if (i % every !== 0) return;
      el("text", { x: xCenter(i), y: H - 8, "text-anchor": "middle" }, axis).textContent = lab;
    });

    const marks = el("g", {}, svg);
    if (type === "bar") {
      const groupW = Math.max(4, Math.min(step * 0.7, 56));
      const gapBetween = series.length > 1 ? 2 : 0;
      const barW = Math.max(2, (groupW - gapBetween * (series.length - 1)) / series.length);
      series.forEach(function (s, si) {
        const color = cssVar(s.color);
        (data[s.key] || []).forEach(function (v, i) {
          const x = xCenter(i) - groupW / 2 + si * (barW + gapBetween);
          const h = Math.max(0, PAD.top + innerH - y(v));
          el("path", { d: roundedTop(x, y(v), barW, h, Math.min(4, barW / 2)), fill: color, class: "bar" }, marks);
        });
      });
    } else {
      series.forEach(function (s) {
        const color = cssVar(s.color);
        const values = data[s.key] || [];
        const d = values.map(function (v, i) { return (i ? "L" : "M") + xCenter(i) + "," + y(v); }).join(" ");
        el("path", { d: d, class: "line", stroke: color }, marks);
        const radius = step < 12 ? 2.5 : 4.5;
        values.forEach(function (v, i) {
          el("circle", { cx: xCenter(i), cy: y(v), r: radius, fill: color, class: "dot" }, marks);
        });
      });
    }

    // Camada de hover/toque: alvo por coluna, maior que a marca
    const tooltip = document.createElement("div");
    tooltip.className = "chart-tooltip hidden";
    tooltip.setAttribute("role", "status");
    container.appendChild(tooltip);
    const guide = el("line", { y1: PAD.top, y2: PAD.top + innerH, stroke: cssVar("--muted"), "stroke-dasharray": "3 3", visibility: "hidden" }, svg);
    function show(i) {
      guide.setAttribute("x1", xCenter(i)); guide.setAttribute("x2", xCenter(i));
      guide.setAttribute("visibility", type === "bar" ? "hidden" : "visible");
      const extra = data.tooltip_extra ? " · " + data.tooltip_extra[i] : "";
      tooltip.innerHTML = "<strong>" + labels[i] + "</strong>" + extra + "<br>" + series.map(function (s) {
        return s.label + ": " + fmt((data[s.key] || [])[i] || 0, s.unit !== undefined ? s.unit : unit);
      }).join("<br>");
      tooltip.classList.remove("hidden");
      const top = Math.min.apply(null, series.map(function (s) { return y((data[s.key] || [])[i] || 0); }));
      // Mantém o tooltip dentro do gráfico nas bordas
      const half = tooltip.offsetWidth / 2;
      tooltip.style.left = Math.min(Math.max(xCenter(i), half), W - half) + "px";
      tooltip.style.top = Math.max(top, tooltip.offsetHeight + 4) + "px";
    }
    function hide() { tooltip.classList.add("hidden"); guide.setAttribute("visibility", "hidden"); }
    labels.forEach(function (lab, i) {
      const hit = el("rect", { x: PAD.left + step * i, y: PAD.top, width: step, height: innerH, fill: "transparent" }, svg);
      hit.addEventListener("mouseenter", function () { show(i); });
      hit.addEventListener("mouseleave", hide);
      hit.addEventListener("touchstart", function () { show(i); }, { passive: true });
    });
    svg.addEventListener("touchend", function () { setTimeout(hide, 1800); }, { passive: true });
  }

  function roundedTop(x, yTop, w, h, r) {
    if (h <= 0) return "";
    r = Math.min(r, h);
    return "M" + x + "," + (yTop + h) + " V" + (yTop + r) + " Q" + x + "," + yTop + " " + (x + r) + "," + yTop +
      " H" + (x + w - r) + " Q" + (x + w) + "," + yTop + " " + (x + w) + "," + (yTop + r) + " V" + (yTop + h) + " Z";
  }

  const charts = Array.prototype.slice.call(document.querySelectorAll("[data-chart]"));
  function drawAll() { charts.forEach(draw); }
  drawAll();

  // Redesenha quando a largura do contêiner muda (rotação, redimensionamento, menu).
  if ("ResizeObserver" in window) {
    const widths = new WeakMap();
    let pending = null;
    const ro = new ResizeObserver(function (entries) {
      const changed = entries.filter(function (e) {
        const w = Math.round(e.contentRect.width);
        if (widths.get(e.target) === w) return false;
        widths.set(e.target, w);
        return true;
      });
      if (!changed.length) return;
      clearTimeout(pending);
      pending = setTimeout(function () { changed.forEach(function (e) { draw(e.target); }); }, 120);
    });
    charts.forEach(function (c) { widths.set(c, Math.round(c.clientWidth)); ro.observe(c); });
  } else {
    window.addEventListener("resize", drawAll);
  }
  if (window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", drawAll);
  }
})();
