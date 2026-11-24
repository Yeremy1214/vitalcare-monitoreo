// Lógica de VitalCare sin interfaz: umbrales, clasificación de signos vitales,
// conteo de paquetes perdidos y resumen de sesión.
//
// Es la traducción directa de logica.py (la versión de escritorio). Los criterios
// clínicos y las reglas del número de secuencia son los mismos en las dos versiones;
// si se cambia algo aquí, hay que cambiarlo también allá.
//
// Los tiempos se manejan en segundos (como time.time() en Python).

export const NIVEL_NORMAL = 0, NIVEL_VIGILANCIA = 1, NIVEL_ALERTA = 2;
export const NOMBRE_NIVEL = { 0: "Normal", 1: "Vigilancia", 2: "Alerta" };
export const ESTADO_PACIENTE = { 0: "ESTABLE", 1: "VIGILANCIA", 2: "ALERTA" };

// "normal": rango sin aviso. "vigilancia": fuera de normal pero todavía no crítico.
// Fuera del rango de vigilancia => nivel Alerta.
export const UMBRALES = {
  hr: { nombre: "Frecuencia cardíaca", corto: "FC", unidad: "lpm", normal: [60, 100], vigilancia: [50, 120] },
  spo2: { nombre: "SpO₂", corto: "SpO₂", unidad: "%", normal: [95, 100], vigilancia: [92, 100] },
  temp: { nombre: "Temperatura", corto: "Temp", unidad: "°C", normal: [36.0, 37.5], vigilancia: [35.0, 38.0] },
};

export const BATERIA_BAJA = 30;
export const BATERIA_CRITICA = 15;

export const ahoraS = () => Date.now() / 1000;

/** Convierte a número o devuelve null si no es un número válido (null, texto, NaN, booleano). */
export function aNumero(valor) {
  if (valor === null || valor === undefined || typeof valor === "boolean") return null;
  if (typeof valor === "string" && valor.trim() === "") return null;
  const n = Number(valor);
  if (!Number.isFinite(n)) return null;
  return n;
}

/** Devuelve [nivel, direccion]. nivel null si no hay lectura válida. */
export function clasificar(variable, valor, umbrales = UMBRALES) {
  const v = aNumero(valor);
  if (v === null) return [null, null];
  const u = umbrales[variable];
  const [lo, hi] = u.normal;
  if (lo <= v && v <= hi) return [NIVEL_NORMAL, null];
  const direccion = v < lo ? "baja" : "alta";
  const [vlo, vhi] = u.vigilancia;
  if (vlo <= v && v <= vhi) return [NIVEL_VIGILANCIA, direccion];
  return [NIVEL_ALERTA, direccion];
}

/** Nivel de la batería según los umbrales del centro (por defecto 30 % y 15 %). */
export function nivelBateria(valor, baja = BATERIA_BAJA, critica = BATERIA_CRITICA) {
  const b = aNumero(valor);
  if (b === null) return null;
  if (b >= baja) return NIVEL_NORMAL;
  if (b >= critica) return NIVEL_VIGILANCIA;
  return NIVEL_ALERTA;
}

/**
 * Minutos de batería que quedan a la velocidad de descarga reciente.
 * puntos: lista de [t_epoch_s, porcentaje]. Ajuste lineal por mínimos cuadrados.
 */
export function estimarAutonomiaMin(puntos) {
  if (puntos.length < 5) return null;
  const t0 = puntos[0][0];
  const xs = puntos.map((p) => p[0] - t0), ys = puntos.map((p) => p[1]);
  if (xs[xs.length - 1] < 20) return null;
  const n = xs.length;
  const mx = xs.reduce((a, b) => a + b, 0) / n, my = ys.reduce((a, b) => a + b, 0) / n;
  let den = 0, num = 0;
  for (let i = 0; i < n; i++) { den += (xs[i] - mx) ** 2; num += (xs[i] - mx) * (ys[i] - my); }
  if (den === 0) return null;
  const pendiente = num / den; // % por segundo
  if (pendiente >= -1e-5) return null;
  return Math.max(0, ys[n - 1] / -pendiente / 60);
}

