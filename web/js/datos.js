// Base de datos del sistema: la misma de la versión de escritorio (datos.py), con el
// mismo esquema SQLite y las mismas consultas. En el navegador corre con sql.js
// (SQLite compilado a WebAssembly) y el archivo de la base se guarda en el propio
// navegador (IndexedDB) cada vez que cambia algo, así que sobrevive a recargas,
// cierres del navegador y apagones.
//
// Reglas que importan para una aplicación médica (iguales que en escritorio):
//   - Nada se borra en silencio. Un paciente con datos no se elimina, se da de alta;
//     una pulsera con historial no se elimina, se da de baja.
//   - Toda alta, baja, cambio de umbral y cambio de pulsera queda en la auditoría.

export const SEXOS = ["Femenino", "Masculino", "Otro"];
export const AMBOS = "Ambos";

export const ESTADO_PULSERA = ["Disponible", "En uso", "Cargando", "Fuera de servicio"];
export const MOTIVOS_FIN = ["Alta del paciente", "Cambio por batería", "Falla del dispositivo",
  "Cambio de habitación", "Otro"];

// Signos que maneja el sistema. Los tres primeros los mide la pulsera; los demás
// los registra el personal a mano, porque la pulsera no tiene cómo medirlos.
export const SIGNOS = {
  hr: ["Frecuencia cardíaca", "lpm", "pulsera"],
  spo2: ["Saturación de oxígeno", "%", "pulsera"],
  temp: ["Temperatura", "°C", "pulsera"],
  pas: ["Presión arterial sistólica", "mmHg", "manual"],
  pad: ["Presión arterial diastólica", "mmHg", "manual"],
  fr: ["Frecuencia respiratoria", "rpm", "manual"],
};
export const SIGNOS_MANUALES = Object.keys(SIGNOS).filter((k) => SIGNOS[k][2] === "manual");

// Perfiles por grupo de edad: [nombre, meses mínimos, meses máximos]
export const PERFILES_BASE = [
  ["Neonato", 0, 1], ["Lactante", 1, 12], ["Niño pequeño", 12, 36], ["Preescolar", 36, 72],
  ["Escolar", 72, 144], ["Adolescente", 144, 216], ["Adulto", 216, 780], ["Adulto mayor", 780, 1500],
];

// Umbrales de referencia por perfil: signo -> [normal_min, normal_max, vig_min, vig_max]
export const UMBRALES_BASE = {
  "Neonato": { hr: [100, 180, 90, 200], spo2: [95, 100, 92, 100], temp: [36.5, 37.5, 36.0, 38.0],
    pas: [60, 90, 50, 100], pad: [20, 60, 15, 70], fr: [30, 60, 25, 70] },
  "Lactante": { hr: [100, 160, 90, 180], spo2: [95, 100, 92, 100], temp: [36.0, 37.5, 35.5, 38.0],
    pas: [70, 100, 60, 110], pad: [35, 65, 30, 75], fr: [30, 53, 24, 60] },
  "Niño pequeño": { hr: [90, 150, 80, 170], spo2: [95, 100, 92, 100], temp: [36.0, 37.5, 35.5, 38.0],
    pas: [80, 110, 70, 120], pad: [40, 70, 35, 80], fr: [22, 37, 18, 45] },
  "Preescolar": { hr: [80, 140, 70, 150], spo2: [95, 100, 92, 100], temp: [36.0, 37.5, 35.5, 38.0],
    pas: [80, 110, 70, 120], pad: [45, 75, 40, 85], fr: [20, 28, 16, 35] },
  "Escolar": { hr: [70, 120, 60, 140], spo2: [95, 100, 92, 100], temp: [36.0, 37.5, 35.0, 38.0],
    pas: [90, 120, 80, 130], pad: [50, 80, 45, 90], fr: [18, 25, 14, 30] },
  "Adolescente": { hr: [60, 100, 50, 120], spo2: [95, 100, 92, 100], temp: [36.0, 37.5, 35.0, 38.0],
    pas: [100, 130, 90, 140], pad: [60, 85, 50, 90], fr: [12, 20, 10, 26] },
  "Adulto": { hr: [60, 100, 50, 120], spo2: [95, 100, 92, 100], temp: [36.0, 37.5, 35.0, 38.0],
    pas: [90, 129, 85, 139], pad: [60, 84, 55, 89], fr: [12, 20, 10, 26] },
  "Adulto mayor": { hr: [60, 100, 50, 120], spo2: [95, 100, 92, 100], temp: [35.8, 37.5, 35.0, 38.0],
    pas: [90, 139, 85, 149], pad: [60, 89, 55, 94], fr: [12, 22, 10, 28] },
};

// Configuración editable por el centro: clave -> [valor, descripción, unidad]
export const CONFIG_BASE = {
  escalamiento_s: [60, "Segundos sin atender antes de marcar una alerta como escalada", "s"],
  escalamiento_bateria_baja_s: [300, "Segundos antes de escalar el aviso de batería baja", "s"],
  escalamiento_bateria_critica_s: [120, "Segundos antes de escalar el aviso de batería crítica", "s"],
  bateria_baja: [30, "Porcentaje en que se avisa para programar el cambio de pulsera", "%"],
  bateria_critica: [15, "Porcentaje en que el cambio de pulsera no puede esperar", "%"],
  bateria_rearme: [40, "Porcentaje de carga a partir del cual se rearman los avisos", "%"],
  sin_senal_s: [7, "Segundos sin recibir paquetes para declarar ausencia de señal", "s"],
  plazo_recuperacion_s: [12, "Segundos para recuperar un paquete antes de declarar la pérdida", "s"],
  reintento_s: [4, "Cada cuántos segundos se vuelve a pedir un paquete faltante", "s"],
  minutos_signos_manuales: [240, "Cada cuántos minutos se recuerda tomar los signos manuales", "min"],
  centro: ["Centro de salud", "Nombre del centro que aparece en los reportes", ""],
};

