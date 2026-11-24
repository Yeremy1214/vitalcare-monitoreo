// Registro en CSV de una sesión: la traducción de registro.py.
//
// Por sesión se crean los mismos archivos que en escritorio:
//   telemetria_<marca>.csv  una fila por paquete de telemetría
//   eventos_<marca>.csv     una fila por evento de la pulsera (FALL, SOS)
//   alertas_<marca>.csv     bitácora de alertas: generada, escalada y cerrada con el detalle del personal
//   huecos_<marca>.csv      cada paquete que faltó: detectado, recuperado o no recuperado
// Cada fila se guarda en el navegador en el momento en que llega (ver almacen.js), así que
// no se pierde nada si la página se cierra de golpe. Desde "Carpeta de datos" se descargan.

const dos = (n) => String(n).padStart(2, "0");

export function horaMs(d = new Date()) {
  return `${d.getFullYear()}-${dos(d.getMonth() + 1)}-${dos(d.getDate())} ${dos(d.getHours())}:${dos(d.getMinutes())}:${dos(d.getSeconds())}.${String(d.getMilliseconds()).padStart(3, "0")}`;
}

export function marcaDe(d = new Date()) {
  return `${d.getFullYear()}${dos(d.getMonth() + 1)}${dos(d.getDate())}_${dos(d.getHours())}${dos(d.getMinutes())}${dos(d.getSeconds())}`;
}

/** Una fila CSV con las mismas reglas de comillas que csv.writer de Python. */
export function filaCSV(valores) {
  return valores.map((v) => {
    if (v === null || v === undefined) return "";
    const s = String(v);
    return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  }).join(",");
}

export const ENCABEZADOS = {
  telemetria: ["hora_local", "id", "seq", "ts_dispositivo_ms", "hr", "spo2", "temp", "batt", "st"],
  eventos: ["hora_local", "id", "seq", "ts_dispositivo_ms", "evento", "severidad"],
  alertas: ["hora_local", "accion", "origen", "tipo", "detalle", "seq", "respuesta_s",
    "resolucion", "accion_realizada", "atendido_por", "indicacion_medica", "medico"],
  huecos: ["hora_local", "seq_faltante", "estado", "detalle"],
};

const valor = (p, k) => (p[k] === undefined ? null : p[k]);

export class RegistradorCSV {
  /** destino: objeto con agregarFila(marca, archivo, linea). */
  constructor(destino, marca = marcaDe()) {
    this.destino = destino;
    this.marca = marca;
    this.cerrado = false;
    this.rutaTelemetria = `telemetria_${marca}.csv`;
    for (const [archivo, enc] of Object.entries(ENCABEZADOS)) destino.agregarFila(marca, archivo, filaCSV(enc));
  }

  _escribir(archivo, valores) {
    if (this.cerrado) return;
    this.destino.agregarFila(this.marca, archivo, filaCSV(valores));
  }

  registrarTelemetria(p) {
    this._escribir("telemetria", [horaMs(), valor(p, "id"), valor(p, "seq"), valor(p, "ts"), valor(p, "hr"),
      valor(p, "spo2"), valor(p, "temp"), valor(p, "batt"), valor(p, "st")]);
  }

  registrarEvento(p) {
    this._escribir("eventos", [horaMs(), valor(p, "id"), valor(p, "seq"), valor(p, "ts"), valor(p, "ev"), valor(p, "sev")]);
  }

  /** accion: GENERADA, ESCALADA o CERRADA. origen: DISPOSITIVO, UMBRAL, MANUAL o SISTEMA. */
  registrarAlerta(accion, origen, tipo, detalle = "", seq = null, respuestaS = null, cierre = null) {
    const c = cierre || {};
    this._escribir("alertas", [horaMs(), accion, origen, tipo, detalle, seq,
      respuestaS === null ? "" : Number(respuestaS).toFixed(1),
      c.resolucion || "", c.accion_realizada || "", c.atendido_por || "",
      cierre ? (c.indicacion_medica ? "SI" : "NO") : "", c.medico || ""]);
  }

  /** estado: DETECTADO, RECUPERADO o NO_RECUPERADO. */
  registrarHueco(seq, estado, detalle = "") {
    this._escribir("huecos", [horaMs(), seq, estado, detalle]);
  }

  cerrar() { this.cerrado = true; }
}
