// VitalCare Web: la estación de monitoreo de app_testbed.py llevada al navegador.
//
// Qué hace (igual que la versión de escritorio):
//   - Recibe telemetría (hr, spo2, temp, batt) y eventos (FALL, SOS) desde el simulador,
//     por Bluetooth directo a la pulsera o desde el hub por MQTT.
//   - Muestra cada signo en su tarjeta (Normal / Vigilancia / Alerta) y el estado general.
//   - Grafica las tendencias en vivo, con el rango normal sombreado y los eventos marcados.
//   - Genera alertas con cronómetro y escalamiento, y las cierra con el detalle del personal.
//   - Detecta paquetes faltantes, pide el reenvío y declara la pérdida real si no llegan.
//   - Guarda cada fila de la sesión en el navegador y arma el resumen.
// Atajos: F2 caída, F3 SOS (solo simulador), F4 caída de red, F5 corte de energía,
//         F8 evidencia, F11 pantalla completa.

import {
  ESTADO_PACIENTE, NIVEL_ALERTA, NIVEL_NORMAL, NIVEL_VIGILANCIA, NOMBRE_NIVEL, UMBRALES, Sesion, aNumero, ahoraS,
  clasificar, estadoGlobal, etiquetaSeveridad, formatoAutonomia, formatearResumen, formatoDuracion, BATERIA_BAJA,
  BATERIA_CRITICA, nivelBateria, textoRango, tituloAlerta, g,
} from "./logica.js";
import { RegistradorCSV, marcaDe } from "./registro.js";
import {
  BLETransport, ESCENARIOS, FUENTES, FUENTE_BLE, FUENTE_MQTT, FUENTE_SIM, MQTTTransport, SimuladorTransport,
} from "./transportes.js";
import { BaseDatos, SIGNOS, textoEdad } from "./datos.js";
import { Almacen, descargar } from "./almacen.js";
import { COLOR, Dialogo, boton, confirmar, el, hayDialogoAbierto, mensaje } from "./ui.js";
import {
  PantallaConfig, PantallaPacientes, PantallaPulseras, dialogoAsignar, dialogoMedicion, nombreCompleto, ubicacion,
} from "./pantallas.js";
import { Graficas } from "./graficas.js";

// ----------------------------------------------------------------------
// Configuración
// ----------------------------------------------------------------------

const ESCALAMIENTO_S = 60;
const SIN_SENAL_S = 7.0;
const MAX_TARJETAS_ALERTA = 40;
const ESCALAMIENTO_BATERIA_BAJA_S = 300;
const ESCALAMIENTO_BATERIA_CRITICA_S = 120;
const PLAZO_RECUPERACION_S = 12.0;
const REINTENTO_S = 4.0;
const BATERIA_REARME = 40;
const VENTANAS = [["2 min", 120], ["10 min", 600], ["Sesión", null]];

const COLOR_NIVEL = {
  null: [COLOR.GRIS, COLOR.GRIS_BG],
  0: [COLOR.OK, COLOR.OK_BG],
  1: [COLOR.WARN, COLOR.WARN_BG],
  2: [COLOR.ALERTA, COLOR.ALERTA_BG],
};
const colorNivel = (n) => COLOR_NIVEL[n === undefined ? null : n] || COLOR_NIVEL.null;

const CONEXION = {
  desconectado: ["#3B5168", "#DCE5EE", "Desconectado"],
  conectando: ["#B7791F", "#FFFFFF", "Conectando…"],
  conectado: ["#1B8A5A", "#FFFFFF", "Conectado"],
  sin_senal: ["#C25E00", "#FFFFFF", "Sin señal"],
  error: ["#C62828", "#FFFFFF", "Error"],
};

const monotono = () => performance.now() / 1000;
const dos = (n) => String(n).padStart(2, "0");
const horaCorta = (d = new Date()) => `${dos(d.getHours())}:${dos(d.getMinutes())}:${dos(d.getSeconds())}`;

// ----------------------------------------------------------------------
// Componentes de la interfaz
// ----------------------------------------------------------------------

/** Tarjeta con el valor actual de un signo vital y su estado. */
class TarjetaVital {
  constructor(titulo, unidad, rangoTxt, { chico = false, alPulsar = null } = {}) {
    this.chip = el("span", { class: "chip-nivel" }, "Sin datos");
    this.valor = el("b", { class: "valor" }, "--");
    this.pie = el("div", { class: "pie-tarjeta" }, rangoTxt);
    this.elemento = el("div", { class: `tarjeta-vital${chico ? " chica" : ""}${alPulsar ? " pulsable" : ""}`, onclick: alPulsar, title: alPulsar ? "Pulse para registrar" : null },
      el("div", { class: "barra" }),
      el("div", { class: "cuerpo-vital" },
        el("div", { class: "fila-titulo" }, el("span", { class: "titulo-vital" }, titulo), this.chip),
        el("div", { class: "fila-valor" }, this.valor, el("span", { class: "unidad" }, unidad)),
        this.pie));
    this.actualizar(null, null, "Sin datos");
  }

  fijarPie(texto) { this.pie.textContent = texto; }

  actualizar(valorTxt, nivel, chipTxt) {
    const [fuerte, suave] = colorNivel(nivel);
    this.elemento.style.setProperty("--fuerte", fuerte);
    this.valor.textContent = valorTxt ?? "--";
    this.valor.style.color = nivel === null || nivel === undefined || nivel === NIVEL_NORMAL ? COLOR.TEXTO : fuerte;
    this.chip.textContent = chipTxt;
    this.chip.style.background = suave;
    this.chip.style.color = fuerte;
  }
}

const RESOLUCIONES_BASE = ["Resuelta por enfermería", "Resuelta con indicación médica", "Falsa alarma",
  "Paciente derivado o trasladado", "Otra"];
const RESOLUCIONES_BATERIA = ["Pulsera reemplazada", "Pulsera puesta a cargar", "Falsa alarma (lectura errónea)", "Otra"];
const RESOLUCIONES_DATOS = ["Datos recuperados por otra vía", "Pérdida confirmada y documentada", "Otra"];

const FRASES = {
  FALL: ["Se acudió y se evaluó al paciente, sin lesiones", "Se notificó al médico de guardia", "Se trasladó al paciente para valoración"],
  SOS: ["Se acudió a la habitación y se atendió al paciente", "El paciente presionó el botón sin querer", "Se notificó al médico de guardia"],
  HR: ["Se verificó el pulso de forma manual", "Paciente en reposo, se volvió a medir", "Se notificó al médico"],
  SPO2: ["Se reposicionó el sensor y se volvió a medir", "Se administró oxígeno según indicación médica", "Se notificó al médico"],
  TEMP: ["Se midió con termómetro clínico", "Se administró antipirético según indicación médica", "Se aplicaron medios físicos", "Se notificó al médico"],
  BATERIA: ["Se reemplazó la pulsera por una cargada", "Se puso la pulsera a cargar"],
  DATOS: ["Se revisó la conexión del receptor", "Se documentó el intervalo sin datos"],
};

export function resolucionesPara(tipo) {
  if (String(tipo).startsWith("BATERIA")) return RESOLUCIONES_BATERIA;
  if (tipo === "DATOS") return RESOLUCIONES_DATOS;
  return RESOLUCIONES_BASE;
}

export function frasesPara(tipo) {
  for (const [clave, frases] of Object.entries(FRASES)) if (String(tipo).startsWith(clave)) return frases;
  return [];
}

/** Reglas para cerrar una alerta: sin estos datos no se cierra. */
export function validarCierre(d) {
  if (!d.resolucion) return "Elige cómo se resolvió la alerta.";
  if (d.accion_realizada.length < 8) return "Describe qué se hizo (incluso si fue falsa alarma, indica el motivo).";
  if (d.atendido_por.length < 2) return "Escribe quién atendió la alerta.";
  if (d.indicacion_medica && d.medico.length < 2) return "Escribe el nombre del médico que dio la indicación.";
  return null;
}

/** Ventana para cerrar una alerta con el detalle escrito por el personal. */
function dialogoAtencion(app, tarjeta) {
  const d = new Dialogo("Registrar atención de la alerta", { ancho: 540 });
  d.titulo.textContent = tarjeta.titulo;
  d.subtitulo(tarjeta.detalle);
  const reloj = el("p", { class: "dlg-reloj" }, `Tiempo sin atender: ${formatoDuracion(tarjeta.segundos())}`);
  const relojInt = setInterval(() => { reloj.textContent = `Tiempo sin atender: ${formatoDuracion(tarjeta.segundos())}`; }, 1000);
  const etq = (t) => el("p", { class: "dlg-etq" }, t);
  const res = el("select", {}, el("option", { value: "" }, ""), resolucionesPara(tarjeta.tipo).map((r) => el("option", { value: r }, r)));
  const txt = el("textarea", { rows: 4, autofocus: true });
  const chkInd = el("input", { type: "checkbox", id: "chk-ind" });
  const medico = el("input", { type: "text", disabled: true });
  const persona = el("input", { type: "text", value: app.ultimoPersonal });
  const alCambiarInd = () => { medico.disabled = !chkInd.checked; };
  chkInd.addEventListener("change", alCambiarInd);
  res.addEventListener("change", () => { if (res.value.toLowerCase().includes("indicación médica")) { chkInd.checked = true; alCambiarInd(); } });
  const insertar = (frase) => {
    let actual = txt.value.trim();
    if (actual) actual += actual.endsWith(".") ? " " : ". ";
    txt.value = actual + frase;
    txt.focus();
  };
  const frases = frasesPara(tarjeta.tipo);
  d.cuerpo.append(reloj, etq("¿Cómo se resolvió?"), res, etq("¿Qué se hizo? (descripción de la acción realizada)"), txt,
    frases.length ? el("div", { class: "frases" }, el("span", {}, "Frases rápidas:"), frases.map((f) => el("button", { type: "button", class: "frase", onclick: () => insertar(f) }, "+ " + f))) : null,
    el("div", { class: "fila-medico" }, el("label", { for: "chk-ind" }, chkInd, " Hubo indicación médica"), el("span", {}, "Médico que indicó:"), medico),
    etq("Atendido por (enfermera o enfermero)"), persona);
  const datos = () => ({
    resolucion: res.value.trim(), accion_realizada: txt.value.trim(), atendido_por: persona.value.trim(),
    indicacion_medica: chkInd.checked, medico: chkInd.checked ? medico.value.trim() : "",
  });
  d.pie.append(
    boton("Guardar y cerrar la alerta", () => {
      const c = datos();
      const error = validarCierre(c);
      if (error) { d.error(error); return; }
      app.ultimoPersonal = c.atendido_por;
      d.cerrar();
      tarjeta.cerrar(c);
    }, "ok"),
    boton("Cancelar (la alerta sigue pendiente)", () => d.cerrar(), "claro"));
  d.abrir().then(() => clearInterval(relojInt));
}

