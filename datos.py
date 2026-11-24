"""
Base de datos local del sistema (SQLite).

Guarda lo que no puede vivir solo en memoria: pacientes ingresados, inventario de
pulseras, a quién está asignada cada pulsera en cada momento, los umbrales clínicos
por grupo de edad, los signos que se toman a mano, la configuración del centro y la
bitácora de quién cambió qué.

Decisiones que importan para una aplicación médica:
  - journal_mode = WAL y synchronous = FULL: si se va la luz en mitad de una
    escritura, la base queda consistente y no se pierde lo ya confirmado.
  - Nada se borra en silencio. Un paciente con datos no se elimina, se archiva;
    una pulsera con historial no se elimina, se da de baja.
  - Toda alta, baja, cambio de umbral y cambio de pulsera queda en la auditoría.

El archivo se crea solo la primera vez (vitalcare.db, junto al programa).
"""

import os
import sqlite3
from datetime import date, datetime

ARCHIVO_BD = "vitalcare.db"

SEXOS = ["Femenino", "Masculino", "Otro"]
AMBOS = "Ambos"

ESTADO_PULSERA = ["Disponible", "En uso", "Cargando", "Fuera de servicio"]
MOTIVOS_FIN = ["Alta del paciente", "Cambio por batería", "Falla del dispositivo",
               "Cambio de habitación", "Otro"]

# Signos que maneja el sistema. Los tres primeros los mide la pulsera; los demás
# los registra el personal a mano, porque la pulsera no tiene cómo medirlos.
SIGNOS = {
    "hr": ("Frecuencia cardíaca", "lpm", "pulsera"),
    "spo2": ("Saturación de oxígeno", "%", "pulsera"),
    "temp": ("Temperatura", "°C", "pulsera"),
    "pas": ("Presión arterial sistólica", "mmHg", "manual"),
    "pad": ("Presión arterial diastólica", "mmHg", "manual"),
    "fr": ("Frecuencia respiratoria", "rpm", "manual"),
}
SIGNOS_MANUALES = [k for k, v in SIGNOS.items() if v[2] == "manual"]

# Perfiles por grupo de edad: (nombre, meses mínimos, meses máximos)
PERFILES_BASE = [
    ("Neonato", 0, 1),
    ("Lactante", 1, 12),
    ("Niño pequeño", 12, 36),
    ("Preescolar", 36, 72),
    ("Escolar", 72, 144),
    ("Adolescente", 144, 216),
    ("Adulto", 216, 780),
    ("Adulto mayor", 780, 1500),
]

# Umbrales de referencia por perfil: signo -> (normal_min, normal_max, vig_min, vig_max)
# Fuera del rango de vigilancia el sistema marca alerta.
UMBRALES_BASE = {
    "Neonato": {"hr": (100, 180, 90, 200), "spo2": (95, 100, 92, 100), "temp": (36.5, 37.5, 36.0, 38.0),
                "pas": (60, 90, 50, 100), "pad": (20, 60, 15, 70), "fr": (30, 60, 25, 70)},
    "Lactante": {"hr": (100, 160, 90, 180), "spo2": (95, 100, 92, 100), "temp": (36.0, 37.5, 35.5, 38.0),
                 "pas": (70, 100, 60, 110), "pad": (35, 65, 30, 75), "fr": (30, 53, 24, 60)},
    "Niño pequeño": {"hr": (90, 150, 80, 170), "spo2": (95, 100, 92, 100), "temp": (36.0, 37.5, 35.5, 38.0),
                     "pas": (80, 110, 70, 120), "pad": (40, 70, 35, 80), "fr": (22, 37, 18, 45)},
    "Preescolar": {"hr": (80, 140, 70, 150), "spo2": (95, 100, 92, 100), "temp": (36.0, 37.5, 35.5, 38.0),
                   "pas": (80, 110, 70, 120), "pad": (45, 75, 40, 85), "fr": (20, 28, 16, 35)},
    "Escolar": {"hr": (70, 120, 60, 140), "spo2": (95, 100, 92, 100), "temp": (36.0, 37.5, 35.0, 38.0),
                "pas": (90, 120, 80, 130), "pad": (50, 80, 45, 90), "fr": (18, 25, 14, 30)},
    "Adolescente": {"hr": (60, 100, 50, 120), "spo2": (95, 100, 92, 100), "temp": (36.0, 37.5, 35.0, 38.0),
                    "pas": (100, 130, 90, 140), "pad": (60, 85, 50, 90), "fr": (12, 20, 10, 26)},
    "Adulto": {"hr": (60, 100, 50, 120), "spo2": (95, 100, 92, 100), "temp": (36.0, 37.5, 35.0, 38.0),
               "pas": (90, 129, 85, 139), "pad": (60, 84, 55, 89), "fr": (12, 20, 10, 26)},
    "Adulto mayor": {"hr": (60, 100, 50, 120), "spo2": (95, 100, 92, 100), "temp": (35.8, 37.5, 35.0, 38.0),
                     "pas": (90, 139, 85, 149), "pad": (60, 89, 55, 94), "fr": (12, 22, 10, 28)},
}