// Conexión con el hub por MQTT. En el navegador solo se puede usar MQTT por WebSocket
// seguro (wss), por eso la web usa el puerto 8884 del broker y no el 1883 que usa el hub.
// mqtt_puerto (1883) es el del programa de escritorio: están los dos para que el mismo
// vitalcare.db sirva en una versión y en la otra.
export const CONFIG_MQTT = {
  mqtt_broker: ["broker.hivemq.com", "Servidor MQTT (broker) al que publica el hub", ""],
  mqtt_puerto: [1883, "Puerto MQTT del broker que usa el programa de escritorio (1883 normal, 8883 cifrado)", "puerto"],
  mqtt_puerto_web: [8884, "Puerto WebSocket seguro del broker (el que usa el navegador)", "puerto"],
  mqtt_prefijo: ["pucmm-pulsera-7f3a", "Prefijo de los tópicos: igual que MQTT_PREFIJO del hub", ""],
  mqtt_usuario: ["", "Usuario del broker (vacío en el broker público de prueba)", ""],
  mqtt_clave: ["", "Clave del broker (vacía en el broker público de prueba)", "clave"],
};

export const ESQUEMA = `
CREATE TABLE IF NOT EXISTS pacientes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    expediente TEXT NOT NULL UNIQUE,
    nombre TEXT NOT NULL,
    apellidos TEXT NOT NULL,
    fecha_nac TEXT NOT NULL,
    sexo TEXT NOT NULL,
    habitacion TEXT,
    cama TEXT,
    diagnostico TEXT,
    alergias TEXT,
    perfil TEXT,
    estado TEXT NOT NULL DEFAULT 'Ingresado',
    ingreso TEXT NOT NULL,
    alta TEXT,
    notas TEXT,
    creado TEXT NOT NULL,
    actualizado TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pulseras (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    serie TEXT NOT NULL UNIQUE,
    id_dispositivo TEXT NOT NULL UNIQUE,
    mac TEXT,
    modelo TEXT,
    estado TEXT NOT NULL DEFAULT 'Disponible',
    bateria INTEGER,
    ultima_vista TEXT,
    notas TEXT,
    creado TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS asignaciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paciente_id INTEGER NOT NULL REFERENCES pacientes(id),
    pulsera_id INTEGER NOT NULL REFERENCES pulseras(id),
    inicio TEXT NOT NULL,
    fin TEXT,
    motivo_fin TEXT,
    asignado_por TEXT,
    cerrado_por TEXT,
    nota TEXT
);
CREATE INDEX IF NOT EXISTS ix_asig_paciente ON asignaciones(paciente_id, fin);
CREATE INDEX IF NOT EXISTS ix_asig_pulsera ON asignaciones(pulsera_id, fin);
CREATE TABLE IF NOT EXISTS perfiles (
    nombre TEXT NOT NULL,
    sexo TEXT NOT NULL DEFAULT 'Ambos',
    meses_min INTEGER NOT NULL,
    meses_max INTEGER NOT NULL,
    PRIMARY KEY (nombre, sexo)
);
CREATE TABLE IF NOT EXISTS umbrales (
    perfil TEXT NOT NULL,
    sexo TEXT NOT NULL DEFAULT 'Ambos',
    signo TEXT NOT NULL,
    normal_min REAL, normal_max REAL,
    vig_min REAL, vig_max REAL,
    PRIMARY KEY (perfil, sexo, signo)
);
CREATE TABLE IF NOT EXISTS umbrales_paciente (
    paciente_id INTEGER NOT NULL REFERENCES pacientes(id),
    signo TEXT NOT NULL,
    normal_min REAL, normal_max REAL,
    vig_min REAL, vig_max REAL,
    motivo TEXT,
    fijado_por TEXT,
    hora TEXT,
    PRIMARY KEY (paciente_id, signo)
);
CREATE TABLE IF NOT EXISTS mediciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paciente_id INTEGER NOT NULL REFERENCES pacientes(id),
    hora TEXT NOT NULL,
    signo TEXT NOT NULL,
    valor REAL NOT NULL,
    tomado_por TEXT,
    nota TEXT
);
CREATE INDEX IF NOT EXISTS ix_med_paciente ON mediciones(paciente_id, hora);
CREATE TABLE IF NOT EXISTS config (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL,
    descripcion TEXT,
    unidad TEXT,
    actualizado TEXT,
    actualizado_por TEXT
);
CREATE TABLE IF NOT EXISTS auditoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hora TEXT NOT NULL,
    usuario TEXT,
    accion TEXT NOT NULL,
    entidad TEXT,
    entidad_id INTEGER,
    detalle TEXT
);
CREATE TABLE IF NOT EXISTS sesiones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paciente_id INTEGER REFERENCES pacientes(id),
    pulsera_id INTEGER REFERENCES pulseras(id),
    inicio TEXT NOT NULL,
    fin TEXT,
    fuente TEXT,
    prefijo TEXT,
    cierre_limpio INTEGER NOT NULL DEFAULT 0,
    latido TEXT
);
CREATE TABLE IF NOT EXISTS eventos_sistema (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hora TEXT NOT NULL,
    tipo TEXT NOT NULL,
    detalle TEXT,
    sesion_id INTEGER,
    reconocido_por TEXT,
    reconocido_hora TEXT
);
`;