/** Una alerta en el panel lateral, con cronómetro. Se cierra con el detalle escrito por el personal. */
class TarjetaAlerta {
  constructor(app, titulo, detalle, gravedad, origen, tipo, seq, requiereAtencion = true, escalamientoS = ESCALAMIENTO_S, leve = false, extra = {}) {
    Object.assign(this, { app, titulo, detalle, gravedad, origen, tipo, seq, requiereAtencion, escalamientoS, leve });
    this.dispositivo = extra.dispositivo || null;
    this.creada = monotono();
    this.atendida = false;
    this.escalada = false;
    this.lblTitulo = el("b", { class: "al-titulo" }, titulo);
    this.boton = el("button", { type: "button", class: "btn al-boton", onclick: () => this.atender() }, requiereAtencion ? "Atender" : "Descartar");
    this.lblDetalle = el("div", { class: "al-detalle" }, detalle);
    this.lblEstado = el("span", { class: "al-estado" });
    this.lblGrav = el("span", { class: "al-grav" }, gravedad ? `Gravedad ${gravedad}` : "");
    this.elemento = el("div", { class: "alerta" }, el("div", { class: "barra" }),
      el("div", { class: "al-cuerpo" }, el("div", { class: "al-fila1" }, this.lblTitulo, this.boton), this.lblDetalle,
        el("div", { class: "al-fila3" }, this.lblEstado, this.lblGrav)));
    this.refrescar();
  }

  _pintar(fondo, barra, textoEstado = null) {
    this.elemento.style.setProperty("--fondo", fondo);
    this.elemento.style.setProperty("--barra", barra);
    if (textoEstado !== null) this.lblEstado.textContent = textoEstado;
  }

  segundos() { return monotono() - this.creada; }

  refrescar() {
    if (this.atendida) return;
    if (!this.requiereAtencion) { this._pintar(COLOR.WARN_BG, COLOR.WARN, "Aviso del sistema"); return; }
    const s = this.segundos();
    if (s >= this.escalamientoS) {
      if (!this.escalada) { this.escalada = true; this.app.alertaEscalada(this); }
      this._pintar("#F9D2D2", "#8E0000", `ESCALADA · sin atender ${formatoDuracion(s)}`);
    } else if (this.leve) {
      this._pintar(COLOR.WARN_BG, COLOR.WARN, `Pendiente · ${formatoDuracion(s)}`);
    } else {
      this._pintar(COLOR.ALERTA_BG, COLOR.ALERTA, `Sin atender · ${formatoDuracion(s)}`);
    }
  }

  atender() {
    if (this.atendida) return;
    if (this.requiereAtencion) dialogoAtencion(this.app, this);
    else { this.atendida = true; this.app.quitarAlerta(this); }
  }

  /** Cierra la alerta con el detalle manual. */
  cerrar(cierre) {
    if (this.atendida) return;
    this.atendida = true;
    const respuesta = this.segundos();
    this._pintar(COLOR.OK_BG, COLOR.OK, `Cerrada · ${cierre.resolucion} · ${respuesta.toFixed(0)} s`);
    const nota = cierre.accion_realizada;
    const resumen = nota.length <= 110 ? nota : nota.slice(0, 107) + "…";
    const extra = cierre.indicacion_medica && cierre.medico ? ` (indicó: ${cierre.medico})` : "";
    this.lblDetalle.textContent = `${cierre.atendido_por}${extra}: ${resumen}`;
    this.lblGrav.textContent = "";
    this.boton.remove();
    this.app.alertaCerrada(this, respuesta, cierre);
  }
}

// ----------------------------------------------------------------------
// Ventana principal
// ----------------------------------------------------------------------

export class Aplicacion {
  constructor(raiz, { SQL, almacen, bytesBase, intervaloSim = 2.0, autoiniciar = true }) {
    this.raiz = raiz;
    this.almacen = almacen;
    this.intervaloSim = intervaloSim;
    this.bd = new BaseDatos(SQL, bytesBase, () => this._guardarBase());
    this.bd.sembrarDemostracion();
    this._guardarBase();
    this.usuario = "";
    this.pacienteId = null;
    this.paciente = null;
    this.dispositivoEsperado = null;
    this.umbrales = structuredClone(UMBRALES);
    this.cfg = {};
    this._cargarConfig();
    this.sesionBd = null;
    this._tLatido = 0;
    this._ajenos = {};
    this._cortePendiente = null;
    this._nivelesVitales = {};
    this._causasVitales = [];
    this._nivelManual = {};
    this._causasManual = [];

    this.transporte = null;
    this.activo = false; // hay una fuente conectada (equivale a self.hilo en escritorio)
    this._conexionId = 0;
    this.registrador = null;
    this.sesion = new Sesion();
    this.fuenteActual = FUENTE_SIM;
    this.conexion = "desconectado";
    this._tUltimoPaquete = null;
    this._criticoActivo = { hr: false, spo2: false, temp: false, batt_baja: false, batt_critica: false };
    this.ultimoPersonal = "";
    this._huecos = new Map();
    this.tarjetasAlerta = [];
    this._ultimoEstado = null;
    this.ventana = 0;
    this.hub = null;
    this._audio = null;

    this._construir();
    this._retomarUltimoPaciente();
    this._alCambiarFuente();
    this._redibujar();
    this._revisarCortesPrevios();

    this._oyentes = [
      [document, "keydown", (e) => this._teclas(e)],
      [window, "resize", () => this._redibujar()],
      [window, "pagehide", () => this._alCerrar()],
      [document, "pointerdown", () => { try { this._contextoAudio().resume(); } catch { /* sin audio */ } }],
    ];
    this._oyentes.forEach(([obj, ev, fn]) => obj.addEventListener(ev, fn));
    this._intervalo = setInterval(() => this._tick(), 500);

    if (autoiniciar) {
      const ultima = this.bd.config("ultima_fuente", FUENTE_SIM);
      if (!this.pacienteId || !this.dispositivoEsperado) setTimeout(() => this._conectar(), 200); // explica qué falta
      else if (ultima === FUENTE_SIM || ultima === FUENTE_MQTT) setTimeout(() => this._conectar(), 200);
      else this.aviso("Pulse Conectar para buscar la pulsera por Bluetooth.");
    }
  }

  // ---- construcción de la interfaz -----------------------------------

  _construir() {
    // Encabezado
    this.selFuente = el("select", { class: "sel-fuente", onchange: () => this._alCambiarFuente() }, FUENTES.map((f) => el("option", { value: f }, f)));
    this.selFuente.value = FUENTES.includes(this.bd.config("ultima_fuente", "")) ? this.bd.config("ultima_fuente") : FUENTE_SIM;
    this.lblParam = el("label", { class: "lbl-param" });
    this.entParam = el("input", { type: "text", class: "ent-param", autocomplete: "off", spellcheck: false });
    this.btnConectar = el("button", { type: "button", class: "btn verde", onclick: () => this._alternarConexion() }, "Conectar");
    this.chipConexion = el("span", { class: "chip-conexion" });
    const encabezado = el("header", { class: "encabezado" },
      el("div", { class: "marca" }, el("b", {}, "VitalCare"), el("span", {}, "Testbed de pulsera · ITT466")),
      el("div", { class: "controles" }, el("label", {}, "Fuente de datos"), this.selFuente, this.lblParam, this.entParam, this.btnConectar, this.chipConexion));

    // Pestañas
    this.pestanas = {};
    const barraPest = el("nav", { class: "pestanas" });
    const paginas = el("div", { class: "paginas" });
    this.tabMon = el("section", { class: "pagina monitoreo" });
    this.tabPacientes = new PantallaPacientes(this);
    this.tabPulseras = new PantallaPulseras(this);
    this.tabConfig = new PantallaConfig(this);
    for (const [clave, texto, pag] of [["mon", "Monitoreo", this.tabMon], ["pac", "Pacientes", this.tabPacientes.elemento],
      ["pul", "Pulseras", this.tabPulseras.elemento], ["cfg", "Configuración", this.tabConfig.elemento]]) {
      const b = el("button", { type: "button", class: "pestana", onclick: () => this.seleccionarPestana(clave) }, texto);
      pag.classList.add("pagina");
      this.pestanas[clave] = [b, pag];
      barraPest.append(b);
      paginas.append(pag);
    }

    this._construirContexto();
    this._construirBanner();
    this._construirTarjetas();
    this._construirCuerpo();
    this._construirPie();
    this.raiz.replaceChildren(encabezado, barraPest, paginas);
    this.seleccionarPestana("mon");
    this.refrescarComboPacientes();
    this._pintarConexion();
  }

  seleccionarPestana(clave) {
    for (const [k, [b, pag]] of Object.entries(this.pestanas)) { b.classList.toggle("activa", k === clave); pag.classList.toggle("activa", k === clave); }
    if (clave === "pac") this.tabPacientes.refrescar();
    else if (clave === "pul") this.tabPulseras.refrescar();
    else if (clave === "cfg") this.tabConfig.refrescar();
    else requestAnimationFrame(() => this._redibujar());
  }

  _construirContexto() {
    this.selPaciente = el("select", { class: "sel-paciente", onchange: () => this._alElegirPaciente() });
    this.lblFicha = el("span", { class: "ficha" }, "Elija un paciente ingresado con pulsera asignada para ver sus signos.");
    this.lblAlergias = el("span", { class: "alergias" });
    this.lblPulsera = el("span", { class: "chip-pulsera" }, "Sin pulsera asignada");
    this.tabMon.append(el("div", { class: "tarjeta contexto" },
      el("div", { class: "ctx-izq" },
        el("div", { class: "ctx-fila" }, el("span", { class: "ctx-etq" }, "Paciente en monitoreo"), this.selPaciente),
        el("div", { class: "ctx-ficha" }, this.lblFicha, this.lblAlergias)),
      el("div", { class: "ctx-der" },
        boton("Cambiar pulsera", () => this.cambiarPulsera(), "claro"),
        boton("Signos a mano", () => this.registrarSignosManuales(), "claro"),
        this.lblPulsera)));
  }

  _construirBanner() {
    this.lblEstado = el("b", { class: "estado" }, "SIN DATOS");
    this.lblCausas = el("span", { class: "causas" }, "Esperando conexión con la fuente de datos");
    this.lblIntegridad = el("span", { class: "integridad" });
    this.banner = el("div", { class: "banner" }, el("div", { class: "barra" }), this.lblEstado, this.lblCausas, this.lblIntegridad);
    this.tabMon.append(this.banner);
  }

  _construirTarjetas() {
    const u = this.umbrales;
    const abrir = () => this.registrarSignosManuales();
    this.tarjetas = {
      hr: new TarjetaVital("FREC. CARDÍACA", u.hr.unidad, textoRango("hr", u)),
      spo2: new TarjetaVital("OXÍGENO EN SANGRE", "%", textoRango("spo2", u)),
      temp: new TarjetaVital("TEMPERATURA", u.temp.unidad, textoRango("temp", u)),
      pa: new TarjetaVital("PRESIÓN ARTERIAL", "mmHg", "la toma enfermería", { chico: true, alPulsar: abrir }),
      fr: new TarjetaVital("FREC. RESPIRATORIA", "rpm", "la toma enfermería", { chico: true, alPulsar: abrir }),
      batt: new TarjetaVital("BATERÍA", "%", `Aviso bajo ${g(this.cfg.bateria_baja)} %`),
    };
    this.tabMon.append(el("div", { class: "fila-tarjetas" }, ["hr", "spo2", "temp", "pa", "fr", "batt"].map((k) => this.tarjetas[k].elemento)));
  }