# Configuración editable por el centro de salud: clave -> (valor, descripción, unidad)
CONFIG_BASE = {
    "escalamiento_s": (60, "Segundos sin atender antes de marcar una alerta como escalada", "s"),
    "escalamiento_bateria_baja_s": (300, "Segundos antes de escalar el aviso de batería baja", "s"),
    "escalamiento_bateria_critica_s": (120, "Segundos antes de escalar el aviso de batería crítica", "s"),
    "bateria_baja": (30, "Porcentaje en que se avisa para programar el cambio de pulsera", "%"),
    "bateria_critica": (15, "Porcentaje en que el cambio de pulsera no puede esperar", "%"),
    "bateria_rearme": (40, "Porcentaje de carga a partir del cual se rearman los avisos", "%"),
    "sin_senal_s": (7, "Segundos sin recibir paquetes para declarar ausencia de señal", "s"),
    "plazo_recuperacion_s": (12, "Segundos para recuperar un paquete antes de declarar la pérdida", "s"),
    "reintento_s": (4, "Cada cuántos segundos se vuelve a pedir un paquete faltante", "s"),
    "minutos_signos_manuales": (240, "Cada cuántos minutos se recuerda tomar los signos manuales", "min"),
    "centro": ("Centro de salud", "Nombre del centro que aparece en los reportes", ""),
}

# Conexión con el hub por MQTT. El programa de escritorio usa mqtt_puerto (1883, MQTT normal);
# la versión web usa mqtt_puerto_web (8884, WebSocket seguro). Están las dos para que el mismo
# vitalcare.db sirva en una y en otra.
CONFIG_MQTT = {
    "mqtt_broker": ("broker.hivemq.com", "Servidor MQTT (broker) al que publica el hub", ""),
    "mqtt_puerto": (1883, "Puerto MQTT del broker que usa este programa (1883 normal, 8883 cifrado)", "puerto"),
    "mqtt_puerto_web": (8884, "Puerto WebSocket seguro del broker (el que usa el navegador)", "puerto"),
    "mqtt_prefijo": ("pucmm-pulsera-7f3a", "Prefijo de los tópicos: igual que MQTT_PREFIJO del hub", ""),
    "mqtt_usuario": ("", "Usuario del broker (vacío en el broker público de prueba)", ""),
    "mqtt_clave": ("", "Clave del broker (vacía en el broker público de prueba)", "clave"),
}

# Valores internos que el programa guarda en la tabla config pero no son parámetros del centro.
CLAVES_INTERNAS = ("ultimo_paciente", "ultima_fuente", "ultimo_parametro")

ESQUEMA = """
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
"""


def ahora():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def meses_de_edad(fecha_nac, referencia=None):
    """Edad en meses a partir de una fecha ISO (YYYY-MM-DD)."""
    if not fecha_nac:
        return None
    try:
        n = date.fromisoformat(str(fecha_nac)[:10])
    except ValueError:
        return None
    hoy = referencia or date.today()
    meses = (hoy.year - n.year) * 12 + (hoy.month - n.month)
    if hoy.day < n.day:
        meses -= 1
    return max(0, meses)


def texto_edad(fecha_nac, referencia=None):
    m = meses_de_edad(fecha_nac, referencia)
    if m is None:
        return "edad desconocida"
    if m < 1:
        return "menos de 1 mes"
    if m < 24:
        return f"{m} {'mes' if m == 1 else 'meses'}"
    a = m // 12
    return f"{a} años"


