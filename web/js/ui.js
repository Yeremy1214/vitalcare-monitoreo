// Piezas de interfaz compartidas (equivalente a estilo.py): botones, tablas, diálogos
// modales y campos de formulario, para que todas las pantallas se vean como un solo programa.

export const COLOR = {
  BG: "#EEF2F6", CARD: "#FFFFFF", NAVY: "#0F2A43", TEXTO: "#16222E", MUTED: "#5B6B7A", BORDE: "#D5DEE7",
  PRIMARIO: "#0B6BCB", OK: "#1B8A5A", OK_BG: "#E3F4EB", WARN: "#B26A00", WARN_BG: "#FFF1D6",
  ALERTA: "#C62828", ALERTA_BG: "#FDE7E7", GRIS: "#6B7B8A", GRIS_BG: "#E6EBF0",
};

/** Crea un elemento: el("div", {class: "x", onclick: fn}, hijo1, "texto"...). */
export function el(tag, attrs = {}, ...hijos) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(e.style, v);
    else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
    else if (k === "text") e.textContent = v;
    else if (k in e && k !== "list") e[k] = v;
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const h of hijos.flat()) {
    if (h === null || h === undefined || h === false) continue;
    e.append(h instanceof Node ? h : document.createTextNode(String(h)));
  }
  return e;
}

/** Botón con el estilo del programa. tipo: "" (azul), "claro", "ok", "alerta", "warn", "naranja", "verde". */
export function boton(texto, fn, tipo = "", extra = {}) {
  return el("button", { type: "button", class: `btn ${tipo}`.trim(), onclick: fn, ...extra }, texto);
}

export function tarjeta(clase = "", ...hijos) { return el("div", { class: `tarjeta ${clase}`.trim() }, ...hijos); }

export function tituloPanel(texto, sub = "") {
  return el("div", { class: "titulo-panel" }, el("b", {}, texto), sub ? el("span", {}, sub) : null);
}

// ---------------------------------------------------------------- tabla
export class Tabla {
  /** columnas: [[id, título, ancho_px, alineación], ...]. La última columna se estira. */
  constructor(columnas, { alto = 12, alElegir = null, alDoble = null } = {}) {
    this.columnas = columnas;
    this.alElegir = alElegir;
    this.alDoble = alDoble;
    this._sel = null;
    const cols = columnas.map(([, , ancho], i) => el("col", { style: { width: i === columnas.length - 1 ? "auto" : `${ancho}px` } }));
    this.cuerpo = el("tbody");
    this.tabla = el("table", { class: "tabla" }, el("colgroup", {}, cols),
      el("thead", {}, el("tr", {}, columnas.map(([, titulo, , al]) => el("th", { style: { textAlign: al === "center" ? "center" : "left" } }, titulo)))),
      this.cuerpo);
    this.elemento = el("div", { class: "tabla-marco", style: { minHeight: `${Math.round(alto * 27 + 32)}px` } }, this.tabla);
  }

  limpiar() { this.cuerpo.replaceChildren(); }

  agregar(iid, valores, tags = []) {
    const tr = el("tr", { class: tags.join(" ") });
    tr.dataset.iid = String(iid);
    valores.forEach((v, i) => tr.append(el("td", { style: { textAlign: this.columnas[i][3] === "center" ? "center" : "left" } }, v ?? "")));
    tr.addEventListener("click", () => this.seleccionar(iid, true));
    tr.addEventListener("dblclick", () => { this.seleccionar(iid, true); if (this.alDoble) this.alDoble(); });
    if (String(iid) === String(this._sel)) tr.classList.add("sel");
    this.cuerpo.append(tr);
  }

  seleccion() {
    if (this._sel === null) return null;
    const existe = this.cuerpo.querySelector(`tr[data-iid="${CSS.escape(String(this._sel))}"]`);
    if (!existe) return null;
    const n = Number(this._sel);
    return Number.isFinite(n) ? n : this._sel;
  }