// round() de Python redondea al par en los empates; aquí se imita para dar el mismo texto.
function redondeoPar(x) {
  const r = Math.round(x);
  return Math.abs(x % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r;
}

export function formatoAutonomia(minutos) {
  if (minutos === null || minutos === undefined) return "calculando…";
  if (minutos > 24 * 60) return "más de 24 h";
  if (minutos < 1) return "menos de 1 min";
  const total = redondeoPar(minutos);
  const h = Math.floor(total / 60), m = total % 60;
  return h ? `~${h} h ${String(m).padStart(2, "0")} min` : `~${m} min`;
}

/** Peor nivel entre los recibidos (ignora null). null si no hay ninguno. */
export function estadoGlobal(niveles) {
  const validos = [...niveles].filter((n) => n !== null && n !== undefined);
  return validos.length ? Math.max(...validos) : null;
}

export function tituloAlerta(variable, direccion) {
  const tabla = {
    "hr|alta": "Taquicardia", "hr|baja": "Bradicardia", "spo2|baja": "SpO₂ baja",
    "spo2|alta": "SpO₂ fuera de rango", "temp|alta": "Fiebre alta", "temp|baja": "Temperatura baja",
  };
  return tabla[`${variable}|${direccion}`] || "Signo vital fuera de rango";
}

/** Formato :g de Python (sin ceros sobrantes). */
export function g(v) {
  if (v === null || v === undefined) return "";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  return String(Number(n.toPrecision(6)));
}

export function textoRango(variable, umbrales = UMBRALES) {
  const u = umbrales[variable];
  const [lo, hi] = u.normal;
  if (variable === "spo2") return `Normal: ≥ ${g(lo)} ${u.unidad}`;
  return `Normal: ${g(lo)} a ${g(hi)} ${u.unidad}`;
}

export function etiquetaSeveridad(sev) {
  return { HIGH: "ALTA", MEDIUM: "MEDIA", LOW: "BAJA" }[String(sev).toUpperCase()] || String(sev);
}

// ----------------------------------------------------------------------
// Conteo de paquetes perdidos
// ----------------------------------------------------------------------

/**
 * El campo seq es un contador compartido por telemetría y eventos. Un salto significa
 * que faltan paquetes. El contador guarda CUÁLES faltan, para poder pedir su reenvío y
 * marcarlos como recuperados cuando llegan tarde.
 *   faltantes:      detectados y todavía en espera de recuperarse
 *   irrecuperables: se agotó el tiempo de recuperación; es una pérdida real
 *   recuperados:    llegaron tarde (reenviados) y ya están completos
 *   perdidos:       faltantes + irrecuperables
 */
export class ContadorSeq {
  static VENTANA_VISTOS = 2000;
  static DISTANCIA_DUPLICADO = 10;

  constructor() {
    this.ultimo = null;
    this.recibidos = 0;
    this.reinicios = 0;
    this.duplicados = 0;
    this.detectados = 0;
    this.recuperados = 0;
    this.faltantes = new Set();
    this.irrecuperables = new Set();
    this._vistos = [];
    this._vistosSet = new Set();
    this.nuevos = [];
    this.recuperadoAhora = false;
    this.recuperadoSeq = null;
  }

  get perdidos() { return this.faltantes.size + this.irrecuperables.size; }

  _ver(s) {
    if (this._vistos.length === ContadorSeq.VENTANA_VISTOS) this._vistosSet.delete(this._vistos.shift());
    this._vistos.push(s);
    this._vistosSet.add(s);
  }

  /** La pulsera nueva empieza su propia numeración; se conserva el conteo histórico. */
  reiniciarSecuencia() {
    this.ultimo = null;
    this.faltantes.clear();
    this.nuevos = [];
    this.recuperadoAhora = false;
    this.recuperadoSeq = null;
    this._vistos = [];
    this._vistosSet.clear();
    this.reinicios += 1;
  }

  /** Registra un seq recibido. Devuelve cuántos faltantes nuevos detectó. */
  registrar(seq) {
    this.nuevos = [];
    this.recuperadoAhora = false;
    this.recuperadoSeq = null;
    let s = aNumero(seq);
    if (s === null) return 0;
    s = Math.trunc(s);
    if (this.faltantes.has(s) || this.irrecuperables.has(s)) {
      this.faltantes.delete(s);
      this.irrecuperables.delete(s);
      this.recuperados += 1;
      this.recibidos += 1;
      this.recuperadoAhora = true;
      this.recuperadoSeq = s;
      this._ver(s);
      return 0;
    }
    if (this._vistosSet.has(s) && this.ultimo !== null && this.ultimo - s >= 0 &&
        this.ultimo - s <= ContadorSeq.DISTANCIA_DUPLICADO) {
      this.duplicados += 1;
      return 0;
    }
    if (this.ultimo === null) {
      this.recibidos += 1;
    } else if (s > this.ultimo) {
      const hueco = s - this.ultimo - 1;
      if (hueco > 0) {
        const fin = Math.min(s, this.ultimo + 1 + 5000);
        for (let k = this.ultimo + 1; k < fin; k++) this.nuevos.push(k);
        this.nuevos.forEach((k) => this.faltantes.add(k));
        this.detectados += this.nuevos.length;
      }
      this.recibidos += 1;
    } else { // seq retrocedió mucho: la pulsera se reinició
      this.reinicios += 1;
      this.recibidos += 1;
      this.faltantes.forEach((k) => this.irrecuperables.add(k));
      this.faltantes = new Set();
      this._vistos = [];
      this._vistosSet.clear();
    }
    this.ultimo = s;
    this._ver(s);
    return this.nuevos.length;
  }

  /** Pasa a irrecuperables los faltantes cuyo plazo venció. */
  marcarIrrecuperables(seqs) {
    for (const s of seqs) {
      if (this.faltantes.has(s)) { this.faltantes.delete(s); this.irrecuperables.add(s); }
    }
  }

  get porcentajePerdida() {
    const total = this.recibidos + this.perdidos;
    return total ? (100 * this.perdidos) / total : 0;
  }
}

// ----------------------------------------------------------------------
// Datos de una sesión de monitoreo
// ----------------------------------------------------------------------

function bisectLeft(arr, x) {
  let lo = 0, hi = arr.length;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (arr[mid] < x) lo = mid + 1; else hi = mid; }
  return lo;
}