const dos = (n) => String(n).padStart(2, "0");

export function ahora(d = new Date()) {
  return `${d.getFullYear()}-${dos(d.getMonth() + 1)}-${dos(d.getDate())} ${dos(d.getHours())}:${dos(d.getMinutes())}:${dos(d.getSeconds())}`;
}

function fechaISO(texto) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(texto || "").slice(0, 10));
  if (!m) return null;
  const [a, me, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const f = new Date(a, me - 1, d);
  if (f.getFullYear() !== a || f.getMonth() !== me - 1 || f.getDate() !== d) return null;
  return { a, me, d };
}

/** Edad en meses a partir de una fecha ISO (AAAA-MM-DD). */
export function mesesDeEdad(fechaNac, referencia = null) {
  if (!fechaNac) return null;
  const n = fechaISO(fechaNac);
  if (!n) return null;
  const hoy = referencia || new Date();
  let meses = (hoy.getFullYear() - n.a) * 12 + (hoy.getMonth() + 1 - n.me);
  if (hoy.getDate() < n.d) meses -= 1;
  return Math.max(0, meses);
}

export function textoEdad(fechaNac, referencia = null) {
  const m = mesesDeEdad(fechaNac, referencia);
  if (m === null) return "edad desconocida";
  if (m < 1) return "menos de 1 mes";
  if (m < 24) return `${m} ${m === 1 ? "mes" : "meses"}`;
  return `${Math.floor(m / 12)} años`;
}

export class BaseDatos {
  /**
   * SQL: el módulo de sql.js ya inicializado. bytes: el archivo guardado (o null la
   * primera vez). alCambiar: se llama después de cada escritura para guardar la base.
   */
  constructor(SQL, bytes = null, alCambiar = null) {
    this.cx = bytes ? new SQL.Database(bytes) : new SQL.Database();
    this.alCambiar = alCambiar;
    this.cx.run("PRAGMA foreign_keys=ON");
    this.cx.exec(ESQUEMA);
    this._sembrar();
  }

  // ---------------- utilidades ----------------
  q(sql, args = []) {
    const st = this.cx.prepare(sql);
    try {
      st.bind(args.map((a) => (a === undefined ? null : a)));
      const filas = [];
      while (st.step()) filas.push(st.getAsObject());
      return filas;
    } finally { st.free(); }
  }

  q1(sql, args = []) {
    const f = this.q(sql, args);
    return f.length ? f[0] : null;
  }

  ejec(sql, args = []) {
    this.cx.run(sql, args.map((a) => (a === undefined ? null : a)));
    const id = this.cx.exec("SELECT last_insert_rowid() AS id")[0].values[0][0];
    if (this.alCambiar) this.alCambiar();
    return id;
  }

  /** Archivo completo de la base (para guardarlo o exportarlo). */
  exportar() {
    const bytes = this.cx.export();
    this.cx.run("PRAGMA foreign_keys=ON"); // export() reabre la base y pierde los PRAGMA
    return bytes;
  }

  cerrar() { try { this.cx.close(); } catch { /* ya cerrada */ } }

  auditar(usuario, accion, entidad = null, entidadId = null, detalle = "") {
    this.ejec("INSERT INTO auditoria (hora, usuario, accion, entidad, entidad_id, detalle) VALUES (?,?,?,?,?,?)",
      [ahora(), usuario || "sin identificar", accion, entidad, entidadId, detalle]);
  }

  listarAuditoria(limite = 300) {
    return this.q("SELECT * FROM auditoria ORDER BY id DESC LIMIT ?", [limite]);
  }

  // ---------------- semilla ----------------
  _sembrar() {
    if (!this.q1("SELECT 1 FROM perfiles LIMIT 1")) {
      for (const [nombre, mn, mx] of PERFILES_BASE) {
        this.cx.run("INSERT INTO perfiles (nombre, sexo, meses_min, meses_max) VALUES (?,?,?,?)", [nombre, AMBOS, mn, mx]);
      }
      for (const [perfil, signos] of Object.entries(UMBRALES_BASE)) {
        for (const [signo, [nmin, nmax, vmin, vmax]] of Object.entries(signos)) {
          this.cx.run("INSERT INTO umbrales (perfil, sexo, signo, normal_min, normal_max, vig_min, vig_max) VALUES (?,?,?,?,?,?,?)",
            [perfil, AMBOS, signo, nmin, nmax, vmin, vmax]);
        }
      }
    }
    // Las claves que falten se agregan sin tocar las que el centro ya cambió.
    for (const [clave, [valor, desc, unidad]] of Object.entries({ ...CONFIG_BASE, ...CONFIG_MQTT })) {
      this.cx.run("INSERT OR IGNORE INTO config (clave, valor, descripcion, unidad, actualizado, actualizado_por) VALUES (?,?,?,?,?,?)",
        [clave, String(valor), desc, unidad, ahora(), "instalación"]);
    }
  }

  // ---------------- configuración ----------------
  config(clave, defecto = null) {
    const f = this.q1("SELECT valor FROM config WHERE clave=?", [clave]);
    if (!f) return defecto;
    const v = f.valor;
    if (/^[+-]?\d+$/.test(String(v).trim())) return parseInt(v, 10);
    if (String(v).trim() !== "" && Number.isFinite(Number(v))) return Number(v);
    return v;
  }

