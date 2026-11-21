"""
Lógica del testbed sin interfaz gráfica: umbrales, clasificación de signos vitales,
conteo de paquetes perdidos y resumen de sesión.

Está separada de la ventana a propósito: así se puede probar sin pantalla
(ver pruebas_testbed.py) y los criterios clínicos quedan en un solo lugar.

Los umbrales de abajo son los de reserva, para un adulto en reposo. Cuando hay un paciente
seleccionado, la aplicación usa los de su perfil clínico por edad y sexo, que viven en la base
de datos del centro y se editan desde la pantalla de Configuración (ver datos.py).
No son un diagnóstico; el programa solo los usa para colorear estados y disparar avisos.
"""

import math
import time
from bisect import bisect_left
from collections import deque

# ----------------------------------------------------------------------
# Umbrales y niveles
# ----------------------------------------------------------------------

NIVEL_NORMAL, NIVEL_VIGILANCIA, NIVEL_ALERTA = 0, 1, 2
NOMBRE_NIVEL = {NIVEL_NORMAL: "Normal", NIVEL_VIGILANCIA: "Vigilancia", NIVEL_ALERTA: "Alerta"}
ESTADO_PACIENTE = {NIVEL_NORMAL: "ESTABLE", NIVEL_VIGILANCIA: "VIGILANCIA", NIVEL_ALERTA: "ALERTA"}

# "normal": rango sin aviso. "vigilancia": fuera de normal pero todavía no crítico.
# Fuera del rango de vigilancia => nivel Alerta.
UMBRALES = {
    "hr": {
        "nombre": "Frecuencia cardíaca",
        "corto": "FC",
        "unidad": "lpm",
        "normal": (60, 100),
        "vigilancia": (50, 120),
    },
    "spo2": {
        "nombre": "SpO₂",
        "corto": "SpO₂",
        "unidad": "%",
        "normal": (95, 100),
        "vigilancia": (92, 100),
    },
    "temp": {
        "nombre": "Temperatura",
        "corto": "Temp",
        "unidad": "°C",
        "normal": (36.0, 37.5),
        "vigilancia": (35.0, 38.0),
    },
}

BATERIA_BAJA = 30
BATERIA_CRITICA = 15


def a_numero(valor):
    """Convierte a float o devuelve None si no es un número válido (None, texto, NaN)."""
    if valor is None or isinstance(valor, bool):
        return None
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    if math.isnan(numero) or math.isinf(numero):
        return None
    return numero


def clasificar(variable, valor, umbrales=UMBRALES):
    """
    Devuelve (nivel, direccion).
    nivel: 0 normal, 1 vigilancia, 2 alerta, o None si no hay lectura válida.
    direccion: "alta", "baja" o None cuando está en rango normal.
    """
    v = a_numero(valor)
    if v is None:
        return None, None
    u = umbrales[variable]
    lo, hi = u["normal"]
    if lo <= v <= hi:
        return NIVEL_NORMAL, None
    direccion = "baja" if v < lo else "alta"
    vlo, vhi = u["vigilancia"]
    if vlo <= v <= vhi:
        return NIVEL_VIGILANCIA, direccion
    return NIVEL_ALERTA, direccion


def nivel_bateria(valor, baja=BATERIA_BAJA, critica=BATERIA_CRITICA):
    """Nivel de la batería según los umbrales del centro (por defecto 30 % y 15 %)."""
    b = a_numero(valor)
    if b is None:
        return None
    if b >= baja:
        return NIVEL_NORMAL
    if b >= critica:
        return NIVEL_VIGILANCIA
    return NIVEL_ALERTA


def estimar_autonomia_min(puntos):
    """
    Minutos de batería que quedan a la velocidad de descarga reciente.
    puntos: lista de (t_epoch_s, porcentaje). Ajuste lineal por mínimos cuadrados.
    Devuelve None si hay pocos datos o la batería no está bajando.
    """
    if len(puntos) < 5:
        return None
    t0 = puntos[0][0]
    xs = [p[0] - t0 for p in puntos]
    ys = [p[1] for p in puntos]
    if xs[-1] < 20:
        return None
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    pendiente = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den  # % por segundo
    if pendiente >= -1e-5:
        return None
    return max(0.0, ys[-1] / -pendiente / 60.0)


def formato_autonomia(minutos):
    if minutos is None:
        return "calculando…"
    if minutos > 24 * 60:
        return "más de 24 h"
    if minutos < 1:
        return "menos de 1 min"
    h, m = divmod(int(round(minutos)), 60)
    return f"~{h} h {m:02d} min" if h else f"~{m} min"


