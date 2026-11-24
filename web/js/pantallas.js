// Pantallas de gestión: pacientes, pulseras y configuración del centro (pantallas.py).
//
// La pantalla de monitoreo vive en app.js. Aquí está todo lo que rodea al monitoreo:
// quién está ingresado, qué pulsera lleva puesta, qué pasa cuando hay que cambiarla,
// qué signos se toman a mano y con qué umbrales se juzga cada paciente.

import { AMBOS, ESTADO_PULSERA, MOTIVOS_FIN, SEXOS, SIGNOS, SIGNOS_MANUALES, mesesDeEdad, textoEdad } from "./datos.js";
import { g } from "./logica.js";
import { Dialogo, Tabla, boton, confirmar, el, filaCampo, grillaFormulario, mensaje, tarjeta, tituloPanel } from "./ui.js";
import { descargar } from "./almacen.js";
import { filaCSV } from "./registro.js";

export const nombreCompleto = (p) => `${p.nombre} ${p.apellidos}`.trim();
export const textoBateria = (v) => (v === null || v === undefined ? "—" : `${Math.trunc(v)} %`);

export function ubicacion(p) {
  const hab = (p.habitacion || "").trim(), cama = (p.cama || "").trim();
  if (hab && cama) return `${hab} · ${cama}`;
  return hab || cama || "sin asignar";
}

const numero = (txt) => {
  const t = String(txt).trim().replace(",", ".");
  if (t === "" || !Number.isFinite(Number(t))) return null;
  return Number(t);
};

// ======================================================================
// Diálogos
// ======================================================================

/** Ingreso de un paciente nuevo o edición de uno ya ingresado. */
export async function dialogoPaciente(app, paciente = null) {
  const bd = app.bd;
  const d = new Dialogo(paciente ? "Editar datos del paciente" : "Ingreso de paciente", { ancho: 620 });
  d.subtitulo("Los datos marcados son obligatorios. La fecha de nacimiento define el perfil de umbrales clínicos que el sistema aplicará a este paciente.");
  const gr = grillaFormulario();
  const p = paciente;
  const exp = p ? p.expediente : `EXP-${String(bd.q1("SELECT COUNT(*) c FROM pacientes").c + 1).padStart(6, "0")}`;
  const vExp = filaCampo(gr, "Expediente *", exp, { ancho: 26 });
  const vNom = filaCampo(gr, "Nombre *", p ? p.nombre : "", { ancho: 26 });
  const vApe = filaCampo(gr, "Apellidos *", p ? p.apellidos : "", { ancho: 26 });
  const vFn = filaCampo(gr, "Fecha de nacimiento *", p ? p.fecha_nac : "", { ancho: 26, ayuda: "formato AAAA-MM-DD" });
  const vSexo = filaCampo(gr, "Sexo *", p ? p.sexo : SEXOS[0], { ancho: 26, opciones: SEXOS });
  const vHab = filaCampo(gr, "Habitación", p ? p.habitacion : "", { ancho: 26 });
  const vCama = filaCampo(gr, "Cama", p ? p.cama : "", { ancho: 26 });
  const vDx = filaCampo(gr, "Diagnóstico de ingreso", p ? p.diagnostico : "", { ancho: 26 });
  const vAle = filaCampo(gr, "Alergias", p ? p.alergias : "", { ancho: 26 });
  const vNot = filaCampo(gr, "Notas", p ? p.notas : "", { ancho: 26 });
  const lblPerfil = el("p", { class: "dlg-perfil" });
  d.cuerpo.append(gr, lblPerfil);
  vFn.setAttribute("autofocus", "");

  const refrescarPerfil = () => {
    const fn = vFn.value.trim();
    if (mesesDeEdad(fn) === null) { lblPerfil.textContent = "Escriba la fecha de nacimiento para ver el perfil clínico."; return; }
    const perfil = bd.perfilDe(fn, vSexo.value);
    const hr = bd.umbralesDePerfil(perfil, vSexo.value).hr;
    const extra = hr ? ` · FC normal ${g(hr.normal_min)} a ${g(hr.normal_max)} lpm` : "";
    lblPerfil.textContent = `Edad: ${textoEdad(fn)} · Perfil clínico: ${perfil}${extra}`;
  };
  vFn.addEventListener("input", refrescarPerfil);
  vSexo.addEventListener("change", refrescarPerfil);
  refrescarPerfil();

  d.botones("Guardar", () => {
    const datos = { expediente: vExp.value.trim(), nombre: vNom.value.trim(), apellidos: vApe.value.trim(),
      fecha_nac: vFn.value.trim(), sexo: vSexo.value, habitacion: vHab.value.trim(), cama: vCama.value.trim(),
      diagnostico: vDx.value.trim(), alergias: vAle.value.trim(), notas: vNot.value.trim() };
    if (!datos.expediente || !datos.nombre || !datos.apellidos) return d.error("Expediente, nombre y apellidos son obligatorios.");
    if (mesesDeEdad(datos.fecha_nac) === null) return d.error("La fecha de nacimiento debe tener el formato AAAA-MM-DD, por ejemplo 1958-04-12.");
    const otro = bd.pacientePorExpediente(datos.expediente);
    if (otro && (!paciente || otro.id !== paciente.id)) return d.error(`Ya hay un paciente con el expediente ${datos.expediente}.`);
    if (paciente) { bd.actualizarPaciente(paciente.id, datos, app.usuario); d.resultado = paciente.id; }
    else d.resultado = bd.crearPaciente(datos, app.usuario);
    d.cerrar();
    return null;
  });
  return d.abrir();
}

