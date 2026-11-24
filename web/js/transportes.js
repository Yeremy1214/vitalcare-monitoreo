// Capa de transporte: de dónde vienen los paquetes. Traducción de transportes.py.
//
//   - SimuladorTransport: genera paquetes en el navegador, sin hardware, con escenarios,
//                         caída / SOS, caída de red y reenvío de paquetes.
//   - BLETransport:       se conecta por Bluetooth directo a la pulsera (Web Bluetooth de
//                         Chrome o Edge), con los UUID de docs/contrato_pulsera.md.
//   - MQTTTransport:      recibe la pulsera real a través del hub (pulsera -> BLE -> hub ->
//                         WiFi -> broker MQTT -> esta página).
//
// La fuente WiFi/UDP de la versión de escritorio no existe aquí: un navegador no puede
// abrir un puerto UDP.
//
// Las tres entregan el mismo formato (contrato JSON):
//   telemetría: {"id":"W01","seq":145,"ts":123456,"hr":78,"spo2":97,"temp":36.8,"batt":82,"st":"OK"}
//   evento:     {"id":"W01","seq":146,"ts":125460,"ev":"FALL","sev":"HIGH"}

export const FUENTE_SIM = "Simulador";
export const FUENTE_BLE = "Bluetooth (BLE)";
export const FUENTE_MQTT = "Hub (MQTT)";
export const FUENTES = [FUENTE_SIM, FUENTE_BLE, FUENTE_MQTT];

const decodificador = typeof TextDecoder !== "undefined" ? new TextDecoder("utf-8", { fatal: true }) : null;

/**
 * Interpreta texto o bytes con uno o varios JSON (uno por línea) y los entrega.
 * Un paquete con la clave "ev" es un evento; cualquier otro es telemetría.
 * Los paquetes corruptos se descartan sin propagar el error.
 */
export function despacharJSON(datos, onTelemetry, onEvent) {
  if (typeof datos !== "string") {
    try {
      const bytes = datos instanceof DataView ? new Uint8Array(datos.buffer, datos.byteOffset, datos.byteLength)
        : datos instanceof ArrayBuffer ? new Uint8Array(datos) : datos;
      datos = decodificador.decode(bytes);
    } catch { return 0; }
  }
  let entregados = 0;
  for (let linea of datos.split(/\r?\n/)) {
    linea = linea.trim();
    if (!linea) continue;
    let paquete;
    try { paquete = JSON.parse(linea); } catch { continue; }
    if (!paquete || typeof paquete !== "object" || Array.isArray(paquete)) continue;
    if ("ev" in paquete) onEvent(paquete); else onTelemetry(paquete);
    entregados += 1;
  }
  return entregados;
}

class Transporte {
  constructor() { this.soportaReenvio = false; this._detenido = false; }
  async desconectar() { this._detenido = true; }
  /** Pide a la fuente reenviar los seq indicados. Devuelve true si la fuente lo soporta. */
  solicitarReenvio() { return false; }
}

// ----------------------------------------------------------------------
// Simulador
// ----------------------------------------------------------------------

export const ESCENARIOS = {
  "Paciente estable": { hr: 75, spo2: 97, temp: 36.7 },
  "Fiebre": { hr: 98, spo2: 96, temp: 38.4 },
  "SpO₂ baja": { hr: 96, spo2: 91, temp: 36.8 },
  "Taquicardia": { hr: 132, spo2: 96, temp: 36.9 },
  "Bradicardia": { hr: 44, spo2: 97, temp: 36.5 },
  "Señal inestable (pérdida de paquetes)": { hr: 78, spo2: 97, temp: 36.7, perdida: 0.4 },
  "Señal inestable SIN reenvío (pérdida real)": { hr: 78, spo2: 97, temp: 36.7, perdida: 0.4, sin_reenvio: true },
  "Batería baja (descarga rápida)": { hr: 75, spo2: 97, temp: 36.7, drenaje: 1.5 },
};
export const ESCENARIO_BATERIA = "Batería baja (descarga rápida)";

const uniforme = (a, b) => a + Math.random() * (b - a);
const monotono = () => (typeof performance !== "undefined" ? performance.now() : Date.now()) / 1000;
const dormir = (s) => new Promise((ok) => setTimeout(ok, s * 1000));
const redondear = (x, d = 0) => { const f = 10 ** d; return Math.round(x * f) / f; };

export class SimuladorTransport extends Transporte {
  constructor({ deviceId = "W01", intervaloS = 2.0, probEventoPorCiclo = 0 } = {}) {
    super();
    this.soportaReenvio = true;
    this.deviceId = deviceId;
    this.intervaloS = intervaloS;
    this.probEventoPorCiclo = probEventoPorCiclo;
    this._seq = 0;
    this._hr = 75.0; this._spo2 = 97.0; this._temp = 36.7; this._batt = 95.0;
    this._t0 = monotono();
    this._activo = false;
    this._pendientes = [];
    this._reenvios = [];
    this._historial = new Map(); // seq -> ["t" | "e", paquete]
    this.escenario = "Paciente estable";
    this._objetivo = { ...ESCENARIOS[this.escenario] };
    this._corteHasta = 0;
  }