def estado_global(niveles):
    """Peor nivel entre los recibidos (ignora None). None si no hay ninguno."""
    validos = [n for n in niveles if n is not None]
    return max(validos) if validos else None


def titulo_alerta(variable, direccion):
    """Título legible para una alerta por umbral."""
    tabla = {
        ("hr", "alta"): "Taquicardia",
        ("hr", "baja"): "Bradicardia",
        ("spo2", "baja"): "SpO₂ baja",
        ("spo2", "alta"): "SpO₂ fuera de rango",
        ("temp", "alta"): "Fiebre alta",
        ("temp", "baja"): "Temperatura baja",
    }
    return tabla.get((variable, direccion), "Signo vital fuera de rango")


def texto_rango(variable, umbrales=UMBRALES):
    u = umbrales[variable]
    lo, hi = u["normal"]
    if variable == "spo2":
        return f"Normal: ≥ {lo:g} {u['unidad']}"
    return f"Normal: {lo:g} a {hi:g} {u['unidad']}"


def etiqueta_severidad(sev):
    return {"HIGH": "ALTA", "MEDIUM": "MEDIA", "LOW": "BAJA"}.get(str(sev).upper(), str(sev))


# ----------------------------------------------------------------------
# Conteo de paquetes perdidos
# ----------------------------------------------------------------------


class ContadorSeq:
    """
    El campo `seq` es un contador compartido por telemetría y eventos (ver el contrato
    JSON: telemetría seq 145, evento seq 146, ...). Un salto en la secuencia significa que
    faltan paquetes. El contador no se limita a contarlos: guarda CUÁLES faltan, para poder
    pedir su reenvío y marcarlos como recuperados cuando llegan tarde.

      faltantes:      detectados y todavía en espera de recuperarse
      irrecuperables: se agotó el tiempo de recuperación; es una pérdida real y queda registrada
      recuperados:    llegaron tarde (reenviados) y ya están completos
      perdidos:       faltantes + irrecuperables (lo que hoy sigue sin llegar)
    """

    VENTANA_VISTOS = 2000
    DISTANCIA_DUPLICADO = 10  # un seq repetido más atrás que esto se toma como reinicio

    def __init__(self):
        self.ultimo = None
        self.recibidos = 0
        self.reinicios = 0
        self.duplicados = 0
        self.detectados = 0
        self.recuperados = 0
        self.faltantes = set()
        self.irrecuperables = set()
        self._vistos = deque(maxlen=self.VENTANA_VISTOS)
        self._vistos_set = set()
        self.nuevos = []  # seq que se detectaron como faltantes en la última llamada
        self.recuperado_ahora = False  # la última llamada era un paquete reenviado
        self.recuperado_seq = None

    @property
    def perdidos(self):
        return len(self.faltantes) + len(self.irrecuperables)

    def _ver(self, s):
        if len(self._vistos) == self._vistos.maxlen:
            self._vistos_set.discard(self._vistos[0])
        self._vistos.append(s)
        self._vistos_set.add(s)

    def reiniciar_secuencia(self, motivo="cambio de pulsera"):
        """
        La pulsera nueva empieza su propia numeración. Se olvida el último seq y los
        huecos en espera (eran de la pulsera anterior), pero se conserva el conteo
        histórico de la sesión para que el resumen siga siendo fiel.
        """
        self.ultimo = None
        self.faltantes.clear()
        self.nuevos = []
        self.recuperado_ahora = False
        self.recuperado_seq = None
        self._vistos.clear()
        self._vistos_set.clear()
        self.reinicios += 1

    def registrar(self, seq):
        """Registra un seq recibido. Devuelve cuántos faltantes nuevos detectó."""
        self.nuevos = []
        self.recuperado_ahora = False
        self.recuperado_seq = None
        s = a_numero(seq)
        if s is None:
            return 0
        s = int(s)
        if s in self.faltantes or s in self.irrecuperables:
            self.faltantes.discard(s)
            self.irrecuperables.discard(s)
            self.recuperados += 1
            self.recibidos += 1
            self.recuperado_ahora = True
            self.recuperado_seq = s
            self._ver(s)
            return 0
        if s in self._vistos_set and self.ultimo is not None and 0 <= self.ultimo - s <= self.DISTANCIA_DUPLICADO:
            self.duplicados += 1
            return 0
        if self.ultimo is None:
            self.recibidos += 1
        elif s > self.ultimo:
            hueco = s - self.ultimo - 1
            if hueco > 0:
                self.nuevos = list(range(self.ultimo + 1, min(s, self.ultimo + 1 + 5000)))
                self.faltantes.update(self.nuevos)
                self.detectados += len(self.nuevos)
            self.recibidos += 1
        else:  # seq retrocedió mucho: la pulsera se reinició
            self.reinicios += 1
            self.recibidos += 1
            self.irrecuperables |= self.faltantes  # lo que faltaba antes no se puede pedir ya
            self.faltantes = set()
            self._vistos.clear()
            self._vistos_set.clear()
        self.ultimo = s
        self._ver(s)
        return len(self.nuevos)

    def marcar_irrecuperables(self, seqs):
        """Pasa a irrecuperables los faltantes cuyo plazo venció."""
        for s in seqs:
            if s in self.faltantes:
                self.faltantes.discard(s)
                self.irrecuperables.add(s)

    @property
    def porcentaje_perdida(self):
        total = self.recibidos + self.perdidos
        return 100.0 * self.perdidos / total if total else 0.0