  fijarConfig(clave, valor, usuario) {
    const antes = this.config(clave);
    this.ejec("UPDATE config SET valor=?, actualizado=?, actualizado_por=? WHERE clave=?", [String(valor), ahora(), usuario, clave]);
    const oculto = clave === "mqtt_clave";
    this.auditar(usuario, "Cambio de configuración", "config", null,
      oculto ? `${clave}: (oculta) → (oculta)` : `${clave}: ${antes} → ${valor}`);
  }

  /** Valor interno que no aparece en la tabla de parámetros (por ejemplo, el último paciente). */
  fijarInterno(clave, valor, descripcion) {
    this.ejec("INSERT INTO config (clave, valor, descripcion, actualizado, actualizado_por) VALUES (?, ?, ?, ?, 'sistema')" +
      " ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor, actualizado=excluded.actualizado",
    [clave, String(valor ?? ""), descripcion, ahora()]);
  }

  listarConfig() {
    return this.q("SELECT * FROM config WHERE clave NOT IN ('ultimo_paciente','ultima_fuente','ultimo_parametro') ORDER BY clave");
  }

  // ---------------- pacientes ----------------
  crearPaciente(datos, usuario) {
    const campos = ["expediente", "nombre", "apellidos", "fecha_nac", "sexo", "habitacion", "cama", "diagnostico", "alergias", "notas"];
    const v = {};
    campos.forEach((c) => { v[c] = datos[c] || ""; });
    const perfil = this.perfilDe(v.fecha_nac, v.sexo);
    const pid = this.ejec(
      "INSERT INTO pacientes (expediente, nombre, apellidos, fecha_nac, sexo, habitacion, cama, diagnostico, alergias, perfil, estado, ingreso, notas, creado, actualizado)" +
      " VALUES (?,?,?,?,?,?,?,?,?,?,'Ingresado',?,?,?,?)",
      [v.expediente, v.nombre, v.apellidos, v.fecha_nac, v.sexo, v.habitacion, v.cama, v.diagnostico, v.alergias, perfil, ahora(), v.notas, ahora(), ahora()]);
    this.auditar(usuario, "Ingreso de paciente", "paciente", pid, `${v.nombre} ${v.apellidos} · expediente ${v.expediente} · perfil ${perfil}`);
    return pid;
  }

  actualizarPaciente(pid, datos, usuario) {
    const actual = this.paciente(pid);
    const campos = ["expediente", "nombre", "apellidos", "fecha_nac", "sexo", "habitacion", "cama", "diagnostico", "alergias", "notas"];
    const cambios = [];
    for (const c of campos) {
      if (c in datos && String(datos[c] || "") !== String(actual[c] || "")) {
        cambios.push(`${c}: ${actual[c] || "(vacío)"} → ${datos[c] || "(vacío)"}`);
      }
    }
    const presentes = campos.filter((c) => c in datos);
    const sets = presentes.map((c) => `${c}=?`).join(", ");
    const args = presentes.map((c) => datos[c]);
    const perfil = this.perfilDe(datos.fecha_nac ?? actual.fecha_nac, datos.sexo ?? actual.sexo);
    this.ejec(`UPDATE pacientes SET ${sets}${sets ? ", " : ""}perfil=?, actualizado=? WHERE id=?`, [...args, perfil, ahora(), pid]);
    if (cambios.length) this.auditar(usuario, "Edición de paciente", "paciente", pid, cambios.join(" · "));
    return cambios;
  }

  paciente(pid) { return this.q1("SELECT * FROM pacientes WHERE id=?", [pid]); }
  pacientePorExpediente(exp) { return this.q1("SELECT * FROM pacientes WHERE expediente=?", [exp]); }

  listarPacientes(texto = "", estado = "Ingresado") {
    let sql = "SELECT p.*, a.id AS asignacion_id, w.serie AS serie, w.id_dispositivo AS dispositivo, w.bateria AS bateria_pulsera" +
      " FROM pacientes p LEFT JOIN asignaciones a ON a.paciente_id=p.id AND a.fin IS NULL" +
      " LEFT JOIN pulseras w ON w.id=a.pulsera_id WHERE 1=1";
    const args = [];
    if (estado && estado !== "Todos") { sql += " AND p.estado=?"; args.push(estado); }
    if (texto) {
      sql += " AND (p.nombre LIKE ? OR p.apellidos LIKE ? OR p.expediente LIKE ? OR p.habitacion LIKE ? OR IFNULL(w.serie,'') LIKE ?)";
      for (let i = 0; i < 5; i++) args.push(`%${texto}%`);
    }
    sql += " ORDER BY p.habitacion, p.cama, p.apellidos";
    return this.q(sql, args);
  }

  /** Da de alta al paciente y libera la pulsera que tuviera asignada. */
  darAlta(pid, usuario, motivo = "Alta del paciente", nota = "") {
    const asig = this.asignacionDePaciente(pid);
    let liberada = null;
    if (asig) { this.cerrarAsignacion(asig.id, usuario, motivo, nota); liberada = asig.serie; }
    this.ejec("UPDATE pacientes SET estado='Alta', alta=?, actualizado=? WHERE id=?", [ahora(), ahora(), pid]);
    const p = this.paciente(pid);
    this.auditar(usuario, "Alta de paciente", "paciente", pid, `${p.nombre} ${p.apellidos}` + (liberada ? ` · pulsera ${liberada} liberada` : ""));
    return liberada;
  }

