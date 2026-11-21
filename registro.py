"""
Registro en CSV de una sesión del testbed.

Por sesión se crean tres archivos en la carpeta datos_sesion/:
  telemetria_<marca>.csv  -> una fila por paquete de telemetría (mismo formato de siempre)
  eventos_<marca>.csv     -> una fila por evento de la pulsera (FALL, SOS)
  alertas_<marca>.csv     -> bitácora de alertas: cuándo se generó, si escaló, y al cerrarla lo que
                             escribió el personal (qué hizo, quién, si hubo indicación médica)
  huecos_<marca>.csv      -> cada paquete que faltó: detectado, recuperado o no recuperado
Cada fila se vacía a disco al escribirse, para no perder datos si el programa se cierra mal.
"""

import csv
import os
from datetime import datetime

CARPETA_DATOS = "datos_sesion"


def _hora():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


class RegistradorCSV:
    def __init__(self, carpeta=CARPETA_DATOS):
        os.makedirs(carpeta, exist_ok=True)
        self.carpeta = carpeta
        self.marca = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.ruta_telemetria = os.path.join(carpeta, f"telemetria_{self.marca}.csv")
        self.ruta_eventos = os.path.join(carpeta, f"eventos_{self.marca}.csv")
        self.ruta_alertas = os.path.join(carpeta, f"alertas_{self.marca}.csv")
        self.ruta_resumen = os.path.join(carpeta, f"resumen_{self.marca}.txt")

        self._archivos = []
        self._cerrado = False
        self._csv_tel = self._abrir(
            self.ruta_telemetria,
            ["hora_local", "id", "seq", "ts_dispositivo_ms", "hr", "spo2", "temp", "batt", "st"],
        )
        self._csv_ev = self._abrir(
            self.ruta_eventos,
            ["hora_local", "id", "seq", "ts_dispositivo_ms", "evento", "severidad"],
        )
        self._csv_al = self._abrir(
            self.ruta_alertas,
            ["hora_local", "accion", "origen", "tipo", "detalle", "seq", "respuesta_s",
             "resolucion", "accion_realizada", "atendido_por", "indicacion_medica", "medico"],
        )
        self.ruta_huecos = os.path.join(carpeta, f"huecos_{self.marca}.csv")
        self._csv_hu = self._abrir(
            self.ruta_huecos, ["hora_local", "seq_faltante", "estado", "detalle"])

    def _abrir(self, ruta, encabezado):
        archivo = open(ruta, "w", newline="", encoding="utf-8")
        self._archivos.append(archivo)
        escritor = csv.writer(archivo)
        escritor.writerow(encabezado)
        archivo.flush()
        return escritor

    def _flush(self):
        # flush + fsync: el dato queda en el disco aunque se apague la laptop justo después.
        for a in self._archivos:
            if not a.closed:
                a.flush()
                try:
                    os.fsync(a.fileno())
                except OSError:
                    pass

    def registrar_telemetria(self, p):
        if self._cerrado:
            return
        self._csv_tel.writerow(
            [_hora(), p.get("id"), p.get("seq"), p.get("ts"), p.get("hr"), p.get("spo2"),
             p.get("temp"), p.get("batt"), p.get("st")]
        )
        self._flush()

    def registrar_evento(self, p):
        if self._cerrado:
            return
        self._csv_ev.writerow(
            [_hora(), p.get("id"), p.get("seq"), p.get("ts"), p.get("ev"), p.get("sev")]
        )
        self._flush()

    def registrar_alerta(self, accion, origen, tipo, detalle="", seq=None, respuesta_s=None, cierre=None):
        """
        accion: GENERADA, ESCALADA o CERRADA. origen: DISPOSITIVO, UMBRAL o SISTEMA.
        cierre: dict con lo que escribió el personal al cerrar la alerta (resolucion,
        accion_realizada, atendido_por, indicacion_medica, medico).
        """
        if self._cerrado:
            return
        c = cierre or {}
        self._csv_al.writerow(
            [_hora(), accion, origen, tipo, detalle, seq,
             "" if respuesta_s is None else f"{respuesta_s:.1f}",
             c.get("resolucion", ""), c.get("accion_realizada", ""), c.get("atendido_por", ""),
             ("SI" if c.get("indicacion_medica") else "NO") if c else "", c.get("medico", "")]
        )
        self._flush()

    def registrar_hueco(self, seq, estado, detalle=""):
        """estado: DETECTADO, RECUPERADO o NO_RECUPERADO."""
        if self._cerrado:
            return
        self._csv_hu.writerow([_hora(), seq, estado, detalle])
        self._flush()

    def guardar_resumen(self, texto):
        with open(self.ruta_resumen, "w", encoding="utf-8") as f:
            f.write(texto)
        return self.ruta_resumen

    def cerrar(self):
        self._cerrado = True
        for a in self._archivos:
            if not a.closed:
                a.close()
