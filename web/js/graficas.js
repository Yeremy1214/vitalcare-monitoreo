// Gráficas de tendencias en vivo: las mismas tres de la versión de escritorio (matplotlib),
// dibujadas en un canvas: frecuencia cardíaca, SpO₂ y temperatura, con el rango normal
// sombreado, las caídas / SOS como líneas rojas y los cambios de pulsera como líneas azules.

import { COLOR } from "./ui.js";

const SERIE = { hr: "#0B6BCB", spo2: "#0E8F8F", temp: "#C2570C" };
const TITULOS = { hr: "Frecuencia cardíaca (lpm)", spo2: "SpO₂ (%)", temp: "Temperatura estimada (°C)" };
const MARGEN = { hr: 8, spo2: 1.5, temp: 0.4 };
const FAMILIA = '"Segoe UI", "Inter", "Noto Sans", system-ui, sans-serif';

function bisectLeft(arr, x) {
  let lo = 0, hi = arr.length;
  while (lo < hi) { const m = (lo + hi) >> 1; if (arr[m] < x) lo = m + 1; else hi = m; }
  return lo;
}

/** Marcas "redondas" para un eje, como hace matplotlib: entre 2 y `maximo` marcas. */
function marcasBonitas(lo, hi, maximo = 5) {
  if (!(hi - lo > 0)) return [lo];
  const pot = 10 ** Math.floor(Math.log10((hi - lo) / maximo));
  const pasos = [0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50].map((m) => m * pot);
  const marcas = (paso) => {
    const r = [];
    for (let v = Math.ceil(lo / paso - 1e-9) * paso; v <= hi + 1e-9; v += paso) r.push(Number(v.toFixed(6)));
    return r;
  };
  for (const paso of pasos) {
    const r = marcas(paso);
    if (r.length >= 2 && r.length <= maximo) return r;
  }
  // Ningún paso cae justo: se toma el más grueso que deja al menos dos marcas y se ralea.
  let r = marcas([...pasos].reverse().find((p) => marcas(p).length >= 2) || pasos[0]);
  while (r.length > maximo) r = r.filter((_, i) => i % 2 === 0);
  return r;
}

const PASOS_TIEMPO = [1, 2, 5, 10, 15, 20, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200];
const dos = (n) => String(n).padStart(2, "0");
const horaTexto = (t) => { const d = new Date(t * 1000); return `${dos(d.getHours())}:${dos(d.getMinutes())}:${dos(d.getSeconds())}`; };
const fmtNum = (v) => String(Number(v.toFixed(2)));