  seleccionar(iid, avisar = false) {
    this._sel = iid;
    this.cuerpo.querySelectorAll("tr").forEach((tr) => tr.classList.toggle("sel", tr.dataset.iid === String(iid)));
    const tr = this.cuerpo.querySelector(`tr[data-iid="${CSS.escape(String(iid))}"]`);
    if (tr && !avisar) tr.scrollIntoView({ block: "nearest" });
    if (avisar && this.alElegir) this.alElegir();
  }
}

// ---------------------------------------------------------------- diálogos
const pilaDialogos = [];

export class Dialogo {
  constructor(titulo, { ancho = 560 } = {}) {
    this.resultado = null;
    this.titulo = el("h2", {}, titulo);
    this.sub = el("p", { class: "dlg-sub" });
    this.cuerpo = el("div", { class: "dlg-cuerpo" });
    this.lblError = el("p", { class: "dlg-error", role: "alert" });
    this.pie = el("div", { class: "dlg-pie" });
    this.caja = el("div", { class: "dialogo", role: "dialog", "aria-modal": "true", style: { width: `${ancho + 40}px` } },
      this.titulo, this.sub, this.cuerpo, this.lblError, this.pie);
    this.fondo = el("div", { class: "dlg-fondo" }, this.caja);
    this._teclas = (e) => {
      if (pilaDialogos[pilaDialogos.length - 1] !== this) return;
      if (e.key === "Escape") { e.preventDefault(); this.cerrar(); }
    };
    this.promesa = new Promise((ok) => { this._ok = ok; });
  }

  subtitulo(texto) { this.sub.textContent = texto; }
  error(texto) { this.lblError.textContent = texto; return null; }

  botones(textoOk, alGuardar, textoCancelar = "Cancelar", tipoOk = "") {
    this.pie.append(boton(textoOk, alGuardar, tipoOk), boton(textoCancelar, () => this.cerrar(), "claro"));
  }

  abrir() {
    document.body.append(this.fondo);
    pilaDialogos.push(this);
    document.addEventListener("keydown", this._teclas, true);
    setTimeout(() => {
      const primero = this.caja.querySelector("[autofocus], textarea, input:not([disabled]), select");
      if (primero) primero.focus();
    }, 30);
    return this.promesa;
  }

  cerrar() {
    document.removeEventListener("keydown", this._teclas, true);
    const i = pilaDialogos.indexOf(this);
    if (i >= 0) pilaDialogos.splice(i, 1);
    this.fondo.remove();
    this._ok(this.resultado);
  }
}

export function hayDialogoAbierto() { return pilaDialogos.length > 0; }

/** Equivalente a messagebox.showinfo / showwarning. */
export function mensaje(titulo, texto, tipo = "info") {
  const d = new Dialogo(titulo, { ancho: 460 });
  d.caja.classList.add(`msg-${tipo}`);
  texto.split("\n\n").forEach((p) => d.cuerpo.append(el("p", { class: "msg-texto" }, p)));
  d.pie.append(boton("Aceptar", () => d.cerrar()));
  return d.abrir();
}

/** Equivalente a messagebox.askyesno. Devuelve true o false. */
export function confirmar(titulo, texto) {
  const d = new Dialogo(titulo, { ancho: 460 });
  texto.split("\n\n").forEach((p) => d.cuerpo.append(el("p", { class: "msg-texto" }, p)));
  d.pie.append(boton("Sí", () => { d.resultado = true; d.cerrar(); }), boton("No", () => { d.resultado = false; d.cerrar(); }, "claro"));
  return d.abrir().then((r) => r === true);
}

/** Etiqueta + entrada (o lista) en una grilla de formulario. Devuelve el control. */
export function filaCampo(grilla, etiqueta, valor = "", { ayuda = "", opciones = null, ancho = 30, tipo = "text" } = {}) {
  let control;
  if (opciones) {
    control = el("select", {}, opciones.map((o) => el("option", { value: o }, o)));
    control.value = valor;
  } else {
    control = el("input", { type: tipo, value: valor ?? "", autocomplete: "off", spellcheck: false });
  }
  control.style.width = `${Math.round(ancho * 8.4)}px`;
  grilla.append(el("label", { class: "fc-etq" }, etiqueta), control, el("span", { class: "fc-ayuda" }, ayuda));
  return control;
}

export const grillaFormulario = () => el("div", { class: "fc-grilla" });