  reingresar(pid, usuario) {
    this.ejec("UPDATE pacientes SET estado='Ingresado', alta=NULL, ingreso=?, actualizado=? WHERE id=?", [ahora(), ahora(), pid]);
    this.auditar(usuario, "Reingreso de paciente", "paciente", pid, "");
  }

  puedeEliminarPaciente(pid) {
    const usos = this.q1("SELECT COUNT(*) c FROM asignaciones WHERE paciente_id=?", [pid]).c;
    const meds = this.q1("SELECT COUNT(*) c FROM mediciones WHERE paciente_id=?", [pid]).c;
    return [usos + meds === 0, usos, meds];
  }

  eliminarPaciente(pid, usuario) {
    const [ok, usos, meds] = this.puedeEliminarPaciente(pid);
    if (!ok) {
      throw new Error(`Este paciente tiene ${usos} asignación(es) de pulsera y ${meds} medición(es) registradas. ` +
        "En un sistema clínico ese historial no se borra: use Dar de alta para sacarlo del monitoreo.");
    }
    const p = this.paciente(pid);
    this.ejec("DELETE FROM umbrales_paciente WHERE paciente_id=?", [pid]);
    this.ejec("DELETE FROM pacientes WHERE id=?", [pid]);
    this.auditar(usuario, "Eliminación de paciente", "paciente", pid, `${p.nombre} ${p.apellidos} · expediente ${p.expediente} (sin historial)`);
  }

  // ---------------- pulseras ----------------
  crearPulsera(datos, usuario) {
    const wid = this.ejec("INSERT INTO pulseras (serie, id_dispositivo, mac, modelo, estado, notas, creado) VALUES (?,?,?,?,?,?,?)",
      [datos.serie, datos.id_dispositivo, datos.mac || "", datos.modelo || "", datos.estado || "Disponible", datos.notas || "", ahora()]);
    this.auditar(usuario, "Alta de pulsera", "pulsera", wid, `${datos.serie} · identificador ${datos.id_dispositivo}`);
    return wid;
  }

  actualizarPulsera(wid, datos, usuario) {
    const actual = this.pulsera(wid);
    const campos = ["serie", "id_dispositivo", "mac", "modelo", "estado", "notas"];
    const cambios = campos.filter((c) => c in datos && String(datos[c] || "") !== String(actual[c] || ""))
      .map((c) => `${c}: ${actual[c] || "(vacío)"} → ${datos[c] || "(vacío)"}`);
    const presentes = campos.filter((c) => c in datos);
    this.ejec(`UPDATE pulseras SET ${presentes.map((c) => `${c}=?`).join(", ")} WHERE id=?`, [...presentes.map((c) => datos[c]), wid]);
    if (cambios.length) this.auditar(usuario, "Edición de pulsera", "pulsera", wid, cambios.join(" · "));
    return cambios;
  }

  pulsera(wid) { return this.q1("SELECT * FROM pulseras WHERE id=?", [wid]); }
  pulseraPorDispositivo(idDispositivo) { return this.q1("SELECT * FROM pulseras WHERE id_dispositivo=?", [idDispositivo]); }

  listarPulseras(texto = "", estado = "Todos") {
    let sql = "SELECT w.*, p.id AS paciente_id, p.nombre AS p_nombre, p.apellidos AS p_apellidos, p.habitacion AS p_habitacion," +
      " p.cama AS p_cama, a.inicio AS asignada_desde FROM pulseras w" +
      " LEFT JOIN asignaciones a ON a.pulsera_id=w.id AND a.fin IS NULL" +
      " LEFT JOIN pacientes p ON p.id=a.paciente_id WHERE 1=1";
    const args = [];
    if (estado && estado !== "Todos") { sql += " AND w.estado=?"; args.push(estado); }
    if (texto) {
      sql += " AND (w.serie LIKE ? OR w.id_dispositivo LIKE ? OR IFNULL(w.mac,'') LIKE ? OR IFNULL(p.apellidos,'') LIKE ?)";
      for (let i = 0; i < 4; i++) args.push(`%${texto}%`);
    }
    sql += " ORDER BY w.serie";
    return this.q(sql, args);
  }

  pulserasDisponibles() {
    return this.q("SELECT * FROM pulseras WHERE estado IN ('Disponible','Cargando')" +
      " AND id NOT IN (SELECT pulsera_id FROM asignaciones WHERE fin IS NULL) ORDER BY estado DESC, bateria DESC, serie");
  }

  actualizarEstadoPulsera(wid, estado, usuario, nota = "") {
    const antes = this.pulsera(wid).estado;
    this.ejec("UPDATE pulseras SET estado=? WHERE id=?", [estado, wid]);
    this.auditar(usuario, "Cambio de estado de pulsera", "pulsera", wid, `${antes} → ${estado}` + (nota ? ` · ${nota}` : ""));
  }

  registrarBateria(idDispositivo, bateria) {
    this.ejec("UPDATE pulseras SET bateria=?, ultima_vista=? WHERE id_dispositivo=?", [bateria, ahora(), idDispositivo]);
  }

  puedeEliminarPulsera(wid) {
    const usos = this.q1("SELECT COUNT(*) c FROM asignaciones WHERE pulsera_id=?", [wid]).c;
    return [usos === 0, usos];
  }