/** Acumula todo lo recibido durante una conexión para graficar y resumir. */
export class Sesion {
  constructor(umbrales = UMBRALES) {
    this.umbrales = umbrales;
    this.inicio = ahoraS();
    this.fin = null;
    this.dispositivo = null;
    this.t = []; this.hr = []; this.spo2 = []; this.temp = []; this.batt = [];
    this.estadoSensor = null;
    this.seq = new ContadorSeq();
    this.eventos = []; // [t_epoch, nombre_evento]
    this.cambios = []; // [t_epoch, texto] cambios de pulsera durante la sesión
    this.paquetesTelemetria = 0;
    this.alertasGeneradas = 0;
    this.respuestasS = [];
    this.resoluciones = {};
    this._base = null;
    this.fueraRango = { hr: 0, spo2: 0, temp: 0 };
    this.validos = { hr: 0, spo2: 0, temp: 0 };
  }

  marcarCambioPulsera(anterior, nueva, t = null) {
    this.cambios.push([t === null ? ahoraS() : t, `${anterior} → ${nueva}`]);
  }

  agregarTelemetria(p, t = null) {
    t = t === null ? ahoraS() : t;
    this.paquetesTelemetria += 1;
    this.dispositivo = p.id ?? this.dispositivo;
    this.estadoSensor = p.st ?? this.estadoSensor;
    this.seq.registrar(p.seq);
    const ts = aNumero(p.ts);
    if (this._base === null && ts !== null) this._base = [t, ts];
    let pos = this.t.length;
    if (this.seq.recuperadoAhora && ts !== null && this._base !== null) {
      // Paquete reenviado: se coloca en su hora original, no en la hora en que llegó.
      t = this._base[0] + (ts - this._base[1]) / 1000;
      pos = bisectLeft(this.t, t);
    }
    this.t.splice(pos, 0, t);
    for (const variable of ["hr", "spo2", "temp"]) {
      const valor = aNumero(p[variable]);
      this[variable].splice(pos, 0, valor);
      if (valor !== null) {
        this.validos[variable] += 1;
        const [nivel] = clasificar(variable, valor, this.umbrales);
        if (nivel && nivel > NIVEL_NORMAL) this.fueraRango[variable] += 1;
      }
    }
    this.batt.splice(pos, 0, aNumero(p.batt));
  }