# ----------------------------------------------------------------------
# Datos de una sesión de monitoreo
# ----------------------------------------------------------------------


class Sesion:
    """Acumula todo lo recibido durante una conexión para graficar y resumir."""

    def __init__(self, umbrales=UMBRALES):
        self.umbrales = umbrales
        self.inicio = time.time()
        self.fin = None
        self.dispositivo = None
        self.t = []
        self.hr = []
        self.spo2 = []
        self.temp = []
        self.batt = []
        self.estado_sensor = None
        self.seq = ContadorSeq()
        self.eventos = []  # (t_epoch, nombre_evento)
        self.cambios = []  # (t_epoch, texto) cambios de pulsera durante la sesión
        self.paquetes_telemetria = 0
        self.alertas_generadas = 0
        self.respuestas_s = []
        self.resoluciones = {}
        self._base = None
        self.fuera_rango = {"hr": 0, "spo2": 0, "temp": 0}
        self.validos = {"hr": 0, "spo2": 0, "temp": 0}

    def marcar_cambio_pulsera(self, anterior, nueva, t=None):
        """Deja una marca en la línea de tiempo: el paciente siguió, la pulsera cambió."""
        self.cambios.append((time.time() if t is None else t, f"{anterior} → {nueva}"))

    def agregar_telemetria(self, p, t=None):
        t = time.time() if t is None else t
        self.paquetes_telemetria += 1
        self.dispositivo = p.get("id", self.dispositivo)
        self.estado_sensor = p.get("st", self.estado_sensor)
        self.seq.registrar(p.get("seq"))
        ts = a_numero(p.get("ts"))
        if self._base is None and ts is not None:
            self._base = (t, ts)
        pos = len(self.t)
        if self.seq.recuperado_ahora and ts is not None and self._base is not None:
            # Paquete reenviado: se coloca en su hora original, no en la hora en que llegó.
            t = self._base[0] + (ts - self._base[1]) / 1000.0
            pos = bisect_left(self.t, t)
        self.t.insert(pos, t)
        for variable in ("hr", "spo2", "temp"):
            valor = a_numero(p.get(variable))
            getattr(self, variable).insert(pos, valor)
            if valor is not None:
                self.validos[variable] += 1
                nivel, _ = clasificar(variable, valor, self.umbrales)
                if nivel and nivel > NIVEL_NORMAL:
                    self.fuera_rango[variable] += 1
        self.batt.insert(pos, a_numero(p.get("batt")))

    def agregar_evento(self, p, t=None):
        t = time.time() if t is None else t
        self.dispositivo = p.get("id", self.dispositivo)
        self.seq.registrar(p.get("seq"))
        self.eventos.append((t, str(p.get("ev"))))

    def autonomia_min(self, ventana_s=900):
        if not self.t:
            return None
        corte = self.t[-1] - ventana_s
        puntos = [(t, b) for t, b in zip(self.t, self.batt) if b is not None and t >= corte]
        return estimar_autonomia_min(puntos)

    def alerta_generada(self):
        self.alertas_generadas += 1

    def alerta_atendida(self, respuesta_s, resolucion=None):
        self.respuestas_s.append(float(respuesta_s))
        if resolucion:
            self.resoluciones[resolucion] = self.resoluciones.get(resolucion, 0) + 1

    def cerrar(self):
        if self.fin is None:
            self.fin = time.time()

    # ---- resumen ---------------------------------------------------------

    def resumen(self):
        fin = self.fin if self.fin is not None else time.time()
        variables = {}
        for variable in ("hr", "spo2", "temp"):
            valores = [v for v in getattr(self, variable) if v is not None]
            n = len(valores)
            variables[variable] = {
                "n": n,
                "min": min(valores) if valores else None,
                "max": max(valores) if valores else None,
                "media": sum(valores) / n if n else None,
                "pct_fuera_rango": (100.0 * self.fuera_rango[variable] / n) if n else None,
            }
        baterias = [b for b in self.batt if b is not None]
        conteo_eventos = {}
        for _, nombre in self.eventos:
            conteo_eventos[nombre] = conteo_eventos.get(nombre, 0) + 1
        return {
            "duracion_s": max(0.0, fin - self.inicio),
            "dispositivo": self.dispositivo,
            "paquetes_telemetria": self.paquetes_telemetria,
            "paquetes_recibidos": self.seq.recibidos,
            "paquetes_perdidos": self.seq.perdidos,
            "paquetes_detectados": self.seq.detectados,
            "paquetes_recuperados": self.seq.recuperados,
            "perdida_pct": self.seq.porcentaje_perdida,
            "reinicios": self.seq.reinicios,
            "variables": variables,
            "bateria_inicial": baterias[0] if baterias else None,
            "bateria_final": baterias[-1] if baterias else None,
            "eventos": conteo_eventos,
            "alertas_generadas": self.alertas_generadas,
            "alertas_atendidas": len(self.respuestas_s),
            "resoluciones": dict(self.resoluciones),
            "respuesta_media_s": (sum(self.respuestas_s) / len(self.respuestas_s))
            if self.respuestas_s
            else None,
            "respuesta_max_s": max(self.respuestas_s) if self.respuestas_s else None,
        }