export async function dialogoPulsera(app, pulsera = null) {
  const bd = app.bd;
  const d = new Dialogo(pulsera ? "Editar pulsera" : "Agregar pulsera al inventario");
  d.subtitulo("El número de serie identifica al dispositivo físico. El identificador es el que la pulsera envía dentro de cada paquete, y es el que permite saber de quién son los datos que están llegando.");
  const gr = grillaFormulario();
  const w = pulsera;
  const vSerie = filaCampo(gr, "Número de serie *", w ? w.serie : bd.siguienteSerie(), { ancho: 24 });
  const vDev = filaCampo(gr, "Identificador en el paquete *", w ? w.id_dispositivo : bd.siguienteDispositivo(),
    { ancho: 24, ayuda: 'el campo "id" del JSON, por ejemplo W01' });
  const vMac = filaCampo(gr, "Dirección MAC (BLE)", w ? w.mac : "", { ancho: 24 });
  const vMod = filaCampo(gr, "Modelo", w ? w.modelo : "VitalCare v1", { ancho: 24 });
  const vEst = filaCampo(gr, "Estado", w ? w.estado : "Disponible", { ancho: 24, opciones: ESTADO_PULSERA });
  const vNot = filaCampo(gr, "Notas", w ? w.notas : "", { ancho: 24 });
  d.cuerpo.append(gr);
  d.botones("Guardar", () => {
    const datos = { serie: vSerie.value.trim(), id_dispositivo: vDev.value.trim(), mac: vMac.value.trim(),
      modelo: vMod.value.trim(), estado: vEst.value, notas: vNot.value.trim() };
    if (!datos.serie || !datos.id_dispositivo) return d.error("El número de serie y el identificador son obligatorios.");
    const existe = bd.q1("SELECT id FROM pulseras WHERE (serie=? OR id_dispositivo=?) AND id<>?",
      [datos.serie, datos.id_dispositivo, pulsera ? pulsera.id : -1]);
    if (existe) return d.error("Ya hay otra pulsera con ese número de serie o ese identificador.");
    if (pulsera) { bd.actualizarPulsera(pulsera.id, datos, app.usuario); d.resultado = pulsera.id; }
    else d.resultado = bd.crearPulsera(datos, app.usuario);
    d.cerrar();
    return null;
  });
  return d.abrir();
}

/** Asignar una pulsera al paciente, o cambiarla por otra sin perder la continuidad. */
export async function dialogoAsignar(app, paciente) {
  const bd = app.bd;
  const actual = bd.asignacionDePaciente(paciente.id);
  const d = new Dialogo(actual ? "Cambiar la pulsera del paciente" : "Asignar pulsera al paciente", { ancho: 620 });
  if (actual) {
    d.subtitulo(`${nombreCompleto(paciente)} lleva puesta la ${actual.serie} (${textoBateria(actual.bateria)}) desde ${actual.inicio}. ` +
      "Al asignar otra, la anterior se libera y el monitoreo del paciente continúa sin cortar la gráfica.");
  } else {
    d.subtitulo(`${nombreCompleto(paciente)} · ${ubicacion(paciente)}. Elija una pulsera disponible del inventario.`);
  }
  d.cuerpo.append(el("p", { class: "dlg-etq" }, "Pulseras disponibles"));
  const tabla = new Tabla([["serie", "Serie", 130], ["dev", "Identificador", 110], ["bat", "Batería", 80, "center"],
    ["est", "Estado", 110], ["mac", "MAC", 150]], { alto: 6 });
  d.cuerpo.append(tabla.elemento);
  const disponibles = bd.pulserasDisponibles();
  for (const w of disponibles) {
    const bat = w.bateria;
    const tag = bat !== null && bat <= 15 ? "alerta" : (bat !== null && bat <= 30 ? "aviso" : "ok");
    tabla.agregar(w.id, [w.serie, w.id_dispositivo, textoBateria(bat), w.estado, w.mac || "—"], [tag]);
  }
  const gr = grillaFormulario();
  gr.style.marginTop = "10px";
  const vMotivo = actual ? filaCampo(gr, "Motivo del cambio *", MOTIVOS_FIN[1], { ancho: 28, opciones: MOTIVOS_FIN }) : { value: "Asignación inicial" };
  const vNota = filaCampo(gr, "Nota", "", { ancho: 28 });
  const vQuien = filaCampo(gr, "Asignada por *", app.usuario, { ancho: 28 });
  d.cuerpo.append(gr);
  if (!disponibles.length) d.error("No hay pulseras disponibles en el inventario. Libere una o registre otra en la pantalla de Pulseras.");
  d.botones("Asignar", () => {
    const wid = tabla.seleccion();
    if (!wid) return d.error("Elija una pulsera de la lista.");
    const quien = vQuien.value.trim();
    if (quien.length < 3) return d.error("Escriba el nombre de quien realiza la asignación.");
    let anterior;
    try { [, anterior] = bd.asignarPulsera(paciente.id, wid, quien, vNota.value.trim(), vMotivo.value); }
    catch (e) { return d.error(e.message); }
    app.usuario = quien;
    const nueva = bd.pulsera(wid);
    d.resultado = { pulsera_id: wid, serie: nueva.serie, dispositivo: nueva.id_dispositivo, anterior, motivo: vMotivo.value };
    d.cerrar();
    return null;
  });
  return d.abrir();
}