export class Graficas {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
  }

  /** sesion: logica.Sesion. umbrales: los del paciente. ventana: segundos o null (toda la sesión). */
  dibujar(sesion, umbrales, ventana, ahoraS) {
    const cv = this.canvas;
    const dpr = window.devicePixelRatio || 1;
    const W = cv.clientWidth, H = cv.clientHeight;
    if (!W || !H) return;
    if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) {
      cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
    }
    const g = this.ctx;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);
    g.fillStyle = COLOR.CARD;
    g.fillRect(0, 0, W, H);

    const t = sesion.t;
    const ahora = t.length ? t[t.length - 1] : ahoraS;
    let idx, xLo, xHi;
    if (ventana) {
      idx = bisectLeft(t, ahora - ventana);
      const inicioVisible = t.length ? Math.max(ahora - ventana, t[0]) : ahora - 60;
      xLo = Math.min(inicioVisible, ahora - 30);
      xHi = ahora + 2;
    } else {
      idx = 0;
      xLo = t.length ? t[0] : ahora - 60;
      xHi = Math.max(ahora + 2, xLo + 30);
    }

    const izq = 44, der = 10, pieX = 22;
    const alto = (H - pieX) / 3;
    const x = (tt) => izq + ((tt - xLo) / (xHi - xLo)) * (W - izq - der);

    // marcas de tiempo (compartidas por las tres gráficas)
    const ancho = xHi - xLo;
    const maxMarcas = Math.max(3, Math.min(7, Math.floor((W - izq - der) / 120)));
    const paso = PASOS_TIEMPO.find((p) => ancho / p <= maxMarcas) || 7200;
    const marcasX = [];
    for (let v = Math.ceil(xLo / paso) * paso; v <= xHi; v += paso) marcasX.push(v);

    ["hr", "spo2", "temp"].forEach((variable, i) => {
      const y0 = i * alto;
      const arr = 26, abj = 8;
      const top = y0 + arr, bottom = y0 + alto - abj;
      const u = umbrales[variable];
      const [lo, hi] = u.normal;
      const vals = sesion[variable].slice(idx).filter((v) => v !== null);
      const minimo = vals.length ? Math.min(lo, ...vals) : lo;
      const maximo = vals.length ? Math.max(hi, ...vals) : hi;
      let techo = maximo + MARGEN[variable];
      if (variable === "spo2") techo = Math.min(techo, 101.5);
      const yLo = minimo - MARGEN[variable], yHi = techo;
      const y = (v) => bottom - ((v - yLo) / (yHi - yLo)) * (bottom - top);

      // títulos
      g.font = `bold 13px ${FAMILIA}`; g.fillStyle = COLOR.TEXTO; g.textBaseline = "alphabetic"; g.textAlign = "left";
      g.fillText(TITULOS[variable], izq, y0 + 17);
      g.font = `11px ${FAMILIA}`; g.fillStyle = COLOR.OK; g.textAlign = "right";
      g.fillText(variable === "spo2" ? `normal ≥ ${fmtNum(lo)}` : `normal ${fmtNum(lo)}–${fmtNum(hi)}`, W - der, y0 + 17);

      // banda normal
      g.fillStyle = "rgba(27,138,90,0.10)";
      const yb1 = y(Math.min(hi, yHi)), yb2 = y(Math.max(lo, yLo));
      g.fillRect(izq, yb1, W - izq - der, yb2 - yb1);

      // rejilla y eje
      g.strokeStyle = "#E3E9EF"; g.lineWidth = 0.8; g.font = `11px ${FAMILIA}`; g.fillStyle = COLOR.MUTED; g.textAlign = "right";
      for (const m of marcasBonitas(yLo, yHi, alto > 120 ? 4 : 3)) {
        g.beginPath(); g.moveTo(izq, y(m)); g.lineTo(W - der, y(m)); g.stroke();
        g.fillText(fmtNum(m), izq - 6, y(m) + 4);
      }
      for (const m of marcasX) { g.beginPath(); g.moveTo(x(m), top); g.lineTo(x(m), bottom); g.stroke(); }
      g.strokeStyle = COLOR.BORDE; g.lineWidth = 1;
      g.beginPath(); g.moveTo(izq, top); g.lineTo(izq, bottom); g.lineTo(W - der, bottom); g.stroke();

      g.save();
      g.beginPath(); g.rect(izq, top - 2, W - izq - der, bottom - top + 4); g.clip();
      // caídas / SOS
      g.setLineDash([5, 4]); g.strokeStyle = COLOR.ALERTA; g.lineWidth = 1.3;
      let bordeEtiqueta = -Infinity, fila = 0;
      for (const [te, nombre] of sesion.eventos) {
        if (te < xLo) continue;
        g.beginPath(); g.moveTo(x(te), top); g.lineTo(x(te), bottom); g.stroke();
        if (variable === "hr") {
          const etq = { FALL: "CAÍDA", SOS: "SOS" }[nombre] || nombre;
          g.font = `bold 11px ${FAMILIA}`; g.textAlign = "right";
          const w = g.measureText(etq).width;
          fila = x(te) - w - 6 < bordeEtiqueta ? (fila + 1) % 3 : 0; // si se encima con la anterior, baja un renglón
          const yy = top + 2 + fila * 15;
          g.fillStyle = "rgba(255,255,255,0.85)"; g.fillRect(x(te) - w - 6, yy, w + 4, 14);
          g.fillStyle = COLOR.ALERTA; g.fillText(etq, x(te) - 4, yy + 11);
          bordeEtiqueta = x(te);
        }
      }
      // cambios de pulsera
      g.setLineDash([2, 3]); g.strokeStyle = COLOR.PRIMARIO;
      for (const [tc, texto] of sesion.cambios) {
        if (tc < xLo) continue;
        g.beginPath(); g.moveTo(x(tc), top); g.lineTo(x(tc), bottom); g.stroke();
        if (variable === "hr") {
          const etq = " PULSERA " + texto.split("→").pop().trim();
          g.font = `bold 11px ${FAMILIA}`; g.textAlign = "left";
          const w = g.measureText(etq).width;
          g.fillStyle = "rgba(255,255,255,0.85)"; g.fillRect(x(tc) + 2, top + 18, w + 4, 14);
          g.fillStyle = COLOR.PRIMARIO; g.fillText(etq, x(tc) + 2, top + 29);
        }
      }
      g.setLineDash([]);
      // serie (se corta donde no hay lectura)
      g.strokeStyle = SERIE[variable]; g.lineWidth = 1.8; g.lineJoin = "round"; g.beginPath();
      let abierto = false, ultimo = null;
      for (let k = idx; k < t.length; k++) {
        const v = sesion[variable][k];
        if (v === null) { abierto = false; continue; }
        if (!abierto) g.moveTo(x(t[k]), y(v)); else g.lineTo(x(t[k]), y(v));
        abierto = true;
      }
      g.stroke();
      if (t.length > idx) {
        const v = sesion[variable][t.length - 1];
        if (v !== null) ultimo = [x(t[t.length - 1]), y(v)];
      }
      if (ultimo) { g.fillStyle = SERIE[variable]; g.beginPath(); g.arc(ultimo[0], ultimo[1], 4.5, 0, Math.PI * 2); g.fill(); }
      g.restore();

      if (variable === "temp") {
        g.font = `11px ${FAMILIA}`; g.fillStyle = COLOR.MUTED; g.textAlign = "center";
        for (const m of marcasX) g.fillText(horaTexto(m), x(m), bottom + 16);
      }
    });
  }
}