  _construirCuerpo() {
    this.segmentos = VENTANAS.map(([texto], i) => el("button", { type: "button", class: "seg", onclick: () => this._elegirVentana(i) }, texto));
    this.canvas = el("canvas", { class: "graficas" });
    this.graficas = new Graficas(this.canvas);
    const panelGraf = el("div", { class: "tarjeta panel-graficas" },
      el("div", { class: "cab-graficas" }, el("b", {}, "Tendencias en vivo"), el("div", { class: "flex1" }), el("span", { class: "muted" }, "Ventana:"),
        el("div", { class: "segmentado" }, this.segmentos)),
      el("div", { class: "zona-canvas" }, this.canvas));
    this._pintarSegmentos();

    this.lblTituloAlertas = el("b", {}, "Alertas");
    this.lblSinAlertas = el("p", { class: "sin-alertas" }, "Sin alertas por ahora");
    this.listaAlertas = el("div", { class: "lista-alertas" });
    const panelAlertas = el("div", { class: "tarjeta panel-alertas" }, el("div", { class: "cab-panel" }, this.lblTituloAlertas), this.lblSinAlertas, this.listaAlertas);

    this.lblEnlace = {};
    const cifra = (clave, texto) => { this.lblEnlace[clave] = el("b", {}, "--"); return el("div", { class: "cifra" }, el("span", {}, texto), this.lblEnlace[clave]); };
    const det = (clave, texto) => { this.lblEnlace[clave] = el("b", {}, "--"); return el("span", { class: "det" }, el("span", {}, texto + ":"), this.lblEnlace[clave]); };
    this.lblHub = el("div", { class: "linea-hub" });
    const panelEnlace = el("div", { class: "tarjeta panel-enlace" }, el("div", { class: "cab-panel" }, el("b", {}, "Enlace con la pulsera")),
      el("div", { class: "cifras" }, cifra("paquetes", "Recibidos"), cifra("recuperados", "Recuperados"), cifra("perdidos", "Perdidos reales")),
      el("div", { class: "detalles" }, det("ultimo", "Último"), el("i", {}, "·"), det("sensor", "Sensor"), el("i", {}, "·"), det("espera", "Recuperando")),
      this.lblHub);

    this.tabMon.append(el("div", { class: "cuerpo-mon" }, panelGraf, el("div", { class: "lateral" }, panelAlertas, panelEnlace)));
  }

  _construirPie() {
    this.selEscenario = el("select", { class: "sel-escenario", onchange: () => this._aplicarEscenario() },
      Object.keys(ESCENARIOS).map((n) => el("option", { value: n }, n)));
    this.btnRed = boton("Caída de red (F4)", () => this.simularCorteRed(), "claro");
    this.btnEnergia = boton("Corte de energía (F5)", () => this.simularCorteEnergia(), "claro");
    this.btnCaida = boton("Simular caída (F2)", () => this._demoEvento("FALL"), "naranja");
    this.btnSos = boton("Simular SOS (F3)", () => this._demoEvento("SOS"), "alerta");
    this.panelDemo = el("div", { class: "tarjeta panel-demo" },
      el("b", {}, "Modo demostración"), el("span", { class: "muted" }, "(solo simulador)"), el("span", { class: "muted etq-esc" }, "Escenario"),
      this.selEscenario, el("div", { class: "flex1" }), this.btnRed, this.btnEnergia, this.btnCaida, this.btnSos);

    this.lblCsv = el("span", { class: "lbl-csv" }, "Datos: sin sesión activa");
    this.lblAviso = el("span", { class: "lbl-aviso" });
    this.chkSonido = el("input", { type: "checkbox", checked: true, id: "chk-sonido" });
    this.tabMon.append(this.panelDemo, el("div", { class: "fila-archivos" }, this.lblCsv, this.lblAviso, el("div", { class: "flex1" }),
      el("label", { class: "chk", for: "chk-sonido" }, this.chkSonido, " Sonido de alerta"),
      boton("Resumen de sesión", () => this._mostrarResumen()),
      boton("Evidencia (F8)", () => this.capturarEvidencia(), "claro"),
      boton("Abrir carpeta de datos", () => this.abrirCarpeta(), "claro"),
      boton("Pantalla completa (F11)", () => this._alternarPantallaCompleta(), "claro")));
  }

  // ---- configuración -------------------------------------------------

  _guardarBase() {
    if (this._guardadoPendiente) return;
    this._guardadoPendiente = true;
    queueMicrotask(() => {
      this._guardadoPendiente = false;
      try { this.almacen.guardarBase(this.bd.exportar()); } catch (e) { console.error("No se pudo guardar la base", e); }
    });
  }

  _cargarConfig() {
    const c = this.bd;
    this.cfg = {
      escalamiento_s: c.config("escalamiento_s", ESCALAMIENTO_S),
      escalamiento_bateria_baja_s: c.config("escalamiento_bateria_baja_s", ESCALAMIENTO_BATERIA_BAJA_S),
      escalamiento_bateria_critica_s: c.config("escalamiento_bateria_critica_s", ESCALAMIENTO_BATERIA_CRITICA_S),
      bateria_baja: c.config("bateria_baja", BATERIA_BAJA),
      bateria_critica: c.config("bateria_critica", BATERIA_CRITICA),
      bateria_rearme: c.config("bateria_rearme", BATERIA_REARME),
      sin_senal_s: c.config("sin_senal_s", SIN_SENAL_S),
      plazo_recuperacion_s: c.config("plazo_recuperacion_s", PLAZO_RECUPERACION_S),
      reintento_s: c.config("reintento_s", REINTENTO_S),
      centro: c.config("centro", "Centro de salud"),
      mqtt_broker: String(c.config("mqtt_broker", "broker.hivemq.com")),
      mqtt_puerto_web: c.config("mqtt_puerto_web", 8884),
      mqtt_prefijo: String(c.config("mqtt_prefijo", "pucmm-pulsera-7f3a")),
      mqtt_usuario: String(c.config("mqtt_usuario", "") ?? ""),
      mqtt_clave: String(c.config("mqtt_clave", "") ?? ""),
    };
  }

  aplicarConfiguracion() {
    this._cargarConfig();
    this.tarjetas.batt.fijarPie(`Aviso a menos de ${g(this.cfg.bateria_baja)} %`);
    this.refrescarUmbrales();
    if (this.selFuente.value === FUENTE_MQTT && !this.activo) this.entParam.value = this.cfg.mqtt_prefijo;
    if (this.activo && this.fuenteActual === FUENTE_MQTT) this.aviso("Detenga y vuelva a conectar para usar los datos nuevos del broker.");
  }

  /** Arma los umbrales del paciente que se está monitoreando. */
  refrescarUmbrales() {
    const nuevos = structuredClone(UMBRALES);
    if (this.pacienteId) {
      for (const [signo, datos] of Object.entries(this.bd.umbralesDePaciente(this.pacienteId))) {
        const base = nuevos[signo] || {};
        const [nombre, unidad] = SIGNOS[signo];
        base.nombre ??= nombre;
        base.corto ??= nombre;
        base.unidad ??= unidad;
        base.normal = datos.normal;
        base.vigilancia = datos.vigilancia;
        base.origen = datos.origen;
        nuevos[signo] = base;
      }
    }
    this.umbrales = nuevos;
    if (this.tarjetas) {
      for (const clave of ["hr", "spo2", "temp"]) this.tarjetas[clave].fijarPie(textoRango(clave, this.umbrales));
      this._pintarSignosManuales();
    }
    this._redibujar();
  }

  // ---- paciente en monitoreo ------------------------------------------

  refrescarComboPacientes() {
    this._opcionesPaciente = new Map();
    const opciones = [el("option", { value: "" }, "Sin paciente seleccionado")];
    for (const p of this.bd.listarPacientes("", "Ingresado")) {
      opciones.push(el("option", { value: String(p.id) }, `${nombreCompleto(p)} · ${ubicacion(p)} · ${p.serie || "sin pulsera"}`));
    }
    this.selPaciente.replaceChildren(...opciones);
    this.selPaciente.value = this.pacienteId ? String(this.pacienteId) : "";
  }

  _alElegirPaciente() {
    const v = this.selPaciente.value;
    this.monitorearPaciente(v ? Number(v) : null);
  }

  /** Deja la pantalla de monitoreo apuntando a este paciente y su pulsera. */
  monitorearPaciente(pid) {
    const cambio = pid !== this.pacienteId;
    this.pacienteId = pid;
    this.bd.fijarInterno("ultimo_paciente", pid || "", "Paciente que se estaba monitoreando");
    this.paciente = pid ? this.bd.paciente(pid) : null;
    const asignacion = pid ? this.bd.asignacionDePaciente(pid) : null;
    this.dispositivoEsperado = asignacion ? asignacion.id_dispositivo : null;
    if (this.transporte instanceof MQTTTransport) this.transporte.idDestino = this.dispositivoEsperado;
    this._ajenos = {};
    this.refrescarUmbrales();
    this.refrescarComboPacientes();
    this._pintarFicha();
    this._pintarSignosManuales();
    this.seleccionarPestana("mon");
    if (pid && asignacion) {
      this.aviso(`Monitoreando a ${nombreCompleto(this.paciente)} con la pulsera ${asignacion.serie} (${asignacion.id_dispositivo}).`);
    } else if (pid) {
      this.aviso(`${nombreCompleto(this.paciente)} no tiene pulsera asignada: use Cambiar pulsera para asignarle una.`);
    }
    // Cada sesión es de un paciente: al cambiar de paciente se cierra la sesión y se abre otra.
    if (cambio && this.activo) {
      const fuente = this.fuenteActual;
      this._detener();
      if (pid && asignacion && fuente !== FUENTE_BLE) this._conectar();
    }
  }

  _retomarUltimoPaciente() {
    const guardado = this.bd.config("ultimo_paciente", "");
    if (!guardado) return;
    const pid = Number(guardado);
    if (!Number.isInteger(pid)) return;
    const p = this.bd.paciente(pid);
    if (p && p.estado === "Ingresado" && this.bd.asignacionDePaciente(pid)) this.monitorearPaciente(pid);
  }

  refrescarPacienteMonitoreado() {
    if (this.pacienteId) this.paciente = this.bd.paciente(this.pacienteId);
    this.refrescarComboPacientes();
    this._pintarFicha();
  }

  _pintarFicha() {
    if (!this.paciente) {
      this.lblFicha.textContent = "Sin paciente en monitoreo. Elija uno con pulsera asignada: los datos de una pulsera solo se muestran y se guardan si está puesta a un paciente.";
      this._chipPulsera("Sin paciente", COLOR.GRIS_BG, COLOR.TEXTO);
      this.lblAlergias.textContent = "";
      return;
    }
    const p = this.paciente;
    const alergias = (p.alergias || "").trim();
    this.lblFicha.textContent = `${textoEdad(p.fecha_nac)} · ${p.sexo.toLowerCase()} · perfil ${p.perfil} · ${p.diagnostico || "sin diagnóstico registrado"}`;
    this.lblAlergias.textContent = alergias && alergias.toLowerCase() !== "ninguna conocida" ? `alergias: ${alergias}` : "";
    const a = this.bd.asignacionDePaciente(p.id);
    if (a) {
      const bat = a.bateria === null ? "" : ` · ${Math.trunc(a.bateria)} %`;
      this._chipPulsera(`Pulsera ${a.serie} (${a.id_dispositivo})${bat}`, COLOR.OK_BG, COLOR.OK);
    } else {
      this._chipPulsera("Sin pulsera asignada", COLOR.WARN_BG, COLOR.WARN);
    }
  }