/** Signos que la pulsera no mide y el personal toma a mano. */
export async function dialogoMedicion(app, paciente) {
  const bd = app.bd;
  const d = new Dialogo("Registrar signos tomados a mano", { ancho: 620 });
  const umbrales = bd.umbralesDePaciente(paciente.id);
  d.subtitulo(`${nombreCompleto(paciente)} · ${textoEdad(paciente.fecha_nac)} · ${paciente.sexo.toLowerCase()} · perfil ${paciente.perfil}. ` +
    "La pulsera no mide presión arterial ni frecuencia respiratoria, así que estos valores se registran a mano y el sistema los compara con el rango del paciente.");
  const gr = grillaFormulario();
  const ult = bd.ultimasMediciones(paciente.id);
  const vars = {};
  for (const signo of SIGNOS_MANUALES) {
    const [nombre, unidad] = SIGNOS[signo];
    const u = umbrales[signo];
    let rango = u ? `normal ${g(u.normal[0])} a ${g(u.normal[1])} ${unidad}` : "";
    const anterior = ult[signo];
    if (anterior) rango += `   ·   último: ${g(anterior.valor)} (${anterior.hora.slice(11, 16)})`;
    vars[signo] = filaCampo(gr, `${nombre} (${unidad})`, "", { ancho: 12, ayuda: rango });
  }
  const vQuien = filaCampo(gr, "Tomado por *", app.usuario, { ancho: 24 });
  const vNota = filaCampo(gr, "Observación", "", { ancho: 24 });
  d.cuerpo.append(gr);
  vars.pas.setAttribute("autofocus", "");
  d.botones("Registrar", () => {
    const quien = vQuien.value.trim();
    if (quien.length < 3) return d.error("Escriba quién tomó los signos.");
    const valores = {};
    for (const [signo, ctl] of Object.entries(vars)) {
      const txt = ctl.value.trim();
      if (!txt) continue;
      const n = numero(txt);
      if (n === null) return d.error(`El valor de ${SIGNOS[signo][0].toLowerCase()} debe ser un número.`);
      valores[signo] = n;
    }
    if (!Object.keys(valores).length) return d.error("Escriba al menos un valor.");
    if ("pas" in valores && "pad" in valores && valores.pad >= valores.pas) {
      return d.error("La presión diastólica no puede ser mayor o igual que la sistólica.");
    }
    for (const [signo, valor] of Object.entries(valores)) bd.registrarMedicion(paciente.id, signo, valor, quien, vNota.value.trim());
    app.usuario = quien;
    d.resultado = valores;
    d.cerrar();
    return null;
  });
  return d.abrir();
}

async function dialogoUmbral(app, perfil, sexo, signo, fila) {
  const [nombre, unidad] = SIGNOS[signo];
  const d = new Dialogo(`Umbrales de ${nombre.toLowerCase()}`);
  d.subtitulo(`Perfil ${perfil} (${sexo.toLowerCase()}). Dentro del rango normal no hay aviso. Entre el rango normal y el de vigilancia el signo se marca en ámbar. Fuera del rango de vigilancia se genera una alerta.`);
  const gr = grillaFormulario();
  const vNmin = filaCampo(gr, `Normal, mínimo (${unidad})`, g(fila.normal_min), { ancho: 12 });
  const vNmax = filaCampo(gr, `Normal, máximo (${unidad})`, g(fila.normal_max), { ancho: 12 });
  const vVmin = filaCampo(gr, `Vigilancia, mínimo (${unidad})`, g(fila.vig_min), { ancho: 12 });
  const vVmax = filaCampo(gr, `Vigilancia, máximo (${unidad})`, g(fila.vig_max), { ancho: 12 });
  const vQuien = filaCampo(gr, "Autorizado por *", app.usuario, { ancho: 24 });
  d.cuerpo.append(gr);
  d.botones("Guardar", () => {
    const v = [vNmin, vNmax, vVmin, vVmax].map((c) => numero(c.value));
    if (v.some((x) => x === null)) return d.error("Todos los umbrales deben ser números.");
    if (!(v[2] <= v[0] && v[0] < v[1] && v[1] <= v[3])) {
      return d.error("Debe cumplirse: vigilancia mínima ≤ normal mínima < normal máxima ≤ vigilancia máxima.");
    }
    const quien = vQuien.value.trim();
    if (quien.length < 3) return d.error("Escriba quién autoriza el cambio. Queda registrado en la auditoría.");
    app.bd.fijarUmbralPerfil(perfil, sexo, signo, v, quien);
    app.usuario = quien;
    d.resultado = v;
    d.cerrar();
    return null;
  });
  return d.abrir();
}

function dialogoHistorial(titulo, columnas, filas) {
  const d = new Dialogo(titulo, { ancho: 1000 });
  const t = new Tabla(columnas, { alto: 14 });
  filas.forEach((f, i) => t.agregar(i, f));
  d.cuerpo.append(t.elemento);
  d.pie.append(boton("Cerrar", () => d.cerrar(), "claro"));
  return d.abrir();
}

// ======================================================================
// Pantalla de pacientes
// ======================================================================