# ----------------------------------------------------------------------
# Formato de texto
# ----------------------------------------------------------------------


def formato_duracion(segundos):
    s = int(max(0, segundos))
    h, resto = divmod(s, 3600)
    m, s = divmod(resto, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _fmt(valor, decimales=1, sufijo=""):
    return "--" if valor is None else f"{valor:.{decimales}f}{sufijo}"


def formatear_resumen(res, fuente="", inicio=None):
    """Resumen en texto plano para guardar junto a los CSV de la sesión."""
    lineas = ["RESUMEN DE SESIÓN - Testbed de pulsera (ITT466)", "=" * 52]
    if inicio is not None:
        lineas.append(f"Inicio:            {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(inicio))}")
    if fuente:
        lineas.append(f"Fuente de datos:   {fuente}")
    lineas.append(f"Dispositivo:       {res['dispositivo'] or '--'}")
    lineas.append(f"Duración:          {formato_duracion(res['duracion_s'])}")
    lineas.append("")
    lineas.append("Enlace")
    lineas.append(f"  Paquetes recibidos: {res['paquetes_recibidos']}")
    lineas.append(f"  Paquetes faltantes detectados: {res['paquetes_detectados']}")
    lineas.append(f"  Recuperados por reenvío:       {res['paquetes_recuperados']}")
    lineas.append(f"  Sin recuperar (pérdida real):  {res['paquetes_perdidos']} ({res['perdida_pct']:.1f} %)")
    lineas.append(f"  Reinicios del dispositivo detectados: {res['reinicios']}")
    lineas.append(f"  Batería: {_fmt(res['bateria_inicial'], 0, ' %')} al inicio, {_fmt(res['bateria_final'], 0, ' %')} al final")
    lineas.append("")
    lineas.append("Signos vitales (mínimo / máximo / promedio / % del tiempo fuera de rango normal)")
    for variable in ("hr", "spo2", "temp"):
        u = UMBRALES[variable]
        v = res["variables"][variable]
        lineas.append(
            f"  {u['corto']:<6} {_fmt(v['min']):>6} / {_fmt(v['max']):>6} / {_fmt(v['media']):>6} {u['unidad']:<4}"
            f" | fuera de rango: {_fmt(v['pct_fuera_rango'], 0, ' %')}"
        )
    lineas.append("")
    lineas.append("Eventos y alertas")
    if res["eventos"]:
        for nombre, n in sorted(res["eventos"].items()):
            lineas.append(f"  Evento {nombre}: {n}")
    else:
        lineas.append("  Sin eventos de la pulsera (caída / SOS)")
    lineas.append(f"  Alertas generadas: {res['alertas_generadas']}")
    lineas.append(f"  Alertas cerradas con detalle: {res['alertas_atendidas']}")
    for nombre, n in sorted(res.get("resoluciones", {}).items()):
        lineas.append(f"    - {nombre}: {n}")
    lineas.append(
        f"  Tiempo de respuesta: promedio {_fmt(res['respuesta_media_s'], 1, ' s')}, "
        f"máximo {_fmt(res['respuesta_max_s'], 1, ' s')}"
    )
    lineas.append("")
    lineas.append("Nota: los umbrales son valores de referencia para adulto en reposo, pendientes de validar.")
    return "\n".join(lineas) + "\n"