  async conectar() { this._activo = !this._detenido; }
  async desconectar() { this._detenido = true; this._activo = false; }

  fijarEscenario(nombre) {
    if (!(nombre in ESCENARIOS)) throw new Error(`Escenario desconocido: ${nombre}`);
    if (nombre === ESCENARIO_BATERIA) this._batt = Math.min(this._batt, 34.0);
    else if (this.escenario === ESCENARIO_BATERIA) this._batt = 100.0; // se simula el cambio de pulsera
    this.escenario = nombre;
    this._objetivo = { ...ESCENARIOS[nombre] };
    this.soportaReenvio = !this._objetivo.sin_reenvio;
  }

  solicitarReenvio(seqs) {
    if (!this.soportaReenvio) return false;
    for (const s of seqs) this._reenvios.push(Math.trunc(s));
    return true;
  }

  dispararEvento(tipo) {
    if (tipo !== "FALL" && tipo !== "SOS") throw new Error("tipo debe ser 'FALL' o 'SOS'");
    this._pendientes.push(tipo);
  }

  _siguienteSeq() { this._seq += 1; return this._seq; }
  _tsMs() { return Math.trunc((monotono() - this._t0) * 1000); }

  _pasoFisiologico() {
    const o = this._objetivo;
    this._hr += (o.hr - this._hr) * 0.35 + uniforme(-1.2, 1.2);
    this._spo2 += (o.spo2 - this._spo2) * 0.35 + uniforme(-0.35, 0.35);
    this._temp += (o.temp - this._temp) * 0.20 + uniforme(-0.04, 0.04);
    this._hr = Math.min(180, Math.max(35, this._hr));
    this._spo2 = Math.min(100, Math.max(80, this._spo2));
    this._temp = Math.min(41.0, Math.max(33.0, this._temp));
    this._batt = Math.max(0, this._batt - 0.01 - (o.drenaje || 0));
  }

  _nuevaTelemetria() {
    this._pasoFisiologico();
    return {
      id: this.deviceId, seq: this._siguienteSeq(), ts: this._tsMs(), hr: Math.round(this._hr),
      spo2: Math.round(this._spo2), temp: redondear(this._temp, 1), batt: Math.round(this._batt),
      st: this._spo2 >= 90 ? "OK" : "ERR",
    };
  }

  _nuevoEvento(tipo = null) {
    return { id: this.deviceId, seq: this._siguienteSeq(), ts: this._tsMs(),
      ev: tipo || (Math.random() < 0.5 ? "FALL" : "SOS"), sev: "HIGH" };
  }

  /** Simula una caída del enlace: la pulsera sigue midiendo y numerando, pero nada sale al aire. */
  cortar(segundos = 12) { this._corteHasta = monotono() + Number(segundos); }
  enlaceCaido() { return monotono() < this._corteHasta; }

  _guardar(tipo, paquete) {
    this._historial.set(paquete.seq, [tipo, paquete]);
    if (this._historial.size > 2000) this._historial.delete(this._historial.keys().next().value);
  }

  async recibirPaquetes(onTelemetry, onEvent) {
    await this.conectar();
    let proximo = monotono() + this.intervaloS;
    while (this._activo) {
      await dormir(0.05);
      if (!this._activo) break;
      while (this._pendientes.length) { // eventos pedidos desde la interfaz: salen de inmediato
        const evento = this._nuevoEvento(this._pendientes.shift());
        this._guardar("e", evento);
        if (!this.enlaceCaido()) onEvent(evento);
      }
      if (!this.enlaceCaido()) {
        while (this._reenvios.length) { // reenvíos pedidos para llenar huecos
          const guardado = this._historial.get(this._reenvios.shift());
          if (guardado) (guardado[0] === "t" ? onTelemetry : onEvent)(guardado[1]);
        }
      }
      if (monotono() < proximo) continue;
      proximo += this.intervaloS;
      if (this._batt <= 0 && this._objetivo.drenaje) continue; // se quedó sin batería: deja de transmitir
      const paquete = this._nuevaTelemetria();
      this._guardar("t", paquete);
      if (this.enlaceCaido()) continue; // el paquete queda guardado para reenviarlo
      if (Math.random() >= (this._objetivo.perdida || 0)) onTelemetry(paquete);
      if (Math.random() < this.probEventoPorCiclo) {
        const evento = this._nuevoEvento();
        this._guardar("e", evento);
        onEvent(evento);
      }
    }
  }
}