export class PantallaPacientes {
  constructor(app) {
    this.app = app;
    this.bd = app.bd;
    this.vBuscar = el("input", { type: "search", class: "buscar", oninput: () => this.refrescar(), autocomplete: "off" });
    this.vEstado = el("select", { onchange: () => this.refrescar() }, ["Ingresado", "Alta", "Todos"].map((o) => el("option", { value: o }, o)));
    const barra = tarjeta("barra-filtro",
      el("label", {}, "Buscar"), this.vBuscar, el("label", {}, "Estado"), this.vEstado, el("div", { class: "flex1" }),
      boton("Eliminar", () => this.eliminar(), "claro"), boton("Editar", () => this.editar(), "claro"),
      boton("Ingresar paciente", () => this.nuevo()));
    this.tabla = new Tabla([["exp", "Expediente", 120], ["nom", "Paciente", 210], ["edad", "Edad", 90], ["sexo", "Sexo", 90],
      ["perfil", "Perfil clínico", 120], ["ubi", "Habitación", 110], ["serie", "Pulsera", 120], ["bat", "Batería", 80, "center"],
      ["est", "Estado", 90], ["ing", "Ingreso", 130]], { alto: 13, alElegir: () => this._alElegir(), alDoble: () => this.editar() });
    this.detalle = el("p", { class: "detalle-sel" });
    const cuerpo = tarjeta("cuerpo-gestion",
      tituloPanel("Pacientes", "doble clic para editar · la columna Pulsera indica qué dispositivo envía sus datos"),
      this.tabla.elemento,
      el("div", { class: "acciones" },
        boton("Monitorear a este paciente", () => this.monitorear()),
        boton("Asignar o cambiar pulsera", () => this.asignar(), "claro"),
        boton("Signos manuales", () => this.medicion(), "claro"),
        boton("Historial de pulseras", () => this.historial(), "claro"),
        el("div", { class: "flex1" }),
        boton("Liberar pulsera", () => this.liberar(), "claro"),
        boton("Dar de alta", () => this.darAlta(), "warn")),
      this.detalle);
    this.elemento = el("section", { class: "pagina-gestion" }, barra, cuerpo);
    this.refrescar();
  }

  refrescar() {
    const sel = this.tabla.seleccion();
    this.tabla.limpiar();
    for (const p of this.bd.listarPacientes(this.vBuscar.value.trim(), this.vEstado.value)) {
      const bat = p.bateria_pulsera;
      let tag = "";
      if (p.estado === "Alta") tag = "gris";
      else if (!p.serie) tag = "aviso";
      else if (bat !== null && bat <= 15) tag = "alerta";
      this.tabla.agregar(p.id, [p.expediente, nombreCompleto(p), textoEdad(p.fecha_nac), p.sexo, p.perfil || "—", ubicacion(p),
        p.serie || "sin pulsera", textoBateria(bat), p.estado, (p.ingreso || "").slice(0, 16)], tag ? [tag] : []);
    }
    if (sel) this.tabla.seleccionar(sel);
    this._alElegir();
  }

  _alElegir() {
    const pid = this.tabla.seleccion();
    if (!pid) { this.detalle.textContent = "Seleccione un paciente para ver su detalle."; return; }
    const p = this.bd.paciente(pid);
    const u = this.bd.umbralesDePaciente(pid);
    const partes = [`Diagnóstico: ${p.diagnostico || "sin registrar"}`, `Alergias: ${p.alergias || "ninguna registrada"}`];
    for (const signo of ["hr", "spo2", "temp", "pas"]) {
      if (u[signo]) {
        const [nombre, unidad] = SIGNOS[signo];
        const n = u[signo].normal;
        partes.push(`${nombre}: ${g(n[0])} a ${g(n[1])} ${unidad} (${u[signo].origen})`);
      }
    }
    const ult = this.bd.ultimasMediciones(pid);
    if (Object.keys(ult).length) {
      partes.push("Últimos signos manuales: " + Object.entries(ult).map(([s, m]) => `${SIGNOS[s][0].split(" ").pop().toLowerCase()} ${g(m.valor)}`).join(", "));
    }
    this.detalle.textContent = partes.join("   ·   ");
  }

  async _seleccionado(exigirIngresado = true) {
    const pid = this.tabla.seleccion();
    if (!pid) { await mensaje("Sin selección", "Elija primero un paciente de la lista."); return null; }
    const p = this.bd.paciente(pid);
    if (exigirIngresado && p.estado !== "Ingresado") {
      await mensaje("Paciente dado de alta", "Este paciente ya fue dado de alta. Solo puede consultar su historial.");
      return null;
    }
    return p;
  }

  async nuevo() {
    const r = await dialogoPaciente(this.app);
    if (r) { this.refrescar(); this.tabla.seleccionar(r); this._alElegir(); this.app.aviso("Paciente ingresado. Ahora asígnele una pulsera."); this.app.refrescarComboPacientes(); }
  }

  async editar() {
    const pid = this.tabla.seleccion();
    if (!pid) return;
    const r = await dialogoPaciente(this.app, this.bd.paciente(pid));
    if (r) { this.refrescar(); this.app.refrescarPacienteMonitoreado(); }
  }

  async eliminar() {
    const pid = this.tabla.seleccion();
    if (!pid) return;
    const p = this.bd.paciente(pid);
    const [ok, usos, meds] = this.bd.puedeEliminarPaciente(pid);
    if (!ok) {
      await mensaje("No se puede eliminar", `${nombreCompleto(p)} tiene ${usos} asignación(es) de pulsera y ${meds} medición(es) registradas.\n\n` +
        "En un sistema clínico ese historial no se borra. Use Dar de alta para sacarlo del monitoreo y liberar su pulsera.", "aviso");
      return;
    }
    if (await confirmar("Eliminar paciente", `¿Eliminar a ${nombreCompleto(p)}?\n\nNo tiene historial registrado, así que la eliminación es definitiva.`)) {
      this.bd.eliminarPaciente(pid, this.app.usuario);
      this.refrescar();
      this.app.refrescarComboPacientes();
      this.app.aviso("Paciente eliminado del sistema.");
    }
  }