  _chipPulsera(texto, fondo, color) {
    this.lblPulsera.textContent = texto;
    this.lblPulsera.style.background = fondo;
    this.lblPulsera.style.color = color;
  }

  async cambiarPulsera() {
    if (!this.pacienteId) { this.aviso("Elija primero un paciente para cambiarle la pulsera."); return; }
    const r = await dialogoAsignar(this, this.bd.paciente(this.pacienteId));
    if (r) this.pulseraCambiada(this.pacienteId, r);
  }

  /**
   * Llamada al asignar, cambiar o liberar la pulsera de un paciente. La serie de datos
   * del paciente no se corta: se marca el cambio en la gráfica y se siguen recibiendo
   * los paquetes del dispositivo nuevo.
   */
  pulseraCambiada(pid, info) {
    if (pid !== this.pacienteId) { this.refrescarComboPacientes(); return; }
    if (info === null) {
      this.dispositivoEsperado = null;
      if (this.activo) this._detener(); // sin pulsera no hay de dónde recibir datos de este paciente
      this._pintarFicha();
      this.refrescarComboPacientes();
      return;
    }
    this.dispositivoEsperado = info.dispositivo;
    if (this.transporte instanceof MQTTTransport) this.transporte.idDestino = info.dispositivo;
    if (this.transporte instanceof SimuladorTransport) this.transporte.cambiarPulsera(info.dispositivo);
    this._ajenos = {};
    this.sesion.seq.reiniciarSecuencia();
    this._huecos = new Map();
    if (info.anterior) {
      this.sesion.marcarCambioPulsera(info.anterior, info.serie);
      this._nuevaAlerta("Pulsera cambiada",
        `${info.anterior} → ${info.serie} (${info.dispositivo}). Motivo: ${info.motivo.toLowerCase()}. El monitoreo del paciente continúa en la misma gráfica.`,
        "MEDIA", "SISTEMA", "CAMBIO_PULSERA", null, { requiereAtencion: false, leve: true });
    }
    this._pintarFicha();
    this.refrescarComboPacientes();
    this._redibujar();
  }

  pacienteDadoDeAlta(pid) {
    if (pid === this.pacienteId) this.monitorearPaciente(null);
    else this.refrescarComboPacientes();
  }

  // ---- signos tomados a mano -------------------------------------------

  async registrarSignosManuales() {
    if (!this.pacienteId) { this.aviso("Los signos a mano se registran sobre un paciente. Elija uno primero."); return; }
    const r = await dialogoMedicion(this, this.bd.paciente(this.pacienteId));
    if (r) this.signosManualesRegistrados(this.pacienteId, r);
  }

  /** Evalúa los signos que acaba de registrar enfermería contra el rango del paciente. */
  signosManualesRegistrados(pid, valores) {
    if (pid !== this.pacienteId) return;
    this._pintarSignosManuales();
    if (this.conexion === "conectado") this._actualizarEstadoGlobal();
    // La presión se juzga completa, no sístole y diástole por separado.
    if ("pas" in valores || "pad" in valores) {
      const ult = this.bd.ultimasMediciones(pid);
      const pas = valores.pas ?? (ult.pas ? ult.pas.valor : null);
      const pad = valores.pad ?? (ult.pad ? ult.pad.valor : null);
      if (pas !== null && pad !== null) {
        const [nS, dS] = clasificar("pas", pas, this.umbrales);
        const [nD, dD] = clasificar("pad", pad, this.umbrales);
        const niveles = [nS, nD].filter((n) => n !== null);
        const nivel = niveles.length ? Math.max(...niveles) : null;
        if (nivel !== null && nivel > NIVEL_NORMAL) {
          const partes = [];
          if (nS && nS > NIVEL_NORMAL) partes.push(`sistólica ${dS}`);
          if (nD && nD > NIVEL_NORMAL) partes.push(`diastólica ${dD}`);
          const uS = this.umbrales.pas, uD = this.umbrales.pad;
          this._nuevaAlerta("Presión arterial fuera de rango",
            `${g(pas)}/${g(pad)} mmHg (${partes.join(" y ")}). Rango normal del paciente: ${g(uS.normal[0])} a ${g(uS.normal[1])} sobre ` +
            `${g(uD.normal[0])} a ${g(uD.normal[1])} mmHg. Valor tomado a mano.`,
            nivel === NIVEL_ALERTA ? "ALTA" : "MEDIA", "MANUAL", "PA", null, { leve: nivel === NIVEL_VIGILANCIA });
        }
      }
    }
    if ("fr" in valores) {
      const [nivel, direccion] = clasificar("fr", valores.fr, this.umbrales);
      if (nivel !== null && nivel > NIVEL_NORMAL) {
        const u = this.umbrales.fr;
        this._nuevaAlerta("Frecuencia respiratoria fuera de rango",
          `${g(valores.fr)} rpm (${direccion}). Rango normal del paciente: ${g(u.normal[0])} a ${g(u.normal[1])} rpm. Valor tomado a mano.`,
          nivel === NIVEL_ALERTA ? "ALTA" : "MEDIA", "MANUAL", "FR", null, { leve: nivel === NIVEL_VIGILANCIA });
      }
    }
  }

  /** El estado del paciente junta lo que mide la pulsera y lo que tomó enfermería. */
  _actualizarEstadoGlobal() {
    const niveles = { ...this._nivelesVitales, ...this._nivelManual };
    const causas = [...this._causasVitales, ...this._causasManual];
    this._pintarBanner(estadoGlobal(Object.values(niveles)), causas);
  }

  _pintarSignosManuales() {
    if (!this.tarjetas) return;
    this._nivelManual = {};
    this._causasManual = [];
    if (!this.pacienteId) {
      for (const clave of ["pa", "fr"]) { this.tarjetas[clave].actualizar(null, null, "Sin datos"); this.tarjetas[clave].fijarPie("la toma enfermería"); }
      return;
    }
    const ult = this.bd.ultimasMediciones(this.pacienteId);
    const pas = ult.pas, pad = ult.pad;
    if (pas && pad) {
      const niveles = [clasificar("pas", pas.valor, this.umbrales)[0], clasificar("pad", pad.valor, this.umbrales)[0]].filter((n) => n !== null);
      const nivel = niveles.length ? Math.max(...niveles) : null;
      this.tarjetas.pa.actualizar(`${g(pas.valor)}/${g(pad.valor)}`, nivel, NOMBRE_NIVEL[nivel] || "Registrada");
      this.tarjetas.pa.fijarPie(`${pas.hora.slice(11, 16)} · ${pas.tomado_por || "sin registrar"}`);
      if (nivel !== null && nivel > NIVEL_NORMAL) {
        this._nivelManual.pa = nivel;
        this._causasManual.push(`Presión arterial ${g(pas.valor)}/${g(pad.valor)} mmHg`);
      }
    } else {
      this.tarjetas.pa.actualizar(null, null, "Sin tomar");
      this.tarjetas.pa.fijarPie("pulse aquí para registrarla");
    }
    const fr = ult.fr;
    if (fr) {
      const nivel = clasificar("fr", fr.valor, this.umbrales)[0];
      this.tarjetas.fr.actualizar(g(fr.valor), nivel, NOMBRE_NIVEL[nivel] || "Registrada");
      this.tarjetas.fr.fijarPie(`${fr.hora.slice(11, 16)} · ${fr.tomado_por || "sin registrar"}`);
      if (nivel !== null && nivel > NIVEL_NORMAL) {
        this._nivelManual.fr = nivel;
        this._causasManual.push(`Frecuencia respiratoria ${g(fr.valor)} rpm`);
      }
    } else {
      this.tarjetas.fr.actualizar(null, null, "Sin tomar");
      this.tarjetas.fr.fijarPie("pulse aquí para registrarla");
    }
  }

  // ---- avisos y evidencias ---------------------------------------------

  /** Mensaje breve en el pie de la ventana. */
  aviso(texto) {
    if (!this.lblAviso) return;
    this.lblAviso.textContent = texto;
    clearTimeout(this._tAviso);
    this._tAviso = setTimeout(() => { this.lblAviso.textContent = ""; }, 9000);
  }