class BaseDatos:
    def __init__(self, ruta=ARCHIVO_BD):
        self.ruta = ruta
        carpeta = os.path.dirname(os.path.abspath(ruta))
        os.makedirs(carpeta, exist_ok=True)
        self.cx = sqlite3.connect(ruta, check_same_thread=False)
        self.cx.row_factory = sqlite3.Row
        self.cx.execute("PRAGMA journal_mode=WAL")
        self.cx.execute("PRAGMA synchronous=FULL")
        self.cx.execute("PRAGMA foreign_keys=ON")
        self.cx.executescript(ESQUEMA)
        self._sembrar()
        self.cx.commit()

    # ---------------- utilidades ----------------
    def q(self, sql, args=()):
        return self.cx.execute(sql, args).fetchall()

    def q1(self, sql, args=()):
        f = self.cx.execute(sql, args).fetchone()
        return f

    def ejec(self, sql, args=()):
        cur = self.cx.execute(sql, args)
        self.cx.commit()
        return cur.lastrowid

    def cerrar(self):
        try:
            self.cx.commit()
            self.cx.close()
        except sqlite3.Error:
            pass

    def auditar(self, usuario, accion, entidad=None, entidad_id=None, detalle=""):
        self.ejec("INSERT INTO auditoria (hora, usuario, accion, entidad, entidad_id, detalle)"
                  " VALUES (?,?,?,?,?,?)", (ahora(), usuario or "sin identificar", accion, entidad,
                                            entidad_id, detalle))

    def listar_auditoria(self, limite=300):
        return self.q("SELECT * FROM auditoria ORDER BY id DESC LIMIT ?", (limite,))

    # ---------------- semilla ----------------
    def _sembrar(self):
        if not self.q1("SELECT 1 FROM perfiles LIMIT 1"):
            for nombre, mn, mx in PERFILES_BASE:
                self.cx.execute("INSERT INTO perfiles (nombre, sexo, meses_min, meses_max) VALUES (?,?,?,?)",
                                (nombre, AMBOS, mn, mx))
            for perfil, signos in UMBRALES_BASE.items():
                for signo, (nmin, nmax, vmin, vmax) in signos.items():
                    self.cx.execute("INSERT INTO umbrales (perfil, sexo, signo, normal_min, normal_max,"
                                    " vig_min, vig_max) VALUES (?,?,?,?,?,?,?)",
                                    (perfil, AMBOS, signo, nmin, nmax, vmin, vmax))
        # Las claves que falten se agregan sin tocar las que el centro ya cambió.
        for clave, (valor, desc, unidad) in {**CONFIG_BASE, **CONFIG_MQTT}.items():
            self.cx.execute("INSERT OR IGNORE INTO config (clave, valor, descripcion, unidad, actualizado,"
                            " actualizado_por) VALUES (?,?,?,?,?,?)",
                            (clave, str(valor), desc, unidad, ahora(), "instalación"))

    # ---------------- configuración ----------------
    def config(self, clave, defecto=None):
        f = self.q1("SELECT valor FROM config WHERE clave=?", (clave,))
        if not f:
            return defecto
        v = f["valor"]
        try:
            return int(v)
        except ValueError:
            try:
                return float(v)
            except ValueError:
                return v

    def fijar_config(self, clave, valor, usuario):
        antes = self.config(clave)
        self.ejec("UPDATE config SET valor=?, actualizado=?, actualizado_por=? WHERE clave=?",
                  (str(valor), ahora(), usuario, clave))
        detalle = f"{clave}: (oculta) → (oculta)" if clave == "mqtt_clave" else f"{clave}: {antes} → {valor}"
        self.auditar(usuario, "Cambio de configuración", "config", None, detalle)

    def fijar_interno(self, clave, valor, descripcion):
        """Valor interno que no aparece en la tabla de parámetros (por ejemplo, el último paciente)."""
        self.ejec("INSERT INTO config (clave, valor, descripcion, actualizado, actualizado_por)"
                  " VALUES (?, ?, ?, ?, 'sistema') ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor,"
                  " actualizado=excluded.actualizado", (clave, str(valor if valor is not None else ""), descripcion, ahora()))

    def listar_config(self):
        marcas = ",".join("?" * len(CLAVES_INTERNAS))
        return self.q(f"SELECT * FROM config WHERE clave NOT IN ({marcas}) ORDER BY clave", CLAVES_INTERNAS)

    # ---------------- pacientes ----------------
    def crear_paciente(self, datos, usuario):
        campos = ("expediente", "nombre", "apellidos", "fecha_nac", "sexo", "habitacion", "cama",
                  "diagnostico", "alergias", "notas")
        v = {c: (datos.get(c) or "") for c in campos}
        perfil = self.perfil_de(v["fecha_nac"], v["sexo"])
        pid = self.ejec(
            "INSERT INTO pacientes (expediente, nombre, apellidos, fecha_nac, sexo, habitacion, cama,"
            " diagnostico, alergias, perfil, estado, ingreso, notas, creado, actualizado)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,'Ingresado',?,?,?,?)",
            (v["expediente"], v["nombre"], v["apellidos"], v["fecha_nac"], v["sexo"], v["habitacion"],
             v["cama"], v["diagnostico"], v["alergias"], perfil, ahora(), v["notas"], ahora(), ahora()))
        self.auditar(usuario, "Ingreso de paciente", "paciente", pid,
                     f"{v['nombre']} {v['apellidos']} · expediente {v['expediente']} · perfil {perfil}")
        return pid

    def actualizar_paciente(self, pid, datos, usuario):
        actual = self.paciente(pid)
        campos = ("expediente", "nombre", "apellidos", "fecha_nac", "sexo", "habitacion", "cama",
                  "diagnostico", "alergias", "notas")
        cambios = []
        for c in campos:
            if c in datos and str(datos[c] or "") != str(actual[c] or ""):
                cambios.append(f"{c}: {actual[c] or '(vacío)'} → {datos[c] or '(vacío)'}")
        sets = ", ".join(f"{c}=?" for c in campos if c in datos)
        args = [datos[c] for c in campos if c in datos]
        perfil = self.perfil_de(datos.get("fecha_nac", actual["fecha_nac"]), datos.get("sexo", actual["sexo"]))
        self.ejec(f"UPDATE pacientes SET {sets}, perfil=?, actualizado=? WHERE id=?",
                  args + [perfil, ahora(), pid])
        if cambios:
            self.auditar(usuario, "Edición de paciente", "paciente", pid, " · ".join(cambios))
        return cambios

    def paciente(self, pid):
        return self.q1("SELECT * FROM pacientes WHERE id=?", (pid,))

    def paciente_por_expediente(self, expediente):
        return self.q1("SELECT * FROM pacientes WHERE expediente=?", (expediente,))

    def listar_pacientes(self, texto="", estado="Ingresado"):
        sql = ("SELECT p.*, a.id AS asignacion_id, w.serie AS serie, w.id_dispositivo AS dispositivo,"
               " w.bateria AS bateria_pulsera"
               " FROM pacientes p"
               " LEFT JOIN asignaciones a ON a.paciente_id=p.id AND a.fin IS NULL"
               " LEFT JOIN pulseras w ON w.id=a.pulsera_id WHERE 1=1")
        args = []
        if estado and estado != "Todos":
            sql += " AND p.estado=?"
            args.append(estado)
        if texto:
            sql += (" AND (p.nombre LIKE ? OR p.apellidos LIKE ? OR p.expediente LIKE ?"
                    " OR p.habitacion LIKE ? OR IFNULL(w.serie,'') LIKE ?)")
            args += [f"%{texto}%"] * 5
        sql += " ORDER BY p.habitacion, p.cama, p.apellidos"
        return self.q(sql, args)

    def dar_alta(self, pid, usuario, motivo="Alta del paciente", nota=""):
        """Da de alta al paciente y libera la pulsera que tuviera asignada."""
        asig = self.asignacion_de_paciente(pid)
        liberada = None
        if asig:
            self.cerrar_asignacion(asig["id"], usuario, motivo, nota)
            liberada = asig["serie"]
        self.ejec("UPDATE pacientes SET estado='Alta', alta=?, actualizado=? WHERE id=?",
                  (ahora(), ahora(), pid))
        p = self.paciente(pid)
        self.auditar(usuario, "Alta de paciente", "paciente", pid,
                     f"{p['nombre']} {p['apellidos']}" + (f" · pulsera {liberada} liberada" if liberada else ""))
        return liberada

    def reingresar(self, pid, usuario):
        self.ejec("UPDATE pacientes SET estado='Ingresado', alta=NULL, ingreso=?, actualizado=? WHERE id=?",
                  (ahora(), ahora(), pid))
        self.auditar(usuario, "Reingreso de paciente", "paciente", pid, "")

    def puede_eliminar_paciente(self, pid):
        usos = self.q1("SELECT COUNT(*) c FROM asignaciones WHERE paciente_id=?", (pid,))["c"]
        meds = self.q1("SELECT COUNT(*) c FROM mediciones WHERE paciente_id=?", (pid,))["c"]
        return (usos + meds) == 0, usos, meds

    def eliminar_paciente(self, pid, usuario):
        ok, usos, meds = self.puede_eliminar_paciente(pid)
        if not ok:
            raise ValueError(
                f"Este paciente tiene {usos} asignación(es) de pulsera y {meds} medición(es) registradas. "
                "En un sistema clínico ese historial no se borra: use Dar de alta para sacarlo del monitoreo.")
        p = self.paciente(pid)
        self.ejec("DELETE FROM umbrales_paciente WHERE paciente_id=?", (pid,))
        self.ejec("DELETE FROM pacientes WHERE id=?", (pid,))
        self.auditar(usuario, "Eliminación de paciente", "paciente", pid,
                     f"{p['nombre']} {p['apellidos']} · expediente {p['expediente']} (sin historial)")

    # ---------------- pulseras ----------------
    def crear_pulsera(self, datos, usuario):
        pid = self.ejec("INSERT INTO pulseras (serie, id_dispositivo, mac, modelo, estado, notas, creado)"
                        " VALUES (?,?,?,?,?,?,?)",
                        (datos["serie"], datos["id_dispositivo"], datos.get("mac", ""),
                         datos.get("modelo", ""), datos.get("estado", "Disponible"),
                         datos.get("notas", ""), ahora()))
        self.auditar(usuario, "Alta de pulsera", "pulsera", pid,
                     f"{datos['serie']} · identificador {datos['id_dispositivo']}")
        return pid

    def actualizar_pulsera(self, wid, datos, usuario):
        actual = self.pulsera(wid)
        campos = ("serie", "id_dispositivo", "mac", "modelo", "estado", "notas")
        cambios = [f"{c}: {actual[c] or '(vacío)'} → {datos[c] or '(vacío)'}"
                   for c in campos if c in datos and str(datos[c] or "") != str(actual[c] or "")]
        sets = ", ".join(f"{c}=?" for c in campos if c in datos)
        args = [datos[c] for c in campos if c in datos]
        self.ejec(f"UPDATE pulseras SET {sets} WHERE id=?", args + [wid])
        if cambios:
            self.auditar(usuario, "Edición de pulsera", "pulsera", wid, " · ".join(cambios))
        return cambios

    def pulsera(self, wid):
        return self.q1("SELECT * FROM pulseras WHERE id=?", (wid,))

    def pulsera_por_dispositivo(self, id_dispositivo):
        return self.q1("SELECT * FROM pulseras WHERE id_dispositivo=?", (id_dispositivo,))

    def listar_pulseras(self, texto="", estado="Todos"):
        sql = ("SELECT w.*, p.id AS paciente_id, p.nombre AS p_nombre, p.apellidos AS p_apellidos,"
               " p.habitacion AS p_habitacion, p.cama AS p_cama, a.inicio AS asignada_desde"
               " FROM pulseras w"
               " LEFT JOIN asignaciones a ON a.pulsera_id=w.id AND a.fin IS NULL"
               " LEFT JOIN pacientes p ON p.id=a.paciente_id WHERE 1=1")
        args = []
        if estado and estado != "Todos":
            sql += " AND w.estado=?"
            args.append(estado)
        if texto:
            sql += (" AND (w.serie LIKE ? OR w.id_dispositivo LIKE ? OR IFNULL(w.mac,'') LIKE ?"
                    " OR IFNULL(p.apellidos,'') LIKE ?)")
            args += [f"%{texto}%"] * 4
        sql += " ORDER BY w.serie"
        return self.q(sql, args)

    def pulseras_disponibles(self):
        return self.q("SELECT * FROM pulseras WHERE estado IN ('Disponible','Cargando')"
                      " AND id NOT IN (SELECT pulsera_id FROM asignaciones WHERE fin IS NULL)"
                      " ORDER BY estado DESC, bateria DESC, serie")

    def actualizar_estado_pulsera(self, wid, estado, usuario, nota=""):
        antes = self.pulsera(wid)["estado"]
        self.ejec("UPDATE pulseras SET estado=? WHERE id=?", (estado, wid))
        self.auditar(usuario, "Cambio de estado de pulsera", "pulsera", wid,
                     f"{antes} → {estado}" + (f" · {nota}" if nota else ""))

    def registrar_bateria(self, id_dispositivo, bateria):
        self.ejec("UPDATE pulseras SET bateria=?, ultima_vista=? WHERE id_dispositivo=?",
                  (bateria, ahora(), id_dispositivo))

    def puede_eliminar_pulsera(self, wid):
        usos = self.q1("SELECT COUNT(*) c FROM asignaciones WHERE pulsera_id=?", (wid,))["c"]
        return usos == 0, usos

    def eliminar_pulsera(self, wid, usuario):
        ok, usos = self.puede_eliminar_pulsera(wid)
        if not ok:
            raise ValueError(
                f"Esta pulsera tiene {usos} asignación(es) en su historial y ese registro no se borra. "
                "Use el estado Fuera de servicio para retirarla del inventario.")
        w = self.pulsera(wid)
        self.ejec("DELETE FROM pulseras WHERE id=?", (wid,))
        self.auditar(usuario, "Eliminación de pulsera", "pulsera", wid, f"{w['serie']} (sin historial)")

    def siguiente_serie(self, prefijo="VC-W-"):
        f = self.q1("SELECT serie FROM pulseras WHERE serie LIKE ? ORDER BY serie DESC LIMIT 1",
                    (prefijo + "%",))
        n = 1
        if f:
            try:
                n = int(str(f["serie"]).split("-")[-1]) + 1
            except ValueError:
                n = self.q1("SELECT COUNT(*) c FROM pulseras")["c"] + 1
        return f"{prefijo}{n:04d}"

    def siguiente_dispositivo(self, prefijo="W"):
        usados = {r["id_dispositivo"] for r in self.q("SELECT id_dispositivo FROM pulseras")}
        n = 1
        while f"{prefijo}{n:02d}" in usados:
            n += 1
        return f"{prefijo}{n:02d}"

    # ---------------- asignaciones ----------------
    def asignacion_de_paciente(self, pid):
        return self.q1("SELECT a.*, w.serie, w.id_dispositivo, w.bateria FROM asignaciones a"
                       " JOIN pulseras w ON w.id=a.pulsera_id"
                       " WHERE a.paciente_id=? AND a.fin IS NULL", (pid,))

    def asignacion_de_pulsera(self, wid):
        return self.q1("SELECT a.*, p.nombre, p.apellidos FROM asignaciones a"
                       " JOIN pacientes p ON p.id=a.paciente_id"
                       " WHERE a.pulsera_id=? AND a.fin IS NULL", (wid,))

    def paciente_por_dispositivo(self, id_dispositivo):
        """A qué paciente le está enviando datos esta pulsera ahora mismo."""
        return self.q1("SELECT p.*, w.serie, w.id_dispositivo, a.id AS asignacion_id, a.inicio AS asignada_desde"
                       " FROM asignaciones a"
                       " JOIN pulseras w ON w.id=a.pulsera_id"
                       " JOIN pacientes p ON p.id=a.paciente_id"
                       " WHERE w.id_dispositivo=? AND a.fin IS NULL", (id_dispositivo,))

    def asignar_pulsera(self, pid, wid, usuario, nota="", motivo_cambio="Cambio por batería"):
        """
        Asigna una pulsera al paciente. Si ya tenía otra, la cierra primero con su motivo,
        de modo que el historial del paciente queda continuo y se sabe qué pulsera envió
        cada tramo de datos.
        """
        ocupada = self.asignacion_de_pulsera(wid)
        if ocupada:
            raise ValueError(f"La pulsera ya está asignada a {ocupada['nombre']} {ocupada['apellidos']}. "
                             "Libérela antes de volver a asignarla.")
        anterior = self.asignacion_de_paciente(pid)
        if anterior:
            self.cerrar_asignacion(anterior["id"], usuario, motivo_cambio, nota)
        aid = self.ejec("INSERT INTO asignaciones (paciente_id, pulsera_id, inicio, asignado_por, nota)"
                        " VALUES (?,?,?,?,?)", (pid, wid, ahora(), usuario, nota))
        self.ejec("UPDATE pulseras SET estado='En uso' WHERE id=?", (wid,))
        p, w = self.paciente(pid), self.pulsera(wid)
        detalle = f"{w['serie']} ({w['id_dispositivo']}) → {p['nombre']} {p['apellidos']}"
        if anterior:
            detalle += f" · reemplaza a {anterior['serie']} por {motivo_cambio.lower()}"
        self.auditar(usuario, "Asignación de pulsera", "asignacion", aid, detalle)
        return aid, (anterior["serie"] if anterior else None)

    def cerrar_asignacion(self, aid, usuario, motivo, nota=""):
        a = self.q1("SELECT a.*, w.serie FROM asignaciones a JOIN pulseras w ON w.id=a.pulsera_id"
                    " WHERE a.id=?", (aid,))
        if not a or a["fin"]:
            return None
        self.ejec("UPDATE asignaciones SET fin=?, motivo_fin=?, cerrado_por=?,"
                  " nota=TRIM(IFNULL(nota,'')||' '||?) WHERE id=?",
                  (ahora(), motivo, usuario, nota, aid))
        nuevo_estado = "Cargando" if motivo == "Cambio por batería" else (
            "Fuera de servicio" if motivo == "Falla del dispositivo" else "Disponible")
        self.ejec("UPDATE pulseras SET estado=? WHERE id=?", (nuevo_estado, a["pulsera_id"]))
        self.auditar(usuario, "Liberación de pulsera", "asignacion", aid,
                     f"{a['serie']} · motivo: {motivo} · queda {nuevo_estado.lower()}")
        return a["serie"]

    def historial_asignaciones(self, pid=None, wid=None, limite=200):
        sql = ("SELECT a.*, w.serie, w.id_dispositivo, p.nombre, p.apellidos, p.expediente"
               " FROM asignaciones a JOIN pulseras w ON w.id=a.pulsera_id"
               " JOIN pacientes p ON p.id=a.paciente_id WHERE 1=1")
        args = []
        if pid:
            sql += " AND a.paciente_id=?"
            args.append(pid)
        if wid:
            sql += " AND a.pulsera_id=?"
            args.append(wid)
        sql += " ORDER BY a.id DESC LIMIT ?"
        args.append(limite)
        return self.q(sql, args)

    # ---------------- perfiles y umbrales ----------------
    def perfil_de(self, fecha_nac, sexo=None):
        m = meses_de_edad(fecha_nac)
        if m is None:
            return "Adulto"
        f = self.q1("SELECT nombre FROM perfiles WHERE ? >= meses_min AND ? < meses_max"
                    " ORDER BY meses_min DESC LIMIT 1", (m, m))
        return f["nombre"] if f else "Adulto mayor"

    def listar_perfiles(self):
        return self.q("SELECT * FROM perfiles ORDER BY meses_min, sexo")

    def umbrales_de_perfil(self, perfil, sexo=AMBOS):
        filas = self.q("SELECT * FROM umbrales WHERE perfil=? AND sexo=?", (perfil, sexo))
        if not filas and sexo != AMBOS:
            filas = self.q("SELECT * FROM umbrales WHERE perfil=? AND sexo=?", (perfil, AMBOS))
        return {f["signo"]: f for f in filas}

    def umbrales_de_paciente(self, pid):
        """
        Umbrales que aplican a un paciente: los de su perfil por edad, con la variante
        por sexo si el centro la definió, y encima los ajustes individuales si los tiene.
        Devuelve {signo: {"normal": (min,max), "vigilancia": (min,max), "origen": texto}}
        """
        p = self.paciente(pid)
        if not p:
            return {}
        perfil = p["perfil"] or self.perfil_de(p["fecha_nac"], p["sexo"])
        propios = self.q("SELECT * FROM umbrales_paciente WHERE paciente_id=?", (pid,))
        por_sexo = {f["signo"]: f for f in self.q(
            "SELECT * FROM umbrales WHERE perfil=? AND sexo=?", (perfil, p["sexo"]))}
        base = self.umbrales_de_perfil(perfil, AMBOS)
        propios = {f["signo"]: f for f in propios}
        salida = {}
        for signo in SIGNOS:
            f = propios.get(signo) or por_sexo.get(signo) or base.get(signo)
            if not f:
                continue
            origen = ("ajuste individual" if signo in propios else
                      f"perfil {perfil} ({p['sexo'].lower()})" if signo in por_sexo else f"perfil {perfil}")
            salida[signo] = {
                "normal": (f["normal_min"], f["normal_max"]),
                "vigilancia": (f["vig_min"], f["vig_max"]),
                "origen": origen,
                "perfil": perfil,
            }
        return salida

    def fijar_umbral_perfil(self, perfil, sexo, signo, valores, usuario):
        antes = self.q1("SELECT * FROM umbrales WHERE perfil=? AND sexo=? AND signo=?", (perfil, sexo, signo))
        self.ejec("INSERT INTO umbrales (perfil, sexo, signo, normal_min, normal_max, vig_min, vig_max)"
                  " VALUES (?,?,?,?,?,?,?)"
                  " ON CONFLICT(perfil, sexo, signo) DO UPDATE SET normal_min=excluded.normal_min,"
                  " normal_max=excluded.normal_max, vig_min=excluded.vig_min, vig_max=excluded.vig_max",
                  (perfil, sexo, signo) + tuple(valores))
        ant = (f"{antes['normal_min']}-{antes['normal_max']} / {antes['vig_min']}-{antes['vig_max']}"
               if antes else "sin definir")
        self.auditar(usuario, "Cambio de umbral", "umbral", None,
                     f"{perfil} ({sexo}) · {signo}: {ant} → {valores[0]}-{valores[1]} / {valores[2]}-{valores[3]}")

    def crear_variante_sexo(self, perfil, sexo, usuario):
        """Duplica los umbrales del perfil para un sexo, para que el centro los ajuste."""
        if sexo == AMBOS:
            return 0
        p = self.q1("SELECT * FROM perfiles WHERE nombre=? AND sexo=?", (perfil, AMBOS))
        self.ejec("INSERT OR IGNORE INTO perfiles (nombre, sexo, meses_min, meses_max) VALUES (?,?,?,?)",
                  (perfil, sexo, p["meses_min"], p["meses_max"]))
        n = 0
        for signo, f in self.umbrales_de_perfil(perfil, AMBOS).items():
            self.ejec("INSERT OR IGNORE INTO umbrales (perfil, sexo, signo, normal_min, normal_max,"
                      " vig_min, vig_max) VALUES (?,?,?,?,?,?,?)",
                      (perfil, sexo, signo, f["normal_min"], f["normal_max"], f["vig_min"], f["vig_max"]))
            n += 1
        self.auditar(usuario, "Creación de variante por sexo", "umbral", None,
                     f"{perfil} · {sexo} · {n} signos copiados del perfil general")
        return n

    def fijar_umbral_paciente(self, pid, signo, valores, motivo, usuario):
        self.ejec("INSERT INTO umbrales_paciente (paciente_id, signo, normal_min, normal_max, vig_min,"
                  " vig_max, motivo, fijado_por, hora) VALUES (?,?,?,?,?,?,?,?,?)"
                  " ON CONFLICT(paciente_id, signo) DO UPDATE SET normal_min=excluded.normal_min,"
                  " normal_max=excluded.normal_max, vig_min=excluded.vig_min, vig_max=excluded.vig_max,"
                  " motivo=excluded.motivo, fijado_por=excluded.fijado_por, hora=excluded.hora",
                  (pid, signo) + tuple(valores) + (motivo, usuario, ahora()))
        self.auditar(usuario, "Ajuste individual de umbral", "paciente", pid,
                     f"{signo}: {valores[0]}-{valores[1]} / {valores[2]}-{valores[3]} · motivo: {motivo}")

    def quitar_umbral_paciente(self, pid, signo, usuario):
        self.ejec("DELETE FROM umbrales_paciente WHERE paciente_id=? AND signo=?", (pid, signo))
        self.auditar(usuario, "Retiro de ajuste individual", "paciente", pid, signo)

    # ---------------- mediciones manuales ----------------
    def registrar_medicion(self, pid, signo, valor, usuario, nota=""):
        mid = self.ejec("INSERT INTO mediciones (paciente_id, hora, signo, valor, tomado_por, nota)"
                        " VALUES (?,?,?,?,?,?)", (pid, ahora(), signo, float(valor), usuario, nota))
        self.auditar(usuario, "Registro manual de signo", "paciente", pid,
                     f"{SIGNOS[signo][0]}: {valor} {SIGNOS[signo][1]}" + (f" · {nota}" if nota else ""))
        return mid

    def ultimas_mediciones(self, pid):
        filas = self.q("SELECT m.* FROM mediciones m WHERE m.paciente_id=? AND m.id IN"
                       " (SELECT MAX(id) FROM mediciones WHERE paciente_id=? GROUP BY signo)", (pid, pid))
        return {f["signo"]: f for f in filas}

    def historial_mediciones(self, pid, limite=200):
        return self.q("SELECT * FROM mediciones WHERE paciente_id=? ORDER BY id DESC LIMIT ?", (pid, limite))

    # ---------------- sesiones y cortes ----------------
    def abrir_sesion(self, paciente_id, pulsera_id, fuente, prefijo):
        sid = self.ejec("INSERT INTO sesiones (paciente_id, pulsera_id, inicio, fuente, prefijo, latido)"
                        " VALUES (?,?,?,?,?,?)", (paciente_id, pulsera_id, ahora(), fuente, prefijo, ahora()))
        return sid

    def latido_sesion(self, sid):
        """Marca de vida: si el programa muere por un corte, la última marca dice hasta cuándo hubo datos."""
        if sid:
            self.ejec("UPDATE sesiones SET latido=? WHERE id=?", (ahora(), sid))

    def cerrar_sesion(self, sid, limpio=True):
        if sid:
            self.ejec("UPDATE sesiones SET fin=?, cierre_limpio=? WHERE id=?", (ahora(), 1 if limpio else 0, sid))

    def sesiones_sin_cerrar(self):
        return self.q("SELECT s.*, p.nombre, p.apellidos, w.serie FROM sesiones s"
                      " LEFT JOIN pacientes p ON p.id=s.paciente_id"
                      " LEFT JOIN pulseras w ON w.id=s.pulsera_id"
                      " WHERE s.fin IS NULL ORDER BY s.id DESC")

    def registrar_evento_sistema(self, tipo, detalle="", sesion_id=None):
        return self.ejec("INSERT INTO eventos_sistema (hora, tipo, detalle, sesion_id) VALUES (?,?,?,?)",
                         (ahora(), tipo, detalle, sesion_id))

    def reconocer_evento_sistema(self, eid, usuario):
        self.ejec("UPDATE eventos_sistema SET reconocido_por=?, reconocido_hora=? WHERE id=?",
                  (usuario, ahora(), eid))
        self.auditar(usuario, "Corte reconocido", "sistema", eid, "")

    def eventos_sistema(self, limite=100, solo_pendientes=False):
        sql = "SELECT * FROM eventos_sistema"
        if solo_pendientes:
            sql += " WHERE reconocido_por IS NULL"
        sql += " ORDER BY id DESC LIMIT ?"
        return self.q(sql, (limite,))

    # ---------------- datos de demostración ----------------
    def sembrar_demostracion(self, usuario="instalación"):
        """Crea cuatro pulseras y dos pacientes de ejemplo la primera vez."""
        if self.q1("SELECT 1 FROM pulseras LIMIT 1"):
            return False
        for n in range(1, 5):
            self.crear_pulsera({"serie": f"VC-W-{n:04d}", "id_dispositivo": f"W{n:02d}",
                                "mac": f"A4:C1:38:00:00:{n:02X}", "modelo": "VitalCare v1",
                                "estado": "Disponible"}, usuario)
        p1 = self.crear_paciente({"expediente": "EXP-2026-0001", "nombre": "María", "apellidos": "Pérez Gómez",
                                  "fecha_nac": "1958-04-12", "sexo": "Femenino", "habitacion": "305",
                                  "cama": "A", "diagnostico": "Neumonía adquirida en la comunidad",
                                  "alergias": "Penicilina"}, usuario)
        self.crear_paciente({"expediente": "EXP-2026-0002", "nombre": "Carlos", "apellidos": "Martínez Díaz",
                             "fecha_nac": "2017-09-03", "sexo": "Masculino", "habitacion": "306", "cama": "B",
                             "diagnostico": "Crisis asmática", "alergias": "Ninguna conocida"}, usuario)
        w1 = self.pulsera_por_dispositivo("W01")
        self.asignar_pulsera(p1, w1["id"], usuario, "Asignación inicial al ingreso")
        return True