  async asignar() {
    const p = await this._seleccionado();
    if (!p) return;
    const r = await dialogoAsignar(this.app, p);
    if (r) {
      this.refrescar();
      this.app.pulseraCambiada(p.id, r);
      if (r.anterior) this.app.aviso(`Pulsera cambiada: ${r.anterior} → ${r.serie}. El monitoreo del paciente continúa.`);
      else this.app.aviso(`Pulsera ${r.serie} asignada a ${nombreCompleto(p)}.`);
    }
  }

  async liberar() {
    const p = await this._seleccionado();
    if (!p) return;
    const a = this.bd.asignacionDePaciente(p.id);
    if (!a) { await mensaje("Sin pulsera", "Este paciente no tiene pulsera asignada."); return; }
    if (await confirmar("Liberar pulsera", `¿Liberar la pulsera ${a.serie} de ${nombreCompleto(p)}?\n\nEl paciente queda sin monitoreo hasta que se le asigne otra.`)) {
      this.bd.cerrarAsignacion(a.id, this.app.usuario, "Otro", "liberada a mano");
      this.refrescar();
      this.app.pulseraCambiada(p.id, null);
      this.app.aviso(`Pulsera ${a.serie} liberada.`);
    }
  }

  async darAlta() {
    const p = await this._seleccionado();
    if (!p) return;
    const a = this.bd.asignacionDePaciente(p.id);
    let texto = `¿Dar de alta a ${nombreCompleto(p)}?`;
    if (a) texto += `\n\nLa pulsera ${a.serie} quedará disponible para otro paciente y el monitoreo de este se detiene.`;
    if (!(await confirmar("Alta del paciente", texto))) return;
    const liberada = this.bd.darAlta(p.id, this.app.usuario);
    this.refrescar();
    this.app.pacienteDadoDeAlta(p.id);
    this.app.aviso(`${nombreCompleto(p)} dado de alta.` + (liberada ? ` Pulsera ${liberada} liberada.` : ""));
  }

  async medicion() {
    const p = await this._seleccionado();
    if (!p) return;
    const r = await dialogoMedicion(this.app, p);
    if (r) { this.refrescar(); this.app.signosManualesRegistrados(p.id, r); }
  }

  historial() {
    const pid = this.tabla.seleccion();
    if (!pid) return;
    const p = this.bd.paciente(pid);
    const filas = this.bd.historialAsignaciones({ pid }).map((h) => [h.serie, h.id_dispositivo, h.inicio, h.fin || "en uso",
      h.motivo_fin || "—", h.asignado_por || "—", h.nota || ""]);
    dialogoHistorial(`Historial de pulseras de ${nombreCompleto(p)}`, [["serie", "Serie", 120], ["dev", "Identificador", 110],
      ["ini", "Desde", 150], ["fin", "Hasta", 150], ["mot", "Motivo", 160], ["quien", "Asignada por", 150], ["nota", "Nota", 220]], filas);
  }

  async monitorear() {
    const p = await this._seleccionado();
    if (!p) return;
    if (!this.bd.asignacionDePaciente(p.id)) {
      await mensaje("Sin pulsera", `${nombreCompleto(p)} no tiene pulsera asignada. Asígnele una primero.`);
      return;
    }
    this.app.monitorearPaciente(p.id);
  }
}

// ======================================================================
// Pantalla de pulseras
// ======================================================================

export class PantallaPulseras {
  constructor(app) {
    this.app = app;
    this.bd = app.bd;
    this.vBuscar = el("input", { type: "search", class: "buscar", oninput: () => this.refrescar(), autocomplete: "off" });
    this.vEstado = el("select", { onchange: () => this.refrescar() }, ["Todos", ...ESTADO_PULSERA].map((o) => el("option", { value: o }, o)));
    const barra = tarjeta("barra-filtro",
      el("label", {}, "Buscar"), this.vBuscar, el("label", {}, "Estado"), this.vEstado, el("div", { class: "flex1" }),
      boton("Eliminar", () => this.eliminar(), "claro"), boton("Editar", () => this.editar(), "claro"),
      boton("Agregar pulsera", () => this.nueva()));
    this.tabla = new Tabla([["serie", "Serie", 130], ["dev", "Identificador", 120], ["mac", "MAC", 160], ["mod", "Modelo", 130],
      ["est", "Estado", 130], ["bat", "Batería", 90, "center"], ["vista", "Último dato", 150], ["pac", "Asignada a", 230]],
    { alto: 14, alDoble: () => this.editar() });
    const acciones = el("div", { class: "acciones" },
      ESTADO_PULSERA.map((e) => boton(`Marcar ${e.toLowerCase()}`, () => this.cambiarEstado(e), "claro")),
      el("div", { class: "flex1" }), boton("Historial", () => this.historial(), "claro"));
    const cuerpo = tarjeta("cuerpo-gestion",
      tituloPanel("Inventario de pulseras", "cada pulsera tiene su número de serie, no está atada a una cama"),
      this.tabla.elemento, acciones);
    this.elemento = el("section", { class: "pagina-gestion" }, barra, cuerpo);
    this.refrescar();
  }