  agregarEvento(p, t = null) {
    t = t === null ? ahoraS() : t;
    this.dispositivo = p.id ?? this.dispositivo;
    this.seq.registrar(p.seq);
    this.eventos.push([t, String(p.ev)]);
  }

  autonomiaMin(ventanaS = 900) {
    if (!this.t.length) return null;
    const corte = this.t[this.t.length - 1] - ventanaS;
    const puntos = [];
    for (let i = 0; i < this.t.length; i++) {
      if (this.batt[i] !== null && this.t[i] >= corte) puntos.push([this.t[i], this.batt[i]]);
    }
    return estimarAutonomiaMin(puntos);
  }

  alertaGenerada() { this.alertasGeneradas += 1; }

  alertaAtendida(respuestaS, resolucion = null) {
    this.respuestasS.push(Number(respuestaS));
    if (resolucion) this.resoluciones[resolucion] = (this.resoluciones[resolucion] || 0) + 1;
  }

  cerrar() { if (this.fin === null) this.fin = ahoraS(); }

  resumen() {
    const fin = this.fin !== null ? this.fin : ahoraS();
    const variables = {};
    for (const variable of ["hr", "spo2", "temp"]) {
      const valores = this[variable].filter((v) => v !== null);
      const n = valores.length;
      variables[variable] = {
        n,
        min: n ? Math.min(...valores) : null,
        max: n ? Math.max(...valores) : null,
        media: n ? valores.reduce((a, b) => a + b, 0) / n : null,
        pct_fuera_rango: n ? (100 * this.fueraRango[variable]) / n : null,
      };
    }
    const baterias = this.batt.filter((b) => b !== null);
    const conteoEventos = {};
    for (const [, nombre] of this.eventos) conteoEventos[nombre] = (conteoEventos[nombre] || 0) + 1;
    const r = this.respuestasS;
    return {
      duracion_s: Math.max(0, fin - this.inicio),
      dispositivo: this.dispositivo,
      paquetes_telemetria: this.paquetesTelemetria,
      paquetes_recibidos: this.seq.recibidos,
      paquetes_perdidos: this.seq.perdidos,
      paquetes_detectados: this.seq.detectados,
      paquetes_recuperados: this.seq.recuperados,
      perdida_pct: this.seq.porcentajePerdida,
      reinicios: this.seq.reinicios,
      variables,
      bateria_inicial: baterias.length ? baterias[0] : null,
      bateria_final: baterias.length ? baterias[baterias.length - 1] : null,
      eventos: conteoEventos,
      alertas_generadas: this.alertasGeneradas,
      alertas_atendidas: r.length,
      resoluciones: { ...this.resoluciones },
      respuesta_media_s: r.length ? r.reduce((a, b) => a + b, 0) / r.length : null,
      respuesta_max_s: r.length ? Math.max(...r) : null,
    };
  }
}

