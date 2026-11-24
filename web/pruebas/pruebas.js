// Pruebas automáticas de VitalCare Web: las mismas de pruebas_testbed.py, en JavaScript.
// No necesitan hardware, Bluetooth ni internet.
//
//   En el navegador: abrir  /pruebas/  (por ejemplo https://vitalcare-pulsera.vercel.app/pruebas/)
//   Con Node:        node web/pruebas/correr_node.mjs   (corre todas menos las dos de la ventana)
//
// Las dos fuentes que no existen en la web se cambiaron por su equivalente: las pruebas de
// WiFi/UDP ahora prueban la fuente Hub (MQTT) con un broker de mentira.

import { ContadorSeq, Sesion, clasificar, estadoGlobal, estimarAutonomiaMin, formatearResumen, formatoAutonomia, nivelBateria } from "../js/logica.js";
import { BaseDatos } from "../js/datos.js";
import { RegistradorCSV } from "../js/registro.js";
import { BLETransport, ESCENARIOS, MQTTTransport, SimuladorTransport, despacharJSON } from "../js/transportes.js";

const dormir = (ms) => new Promise((ok) => setTimeout(ok, ms));

function afirmar(cond, msg = "falló la comprobación") { if (!cond) throw new Error(msg); }
const igual = (a, b) => JSON.stringify(a) === JSON.stringify(b);
function afirmarIgual(a, b, msg = "") { if (!igual(a, b)) throw new Error(`${msg} esperado ${JSON.stringify(b)}, llegó ${JSON.stringify(a)}`); }
const conjunto = (s) => [...s].sort((x, y) => x - y);

/** Destino en memoria para el registro CSV (en lugar de IndexedDB). */
export class AlmacenMemoria {
  constructor() { this.enMemoria = true; this.filas = []; this.sesiones = []; this.base = null; this.evidencias = []; }
  async leerBase() { return this.base; }
  async guardarBase(b) { this.base = b; }
  async borrarTodo() { this.filas = []; this.sesiones = []; this.base = null; }
  registrarSesion(info) { this.sesiones.push(info); }
  actualizarSesion() {}
  agregarFila(marca, archivo, linea) { this.filas.push({ marca, archivo, linea }); }
  async listarSesiones() { return [...this.sesiones].reverse(); }
  async lineas(marca, archivo) { return this.filas.filter((f) => f.marca === marca && f.archivo === archivo).map((f) => f.linea); }
  lineasDe(archivo) { return this.filas.filter((f) => f.archivo === archivo).map((f) => f.linea); }
  async contarFilas(marca, archivo) { return (await this.lineas(marca, archivo)).length; }
  guardarEvidencia(nombre, blob) { this.evidencias.push({ nombre, blob, hora: Date.now() }); }
  async listarEvidencias() { return this.evidencias; }
}

/** Lee una línea CSV respetando las comillas (para revisar lo escrito). */
function leerCSV(linea) {
  const r = []; let actual = "", comillas = false;
  for (let i = 0; i < linea.length; i++) {
    const c = linea[i];
    if (comillas) {
      if (c === '"' && linea[i + 1] === '"') { actual += '"'; i++; } else if (c === '"') comillas = false; else actual += c;
    } else if (c === '"') comillas = true; else if (c === ",") { r.push(actual); actual = ""; } else actual += c;
  }
  r.push(actual);
  return r;
}