  refrescar() {
    const sel = this.tabla.seleccion();
    this.tabla.limpiar();
    for (const w of this.bd.listarPulseras(this.vBuscar.value.trim(), this.vEstado.value)) {
      const bat = w.bateria;
      let tag = "";
      if (w.estado === "Fuera de servicio") tag = "gris";
      else if (bat !== null && bat <= 15) tag = "alerta";
      else if (bat !== null && bat <= 30) tag = "aviso";
      else if (w.estado === "Disponible") tag = "ok";
      const paciente = w.paciente_id ? `${w.p_nombre} ${w.p_apellidos} · ${w.p_habitacion || "s/h"}` : "—";
      this.tabla.agregar(w.id, [w.serie, w.id_dispositivo, w.mac || "—", w.modelo || "—", w.estado, textoBateria(bat),
        (w.ultima_vista || "nunca").slice(0, 16), paciente], tag ? [tag] : []);
    }
    if (sel) this.tabla.seleccionar(sel);
  }

  async nueva() {
    const r = await dialogoPulsera(this.app);
    if (r) { this.refrescar(); this.app.aviso("Pulsera agregada al inventario."); }
  }

  async editar() {
    const wid = this.tabla.seleccion();
    if (!wid) return;
    const r = await dialogoPulsera(this.app, this.bd.pulsera(wid));
    if (r) { this.refrescar(); this.app.refrescarPacienteMonitoreado(); }
  }

  async eliminar() {
    const wid = this.tabla.seleccion();
    if (!wid) return;
    const w = this.bd.pulsera(wid);
    const [ok, usos] = this.bd.puedeEliminarPulsera(wid);
    if (!ok) {
      await mensaje("No se puede eliminar", `La pulsera ${w.serie} tiene ${usos} asignación(es) en su historial y ese registro no se borra.\n\n` +
        "Use el estado Fuera de servicio para retirarla del inventario.", "aviso");
      return;
    }
    if (await confirmar("Eliminar pulsera", `¿Eliminar la pulsera ${w.serie} del inventario?`)) {
      this.bd.eliminarPulsera(wid, this.app.usuario);
      this.refrescar();
    }
  }

  async cambiarEstado(estado) {
    const wid = this.tabla.seleccion();
    if (!wid) { await mensaje("Sin selección", "Elija una pulsera de la lista."); return; }
    const w = this.bd.pulsera(wid);
    const asig = this.bd.asignacionDePulsera(wid);
    if (asig && estado !== "En uso") {
      await mensaje("Pulsera en uso", `La ${w.serie} está puesta a ${asig.nombre} ${asig.apellidos}. Libérela desde la pantalla de Pacientes antes de cambiar su estado.`, "aviso");
      return;
    }
    this.bd.actualizarEstadoPulsera(wid, estado, this.app.usuario);
    this.refrescar();
  }

  historial() {
    const wid = this.tabla.seleccion();
    if (!wid) return;
    const w = this.bd.pulsera(wid);
    const filas = this.bd.historialAsignaciones({ wid }).map((h) => [`${h.nombre} ${h.apellidos}`, h.expediente, h.inicio,
      h.fin || "en uso", h.motivo_fin || "—", h.asignado_por || "—"]);
    dialogoHistorial(`Historial de la pulsera ${w.serie}`, [["pac", "Paciente", 220], ["exp", "Expediente", 130], ["ini", "Desde", 150],
      ["fin", "Hasta", 150], ["mot", "Motivo", 170], ["quien", "Asignada por", 150]], filas);
  }
}

// ======================================================================
// Pantalla de configuración
// ======================================================================

export class PantallaConfig {
  constructor(app) {
    this.app = app;
    this.bd = app.bd;
    this.subs = {};
    const nb = el("div", { class: "sub-pestanas" });
    const paginas = el("div", { class: "sub-paginas" });
    for (const [clave, texto] of [["umbrales", "Umbrales clínicos"], ["param", "Parámetros de alerta"], ["audit", "Auditoría"], ["datos", "Datos del navegador"]]) {
      const b = el("button", { type: "button", class: "sub-pestana", onclick: () => this.mostrar(clave) }, texto);
      const pag = el("div", { class: "sub-pagina" });
      this.subs[clave] = [b, pag];
      nb.append(b);
      paginas.append(pag);
    }
    this._construirUmbrales();
    this._construirParametros();
    this._construirAuditoria();
    this._construirDatos();
    this.elemento = el("section", { class: "pagina-gestion config" }, nb, paginas);
    this.mostrar("umbrales");
  }

  mostrar(clave) {
    for (const [k, [b, pag]] of Object.entries(this.subs)) { b.classList.toggle("activa", k === clave); pag.classList.toggle("activa", k === clave); }
    this.refrescar();
  }

  // ---- umbrales ----
  _construirUmbrales() {
    this.cbPerfil = el("select", { onchange: () => this._cargarUmbrales() });
    this.cbSexo = el("select", { onchange: () => this._cargarUmbrales() });
    this.tablaU = new Tabla([["signo", "Signo", 240], ["mide", "Lo mide", 110], ["normal", "Rango normal", 180],
      ["vig", "Rango de vigilancia", 200], ["alerta", "Genera alerta", 260]], { alto: 8, alDoble: () => this._editarUmbral() });
    this.subs.umbrales[1].append(tarjeta("cuerpo-gestion",
      tituloPanel("Umbrales por perfil clínico", "el perfil se asigna por la edad del paciente; cada centro puede ajustarlo"),
      el("div", { class: "fila-filtros" }, el("label", {}, "Perfil"), this.cbPerfil, el("label", {}, "Variante"), this.cbSexo,
        el("div", { class: "flex1" }), boton("Crear variante por sexo", () => this._crearVariante(), "claro"),
        boton("Editar umbral", () => this._editarUmbral())),
      this.tablaU.elemento,
      el("p", { class: "nota" }, "Cada cambio queda registrado en la pestaña de Auditoría con el nombre de quien lo autorizó. " +
        "El sistema admite variantes por sexo: se crean copiando el perfil general y ajustando los valores que el centro decida.")));
  }