  eliminarPulsera(wid, usuario) {
    const [ok, usos] = this.puedeEliminarPulsera(wid);
    if (!ok) {
      throw new Error(`Esta pulsera tiene ${usos} asignación(es) en su historial y ese registro no se borra. ` +
        "Use el estado Fuera de servicio para retirarla del inventario.");
    }
    const w = this.pulsera(wid);
    this.ejec("DELETE FROM pulseras WHERE id=?", [wid]);
    this.auditar(usuario, "Eliminación de pulsera", "pulsera", wid, `${w.serie} (sin historial)`);
  }

  siguienteSerie(prefijo = "VC-W-") {
    const f = this.q1("SELECT serie FROM pulseras WHERE serie LIKE ? ORDER BY serie DESC LIMIT 1", [prefijo + "%"]);
    let n = 1;
    if (f) {
      const ult = parseInt(String(f.serie).split("-").pop(), 10);
      n = Number.isFinite(ult) ? ult + 1 : this.q1("SELECT COUNT(*) c FROM pulseras").c + 1;
    }
    return `${prefijo}${String(n).padStart(4, "0")}`;
  }

  siguienteDispositivo(prefijo = "W") {
    const usados = new Set(this.q("SELECT id_dispositivo FROM pulseras").map((r) => r.id_dispositivo));
    let n = 1;
    while (usados.has(`${prefijo}${String(n).padStart(2, "0")}`)) n += 1;
    return `${prefijo}${String(n).padStart(2, "0")}`;
  }

  // ---------------- asignaciones ----------------
  asignacionDePaciente(pid) {
    return this.q1("SELECT a.*, w.serie, w.id_dispositivo, w.bateria FROM asignaciones a JOIN pulseras w ON w.id=a.pulsera_id" +
      " WHERE a.paciente_id=? AND a.fin IS NULL", [pid]);
  }

  asignacionDePulsera(wid) {
    return this.q1("SELECT a.*, p.nombre, p.apellidos FROM asignaciones a JOIN pacientes p ON p.id=a.paciente_id" +
      " WHERE a.pulsera_id=? AND a.fin IS NULL", [wid]);
  }

  /** A qué paciente le está enviando datos esta pulsera ahora mismo. */
  pacientePorDispositivo(idDispositivo) {
    return this.q1("SELECT p.*, w.serie, w.id_dispositivo, a.id AS asignacion_id, a.inicio AS asignada_desde FROM asignaciones a" +
      " JOIN pulseras w ON w.id=a.pulsera_id JOIN pacientes p ON p.id=a.paciente_id" +
      " WHERE w.id_dispositivo=? AND a.fin IS NULL", [idDispositivo]);
  }

  /**
   * Asigna una pulsera al paciente. Si ya tenía otra, la cierra primero con su motivo,
   * de modo que el historial del paciente queda continuo.
   */
  asignarPulsera(pid, wid, usuario, nota = "", motivoCambio = "Cambio por batería") {
    const ocupada = this.asignacionDePulsera(wid);
    if (ocupada) {
      throw new Error(`La pulsera ya está asignada a ${ocupada.nombre} ${ocupada.apellidos}. Libérela antes de volver a asignarla.`);
    }
    const anterior = this.asignacionDePaciente(pid);
    if (anterior) this.cerrarAsignacion(anterior.id, usuario, motivoCambio, nota);
    const aid = this.ejec("INSERT INTO asignaciones (paciente_id, pulsera_id, inicio, asignado_por, nota) VALUES (?,?,?,?,?)",
      [pid, wid, ahora(), usuario, nota]);
    this.ejec("UPDATE pulseras SET estado='En uso' WHERE id=?", [wid]);
    const p = this.paciente(pid), w = this.pulsera(wid);
    let detalle = `${w.serie} (${w.id_dispositivo}) → ${p.nombre} ${p.apellidos}`;
    if (anterior) detalle += ` · reemplaza a ${anterior.serie} por ${motivoCambio.toLowerCase()}`;
    this.auditar(usuario, "Asignación de pulsera", "asignacion", aid, detalle);
    return [aid, anterior ? anterior.serie : null];
  }

  cerrarAsignacion(aid, usuario, motivo, nota = "") {
    const a = this.q1("SELECT a.*, w.serie FROM asignaciones a JOIN pulseras w ON w.id=a.pulsera_id WHERE a.id=?", [aid]);
    if (!a || a.fin) return null;
    this.ejec("UPDATE asignaciones SET fin=?, motivo_fin=?, cerrado_por=?, nota=TRIM(IFNULL(nota,'')||' '||?) WHERE id=?",
      [ahora(), motivo, usuario, nota, aid]);
    const nuevoEstado = motivo === "Cambio por batería" ? "Cargando" : (motivo === "Falla del dispositivo" ? "Fuera de servicio" : "Disponible");
    this.ejec("UPDATE pulseras SET estado=? WHERE id=?", [nuevoEstado, a.pulsera_id]);
    this.auditar(usuario, "Liberación de pulsera", "asignacion", aid, `${a.serie} · motivo: ${motivo} · queda ${nuevoEstado.toLowerCase()}`);
    return a.serie;
  }

  historialAsignaciones({ pid = null, wid = null, limite = 200 } = {}) {
    let sql = "SELECT a.*, w.serie, w.id_dispositivo, p.nombre, p.apellidos, p.expediente FROM asignaciones a" +
      " JOIN pulseras w ON w.id=a.pulsera_id JOIN pacientes p ON p.id=a.paciente_id WHERE 1=1";
    const args = [];
    if (pid) { sql += " AND a.paciente_id=?"; args.push(pid); }
    if (wid) { sql += " AND a.pulsera_id=?"; args.push(wid); }
    sql += " ORDER BY a.id DESC LIMIT ?";
    args.push(limite);
    return this.q(sql, args);
  }