// ----------------------------------------------------------------------
// Bluetooth directo (Web Bluetooth)
// ----------------------------------------------------------------------

export const SERVICE_UUID = "81540fdd-3371-4ebf-b228-7363bed623e7";
export const TELEMETRY_CHAR_UUID = "3e9ad104-fdf4-4e20-a4f1-e5d67040c7a6";
export const EVENT_CHAR_UUID = "72058786-0707-460c-9eda-342769fe84b6";

export class BLETransport extends Transporte {
  /** nombre: con el que se anuncia la pulsera (W01). Vacío: muestra todas las que tengan el servicio. */
  constructor(nombre = "W01") {
    super();
    this.nombre = (nombre || "").trim();
    this.dispositivo = null;
    this._buffers = { t: "", e: "" };
  }

  static disponible() { return typeof navigator !== "undefined" && !!navigator.bluetooth; }

  /**
   * Hay que llamarlo directamente desde el clic en Conectar: el navegador solo deja buscar
   * dispositivos Bluetooth como respuesta a un gesto del usuario.
   */
  async recibirPaquetes(onTelemetry, onEvent) {
    if (!BLETransport.disponible()) {
      throw new Error("Este navegador no tiene Bluetooth. Use Google Chrome o Microsoft Edge en Windows, " +
        "con el Bluetooth de la PC encendido, y abra la página desde https.");
    }
    const filtros = this.nombre ? [{ name: this.nombre }] : [{ services: [SERVICE_UUID] }];
    try {
      this.dispositivo = await navigator.bluetooth.requestDevice({ filters: filtros, optionalServices: [SERVICE_UUID] });
    } catch (e) {
      if (e && e.name === "NotFoundError") {
        throw new Error(`No se eligió ninguna pulsera${this.nombre ? ` anunciándose como '${this.nombre}'` : ""}. ` +
          "Verifique que esté encendida y que no esté conectada al hub ni a otra PC.");
      }
      throw e;
    }
    if (this._detenido) return;
    const servidor = await this.dispositivo.gatt.connect();
    const servicio = await servidor.getPrimaryService(SERVICE_UUID);
    const tel = await servicio.getCharacteristic(TELEMETRY_CHAR_UUID);
    const ev = await servicio.getCharacteristic(EVENT_CHAR_UUID);
    const alRecibir = (clave) => (e) => {
      // Si la MTU es chica el JSON puede llegar en varios pedazos: se juntan hasta tener uno completo.
      let texto;
      try { texto = decodificador.decode(new Uint8Array(e.target.value.buffer)); } catch { return; }
      this._buffers[clave] += texto;
      const b = this._buffers[clave].trim();
      let completo = false;
      try { JSON.parse(b.split(/\r?\n/).filter(Boolean).pop() || ""); completo = true; } catch { /* falta un pedazo */ }
      if (completo || this._buffers[clave].length > 2048) {
        despacharJSON(this._buffers[clave], onTelemetry, onEvent);
        this._buffers[clave] = "";
      }
    };
    tel.addEventListener("characteristicvaluechanged", alRecibir("t"));
    ev.addEventListener("characteristicvaluechanged", alRecibir("e"));
    await tel.startNotifications();
    await ev.startNotifications();
    await new Promise((fin) => {
      this._fin = fin;
      this.dispositivo.addEventListener("gattserverdisconnected", () => fin());
      if (this._detenido) fin();
    });
  }

  async desconectar() {
    this._detenido = true;
    try { if (this.dispositivo && this.dispositivo.gatt.connected) this.dispositivo.gatt.disconnect(); } catch { /* ya desconectada */ }
    if (this._fin) this._fin();
  }
}

// ----------------------------------------------------------------------
// Hub por MQTT
// ----------------------------------------------------------------------

/**
 * Tópicos que publica el hub (FIRMWARE/hub):
 *   <prefijo>/pulsera/<id>/telemetria   JSON de telemetría
 *   <prefijo>/pulsera/<id>/evento       JSON de evento (FALL, SOS)
 *   <prefijo>/hub/<hub>/estado          "online" / "offline" (mensaje retenido)
 *   <prefijo>/hub/<hub>/info            {"pulsera":"conectada","pendientes":N}
 * Y el que escucha el hub para mandarle órdenes a la pulsera:
 *   <prefijo>/pulsera/<id>/comando      {"cmd":"ack",...} o {"cmd":"resend","seq":[...]}
 */
// Si faltan muchísimos (por ejemplo, la pulsera estuvo apagada), se piden los primeros 120 por ciclo.
const MAX_REENVIO_POR_CICLO = 120;

const textoDe = (payload) => (typeof payload === "string" ? payload : new TextDecoder().decode(payload));