// ----------------------------------------------------------------------
// Formato de texto
// ----------------------------------------------------------------------

const dos = (n) => String(n).padStart(2, "0");

export function formatoDuracion(segundos) {
  let s = Math.trunc(Math.max(0, segundos));
  const h = Math.floor(s / 3600);
  const resto = s % 3600;
  const m = Math.floor(resto / 60);
  s = resto % 60;
  return h ? `${dos(h)}:${dos(m)}:${dos(s)}` : `${dos(m)}:${dos(s)}`;
}

function fmt(valor, decimales = 1, sufijo = "") {
  return valor === null || valor === undefined ? "--" : `${valor.toFixed(decimales)}${sufijo}`;
}

export function horaLocal(tS, conMs = false) {
  const d = new Date(tS * 1000);
  const base = `${d.getFullYear()}-${dos(d.getMonth() + 1)}-${dos(d.getDate())} ${dos(d.getHours())}:${dos(d.getMinutes())}:${dos(d.getSeconds())}`;
  return conMs ? `${base}.${String(d.getMilliseconds()).padStart(3, "0")}` : base;
}

/** Resumen en texto plano para guardar junto a los CSV de la sesión. */
export function formatearResumen(res, fuente = "", inicio = null) {
  const L = ["RESUMEN DE SESIÓN - Testbed de pulsera (ITT466)", "=".repeat(52)];
  if (inicio !== null) L.push(`Inicio:            ${horaLocal(inicio)}`);
  if (fuente) L.push(`Fuente de datos:   ${fuente}`);
  L.push(`Dispositivo:       ${res.dispositivo || "--"}`);
  L.push(`Duración:          ${formatoDuracion(res.duracion_s)}`);
  L.push("");
  L.push("Enlace");
  L.push(`  Paquetes recibidos: ${res.paquetes_recibidos}`);
  L.push(`  Paquetes faltantes detectados: ${res.paquetes_detectados}`);
  L.push(`  Recuperados por reenvío:       ${res.paquetes_recuperados}`);
  L.push(`  Sin recuperar (pérdida real):  ${res.paquetes_perdidos} (${res.perdida_pct.toFixed(1)} %)`);
  L.push(`  Reinicios del dispositivo detectados: ${res.reinicios}`);
  L.push(`  Batería: ${fmt(res.bateria_inicial, 0, " %")} al inicio, ${fmt(res.bateria_final, 0, " %")} al final`);
  L.push("");
  L.push("Signos vitales (mínimo / máximo / promedio / % del tiempo fuera de rango normal)");
  for (const variable of ["hr", "spo2", "temp"]) {
    const u = UMBRALES[variable], v = res.variables[variable];
    L.push(`  ${u.corto.padEnd(6)} ${fmt(v.min).padStart(6)} / ${fmt(v.max).padStart(6)} / ${fmt(v.media).padStart(6)} ${u.unidad.padEnd(4)}` +
      ` | fuera de rango: ${fmt(v.pct_fuera_rango, 0, " %")}`);
  }
  L.push("");
  L.push("Eventos y alertas");
  const nombres = Object.keys(res.eventos).sort();
  if (nombres.length) nombres.forEach((n) => L.push(`  Evento ${n}: ${res.eventos[n]}`));
  else L.push("  Sin eventos de la pulsera (caída / SOS)");
  L.push(`  Alertas generadas: ${res.alertas_generadas}`);
  L.push(`  Alertas cerradas con detalle: ${res.alertas_atendidas}`);
  Object.keys(res.resoluciones || {}).sort().forEach((n) => L.push(`    - ${n}: ${res.resoluciones[n]}`));
  L.push(`  Tiempo de respuesta: promedio ${fmt(res.respuesta_media_s, 1, " s")}, máximo ${fmt(res.respuesta_max_s, 1, " s")}`);
  L.push("");
  L.push("Nota: los umbrales son valores de referencia para adulto en reposo, pendientes de validar.");
  return L.join("\n") + "\n";
}