  // ---------------- perfiles y umbrales ----------------
  perfilDe(fechaNac) {
    const m = mesesDeEdad(fechaNac);
    if (m === null) return "Adulto";
    const f = this.q1("SELECT nombre FROM perfiles WHERE ? >= meses_min AND ? < meses_max ORDER BY meses_min DESC LIMIT 1", [m, m]);
    return f ? f.nombre : "Adulto mayor";
  }

  listarPerfiles() { return this.q("SELECT * FROM perfiles ORDER BY meses_min, sexo"); }

  umbralesDePerfil(perfil, sexo = AMBOS) {
    let filas = this.q("SELECT * FROM umbrales WHERE perfil=? AND sexo=?", [perfil, sexo]);
    if (!filas.length && sexo !== AMBOS) filas = this.q("SELECT * FROM umbrales WHERE perfil=? AND sexo=?", [perfil, AMBOS]);
    const r = {};
    filas.forEach((f) => { r[f.signo] = f; });
    return r;
  }

  /**
   * Umbrales que aplican a un paciente: los de su perfil por edad, con la variante por
   * sexo si el centro la definió, y encima los ajustes individuales si los tiene.
   */
  umbralesDePaciente(pid) {
    const p = this.paciente(pid);
    if (!p) return {};
    const perfil = p.perfil || this.perfilDe(p.fecha_nac, p.sexo);
    const propios = {};
    this.q("SELECT * FROM umbrales_paciente WHERE paciente_id=?", [pid]).forEach((f) => { propios[f.signo] = f; });
    const porSexo = {};
    this.q("SELECT * FROM umbrales WHERE perfil=? AND sexo=?", [perfil, p.sexo]).forEach((f) => { porSexo[f.signo] = f; });
    const base = this.umbralesDePerfil(perfil, AMBOS);
    const salida = {};
    for (const signo of Object.keys(SIGNOS)) {
      const f = propios[signo] || porSexo[signo] || base[signo];
      if (!f) continue;
      const origen = signo in propios ? "ajuste individual"
        : signo in porSexo ? `perfil ${perfil} (${p.sexo.toLowerCase()})` : `perfil ${perfil}`;
      salida[signo] = { normal: [f.normal_min, f.normal_max], vigilancia: [f.vig_min, f.vig_max], origen, perfil };
    }
    return salida;
  }

  fijarUmbralPerfil(perfil, sexo, signo, valores, usuario) {
    const antes = this.q1("SELECT * FROM umbrales WHERE perfil=? AND sexo=? AND signo=?", [perfil, sexo, signo]);
    this.ejec("INSERT INTO umbrales (perfil, sexo, signo, normal_min, normal_max, vig_min, vig_max) VALUES (?,?,?,?,?,?,?)" +
      " ON CONFLICT(perfil, sexo, signo) DO UPDATE SET normal_min=excluded.normal_min, normal_max=excluded.normal_max," +
      " vig_min=excluded.vig_min, vig_max=excluded.vig_max", [perfil, sexo, signo, ...valores]);
    const ant = antes ? `${antes.normal_min}-${antes.normal_max} / ${antes.vig_min}-${antes.vig_max}` : "sin definir";
    this.auditar(usuario, "Cambio de umbral", "umbral", null,
      `${perfil} (${sexo}) · ${signo}: ${ant} → ${valores[0]}-${valores[1]} / ${valores[2]}-${valores[3]}`);
  }

  /** Duplica los umbrales del perfil para un sexo, para que el centro los ajuste. */
  crearVarianteSexo(perfil, sexo, usuario) {
    if (sexo === AMBOS) return 0;
    const p = this.q1("SELECT * FROM perfiles WHERE nombre=? AND sexo=?", [perfil, AMBOS]);
    this.ejec("INSERT OR IGNORE INTO perfiles (nombre, sexo, meses_min, meses_max) VALUES (?,?,?,?)", [perfil, sexo, p.meses_min, p.meses_max]);
    let n = 0;
    for (const [signo, f] of Object.entries(this.umbralesDePerfil(perfil, AMBOS))) {
      this.ejec("INSERT OR IGNORE INTO umbrales (perfil, sexo, signo, normal_min, normal_max, vig_min, vig_max) VALUES (?,?,?,?,?,?,?)",
        [perfil, sexo, signo, f.normal_min, f.normal_max, f.vig_min, f.vig_max]);
      n += 1;
    }
    this.auditar(usuario, "Creación de variante por sexo", "umbral", null, `${perfil} · ${sexo} · ${n} signos copiados del perfil general`);
    return n;
  }

  fijarUmbralPaciente(pid, signo, valores, motivo, usuario) {
    this.ejec("INSERT INTO umbrales_paciente (paciente_id, signo, normal_min, normal_max, vig_min, vig_max, motivo, fijado_por, hora)" +
      " VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(paciente_id, signo) DO UPDATE SET normal_min=excluded.normal_min," +
      " normal_max=excluded.normal_max, vig_min=excluded.vig_min, vig_max=excluded.vig_max, motivo=excluded.motivo," +
      " fijado_por=excluded.fijado_por, hora=excluded.hora", [pid, signo, ...valores, motivo, usuario, ahora()]);
    this.auditar(usuario, "Ajuste individual de umbral", "paciente", pid,
      `${signo}: ${valores[0]}-${valores[1]} / ${valores[2]}-${valores[3]} · motivo: ${motivo}`);
  }