export function topicosMQTT(prefijo) {
  return [`${prefijo}/pulsera/+/telemetria`, `${prefijo}/pulsera/+/evento`, `${prefijo}/hub/+/estado`, `${prefijo}/hub/+/info`];
}

export class MQTTTransport extends Transporte {
  constructor({ broker, puerto = 8884, ruta = "/mqtt", prefijo, usuario = "", clave = "", alHub = null, mqttLib = null }) {
    super();
    // Pedir el reenvío no cuesta nada: si el firmware todavía no lo atiende, el paquete se
    // declara perdido a los 12 s, igual que en la versión de escritorio.
    this.soportaReenvio = true;
    Object.assign(this, { broker, puerto, ruta, prefijo, usuario, clave, alHub });
    this.mqtt = mqttLib || (typeof window !== "undefined" ? window.mqtt : null);
    this.cliente = null;
    this.ultimoId = null;
    this.idDestino = null; // la pulsera del paciente en monitoreo
  }

  url() {
    const esquema = Number(this.puerto) === 80 || Number(this.puerto) === 8000 || Number(this.puerto) === 8080 || Number(this.puerto) === 9001 ? "ws" : "wss";
    return `${esquema}://${this.broker}:${this.puerto}${this.ruta}`;
  }

  async recibirPaquetes(onTelemetry, onEvent) {
    if (!this.mqtt) throw new Error("No se cargó la librería MQTT (vendor/mqtt.min.js).");
    if (!this.prefijo) throw new Error("Falta el prefijo de los tópicos (Configuración → mqtt_prefijo).");
    const op = { clientId: "vitalcare-web-" + Math.random().toString(16).slice(2, 10), reconnectPeriod: 3000,
      connectTimeout: 10000, clean: true };
    if (this.usuario) { op.username = this.usuario; op.password = this.clave; }
    await new Promise((listo, fallo) => {
      if (this._detenido) { listo(); return; }
      const c = this.mqtt.connect(this.url(), op);
      this.cliente = c;
      let conectado = false;
      const plazo = setTimeout(() => {
        if (!conectado) { c.end(true); fallo(new Error(`No se pudo conectar al broker ${this.broker}:${this.puerto}. ` +
          "Revise la conexión a internet y los datos del broker en Configuración.")); }
      }, 15000);
      c.on("connect", () => {
        conectado = true;
        clearTimeout(plazo);
        c.subscribe(topicosMQTT(this.prefijo), { qos: 1 });
        if (this.alHub) this.alHub({ tipo: "broker", texto: "conectado" });
      });
      c.on("reconnect", () => this.alHub && this.alHub({ tipo: "broker", texto: "reconectando" }));
      c.on("offline", () => this.alHub && this.alHub({ tipo: "broker", texto: "sin conexión" }));
      c.on("error", (e) => { if (!conectado) { clearTimeout(plazo); c.end(true); fallo(new Error(`Error del broker: ${e.message || e}`)); } });
      c.on("close", () => { if (this._detenido) listo(); });
      c.on("message", (tema, payload) => {
        const partes = tema.split("/");
        if (tema.endsWith("/estado")) {
          if (this.alHub) this.alHub({ tipo: "estado", hub: partes[partes.length - 2], texto: textoDe(payload).trim() });
          return;
        }
        if (tema.endsWith("/info")) {
          let d = null;
          try { d = JSON.parse(textoDe(payload)); } catch { return; }
          if (this.alHub) this.alHub({ tipo: "info", hub: partes[partes.length - 2], datos: d });
          return;
        }
        despacharJSON(payload, (p) => { this.ultimoId = p.id || this.ultimoId; onTelemetry(p); },
          (p) => { this.ultimoId = p.id || this.ultimoId; onEvent(p); });
      });
    });
  }

  enviarComando(idPulsera, comando) {
    if (!this.cliente || !this.cliente.connected || !idPulsera) return Promise.resolve(false);
    return new Promise((ok) => {
      this.cliente.publish(`${this.prefijo}/pulsera/${idPulsera}/comando`, JSON.stringify(comando), { qos: 1 },
        (err) => ok(!err));
    });
  }

  solicitarReenvio(seqs) {
    const id = this.idDestino || this.ultimoId;
    if (!id || !this.cliente || !this.cliente.connected) return true; // se reintenta; vence a los 12 s
    // En tandas de 20 como máximo: el hub y la pulsera tienen poca memoria para cada orden.
    const lista = seqs.map((s) => Math.trunc(s)).slice(0, MAX_REENVIO_POR_CICLO);
    for (let i = 0; i < lista.length; i += 20) this.enviarComando(id, { cmd: "resend", seq: lista.slice(i, i + 20) });
    return true;
  }

  async desconectar() {
    this._detenido = true;
    if (this.cliente) { const c = this.cliente; this.cliente = null; c.end(true); }
  }
}