export function definirPruebas({ SQL, conVentana = false, crearApp = null }) {
  const P = [];
  const prueba = (nombre, fn, ventana = false) => P.push({ nombre, fn, ventana });
  // Base con los datos de ejemplo y la W01 puesta a María, como se haría en la demostración.
  const bd = () => {
    const b = new BaseDatos(SQL);
    b.sembrarDemostracion("pruebas");
    b.asignarPulsera(b.pacientePorExpediente("EXP-2026-0001").id, b.pulseraPorDispositivo("W01").id, "pruebas", "Asignación inicial al ingreso");
    return b;
  };

  // ------------------------------------------------------------ lógica
  prueba("clasifica_signos_vitales", () => {
    afirmarIgual(clasificar("hr", 75), [0, null]);
    afirmar(igual(clasificar("hr", 60), [0, null]) && igual(clasificar("hr", 100), [0, null]));
    afirmarIgual(clasificar("hr", 110), [1, "alta"]);
    afirmarIgual(clasificar("hr", 55), [1, "baja"]);
    afirmarIgual(clasificar("hr", 132), [2, "alta"]);
    afirmarIgual(clasificar("hr", 44), [2, "baja"]);
    afirmarIgual(clasificar("spo2", 97), [0, null]);
    afirmarIgual(clasificar("spo2", 93), [1, "baja"]);
    afirmarIgual(clasificar("spo2", 91), [2, "baja"]);
    afirmarIgual(clasificar("temp", 36.7), [0, null]);
    afirmarIgual(clasificar("temp", 37.8), [1, "alta"]);
    afirmarIgual(clasificar("temp", 38.4), [2, "alta"]);
    afirmarIgual(clasificar("temp", 34.5), [2, "baja"]);
  });

  prueba("valores_invalidos_no_rompen", () => {
    for (const malo of [null, "abc", NaN, Infinity, true]) afirmarIgual(clasificar("hr", malo), [null, null], String(malo));
    afirmarIgual(clasificar("hr", "80"), [0, null]);
    afirmar(nivelBateria(null) === null);
  });

  prueba("estado_global_toma_el_peor", () => {
    afirmar(estadoGlobal([0, 1, null]) === 1);
    afirmar(estadoGlobal([0, 2, 1]) === 2);
    afirmar(estadoGlobal([null, null]) === null);
  });

  prueba("bateria_por_niveles", () => {
    afirmar(nivelBateria(80) === 0 && nivelBateria(29) === 1 && nivelBateria(10) === 2);
  });

  prueba("contador_de_paquetes_perdidos", () => {
    const c = new ContadorSeq();
    for (const s of [1, 2, 3, 7, 8]) c.registrar(s);
    afirmar(c.recibidos === 5 && c.perdidos === 3, `${c.recibidos} ${c.perdidos}`);
    afirmarIgual(conjunto(c.faltantes), [4, 5, 6]);
    afirmar(c.detectados === 3);
    afirmar(Math.abs(c.porcentajePerdida - 37.5) < 1e-9);
    c.registrar(8);
    afirmar(c.duplicados === 1 && c.recibidos === 5);
    afirmar(c.registrar("x") === 0);
    for (let s = 9; s < 60; s++) c.registrar(s);
    c.registrar(1);
    afirmar(c.reinicios === 1);
    afirmarIgual(conjunto(c.irrecuperables), [4, 5, 6]);
  });

  prueba("resumen_de_sesion", () => {
    const s = new Sesion();
    const t0 = Date.now() / 1000;
    const paquetes = [
      { id: "W01", seq: 1, hr: 70, spo2: 98, temp: 36.6, batt: 90, st: "OK" },
      { id: "W01", seq: 2, hr: 130, spo2: 91, temp: 36.8, batt: 89, st: "OK" },
      { id: "W01", seq: 4, hr: 80, spo2: 97, temp: 38.5, batt: 88, st: "OK" },
    ];
    paquetes.forEach((p, i) => s.agregarTelemetria(p, t0 + i * 2));
    s.agregarEvento({ id: "W01", seq: 5, ev: "FALL", sev: "HIGH" }, t0 + 7);
    s.alertaGenerada();
    s.alertaAtendida(12.5);
    const r = s.resumen();
    afirmar(r.paquetes_perdidos === 1 && r.paquetes_recibidos === 4);
    afirmar(r.variables.hr.min === 70 && r.variables.hr.max === 130);
    afirmar(Math.abs(r.variables.hr.media - 93.3333) < 1e-3);
    afirmar(Math.round(r.variables.hr.pct_fuera_rango) === 33);
    afirmarIgual(r.eventos, { FALL: 1 });
    afirmar(r.alertas_generadas === 1 && r.alertas_atendidas === 1);
    afirmar(r.respuesta_media_s === 12.5);
    afirmar(r.bateria_inicial === 90 && r.bateria_final === 88);
    const texto = formatearResumen(r, "Simulador", t0);
    afirmar(texto.includes("RESUMEN DE SESIÓN") && texto.includes("FALL: 1"));
  });

  prueba("resumen_de_sesion_vacia", () => {
    const r = new Sesion().resumen();
    afirmar(r.paquetes_recibidos === 0 && r.variables.hr.media === null);
    afirmar(formatearResumen(r).includes("--"));
  });

  // ------------------------------------------------------------ CSV
  prueba("csv_escribe_y_deja_de_escribir_al_cerrar", () => {
    const a = new AlmacenMemoria();
    const reg = new RegistradorCSV(a);
    reg.registrarTelemetria({ id: "W01", seq: 1, ts: 10, hr: 72, spo2: 98, temp: 36.7, batt: 90, st: "OK" });
    reg.registrarEvento({ id: "W01", seq: 2, ts: 20, ev: "SOS", sev: "HIGH" });
    reg.registrarAlerta("GENERADA", "DISPOSITIVO", "SOS", "detalle", 2);
    const cierre = { resolucion: "Falsa alarma", accion_realizada: "Presionó el botón sin querer", atendido_por: "Enf. Pérez", indicacion_medica: false, medico: "" };
    reg.registrarAlerta("CERRADA", "DISPOSITIVO", "SOS", "detalle", 2, 8.4, cierre);
    reg.registrarHueco(5, "DETECTADO", "faltante");
    reg.registrarHueco(5, "RECUPERADO", "2 s");
    reg.cerrar();
    reg.registrarTelemetria({ id: "W01", seq: 3 });
    const filas = a.lineasDe("telemetria");
    afirmar(filas.length === 2 && filas[0].startsWith("hora_local,id,seq"), JSON.stringify(filas));
    afirmarIgual(filas[1].split(",").slice(2), ["1", "10", "72", "98", "36.7", "90", "OK"]);
    afirmar(a.lineasDe("eventos").length === 2);
    const al = a.lineasDe("alertas").map(leerCSV);
    afirmar(al.length === 3);
    afirmarIgual(al[0].slice(-5), ["resolucion", "accion_realizada", "atendido_por", "indicacion_medica", "medico"]);
    afirmar(al[2][1] === "CERRADA" && al[2][6] === "8.4");
    afirmarIgual(al[2].slice(7), ["Falsa alarma", "Presionó el botón sin querer", "Enf. Pérez", "NO", ""]);
    afirmarIgual(a.lineasDe("huecos").slice(1).map((l) => l.split(",")[2]), ["DETECTADO", "RECUPERADO"]);
  });

  // ------------------------------------------------------------ transportes
  prueba("despachar_json_clasifica_y_descarta_basura", () => {
    const tel = [], ev = [];
    const bytes = new TextEncoder().encode('{"id":"W01","seq":1,"hr":70}\n{"id":"W01","seq":2,"ev":"FALL","sev":"HIGH"}\nbasura\n\n[1,2]\n');
    const n = despacharJSON(bytes, (p) => tel.push(p), (p) => ev.push(p));
    afirmar(n === 2 && tel.length === 1 && ev.length === 1);
    afirmar(despacharJSON(new Uint8Array([0xff, 0xfe]), (p) => tel.push(p), (p) => ev.push(p)) === 0);
  });

  async function correrSimulador(sim, ms, tras = null) {
    const tel = [], ev = [];
    const tarea = sim.recibirPaquetes((p) => tel.push(p), (p) => ev.push(p));
    if (tras) { await dormir(120); tras(sim); }
    await dormir(ms);
    await sim.desconectar();
    await tarea;
    return [tel, ev];
  }

  prueba("simulador_formato_y_eventos_inmediatos", async () => {
    const sim = new SimuladorTransport({ intervaloS: 0.05 });
    const [tel, ev] = await correrSimulador(sim, 500, (s) => s.dispararEvento("FALL"));
    afirmar(tel.length >= 5, String(tel.length));
    const claves = ["batt", "hr", "id", "seq", "spo2", "st", "temp", "ts"];
    afirmar(tel.every((p) => igual(Object.keys(p).sort(), claves)));
    afirmar(ev.length === 1 && ev[0].ev === "FALL" && ev[0].sev === "HIGH");
    const seqs = [...tel.map((p) => p.seq), ev[0].seq];
    afirmar(new Set(seqs).size === seqs.length);
  });

  prueba("simulador_escenarios_llegan_al_objetivo", async () => {
    const sim = new SimuladorTransport({ intervaloS: 0.03 });
    sim.fijarEscenario("Taquicardia");
    const [tel] = await correrSimulador(sim, 1200);
    const ultimos = tel.slice(-5).map((p) => p.hr);
    afirmar(Math.min(...ultimos) >= 115, JSON.stringify(ultimos));
    for (const nombre of Object.keys(ESCENARIOS)) sim.fijarEscenario(nombre);
  });

  prueba("simulador_perdida_genera_huecos_de_seq", async () => {
    const sim = new SimuladorTransport({ intervaloS: 0.02 });
    sim.fijarEscenario("Señal inestable (pérdida de paquetes)");
    const [tel] = await correrSimulador(sim, 1500);
    const c = new ContadorSeq();
    tel.forEach((p) => c.registrar(p.seq));
    afirmar(c.perdidos > 0 && c.recibidos === tel.length, `${c.perdidos} ${c.recibidos}`);
  });

  prueba("simulador_se_puede_detener_antes_de_arrancar", async () => {
    const sim = new SimuladorTransport({ intervaloS: 0.05 });
    sim._detenido = true;
    const [tel] = await correrSimulador(sim, 200);
    afirmar(tel.length === 0);
  });

  /** Un broker MQTT de mentira, para probar la fuente Hub sin internet. */
  function mqttFalso({ fallar = false } = {}) {
    const publicados = [];
    const lib = {
      publicados,
      connect(url, op) {
        const oyentes = {};
        const c = {
          url, op, connected: false, suscrito: null,
          on(ev, fn) { (oyentes[ev] ||= []).push(fn); return c; },
          emitir(ev, ...a) { (oyentes[ev] || []).forEach((f) => f(...a)); },
          subscribe(t) { c.suscrito = t; },
          publish(tema, msg, o, cb) { publicados.push([tema, msg]); if (cb) cb(null); },
          end() { c.connected = false; c.emitir("close"); },
        };
        lib.cliente = c;
        setTimeout(() => {
          if (fallar) { c.emitir("error", new Error("conexión rechazada")); return; }
          c.connected = true;
          c.emitir("connect");
        }, 10);
        return c;
      },
    };
    return lib;
  }

  prueba("mqtt_recibe_telemetria_y_eventos (antes: udp_recibe_telemetria_y_eventos)", async () => {
    const lib = mqttFalso();
    const hub = [];
    const t = new MQTTTransport({ broker: "prueba", prefijo: "pre", mqttLib: lib, alHub: (h) => hub.push(h) });
    const tel = [], ev = [];
    const tarea = t.recibirPaquetes((p) => tel.push(p), (p) => ev.push(p));
    await dormir(40);
    const c = lib.cliente;
    afirmar(c.url === "wss://prueba:8884/mqtt" && c.suscrito.includes("pre/pulsera/+/telemetria"));
    const enc = (o) => new TextEncoder().encode(typeof o === "string" ? o : JSON.stringify(o));
    c.emitir("message", "pre/pulsera/W01/telemetria", enc({ id: "W01", seq: 1, ts: 1, hr: 77, spo2: 98, temp: 36.7, batt: 80, st: "OK" }));
    c.emitir("message", "pre/pulsera/W01/evento", enc({ id: "W01", seq: 2, ts: 2, ev: "SOS", sev: "HIGH" }));
    c.emitir("message", "pre/pulsera/W01/telemetria", enc("esto no es json"));
    c.emitir("message", "pre/hub/H1/estado", enc("online"));
    afirmar(tel.length === 1 && tel[0].hr === 77);
    afirmar(ev.length === 1 && ev[0].ev === "SOS");
    afirmar(hub.some((h) => h.tipo === "estado" && h.texto === "online"));
    t.idDestino = "W01";
    afirmar(t.solicitarReenvio([5, 6]) === true);
    afirmar(lib.publicados.some(([tema, m]) => tema === "pre/pulsera/W01/comando" && igual(JSON.parse(m), { cmd: "resend", seq: [5, 6] })));
    await t.desconectar();
    await tarea;
  });

  prueba("mqtt_broker_caido_da_error_claro (antes: udp_puerto_ocupado_da_error_claro)", async () => {
    const t = new MQTTTransport({ broker: "prueba", prefijo: "pre", mqttLib: mqttFalso({ fallar: true }) });
    try {
      await t.recibirPaquetes(() => {}, () => {});
    } catch (e) {
      afirmar(/broker/i.test(e.message), e.message);
      return;
    }
    throw new Error("debió fallar porque el broker rechazó la conexión");
  });

  prueba("contador_recupera_faltantes_y_cuenta_la_perdida_real", () => {
    const c = new ContadorSeq();
    for (const s of [1, 2, 5]) c.registrar(s);
    afirmar(igual(c.nuevos, [3, 4]) && c.perdidos === 2 && c.detectados === 2);
    c.registrar(3);
    afirmar(c.recuperadoAhora && c.recuperadoSeq === 3 && c.recuperados === 1 && c.perdidos === 1);
    c.marcarIrrecuperables([4]);
    afirmar(c.faltantes.size === 0 && igual(conjunto(c.irrecuperables), [4]) && c.perdidos === 1);
    c.registrar(4);
    afirmar(c.perdidos === 0 && c.recuperados === 2);
  });

  prueba("sesion_coloca_el_paquete_recuperado_en_su_hora", () => {
    const s = new Sesion();
    const t0 = 1000000.0;
    for (const [seq, ts, hr] of [[1, 0, 70], [2, 2000, 71], [4, 6000, 73]]) {
      s.agregarTelemetria({ id: "W01", seq, ts, hr, spo2: 97, temp: 36.7, batt: 90 }, t0 + ts / 1000 + 0.1);
    }
    s.agregarTelemetria({ id: "W01", seq: 3, ts: 4000, hr: 72, spo2: 97, temp: 36.7, batt: 90 }, t0 + 20);
    afirmarIgual(s.hr, [70, 71, 72, 73]);
    afirmar(igual(s.t, [...s.t].sort((a, b) => a - b)) && s.seq.perdidos === 0 && s.seq.recuperados === 1);
  });

  prueba("autonomia_de_bateria", () => {
    const bajando = Array.from({ length: 10 }, (_, i) => [i * 60, 100 - i]);
    const minutos = estimarAutonomiaMin(bajando);
    afirmar(minutos !== null && Math.abs(minutos - 91) < 1, String(minutos));
    afirmar(estimarAutonomiaMin(Array.from({ length: 10 }, (_, i) => [i * 60, 80])) === null);
    afirmar(estimarAutonomiaMin([[0, 90], [1, 90]]) === null);
    afirmar(formatoAutonomia(null) === "calculando…");
    afirmar(formatoAutonomia(45) === "~45 min" && formatoAutonomia(130) === "~2 h 10 min");
  });

  prueba("cierre_de_alerta_exige_detalle_manual", async () => {
    const { validarCierre } = await import("../js/app.js");
    const ok = { resolucion: "Resuelta con indicación médica", accion_realizada: "Se administró paracetamol", atendido_por: "Enf. Pérez", indicacion_medica: true, medico: "Dr. Gómez" };
    afirmar(validarCierre(ok) === null);
    afirmar(validarCierre({ ...ok, resolucion: "" }) !== null);
    afirmar(validarCierre({ ...ok, accion_realizada: "ok" }) !== null);
    afirmar(validarCierre({ ...ok, atendido_por: "" }) !== null);
    afirmar(validarCierre({ ...ok, medico: "" }) !== null);
    afirmar(validarCierre({ ...ok, indicacion_medica: false, medico: "" }) === null);
  });

  prueba("simulador_recupera_lo_perdido_con_reenvio", async () => {
    const sim = new SimuladorTransport({ intervaloS: 0.02 });
    sim.fijarEscenario("Señal inestable (pérdida de paquetes)");
    const tel = [], ev = [];
    const tarea = sim.recibirPaquetes((p) => tel.push(p), (p) => ev.push(p));
    await dormir(1500);
    const c = new ContadorSeq();
    tel.forEach((p) => c.registrar(p.seq));
    const faltan = conjunto(c.faltantes);
    afirmar(faltan.length, "el escenario debía perder paquetes");
    afirmar(sim.solicitarReenvio(faltan) === true);
    await dormir(300);
    sim.fijarEscenario("Paciente estable");
    await sim.desconectar();
    await tarea;
    const c2 = new ContadorSeq();
    tel.forEach((p) => c2.registrar(p.seq));
    const pendientes = faltan.filter((s) => c2.faltantes.has(s));
    afirmar(!pendientes.length, JSON.stringify(pendientes));
    afirmar(c2.recuperados >= faltan.length - 1);
  });

  prueba("simulador_sin_reenvio_lo_dice", () => {
    const sim = new SimuladorTransport({ intervaloS: 0.02 });
    sim.fijarEscenario("Señal inestable SIN reenvío (pérdida real)");
    afirmar(sim.solicitarReenvio([1, 2]) === false);
    afirmar(new BLETransport("W01").solicitarReenvio([1]) === false); // Bluetooth directo no tiene reenvío todavía
  });

  // ------------------------------------------------------------ ventana
  prueba("ventana_arranca_recibe_y_cierra_sin_errores", async () => {
    const { app, almacen, errores } = await crearApp({ intervaloSim: 0.1, autoiniciar: false });
    try {
      // Como en la demostración: se le pone una pulsera simulada a María y se monitorea.
      const maria = app.bd.pacientePorExpediente("EXP-2026-0001");
      app.bd.asignarPulsera(maria.id, app.bd.pulseraPorDispositivo("W02").id, "pruebas");
      app.monitorearPaciente(maria.id);
      app._conectar();
      const cierre = { resolucion: "Falsa alarma", accion_realizada: "El paciente presionó sin querer", atendido_por: "Enf. Pérez", indicacion_medica: false, medico: "" };
      const tipos = new Set();
      await dormir(800); app._demoEvento("FALL");
      await dormir(200); app._demoEvento("SOS");
      await dormir(200); app.selEscenario.value = "Señal inestable (pérdida de paquetes)"; app._aplicarEscenario();
      await dormir(1600); app.tarjetasAlerta.filter((t) => t.requiereAtencion && ["FALL", "SOS"].includes(t.tipo)).forEach((t) => t.cerrar(cierre));
      await dormir(200); app.selEscenario.value = "Batería baja (descarga rápida)"; app._aplicarEscenario();
      await dormir(200); app._elegirVentana(2);
      await dormir(4300); app.tarjetasAlerta.forEach((t) => tipos.add(t.tipo));
      await dormir(400); app._detener();
      await dormir(200); app._conectar();
      await dormir(1000);
      afirmar(!errores.length, errores[0]);
      afirmar(almacen.lineasDe("telemetria").length > 1, "no se escribió la telemetría");
      afirmar(app.sesion.paquetesTelemetria > 0);
      afirmar(tipos.has("BATERIA_BAJA") && tipos.has("BATERIA_CRITICA"), JSON.stringify([...tipos]));
      afirmar(almacen.lineasDe("huecos").length > 0);
    } finally { app.destruir(); }
  }, true);

  prueba("sin_paciente_con_pulsera_no_se_muestra_nada", async () => {
    const { app } = await crearApp({ intervaloSim: 0.1, autoiniciar: false });
    try {
      afirmar(app.bd.pacientePorDispositivo("W01") === null, "los datos de ejemplo no deben traer pulsera puesta");
      app._conectar();
      afirmar(!app.activo, "conectó sin paciente");
      app._manejarTelemetria({ id: "W01", seq: 1, hr: 80, spo2: 97, temp: 36.6, batt: 90 });
      app._manejarEvento({ id: "W01", seq: 2, ev: "FALL", sev: "HIGH" });
      afirmar(app.sesion.paquetesTelemetria === 0 && app.sesion.eventos.length === 0);
      afirmar(app.tarjetasAlerta.some((t) => t.tipo === "PULSERA_SIN_PACIENTE"));
      const carlos = app.bd.pacientePorExpediente("EXP-2026-0002");
      app.monitorearPaciente(carlos.id);
      app._conectar();
      afirmar(!app.activo, "conectó con un paciente sin pulsera");
      app.bd.asignarPulsera(carlos.id, app.bd.pulseraPorDispositivo("W03").id, "pruebas");
      app.monitorearPaciente(carlos.id);
      app._conectar();
      afirmar(app.activo && app.transporte.deviceId === "W03", "el simulador debe transmitir como W03");
    } finally { app.destruir(); }
  }, true);

  // ------------------------------------------------------------ gestión
  prueba("perfil_clinico_sale_de_la_edad", () => {
    const b = bd();
    const nino = b.pacientePorExpediente("EXP-2026-0002");
    const adulta = b.pacientePorExpediente("EXP-2026-0001");
    afirmar(nino.perfil === "Escolar", nino.perfil);
    afirmar(adulta.perfil === "Adulto mayor", adulta.perfil);
    const uN = b.umbralesDePaciente(nino.id), uA = b.umbralesDePaciente(adulta.id);
    afirmar(uN.hr.normal[1] > uA.hr.normal[1]);
    afirmar(!igual(uN.pas.normal, uA.pas.normal));
    b.cerrar();
  });

  prueba("variante_por_sexo_y_ajuste_individual", () => {
    const b = bd();
    const pid = b.crearPaciente({ expediente: "E-9", nombre: "Ana", apellidos: "López", fecha_nac: "1990-05-05", sexo: "Femenino" }, "pruebas");
    b.crearVarianteSexo("Adulto", "Femenino", "supervisora");
    b.fijarUmbralPerfil("Adulto", "Femenino", "hr", [62, 104, 52, 124], "supervisora");
    let u = b.umbralesDePaciente(pid);
    afirmarIgual(u.hr.normal, [62, 104]);
    afirmar(u.hr.origen.includes("femenino"));
    b.fijarUmbralPaciente(pid, "spo2", [92, 100, 88, 100], "EPOC conocida", "Dr. Gómez");
    u = b.umbralesDePaciente(pid);
    afirmarIgual(u.spo2.normal, [92, 100]);
    afirmar(u.spo2.origen === "ajuste individual");
    afirmar(b.listarAuditoria().some((a) => (a.detalle || "").includes("EPOC")));
    b.cerrar();
  });

  prueba("cambio_de_pulsera_conserva_el_hilo_del_paciente", () => {
    const b = bd();
    const p = b.pacientePorExpediente("EXP-2026-0001");
    afirmar(b.pacientePorDispositivo("W01").id === p.id);
    const w2 = b.pulseraPorDispositivo("W02");
    const [, anterior] = b.asignarPulsera(p.id, w2.id, "enf. Santos", "batería al 12 %");
    afirmar(anterior === "VC-W-0001");
    afirmar(b.pacientePorDispositivo("W02").id === p.id);
    afirmar(b.pacientePorDispositivo("W01") === null);
    afirmar(b.pulseraPorDispositivo("W01").estado === "Cargando");
    const historial = b.historialAsignaciones({ pid: p.id });
    afirmar(historial.length === 2 && historial[0].fin === null);
    b.cerrar();
  });

  prueba("una_pulsera_no_se_asigna_a_dos_pacientes", () => {
    const b = bd();
    const otro = b.pacientePorExpediente("EXP-2026-0002");
    const w1 = b.pulseraPorDispositivo("W01");
    try { b.asignarPulsera(otro.id, w1.id, "enf. Santos"); } catch { b.cerrar(); return; }
    throw new Error("permitió asignar la misma pulsera a dos pacientes");
  });

  prueba("alta_libera_la_pulsera_y_protege_el_historial", () => {
    const b = bd();
    const p = b.pacientePorExpediente("EXP-2026-0001");
    afirmar(b.darAlta(p.id, "enf. Santos") === "VC-W-0001");
    afirmar(b.pulseraPorDispositivo("W01").estado === "Disponible");
    afirmar(b.paciente(p.id).estado === "Alta");
    afirmar(b.pacientePorDispositivo("W01") === null);
    try { b.eliminarPaciente(p.id, "admin"); } catch { b.cerrar(); return; }
    throw new Error("borró un paciente que tenía historial");
  });

  prueba("configuracion_del_centro_queda_auditada", () => {
    const b = bd();
    afirmar(b.config("escalamiento_s") === 60);
    b.fijarConfig("escalamiento_s", 45, "supervisora Reyes");
    afirmar(b.config("escalamiento_s") === 45);
    const ultima = b.listarAuditoria(1)[0];
    afirmar(ultima.usuario === "supervisora Reyes");
    afirmar(ultima.detalle.includes("60 → 45"));
    b.cerrar();
  });

  prueba("corte_de_energia_deja_la_sesion_abierta", () => {
    const b = bd();
    const p = b.pacientePorExpediente("EXP-2026-0001");
    const sid = b.abrirSesion(p.id, 1, "Simulador", "20261121_0100");
    b.latidoSesion(sid);
    afirmar(b.sesionesSinCerrar().length === 1);
    const pendiente = b.sesionesSinCerrar()[0];
    b.registrarEventoSistema("CORTE_DETECTADO", "prueba", pendiente.id);
    b.cerrarSesion(pendiente.id, false);
    afirmar(b.sesionesSinCerrar().length === 0);
    afirmar(b.eventosSistema(100, true)[0].tipo === "CORTE_DETECTADO");
    b.cerrar();
  });

  prueba("signos_manuales_se_comparan_con_el_rango_del_paciente", () => {
    const b = bd();
    const p = b.pacientePorExpediente("EXP-2026-0001");
    b.registrarMedicion(p.id, "pas", 165, "enf. Santos", "brazo derecho");
    b.registrarMedicion(p.id, "pad", 70, "enf. Santos");
    const u = b.umbralesDePaciente(p.id);
    const umbrales = { pas: { normal: u.pas.normal, vigilancia: u.pas.vigilancia }, pad: { normal: u.pad.normal, vigilancia: u.pad.vigilancia } };
    afirmar(clasificar("pas", 165, umbrales)[0] === 2);
    afirmar(clasificar("pad", 70, umbrales)[0] === 0);
    const ult = b.ultimasMediciones(p.id);
    afirmar(ult.pas.valor === 165 && ult.pas.tomado_por === "enf. Santos");
    b.cerrar();
  });

  prueba("simulador_corta_el_enlace_y_luego_recupera", async () => {
    const sim = new SimuladorTransport({ intervaloS: 0.05 });
    const recibidos = [];
    const tarea = sim.recibirPaquetes((p) => recibidos.push(p), () => {});
    await dormir(250);
    sim.cortar(0.3);
    await dormir(550);
    const llegados = new Set(recibidos.map((p) => p.seq));
    const faltantes = [];
    for (let s = 1; s < recibidos[recibidos.length - 1].seq; s++) if (!llegados.has(s)) faltantes.push(s);
    for (const s of faltantes) sim.solicitarReenvio([s]);
    await dormir(300);
    await sim.desconectar();
    await tarea;
    afirmar(faltantes.length, "el corte debía dejar huecos de secuencia");
    const todos = new Set(recibidos.map((p) => p.seq));
    afirmar(faltantes.every((s) => todos.has(s)), "los paquetes del corte no se recuperaron");
  });

  prueba("ventana_con_paciente_no_mezcla_datos_de_otra_pulsera", async () => {
    const { app } = await crearApp({ intervaloSim: 0.1, autoiniciar: false });
    try {
      const paciente = app.bd.pacientePorExpediente("EXP-2026-0001");
      app.bd.asignarPulsera(paciente.id, app.bd.pulseraPorDispositivo("W01").id, "pruebas");
      app.monitorearPaciente(paciente.id);
      afirmar(app.dispositivoEsperado === "W01");
      afirmarIgual(app.umbrales.hr.normal, [60, 100]);
      const propio = { id: "W01", seq: 1, ts: 1000, hr: 78, spo2: 97, temp: 36.6, batt: 90, st: "OK" };
      app._manejarTelemetria(propio);
      afirmar(app.sesion.paquetesTelemetria === 1);
      app._manejarTelemetria({ ...propio, id: "W03" });
      afirmar(app.sesion.paquetesTelemetria === 1, "aceptó datos de una pulsera ajena");
      afirmar(app.tarjetasAlerta.some((t) => t.tipo === "DISPOSITIVO_AJENO"));
      const w3 = app.bd.pulseraPorDispositivo("W03");
      const [, anterior] = app.bd.asignarPulsera(paciente.id, w3.id, "enf. Santos");
      app.pulseraCambiada(paciente.id, { pulsera_id: w3.id, serie: w3.serie, dispositivo: "W03", anterior, motivo: "Cambio por batería" });
      afirmar(app.dispositivoEsperado === "W03");
      afirmar(app.sesion.cambios.length && app.sesion.cambios[0][1].includes("VC-W-0003"));
      app._manejarTelemetria({ ...propio, id: "W03", seq: 1 });
      afirmar(app.sesion.paquetesTelemetria === 2, "no aceptó los datos de la pulsera nueva");
    } finally { app.destruir(); }
  }, true);

  return conVentana ? P : P.filter((p) => !p.ventana);
}

/** Corre las pruebas y avisa el resultado de cada una. Devuelve cuántas fallaron. */
export async function correr(pruebas, informar) {
  let fallos = 0;
  for (const p of pruebas) {
    const t0 = Date.now();
    try {
      await p.fn();
      informar({ nombre: p.nombre, ok: true, ms: Date.now() - t0 });
    } catch (e) {
      fallos += 1;
      informar({ nombre: p.nombre, ok: false, error: (e && (e.stack || e.message)) || String(e), ms: Date.now() - t0 });
    }
  }
  return fallos;
}