  _cargarUmbrales() {
    const perfil = this.cbPerfil.value;
    let sexo = this.cbSexo.value || AMBOS;
    const variantes = this.bd.q("SELECT DISTINCT sexo FROM perfiles WHERE nombre=?", [perfil]).map((r) => r.sexo);
    this.cbSexo.replaceChildren(...(variantes.length ? variantes : [AMBOS]).map((v) => el("option", { value: v }, v)));
    if (!variantes.includes(sexo)) sexo = AMBOS;
    this.cbSexo.value = sexo;
    this.tablaU.limpiar();
    const u = this.bd.umbralesDePerfil(perfil, sexo);
    Object.entries(SIGNOS).forEach(([signo, [nombre, unidad, origen]], i) => {
      const f = u[signo];
      if (!f) return;
      this.tablaU.agregar(i, [`${nombre} (${unidad})`, origen === "pulsera" ? "la pulsera" : "a mano",
        `${g(f.normal_min)} a ${g(f.normal_max)}`, `${g(f.vig_min)} a ${g(f.vig_max)}`,
        `menos de ${g(f.vig_min)} o más de ${g(f.vig_max)}`]);
    });
  }

  async _editarUmbral() {
    const i = this.tablaU.seleccion();
    if (i === null) { await mensaje("Sin selección", "Elija un signo de la tabla."); return; }
    const signo = Object.keys(SIGNOS)[i];
    const perfil = this.cbPerfil.value, sexo = this.cbSexo.value;
    const fila = this.bd.umbralesDePerfil(perfil, sexo)[signo];
    const r = await dialogoUmbral(this.app, perfil, sexo, signo, fila);
    if (r) {
      this._cargarUmbrales();
      this.app.refrescarUmbrales();
      this.app.aviso(`Umbral de ${SIGNOS[signo][0].toLowerCase()} actualizado para el perfil ${perfil}.`);
    }
  }

  async _crearVariante() {
    const perfil = this.cbPerfil.value;
    const d = new Dialogo("Crear variante por sexo");
    d.subtitulo(`Copia los umbrales del perfil ${perfil} para un sexo, de modo que el centro pueda ajustarlos sin tocar el perfil general.`);
    const gr = grillaFormulario();
    const vSexo = filaCampo(gr, "Sexo", SEXOS[0], { ancho: 18, opciones: SEXOS });
    const vQuien = filaCampo(gr, "Autorizado por *", this.app.usuario, { ancho: 24 });
    d.cuerpo.append(gr);
    d.botones("Crear", () => {
      if (vQuien.value.trim().length < 3) return d.error("Escriba quién autoriza la creación de la variante.");
      const n = this.bd.crearVarianteSexo(perfil, vSexo.value, vQuien.value.trim());
      this.app.usuario = vQuien.value.trim();
      d.resultado = n;
      d.cerrar();
      return null;
    });
    const r = await d.abrir();
    if (r) { this._cargarUmbrales(); this.app.aviso(`Variante creada con ${r} signos. Ahora puede ajustar cada valor.`); }
  }

  // ---- parámetros ----
  _construirParametros() {
    this.tablaP = new Tabla([["clave", "Parámetro", 280], ["valor", "Valor", 150, "center"], ["desc", "Qué controla", 560], ["act", "Último cambio", 190]],
      { alto: 11, alDoble: () => this._editarParam() });
    this.subs.param[1].append(tarjeta("cuerpo-gestion",
      tituloPanel("Parámetros de alerta del centro", "doble clic sobre un parámetro para cambiarlo"),
      this.tablaP.elemento,
      el("div", { class: "acciones" }, boton("Cambiar parámetro", () => this._editarParam()),
        el("span", { class: "nota" }, "Los cambios se aplican de inmediato al monitoreo en curso. Los parámetros mqtt_ son los de la conexión con el hub."))));
  }

  _cargarParametros() {
    this.tablaP.limpiar();
    this.claves = [];
    this.bd.listarConfig().forEach((f, i) => {
      this.claves.push(f.clave);
      const valor = f.unidad === "clave" ? (f.valor ? "••••••" : "(vacía)") : `${f.valor} ${f.unidad === "puerto" ? "" : f.unidad || ""}`.trim();
      this.tablaP.agregar(i, [f.clave, valor, f.descripcion, `${(f.actualizado || "").slice(0, 16)} · ${f.actualizado_por || ""}`]);
    });
  }