  quitarUmbralPaciente(pid, signo, usuario) {
    this.ejec("DELETE FROM umbrales_paciente WHERE paciente_id=? AND signo=?", [pid, signo]);
    this.auditar(usuario, "Retiro de ajuste individual", "paciente", pid, signo);
  }

  // ---------------- mediciones manuales ----------------
  registrarMedicion(pid, signo, valor, usuario, nota = "") {
    const mid = this.ejec("INSERT INTO mediciones (paciente_id, hora, signo, valor, tomado_por, nota) VALUES (?,?,?,?,?,?)",
      [pid, ahora(), signo, Number(valor), usuario, nota]);
    this.auditar(usuario, "Registro manual de signo", "paciente", pid,
      `${SIGNOS[signo][0]}: ${valor} ${SIGNOS[signo][1]}` + (nota ? ` · ${nota}` : ""));
    return mid;
  }

  ultimasMediciones(pid) {
    const filas = this.q("SELECT m.* FROM mediciones m WHERE m.paciente_id=? AND m.id IN" +
      " (SELECT MAX(id) FROM mediciones WHERE paciente_id=? GROUP BY signo)", [pid, pid]);
    const r = {};
    filas.forEach((f) => { r[f.signo] = f; });
    return r;
  }

  historialMediciones(pid, limite = 200) {
    return this.q("SELECT * FROM mediciones WHERE paciente_id=? ORDER BY id DESC LIMIT ?", [pid, limite]);
  }

  // ---------------- sesiones y cortes ----------------
  abrirSesion(pacienteId, pulseraId, fuente, prefijo) {
    return this.ejec("INSERT INTO sesiones (paciente_id, pulsera_id, inicio, fuente, prefijo, latido) VALUES (?,?,?,?,?,?)",
      [pacienteId, pulseraId, ahora(), fuente, prefijo, ahora()]);
  }

  /** Marca de vida: si el programa muere por un corte, la última marca dice hasta cuándo hubo datos. */
  latidoSesion(sid) { if (sid) this.ejec("UPDATE sesiones SET latido=? WHERE id=?", [ahora(), sid]); }

  cerrarSesion(sid, limpio = true) {
    if (sid) this.ejec("UPDATE sesiones SET fin=?, cierre_limpio=? WHERE id=?", [ahora(), limpio ? 1 : 0, sid]);
  }

  sesionesSinCerrar() {
    return this.q("SELECT s.*, p.nombre, p.apellidos, w.serie FROM sesiones s LEFT JOIN pacientes p ON p.id=s.paciente_id" +
      " LEFT JOIN pulseras w ON w.id=s.pulsera_id WHERE s.fin IS NULL ORDER BY s.id DESC");
  }

  registrarEventoSistema(tipo, detalle = "", sesionId = null) {
    return this.ejec("INSERT INTO eventos_sistema (hora, tipo, detalle, sesion_id) VALUES (?,?,?,?)", [ahora(), tipo, detalle, sesionId]);
  }

  reconocerEventoSistema(eid, usuario) {
    this.ejec("UPDATE eventos_sistema SET reconocido_por=?, reconocido_hora=? WHERE id=?", [usuario, ahora(), eid]);
    this.auditar(usuario, "Corte reconocido", "sistema", eid, "");
  }

  eventosSistema(limite = 100, soloPendientes = false) {
    let sql = "SELECT * FROM eventos_sistema";
    if (soloPendientes) sql += " WHERE reconocido_por IS NULL";
    sql += " ORDER BY id DESC LIMIT ?";
    return this.q(sql, [limite]);
  }

  // ---------------- datos de demostración ----------------
  /**
   * Crea cuatro pulseras y dos pacientes de ejemplo la primera vez. Ninguna pulsera queda
   * asignada: en la demostración se ve el proceso completo (ingreso, asignación y monitoreo).
   * La VC-W-0001 (W01) es la pulsera física del prototipo; las otras tres son simuladas.
   */
  sembrarDemostracion(usuario = "instalación") {
    if (this.q1("SELECT 1 FROM pulseras LIMIT 1")) return false;
    for (let n = 1; n <= 4; n++) {
      this.crearPulsera({ serie: `VC-W-${String(n).padStart(4, "0")}`, id_dispositivo: `W${String(n).padStart(2, "0")}`,
        mac: `A4:C1:38:00:00:${n.toString(16).toUpperCase().padStart(2, "0")}`, modelo: "VitalCare v1", estado: "Disponible",
        notas: n === 1 ? "Pulsera física del prototipo" : "Simulada (demostración)" }, usuario);
    }
    this.crearPaciente({ expediente: "EXP-2026-0001", nombre: "María", apellidos: "Pérez Gómez", fecha_nac: "1958-04-12",
      sexo: "Femenino", habitacion: "305", cama: "A", diagnostico: "Neumonía adquirida en la comunidad", alergias: "Penicilina" }, usuario);
    this.crearPaciente({ expediente: "EXP-2026-0002", nombre: "Carlos", apellidos: "Martínez Díaz", fecha_nac: "2017-09-03",
      sexo: "Masculino", habitacion: "306", cama: "B", diagnostico: "Crisis asmática", alergias: "Ninguna conocida" }, usuario);
    return true;
  }
}