  /** Guarda una imagen de la pantalla para los anexos del informe. */
  async capturarEvidencia(etiqueta = "") {
    const sufijo = etiqueta.replace(/[^A-Za-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "");
    const nombre = `evidencia_${marcaDe()}${sufijo ? "_" + sufijo : ""}.png`;
    try {
      if (typeof window.html2canvas !== "function") throw new Error("falta vendor/html2canvas.min.js");
      const lienzo = await window.html2canvas(document.body, { backgroundColor: COLOR.BG, scale: Math.min(2, window.devicePixelRatio || 1), logging: false,
        ignoreElements: (e) => e.tagName === "NOSCRIPT" || (e.tagName === "INPUT" && e.type === "file") });
      const blob = await new Promise((ok) => lienzo.toBlob(ok, "image/png"));
      this.almacen.guardarEvidencia(nombre, blob);
      descargar(nombre, blob);
    } catch (e) {
      this.aviso(`No se pudo capturar la pantalla (${e.message || e}). Use la tecla Impr Pant.`);
      return null;
    }
    this.bd.auditar(this.usuario, "Captura de evidencia", "sistema", null, nombre);
    this.aviso(`Evidencia guardada: ${nombre} (en Descargas y en la carpeta de datos).`);
    return nombre;
  }

  // ---- escenarios críticos ---------------------------------------------

  /**
   * Al arrancar: si quedó una sesión abierta, la página se cerró de golpe (corte de
   * energía, apagón del equipo o cierre forzado del navegador). Se avisa, se dice hasta
   * qué hora hay datos y la alerta queda pendiente de reconocer.
   */
  _revisarCortesPrevios() {
    let limpias = {};
    if (!this.almacen.enMemoria) try { limpias = JSON.parse(localStorage.getItem("vitalcare-cierres-limpios") || "{}"); } catch { limpias = {}; }
    const abiertas = this.bd.sesionesSinCerrar();
    for (const s of abiertas) {
      if (limpias[s.id]) { // la página se cerró bien, pero no alcanzó a guardarlo en la base
        this.bd.ejec("UPDATE sesiones SET fin=?, cierre_limpio=1 WHERE id=?", [limpias[s.id], s.id]);
        continue;
      }
      const quien = s.nombre ? `${s.nombre} ${s.apellidos}` : "sin paciente asociado";
      const detalle = `La sesión del ${s.inicio} (${quien}, pulsera ${s.serie || "--"}) no se cerró bien. ` +
        `Hay datos guardados hasta las ${(s.latido || s.inicio).slice(11, 19)}. Revise ese intervalo antes de continuar.`;
      const eid = this.bd.registrarEventoSistema("CORTE_DETECTADO", detalle, s.id);
      this.bd.cerrarSesion(s.id, false);
      this._nuevaAlerta("Corte detectado en la sesión anterior", detalle, "ALTA", "SISTEMA", "CORTE", null);
      this._cortePendiente = eid;
    }
    if (!this.almacen.enMemoria) try { localStorage.removeItem("vitalcare-cierres-limpios"); } catch { /* sin almacenamiento */ }
  }

  /** Deja de llegar telemetría durante unos segundos, como en una caída de red. */
  simularCorteRed(segundos = 12) {
    if (!this.transporte || typeof this.transporte.cortar !== "function") {
      this.aviso("La simulación de caída de red funciona con la fuente Simulador.");
      return;
    }
    this.transporte.cortar(segundos);
    this.bd.registrarEventoSistema("ENLACE_CAIDO", `Caída de red simulada de ${segundos} s`, this.sesionBd);
    this._nuevaAlerta("Caída de red simulada",
      `Se interrumpió el enlace por ${segundos} s. Los paquetes de ese intervalo quedan marcados como faltantes y se piden de vuelta al restablecerse.`,
      "MEDIA", "SISTEMA", "SIMULACION", null, { requiereAtencion: false, leve: true });
    this.aviso(`Enlace interrumpido por ${segundos} s. Observe el indicador de integridad.`);
  }

  /**
   * Reproduce lo que ocurre cuando se va la luz: la sesión queda abierta, los datos
   * escritos hasta ese instante permanecen guardados y al volver el sistema lo avisa.
   */
  simularCorteEnergia() {
    if (this.sesionBd === null) { this.aviso("Conecte una sesión antes de simular el corte de energía."); return; }
    const sid = this.sesionBd;
    this.bd.latidoSesion(sid);
    const corte = this.bd.q1("SELECT latido FROM sesiones WHERE id=?", [sid]).latido;
    // La sesión se deja ABIERTA a propósito: eso es lo que ocurre cuando se va la luz.
    this.sesionBd = null;
    this._detener();
    const detalle = `Corte de energía simulado. La sesión quedó abierta con datos hasta las ${corte.slice(11, 19)}. ` +
      "Los archivos de la sesión conservan todo lo recibido hasta ese instante porque cada fila se guarda al llegar.";
    const eid = this.bd.registrarEventoSistema("CORTE_ENERGIA", detalle, sid);
    this._cortePendiente = eid;
    this._nuevaAlerta("Corte de energía", detalle, "ALTA", "SISTEMA", "CORTE", null);
    this.aviso("Corte simulado. Pulse Conectar para reanudar el monitoreo del paciente.");
  }

  _alCambiarFuente() {
    const fuente = this.selFuente.value;
    const mostrar = fuente !== FUENTE_SIM;
    this.lblParam.hidden = !mostrar;
    this.entParam.hidden = !mostrar;
    if (fuente === FUENTE_BLE) { this.lblParam.textContent = "Nombre de la pulsera"; this.entParam.value = "W01"; this.entParam.style.width = "90px"; }
    else if (fuente === FUENTE_MQTT) { this.lblParam.textContent = "Prefijo"; this.entParam.value = this.cfg.mqtt_prefijo; this.entParam.style.width = "170px"; }
    else this.entParam.value = "";
    this._estadoDemo();
  }

  _estadoDemo() {
    const activo = this.fuenteActual === FUENTE_SIM && this.activo;
    this.btnCaida.disabled = !activo;
    this.btnSos.disabled = !activo;
    this.selEscenario.disabled = !activo;
  }

  _crearTransporte(fuente, param) {
    if (fuente === FUENTE_SIM) return new SimuladorTransport({ deviceId: this.dispositivoEsperado || "W01", intervaloS: this.intervaloSim });
    if (fuente === FUENTE_BLE) return new BLETransport(param.trim() || "W01");
    const prefijo = param.trim() || this.cfg.mqtt_prefijo;
    if (!prefijo) throw new Error("Escriba el prefijo de los tópicos del hub (por ejemplo pucmm-pulsera-7f3a).");
    const t = new MQTTTransport({ broker: this.cfg.mqtt_broker, puerto: this.cfg.mqtt_puerto_web, prefijo,
      usuario: this.cfg.mqtt_usuario, clave: this.cfg.mqtt_clave, alHub: (info) => this._alHub(info) });
    t.idDestino = this.dispositivoEsperado;
    return t;
  }

  _alternarConexion() {
    if (!this.activo) this._conectar();
    else this._detener();
  }

  _conectar() {
    if (this.activo) return;
    // Como en un hospital: primero se ingresa al paciente y se le pone la pulsera; después se monitorea.
    if (!this.pacienteId || !this.dispositivoEsperado) {
      const texto = !this.pacienteId ? "Elija primero al paciente en monitoreo."
        : "Este paciente no tiene pulsera asignada: use Cambiar pulsera para asignarle una.";
      this.aviso(texto);
      this._bannerObsoleto(!this.pacienteId ? "SIN PACIENTE" : "SIN PULSERA ASIGNADA");
      this.lblCausas.textContent = texto;
      return;
    }
    const fuente = this.selFuente.value;
    let transporte;
    try {
      transporte = this._crearTransporte(fuente, this.entParam.value);
    } catch (e) {
      this._cambiarConexion("error");
      this._nuevaAlerta("Configuración no válida", e.message, null, "SISTEMA", "CONFIG", null, { requiereAtencion: false });
      return;
    }
    // Nueva sesión: gráficas, contadores y alertas empiezan de cero.
    this._cerrarRegistro();
    this.sesion = new Sesion();
    this.registrador = new RegistradorCSV(this.almacen);
    this.lblCsv.textContent = `Guardando en: datos_sesion/${this.registrador.rutaTelemetria}`;
    const asignacion = this.pacienteId ? this.bd.asignacionDePaciente(this.pacienteId) : null;
    this.sesionBd = this.bd.abrirSesion(this.pacienteId, asignacion ? asignacion.pulsera_id : null, fuente, this.registrador.marca);
    this.almacen.registrarSesion({ marca: this.registrador.marca, inicio: ahoraS(), fuente,
      paciente: this.paciente ? nombreCompleto(this.paciente) : "modo libre", dispositivo: this.dispositivoEsperado || "", sesionId: this.sesionBd });
    this.bd.fijarInterno("ultima_fuente", fuente, "Última fuente de datos usada");
    // Las alertas que siguen sin atender no desaparecen porque se reinicie el monitoreo:
    // se arrastran y se vuelven a anotar en la bitácora de la sesión nueva.
    let arrastradas = 0;
    for (const t of [...this.tarjetasAlerta]) {
      if (t.requiereAtencion && !t.atendida) {
        arrastradas += 1;
        this.registrador.registrarAlerta("GENERADA", t.origen, t.tipo, `${t.detalle} (pendiente de la sesión anterior)`, t.seq);
        continue;
      }
      t.elemento.remove();
      this.tarjetasAlerta.splice(this.tarjetasAlerta.indexOf(t), 1);
    }
    if (!arrastradas) Object.keys(this._criticoActivo).forEach((k) => { this._criticoActivo[k] = false; });
    this._huecos = new Map();
    this._ultimoEstado = null;
    this._tUltimoPaquete = null;
    this.hub = null;
    this.fuenteActual = fuente;
    this.transporte = transporte;
    this.activo = true;
    const id = ++this._conexionId;
    const vigente = (fn) => (p) => { if (id === this._conexionId && this.activo) fn(p); };
    // Se llama sin esperar a nada: Bluetooth solo deja buscar la pulsera dentro del clic.
    transporte.recibirPaquetes(vigente((p) => this._manejarTelemetria(p)), vigente((p) => this._manejarEvento(p)))
      .then(() => { if (id === this._conexionId && this.activo) this._manejarFin(); })
      .catch((e) => { if (id === this._conexionId && this.activo) this._manejarError(e.message || String(e)); });
    this.selFuente.disabled = true;
    this.entParam.disabled = true;
    this.btnConectar.textContent = "Detener";
    this.btnConectar.className = "btn alerta";
    this._cambiarConexion("conectando");
    this._limpiarEstadoVisual();
    if (fuente === FUENTE_MQTT) this.lblCausas.textContent = `Conectando con el broker ${this.cfg.mqtt_broker} · esperando los datos del hub`;
    if (fuente === FUENTE_BLE) this.lblCausas.textContent = "Buscando la pulsera por Bluetooth: elíjala en la ventana del navegador";
    this._estadoDemo();
    this._refrescarVacio();
    this._actualizarTituloAlertas();
    this._pintarHub();
    if (arrastradas) this.aviso(`${arrastradas} alerta(s) sin atender siguen abiertas de la sesión anterior.`);
    this._redibujar();
  }

  _detener() {
    if (this.sesionBd !== null) { this.bd.cerrarSesion(this.sesionBd, true); this.sesionBd = null; }
    if (this.transporte) this.transporte.desconectar();
    this.activo = false;
    this._conexionId += 1;
    this.sesion.cerrar();
    this._cerrarRegistro(true);
    this._volverABotonConectar();
    this._cambiarConexion("desconectado");
    this._bannerObsoleto("SESIÓN DETENIDA");
    this._estadoDemo();
    this._actualizarEnlace();
  }

  _volverABotonConectar() {
    this.selFuente.disabled = false;
    this.entParam.disabled = false;
    this._alCambiarFuente();
    this.btnConectar.textContent = "Conectar";
    this.btnConectar.className = "btn verde";
  }

  _cerrarRegistro(conservarRuta = false) {
    if (this.registrador) {
      this.registrador.cerrar();
      if (conservarRuta) this.lblCsv.textContent = `Datos guardados en: datos_sesion/${this.registrador.rutaTelemetria}`;
    } else if (!conservarRuta) {
      this.lblCsv.textContent = "Datos: sin sesión activa";
    }
  }

  _cambiarConexion(estado) { this.conexion = estado; this._pintarConexion(); }

  _pintarConexion() {
    const [fondo, texto, etiqueta] = CONEXION[this.conexion];
    this.chipConexion.textContent = etiqueta;
    this.chipConexion.style.background = fondo;
    this.chipConexion.style.color = texto;
  }

  // ---- hub (solo MQTT) ---------------------------------------------------

  _alHub(info) {
    this.hub = { ...(this.hub || {}) };
    if (info.tipo === "broker") this.hub.broker = info.texto;
    if (info.tipo === "estado") {
      const antes = this.hub.estado;
      this.hub.estado = info.texto === "online" ? "en línea" : "apagado";
      this.hub.id = info.hub;
      if (antes && antes !== this.hub.estado) this.aviso(`El hub ${info.hub} está ${this.hub.estado}.`);
    }
    if (info.tipo === "info") { this.hub.info = info.datos; this.hub.id = info.hub; }
    this._pintarHub();
  }

  _pintarHub() {
    if (this.fuenteActual !== FUENTE_MQTT || !this.activo) { this.lblHub.textContent = ""; this.lblHub.hidden = true; return; }
    const h = this.hub || {};
    const partes = [`Broker: ${h.broker || "conectando"}`, `Hub${h.id ? " " + h.id : ""}: ${h.estado || "sin noticias"}`];
    if (h.info) {
      partes.push(h.info.pulsera === "conectada" ? `pulsera conectada · ${h.info.pendientes ?? 0} en cola` : "buscando la pulsera");
    }
    this.lblHub.hidden = false;
    this.lblHub.textContent = partes.join(" · ");
    this.lblHub.classList.toggle("mal", h.estado === "apagado" || h.broker === "sin conexión");
  }

  // ---- bucle de actualización ------------------------------------------

  /** Dos veces por segundo: relojes, enlace y escalamiento de alertas. */
  _tick() {
    if (this.sesionBd !== null && monotono() - this._tLatido > 10) {
      this._tLatido = monotono();
      this.bd.latidoSesion(this.sesionBd);
    }
    this._vigilarHuecos();
    this._actualizarEnlace();
    for (const t of [...this.tarjetasAlerta]) t.refrescar();
    if (this.activo && this._tUltimoPaquete !== null) {
      const sinPaquetes = monotono() - this._tUltimoPaquete;
      if (sinPaquetes > this.cfg.sin_senal_s && this.conexion === "conectado") {
        this._cambiarConexion("sin_senal");
        this._bannerObsoleto("SIN SEÑAL", true);
        this._nuevaAlerta("Sin señal de la pulsera", `No llegan paquetes hace más de ${Number(this.cfg.sin_senal_s).toFixed(0)} s.`, null,
          "SISTEMA", "SIN_SENAL", null, { requiereAtencion: false });
      }
    }
  }

  _manejarTelemetria(p) {
    if (!this._esDelPaciente(p)) return;
    if (p.id) this.bd.registrarBateria(String(p.id), aNumero(p.batt));
    if (this.registrador) this.registrador.registrarTelemetria(p);
    this.sesion.agregarTelemetria(p);
    this._revisarIntegridad();
    this._tUltimoPaquete = monotono();
    if (this.conexion === "conectando" || this.conexion === "sin_senal") this._cambiarConexion("conectado");

    const niveles = {};
    const causas = [];
    for (const variable of ["hr", "spo2", "temp"]) {
      const valor = aNumero(p[variable]);
      const [nivel, direccion] = clasificar(variable, valor, this.umbrales);
      niveles[variable] = nivel;
      const u = this.umbrales[variable];
      if (valor === null) { this.tarjetas[variable].actualizar(null, null, "Sin lectura"); continue; }
      const texto = variable === "temp" ? valor.toFixed(1) : valor.toFixed(0);
      const chip = nivel === NIVEL_NORMAL ? NOMBRE_NIVEL[nivel] : `${NOMBRE_NIVEL[nivel]} · ${direccion}`;
      this.tarjetas[variable].actualizar(texto, nivel, chip);
      if (nivel && nivel > NIVEL_NORMAL) causas.push(`${u.corto} ${texto} ${u.unidad} (${direccion})`);
      this._evaluarCritico(variable, nivel, direccion, texto, p.seq);
    }

    const bateria = aNumero(p.batt);
    const nb = nivelBateria(bateria, this.cfg.bateria_baja, this.cfg.bateria_critica);
    if (bateria === null) {
      this.tarjetas.batt.actualizar(null, null, "Sin lectura");
    } else {
      this.tarjetas.batt.actualizar(bateria.toFixed(0), nb, { 0: "Carga OK", 1: "Baja", 2: "Crítica" }[nb]);
      const autonomia = this.sesion.autonomiaMin();
      let pie;
      if (autonomia !== null) pie = `Autonomía estimada: ${formatoAutonomia(autonomia)}`;
      else if (this.sesion.paquetesTelemetria < 8) pie = "Autonomía: calculando…";
      else pie = "Sin descarga medible";
      this.tarjetas.batt.fijarPie(pie);
      this._evaluarBateria(bateria, autonomia, p.seq);
    }

    this._nivelesVitales = niveles;
    this._causasVitales = causas;
    this._actualizarEstadoGlobal();
    this._redibujar();
    this._actualizarEnlace();
    if (p.id && this.paciente) this._pintarFichaBateria(bateria);
  }

  _pintarFichaBateria(bateria) {
    // La batería del chip de la pulsera se actualiza sin volver a leer toda la ficha en cada paquete.
    if (bateria === null || !this.dispositivoEsperado) return;
    const t = this.lblPulsera.textContent.replace(/ · \d+ %$/, "");
    if (t.startsWith("Pulsera ")) this.lblPulsera.textContent = `${t} · ${Math.trunc(bateria)} %`;
  }

  /**
   * Con un paciente seleccionado, solo se aceptan los paquetes de la pulsera que tiene
   * asignada. Si llega otra, se avisa en lugar de mezclar datos de dos personas.
   */
  _esDelPaciente(p) {
    const ident = String(p.id ?? "").trim();
    if (!this.dispositivoEsperado) {
      if (ident && !this._ajenos[ident]) {
        this._ajenos[ident] = true;
        const duena = this.bd.pacientePorDispositivo(ident);
        const deQuien = duena ? `, asignada a ${duena.nombre} ${duena.apellidos}` : ", que no está asignada a ningún paciente";
        this._nuevaAlerta("Pulsera transmitiendo sin paciente en monitoreo",
          `Llegan datos de la pulsera ${ident}${deQuien}. No se muestran ni se guardan hasta que se elija al paciente y tenga esa pulsera asignada.`,
          "MEDIA", "SISTEMA", "PULSERA_SIN_PACIENTE", null, { requiereAtencion: false, leve: true });
      }
      return false;
    }
    if (ident === this.dispositivoEsperado) return true;
    if (ident && !this._ajenos[ident]) {
      this._ajenos[ident] = true;
      const duena = this.bd.pacientePorDispositivo(ident);
      const deQuien = duena ? ` Esa pulsera está asignada a ${duena.nombre} ${duena.apellidos}.` : " Esa pulsera no está asignada a ningún paciente.";
      this._nuevaAlerta("Llegan datos de otra pulsera",
        `Se reciben paquetes del dispositivo ${ident}, pero este paciente tiene asignada la ${this.dispositivoEsperado}.${deQuien} ` +
        "Los datos ajenos se descartan para no mezclar información de dos pacientes.", "ALTA", "SISTEMA", "DISPOSITIVO_AJENO", null);
    }
    return false;
  }

  /** Genera una alerta cuando un signo cruza a nivel Alerta (una vez por episodio). */
  _evaluarCritico(variable, nivel, direccion, texto, seq) {
    if (nivel === NIVEL_ALERTA && !this._criticoActivo[variable]) {
      this._criticoActivo[variable] = true;
      const u = this.umbrales[variable];
      this._nuevaAlerta(tituloAlerta(variable, direccion), `${u.nombre}: ${texto} ${u.unidad} fuera del límite seguro.`,
        "ALTA", "UMBRAL", variable.toUpperCase(), seq ?? null);
    } else if (nivel !== null && nivel < NIVEL_ALERTA) {
      this._criticoActivo[variable] = false;
    }
  }

  /**
   * Dos avisos para cambiar la pulsera ANTES de que se apague:
   *   - batería baja (30 % o menos): aviso preventivo, hay que programar el cambio;
   *   - batería crítica (15 % o menos): hay que cambiarla ya.
   * Se rearman cuando la batería vuelve a subir de 40 % (pulsera cambiada o recargada).
   */
  _evaluarBateria(bateria, autonomia, seq) {
    const act = this._criticoActivo;
    if (bateria >= this.cfg.bateria_rearme) { act.batt_baja = act.batt_critica = false; return; }
    const resto = autonomia !== null ? `Autonomía estimada ${formatoAutonomia(autonomia)}. ` : "";
    if (bateria <= this.cfg.bateria_critica && !act.batt_critica) {
      act.batt_critica = act.batt_baja = true;
      for (const t of [...this.tarjetasAlerta]) { // el aviso preventivo queda superado por el crítico
        if (t.tipo === "BATERIA_BAJA" && !t.atendida) {
          t.cerrar({ resolucion: "Superada por alerta crítica", atendido_por: "SISTEMA",
            accion_realizada: "Cierre automático: la batería llegó al nivel crítico.", indicacion_medica: false, medico: "" });
        }
      }
      this._nuevaAlerta("Batería crítica: cambiar la pulsera ya", `Queda ${bateria.toFixed(0)} %. ${resto}Reemplazarla antes de que se apague.`,
        "ALTA", "UMBRAL", "BATERIA_CRITICA", seq ?? null, { escalamientoS: this.cfg.escalamiento_bateria_critica_s });
    } else if (bateria <= this.cfg.bateria_baja && !act.batt_baja) {
      act.batt_baja = true;
      this._nuevaAlerta("Batería baja: programar el cambio", `Queda ${bateria.toFixed(0)} %. ${resto}Preparar una pulsera cargada.`,
        "MEDIA", "UMBRAL", "BATERIA_BAJA", seq ?? null, { escalamientoS: this.cfg.escalamiento_bateria_baja_s, leve: true });
    }
  }

  _manejarEvento(p) {
    if (!this._esDelPaciente(p)) return;
    if (this.registrador) this.registrador.registrarEvento(p);
    this.sesion.agregarEvento(p, ahoraS());
    this._revisarIntegridad();
    this._tUltimoPaquete = monotono();
    if (this.conexion === "conectando" || this.conexion === "sin_senal") this._cambiarConexion("conectado");
    const nombre = String(p.ev);
    const titulo = { FALL: "Caída detectada", SOS: "Botón SOS presionado" }[nombre] || `Evento ${nombre}`;
    const detalle = `Dispositivo ${p.id ?? "--"} · seq ${p.seq ?? "--"} · ${horaCorta()}`;
    this._nuevaAlerta(titulo, detalle, etiquetaSeveridad(p.sev), "DISPOSITIVO", nombre, p.seq ?? null, { dispositivo: p.id || null });
    this._redibujar();
    this._actualizarEnlace();
  }

  _manejarError(mensajeError) {
    this.activo = false;
    this._conexionId += 1;
    if (this.sesionBd !== null) { this.bd.cerrarSesion(this.sesionBd, true); this.sesionBd = null; }
    this.sesion.cerrar();
    this._cerrarRegistro(true);
    this._volverABotonConectar();
    this._cambiarConexion("error");
    this._bannerObsoleto("SIN CONEXIÓN", true);
    this._estadoDemo();
    this._pintarHub();
    this._nuevaAlerta("Error de conexión", mensajeError, null, "SISTEMA", "ERROR", null, { requiereAtencion: false });
  }

  /** El transporte terminó solo (por ejemplo, la pulsera se desconectó). */
  _manejarFin() {
    this.activo = false;
    this._conexionId += 1;
    if (this.sesionBd !== null) { this.bd.cerrarSesion(this.sesionBd, true); this.sesionBd = null; }
    this.sesion.cerrar();
    this._cerrarRegistro(true);
    this._volverABotonConectar();
    this._cambiarConexion("desconectado");
    this._bannerObsoleto("CONEXIÓN FINALIZADA", true);
    this._estadoDemo();
    this._pintarHub();
    this._nuevaAlerta("Conexión finalizada", "La fuente de datos se desconectó.", null, "SISTEMA", "FIN", null, { requiereAtencion: false });
  }

  // ---- alertas ----------------------------------------------------------

  _nuevaAlerta(titulo, detalle, gravedad, origen, tipo, seq, { requiereAtencion = true, escalamientoS = null, leve = false, dispositivo = null } = {}) {
    escalamientoS = escalamientoS || this.cfg.escalamiento_s;
    this.lblSinAlertas.hidden = true;
    const tarjeta = new TarjetaAlerta(this, titulo, detalle, gravedad, origen, tipo, seq, requiereAtencion, escalamientoS, leve, { dispositivo });
    this.listaAlertas.prepend(tarjeta.elemento);
    this.tarjetasAlerta.unshift(tarjeta);
    this.listaAlertas.scrollTop = 0;
    // Si hay demasiadas, se retiran primero las ya atendidas o los avisos del sistema;
    // una alerta pendiente nunca se oculta sola.
    while (this.tarjetasAlerta.length > MAX_TARJETAS_ALERTA) {
      const candidata = [...this.tarjetasAlerta].reverse().find((t) => t.atendida || !t.requiereAtencion);
      if (!candidata) break;
      this.tarjetasAlerta.splice(this.tarjetasAlerta.indexOf(candidata), 1);
      candidata.elemento.remove();
    }
    if (requiereAtencion) { this.sesion.alertaGenerada(); this._sonar(); }
    if (this.registrador) this.registrador.registrarAlerta("GENERADA", origen, tipo, detalle, seq);
    this._actualizarTituloAlertas();
  }

  alertaCerrada(tarjeta, respuestaS, cierre) {
    this.sesion.alertaAtendida(respuestaS, cierre.resolucion);
    if (this.registrador) this.registrador.registrarAlerta("CERRADA", tarjeta.origen, tarjeta.tipo, tarjeta.detalle, tarjeta.seq, respuestaS, cierre);
    this._actualizarTituloAlertas();
    // Con el hub, al atender una caída o un SOS se avisa a la pulsera: "AYUDA EN CAMINO" en su pantalla.
    if ((tarjeta.tipo === "FALL" || tarjeta.tipo === "SOS") && this.activo && this.transporte instanceof MQTTTransport) {
      const id = tarjeta.dispositivo || this.dispositivoEsperado || this.transporte.ultimoId;
      this.transporte.enviarComando(id, { cmd: "ack", ev: tarjeta.tipo, seq: tarjeta.seq }).then((ok) => {
        this.aviso(ok ? `Aviso enviado a la pulsera ${id}: ayuda en camino.` : "No se pudo avisar a la pulsera (sin conexión con el broker).");
      });
    }
  }

  alertaEscalada(tarjeta) {
    if (this.registrador) this.registrador.registrarAlerta("ESCALADA", tarjeta.origen, tarjeta.tipo, tarjeta.detalle, tarjeta.seq, tarjeta.segundos());
    this._sonar();
  }

  quitarAlerta(tarjeta) {
    const i = this.tarjetasAlerta.indexOf(tarjeta);
    if (i >= 0) this.tarjetasAlerta.splice(i, 1);
    tarjeta.elemento.remove();
    if (!this.tarjetasAlerta.length) this.lblSinAlertas.hidden = false;
    this._actualizarTituloAlertas();
  }

  _contextoAudio() {
    if (!this._audio) this._audio = new (window.AudioContext || window.webkitAudioContext)();
    return this._audio;
  }

  _sonar() {
    if (!this.chkSonido.checked) return;
    try {
      const a = this._contextoAudio();
      [0, 0.21].forEach((dt) => {
        const o = a.createOscillator(), v = a.createGain();
        o.frequency.value = 1000; v.gain.value = 0.15;
        o.connect(v); v.connect(a.destination);
        o.start(a.currentTime + dt); o.stop(a.currentTime + dt + 0.16);
      });
    } catch { /* el sonido nunca debe tumbar la aplicación */ }
  }

  // ---- demostración -----------------------------------------------------

  _demoEvento(tipo) {
    if (this.fuenteActual === FUENTE_SIM && this.activo && this.transporte && typeof this.transporte.dispararEvento === "function") {
      this.transporte.dispararEvento(tipo);
    }
  }

  _aplicarEscenario() {
    if (this.fuenteActual === FUENTE_SIM && this.transporte && typeof this.transporte.fijarEscenario === "function") {
      this.transporte.fijarEscenario(this.selEscenario.value);
    }
  }

  // ---- vista --------------------------------------------------------------

  _pintarBanner(nivel, causas) {
    const [fuerte, suave] = colorNivel(nivel);
    const texto = ESTADO_PACIENTE[nivel] || "SIN DATOS";
    let detalle;
    if (nivel === NIVEL_NORMAL) detalle = "Todos los signos vitales dentro del rango normal";
    else if (causas.length) detalle = causas.join("  ·  ");
    else detalle = "Esperando lecturas válidas";
    this.banner.style.setProperty("--fondo", suave);
    this.banner.style.setProperty("--fuerte", fuerte);
    this.lblEstado.textContent = nivel !== null && nivel !== undefined ? `PACIENTE ${texto}` : texto;
    this.lblCausas.textContent = detalle;
    this.lblCausas.style.color = nivel ? COLOR.TEXTO : COLOR.MUTED;
    this._ultimoEstado = nivel;
  }

  /** El estado del paciente deja de ser en vivo: se muestra como último dato conocido. */
  _bannerObsoleto(titulo, sinSenal = false) {
    const [fuerte, suave] = sinSenal ? [COLOR.WARN, COLOR.WARN_BG] : [COLOR.GRIS, COLOR.GRIS_BG];
    const ultimo = ESTADO_PACIENTE[this._ultimoEstado];
    this.banner.style.setProperty("--fondo", suave);
    this.banner.style.setProperty("--fuerte", fuerte);
    this.lblEstado.textContent = titulo;
    this.lblCausas.textContent = ultimo ? `Último estado recibido: ${ultimo}` : "Sin lecturas recibidas";
    this.lblCausas.style.color = COLOR.MUTED;
  }

  _limpiarEstadoVisual() {
    this._pintarBanner(null, []);
    this.lblCausas.textContent = "Conectando con la fuente de datos…";
  }

  /** Deja en blanco lo que mide la pulsera; los signos tomados a mano se conservan. */
  _refrescarVacio() {
    for (const clave of ["hr", "spo2", "temp", "batt"]) this.tarjetas[clave].actualizar(null, null, "Sin datos");
    this._pintarSignosManuales();
    if (!this.tarjetasAlerta.length) this.lblSinAlertas.hidden = false;
  }

  _actualizarTituloAlertas() {
    const pendientes = this.tarjetasAlerta.filter((t) => t.requiereAtencion && !t.atendida).length;
    this.lblTituloAlertas.textContent = pendientes ? `Alertas · ${pendientes} sin atender` : "Alertas";
    this.lblTituloAlertas.style.color = pendientes ? COLOR.ALERTA : COLOR.TEXTO;
  }

  _actualizarEnlace() {
    this._actualizarTituloAlertas();
    const sq = this.sesion.seq;
    const L = this.lblEnlace;
    L.paquetes.textContent = String(sq.recibidos);
    L.recuperados.textContent = String(sq.recuperados);
    L.recuperados.style.color = sq.recuperados ? COLOR.OK : COLOR.TEXTO;
    L.perdidos.textContent = String(sq.irrecuperables.size);
    L.perdidos.style.color = sq.irrecuperables.size ? COLOR.ALERTA : COLOR.TEXTO;
    L.espera.textContent = String(sq.faltantes.size);
    L.espera.style.color = sq.faltantes.size ? COLOR.WARN : COLOR.TEXTO;
    L.ultimo.textContent = this._tUltimoPaquete === null ? "--" : `hace ${(monotono() - this._tUltimoPaquete).toFixed(0)} s`;
    const st = this.sesion.estadoSensor;
    L.sensor.textContent = st ? String(st) : "--";
    L.sensor.style.color = st === null || st === undefined || st === "OK" ? COLOR.TEXTO : COLOR.ALERTA;
    // Integridad: la pregunta clave es "¿tengo todos los datos?"
    const I = this.lblIntegridad;
    if (sq.recibidos === 0) { I.textContent = ""; }
    else if (sq.irrecuperables.size) { I.textContent = `Integridad de datos: PÉRDIDA REAL (${sq.irrecuperables.size})`; I.style.color = COLOR.ALERTA; }
    else if (sq.faltantes.size) { I.textContent = `Integridad de datos: recuperando ${sq.faltantes.size}`; I.style.color = COLOR.WARN; }
    else { I.textContent = `Integridad de datos: completa${sq.recuperados ? ` (${sq.recuperados} recuperados)` : ""}`; I.style.color = COLOR.OK; }
  }

  // ---- integridad de datos: detectar, pedir reenvío, recuperar o declarar la pérdida ----

  _revisarIntegridad() {
    const sq = this.sesion.seq;
    const ahora = monotono();
    if (sq.nuevos.length) {
      for (const seq of sq.nuevos) {
        this._huecos.set(seq, { detectado: ahora, pedido: ahora });
        if (this.registrador) this.registrador.registrarHueco(seq, "DETECTADO", "faltante en la secuencia");
      }
      this._pedirReenvio([...sq.nuevos]);
    }
    if (sq.recuperadoSeq !== null) {
      const info = this._huecos.get(sq.recuperadoSeq);
      this._huecos.delete(sq.recuperadoSeq);
      const demora = info ? `${(ahora - info.detectado).toFixed(1)} s después de detectarse` : "llegó tarde";
      if (this.registrador) this.registrador.registrarHueco(sq.recuperadoSeq, "RECUPERADO", demora);
    }
  }

  _pedirReenvio(seqs) {
    const f = this.transporte;
    if (f && f.soportaReenvio && f.solicitarReenvio(seqs)) return;
    this._declararNoRecuperados(seqs, "esta fuente no permite pedir reenvío");
  }

  _vigilarHuecos() {
    if (!this._huecos.size) return;
    const ahora = monotono();
    const vencidos = [], reintentar = [];
    for (const [seq, info] of this._huecos) {
      if (ahora - info.detectado > this.cfg.plazo_recuperacion_s) vencidos.push(seq);
      else if (ahora - info.pedido > this.cfg.reintento_s) { reintentar.push(seq); info.pedido = ahora; }
    }
    if (reintentar.length && this.transporte && this.transporte.soportaReenvio) this.transporte.solicitarReenvio(reintentar);
    if (vencidos.length) this._declararNoRecuperados(vencidos, `no llegaron en ${Number(this.cfg.plazo_recuperacion_s).toFixed(0)} s`);
  }

  _declararNoRecuperados(seqs, motivo) {
    this.sesion.seq.marcarIrrecuperables(seqs);
    for (const seq of seqs) {
      this._huecos.delete(seq);
      if (this.registrador) this.registrador.registrarHueco(seq, "NO_RECUPERADO", motivo);
    }
    const rango = seqs.length === 1 ? `${Math.min(...seqs)}` : `${Math.min(...seqs)} a ${Math.max(...seqs)}`;
    this._nuevaAlerta("Datos no recuperados", `Faltan ${seqs.length} paquete(s), seq ${rango}: ${motivo}. Documentar el incidente.`,
      "ALTA", "SISTEMA", "DATOS", null);
  }

  _elegirVentana(i) { this.ventana = i; this._pintarSegmentos(); this._redibujar(); }

  _pintarSegmentos() { this.segmentos.forEach((s, i) => s.classList.toggle("sel", i === this.ventana)); }

  _redibujar() {
    if (!this.graficas || !this.tabMon.classList.contains("activa")) return;
    if (this._dibujoPendiente) return;
    this._dibujoPendiente = true;
    requestAnimationFrame(() => {
      this._dibujoPendiente = false;
      this.graficas.dibujar(this.sesion, this.umbrales, VENTANAS[this.ventana][1], ahoraS());
    });
  }

  // ---- utilidades -----------------------------------------------------------

  _mostrarResumen() {
    const res = this.sesion.resumen();
    const d = new Dialogo("Resumen de sesión", { ancho: 600 });
    d.subtitulo(`${this.fuenteActual} · dispositivo ${res.dispositivo || "--"} · duración ${formatoDuracion(res.duracion_s)}`);
    const f = (v, dec = 1) => (v === null || v === undefined ? "--" : v.toFixed(dec));
    const filas = ["hr", "spo2", "temp"].map((variable) => {
      const u = this.umbrales[variable], v = res.variables[variable];
      const dec = variable === "temp" ? 1 : 0;
      return el("tr", {}, el("td", { class: "neg" }, `${u.nombre} (${u.unidad})`), el("td", {}, f(v.min, dec)), el("td", {}, f(v.max, dec)),
        el("td", {}, f(v.media, 1)), el("td", {}, v.pct_fuera_rango === null ? "--" : `${v.pct_fuera_rango.toFixed(0)} %`));
    });
    const tabla = el("table", { class: "tabla-resumen" }, el("thead", {}, el("tr", {}, ["Signo vital", "Mínimo", "Máximo", "Promedio", "Fuera de rango"].map((h) => el("th", {}, h)))),
      el("tbody", {}, filas));
    const datos = [
      ["Paquetes recibidos", String(res.paquetes_recibidos)],
      ["Faltantes detectados / recuperados", `${res.paquetes_detectados} / ${res.paquetes_recuperados}`],
      ["Pérdida real de datos", `${res.paquetes_perdidos} paquetes (${res.perdida_pct.toFixed(1)} %)`],
      ["Batería", `${f(res.bateria_inicial, 0)} % al inicio → ${f(res.bateria_final, 0)} % al final`],
      ["Eventos de la pulsera", Object.keys(res.eventos).sort().map((k) => `${k}: ${res.eventos[k]}`).join(", ") || "ninguno"],
      ["Alertas generadas / atendidas", `${res.alertas_generadas} / ${res.alertas_atendidas}`],
      ["Tiempo de respuesta", `promedio ${f(res.respuesta_media_s)} s · máximo ${f(res.respuesta_max_s)} s`],
    ];
    const cuadro = el("div", { class: "cuadro-resumen" }, datos.map(([k, v]) => el("div", { class: "fila-res" }, el("span", {}, k), el("b", {}, v))));
    const estado = el("p", { class: "nota ok" });
    d.cuerpo.append(tabla, cuadro, el("p", { class: "nota" }, "Umbrales de referencia para adulto en reposo, pendientes de validar."), estado);
    const marca = this.registrador ? this.registrador.marca : marcaDe();
    d.pie.append(
      boton("Guardar como .txt", () => {
        const texto = formatearResumen(res, this.fuenteActual, this.sesion.inicio);
        const nombre = `resumen_${marca}.txt`;
        this.almacen.agregarFila(marca, "resumen", texto);
        descargar(nombre, texto);
        estado.textContent = `Guardado en la carpeta de datos y en Descargas (${nombre}).`;
      }),
      boton("Cerrar", () => d.cerrar(), "claro"));
    d.abrir();
  }

  /** "Carpeta de datos": las sesiones guardadas en el navegador, con sus archivos para descargar. */
  async abrirCarpeta() {
    const d = new Dialogo("Carpeta de datos (datos_sesion)", { ancho: 900 });
    d.subtitulo("Cada vez que se pulsa Conectar se crea una sesión nueva. Sus archivos quedan guardados en este navegador; " +
      "pulse un archivo para descargarlo a la carpeta Descargas.");
    const lista = el("div", { class: "lista-sesiones" }, el("p", { class: "nota" }, "Cargando…"));
    d.cuerpo.append(lista);
    d.pie.append(boton("Cerrar", () => d.cerrar(), "claro"));
    d.abrir();
    const sesiones = await this.almacen.listarSesiones();
    lista.replaceChildren();
    if (!sesiones.length) lista.append(el("p", { class: "nota" }, "Todavía no hay sesiones guardadas."));
    for (const s of sesiones.slice(0, 60)) {
      const botones = el("div", { class: "archivos" });
      for (const archivo of ["telemetria", "eventos", "alertas", "huecos", "resumen"]) {
        const n = await this.almacen.contarFilas(s.marca, archivo);
        if (archivo === "resumen" && !n) continue;
        const etiqueta = archivo === "resumen" ? `resumen_${s.marca}.txt` : `${archivo}_${s.marca}.csv · ${Math.max(0, n - 1)} fila(s)`;
        botones.append(boton(etiqueta, () => this._descargarArchivo(s.marca, archivo), "claro chico"));
      }
      const fecha = new Date(s.inicio * 1000);
      const dia = `${fecha.getFullYear()}-${dos(fecha.getMonth() + 1)}-${dos(fecha.getDate())}`;
      lista.append(el("div", { class: "sesion-guardada" },
        el("div", {}, el("b", {}, `${dia} ${horaCorta(fecha)}`), el("span", {}, ` · ${s.fuente} · ${s.paciente}${s.dispositivo ? " · " + s.dispositivo : ""}`)),
        botones));
    }
    const evidencias = await this.almacen.listarEvidencias();
    if (evidencias.length) {
      lista.append(el("p", { class: "dlg-etq" }, "evidencias/"),
        el("div", { class: "archivos" }, evidencias.sort((a, b) => b.hora - a.hora).map((e) => boton(e.nombre, () => descargar(e.nombre, e.blob), "claro chico"))));
    }
  }

  async _descargarArchivo(marca, archivo) {
    const lineas = await this.almacen.lineas(marca, archivo);
    if (archivo === "resumen") { descargar(`resumen_${marca}.txt`, lineas[lineas.length - 1] || ""); return; }
    // Con la marca BOM, Excel abre bien las tildes.
    descargar(`${archivo}_${marca}.csv`, "﻿" + lineas.join("\r\n") + "\r\n", "text/csv;charset=utf-8");
  }

  exportarBase() {
    descargar("vitalcare.db", new Blob([this.bd.exportar()], { type: "application/octet-stream" }));
    this.aviso("Base de datos exportada a la carpeta Descargas (vitalcare.db).");
  }

  async importarBase(archivo) {
    const ok = await confirmar("Importar base de datos",
      `¿Reemplazar los datos de este navegador por los de ${archivo.name}?\n\nLos pacientes, las pulseras, los umbrales y la auditoría actuales se sustituyen. ` +
      "Los archivos de las sesiones anteriores se conservan.");
    if (!ok) return;
    const bytes = new Uint8Array(await archivo.arrayBuffer());
    try {
      const prueba = new this.bd.cx.constructor(bytes);
      prueba.exec("SELECT COUNT(*) FROM pacientes; SELECT COUNT(*) FROM pulseras;");
      prueba.close();
    } catch {
      await mensaje("Archivo no válido", "El archivo no es una base de datos de VitalCare.", "aviso");
      return;
    }
    if (this.activo) this._detener();
    // A partir de aquí la base vieja ya no se guarda, para que no pise a la importada.
    this._sinCierre = true;
    this.bd.alCambiar = null;
    await this.almacen.guardarBase(bytes);
    try { localStorage.setItem("vitalcare-aviso", `Base de datos importada desde ${archivo.name}.`); } catch { /* sin almacenamiento */ }
    location.reload();
  }

  async borrarTodo() {
    const ok = await confirmar("Borrar todos los datos",
      "¿Borrar todos los datos de VitalCare guardados en este navegador?\n\nSe eliminan los pacientes, las pulseras, la auditoría y los archivos de todas las sesiones. " +
      "La página vuelve a empezar con los datos de ejemplo. Si necesita conservarlos, exporte antes la base de datos.");
    if (!ok) return;
    if (this.activo) this._detener();
    this._sinCierre = true;
    this.bd.alCambiar = null;
    await this.almacen.guardarBase(null);
    await this.almacen.borrarTodo();
    try { localStorage.removeItem("vitalcare-cierres-limpios"); } catch { /* sin almacenamiento */ }
    location.reload();
  }

  _alternarPantallaCompleta() {
    if (document.fullscreenElement) document.exitFullscreen();
    else document.documentElement.requestFullscreen().catch(() => this.aviso("El navegador no permitió la pantalla completa."));
  }

  _teclas(e) {
    if (hayDialogoAbierto()) return;
    const acciones = {
      F2: () => this._demoEvento("FALL"), F3: () => this._demoEvento("SOS"), F4: () => this.simularCorteRed(),
      F5: () => this.simularCorteEnergia(), F8: () => this.capturarEvidencia(), F11: () => this._alternarPantallaCompleta(),
    };
    if (acciones[e.key] && !e.ctrlKey && !e.altKey && !e.metaKey) { e.preventDefault(); acciones[e.key](); }
  }

  /** Para las pruebas: cierra la aplicación como al cerrar la ventana y suelta todo. */
  destruir() {
    this._alCerrar();
    this.activo = false;
    this._conexionId += 1;
    clearInterval(this._intervalo);
    this._oyentes.forEach(([obj, ev, fn]) => obj.removeEventListener(ev, fn));
    this.raiz.replaceChildren();
  }

  /** Cierre normal de la página: la sesión queda cerrada bien (no es un corte). */
  _alCerrar() {
    if (this._sinCierre) return;
    if (this.sesionBd !== null) {
      if (!this.almacen.enMemoria) try {
        const limpias = JSON.parse(localStorage.getItem("vitalcare-cierres-limpios") || "{}");
        limpias[this.sesionBd] = new Date().toISOString().replace("T", " ").slice(0, 19);
        localStorage.setItem("vitalcare-cierres-limpios", JSON.stringify(limpias));
      } catch { /* sin almacenamiento */ }
      this.bd.cerrarSesion(this.sesionBd, true);
      this.sesionBd = null;
    }
    if (this.transporte) this.transporte.desconectar();
    this.bd.registrarEventoSistema("CIERRE_LIMPIO", "La página se cerró de forma normal");
  }
}

// ----------------------------------------------------------------------
// Arranque
// ----------------------------------------------------------------------

export async function iniciar(raiz) {
  const SQL = await window.initSqlJs({ locateFile: (f) => `vendor/${f}` });
  const almacen = await Almacen.abrir();
  const bytes = await almacen.leerBase();
  const app = new Aplicacion(raiz, { SQL, almacen, bytesBase: bytes ? new Uint8Array(bytes) : null });
  let avisoPrevio = null;
  try { avisoPrevio = localStorage.getItem("vitalcare-aviso"); localStorage.removeItem("vitalcare-aviso"); } catch { /* sin almacenamiento */ }
  if (avisoPrevio) app.aviso(avisoPrevio);
  window.vitalcare = app; // para pruebas desde la consola
  return app;
}