  async _editarParam() {
    const i = this.tablaP.seleccion();
    if (i === null) { await mensaje("Sin selección", "Elija un parámetro de la tabla."); return; }
    const clave = this.claves[i];
    const f = this.bd.q1("SELECT * FROM config WHERE clave=?", [clave]);
    const d = new Dialogo(`Parámetro: ${clave}`);
    d.subtitulo(f.descripcion || "");
    const gr = grillaFormulario();
    const etiquetaUnidad = f.unidad === "clave" ? "texto" : (f.unidad || "texto");
    const vVal = filaCampo(gr, `Valor (${etiquetaUnidad})`, f.valor, { ancho: 18, tipo: f.unidad === "clave" ? "password" : "text" });
    const vQuien = filaCampo(gr, "Autorizado por *", this.app.usuario, { ancho: 24 });
    d.cuerpo.append(gr);
    d.botones("Guardar", () => {
      if (vQuien.value.trim().length < 3) return d.error("Escriba quién autoriza el cambio.");
      let valor = vVal.value.trim();
      if (["s", "%", "min", "puerto"].includes(f.unidad)) {
        const n = numero(valor);
        if (n === null) return d.error("Este parámetro debe ser un número.");
        if (n <= 0) return d.error("El valor debe ser mayor que cero.");
        valor = Number.isInteger(n) ? n : n;
      }
      this.bd.fijarConfig(clave, valor, vQuien.value.trim());
      this.app.usuario = vQuien.value.trim();
      d.resultado = { valor };
      d.cerrar();
      return null;
    });
    const r = await d.abrir();
    if (r) {
      this._cargarParametros();
      this.app.aplicarConfiguracion();
      this.app.aviso(`Parámetro ${clave} actualizado${f.unidad === "clave" ? "" : ` a ${r.valor}`}.`);
    }
  }

  // ---- auditoría ----
  _construirAuditoria() {
    this.tablaA = new Tabla([["hora", "Fecha y hora", 160], ["usr", "Usuario", 170], ["acc", "Acción", 230], ["det", "Detalle", 620]], { alto: 16 });
    this.subs.audit[1].append(tarjeta("cuerpo-gestion",
      tituloPanel("Auditoría del sistema", "quién hizo qué y cuándo: ingresos, altas, cambios de pulsera y de umbrales"),
      this.tablaA.elemento,
      el("div", { class: "acciones" }, boton("Actualizar", () => this._cargarAuditoria(), "claro"),
        boton("Exportar a CSV", () => this._exportar(), "claro"))));
  }

  _cargarAuditoria() {
    this.tablaA.limpiar();
    for (const f of this.bd.listarAuditoria(400)) this.tablaA.agregar(f.id, [f.hora, f.usuario, f.accion, f.detalle || ""]);
  }

  _exportar() {
    const lineas = [filaCSV(["hora", "usuario", "accion", "entidad", "detalle"])];
    for (const f of this.bd.listarAuditoria(5000)) lineas.push(filaCSV([f.hora, f.usuario, f.accion, f.entidad, f.detalle]));
    descargar("auditoria.csv", "﻿" + lineas.join("\r\n") + "\r\n", "text/csv;charset=utf-8");
    this.app.aviso("Auditoría exportada a la carpeta Descargas (auditoria.csv).");
  }

  // ---- datos del navegador (solo web) ----
  _construirDatos() {
    this.lblDatos = el("p", { class: "nota" });
    const archivo = el("input", { type: "file", accept: ".db,.sqlite,application/octet-stream", style: { display: "none" },
      onchange: () => { if (archivo.files[0]) this.app.importarBase(archivo.files[0]); archivo.value = ""; } });
    this.subs.datos[1].append(tarjeta("cuerpo-gestion",
      tituloPanel("Datos guardados en este navegador", "la base de datos y los archivos de cada sesión"),
      el("div", { class: "texto-datos" },
        el("p", {}, "En la versión de escritorio los datos viven en vitalcare.db y en la carpeta datos_sesion. En la web viven dentro de este navegador, " +
          "en esta computadora: no se pierden al recargar ni al cerrar, pero no aparecen solos en otra computadora ni en otro navegador."),
        el("p", {}, "Para pasar los pacientes, las pulseras, los umbrales y la auditoría a otra computadora, exporte la base aquí y luego impórtela allá. " +
          "El archivo es el mismo vitalcare.db de la versión de escritorio, así que también sirve para abrir en una y otra."),
        this.lblDatos),
      el("div", { class: "acciones" },
        boton("Exportar base de datos (vitalcare.db)", () => this.app.exportarBase()),
        boton("Importar base de datos", () => archivo.click(), "claro"), archivo,
        boton("Carpeta de datos de las sesiones", () => this.app.abrirCarpeta(), "claro"),
        el("div", { class: "flex1" }),
        boton("Borrar todos los datos de este navegador", () => this.app.borrarTodo(), "alerta"))));
  }

  refrescar() {
    const perfiles = this.bd.q("SELECT DISTINCT nombre FROM perfiles ORDER BY meses_min").map((r) => r.nombre);
    const actual = this.cbPerfil.value;
    this.cbPerfil.replaceChildren(...perfiles.map((p) => el("option", { value: p }, p)));
    this.cbPerfil.value = perfiles.includes(actual) ? actual : (perfiles.includes("Adulto") ? "Adulto" : perfiles[0]);
    this._cargarUmbrales();
    this._cargarParametros();
    this._cargarAuditoria();
    const n = this.bd.q1("SELECT (SELECT COUNT(*) FROM pacientes) p, (SELECT COUNT(*) FROM pulseras) w, (SELECT COUNT(*) FROM sesiones) s");
    this.lblDatos.textContent = `Ahora hay ${n.p} paciente(s), ${n.w} pulsera(s) y ${n.s} sesión(es) de monitoreo guardadas en este navegador.`;
  }
}
