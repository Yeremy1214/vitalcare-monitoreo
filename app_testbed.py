"""
Testbed de la pulsera wearable - Anteproyecto ITT466 (PUCMM)
Sistema Inteligente de Monitoreo de Signos Vitales y Gestión de Emergencias Hospitalarias

Qué hace:
  - Recibe paquetes de telemetría (hr, spo2, temp, batt) y de eventos (FALL, SOS)
    desde cuatro fuentes que se eligen en la ventana: Simulador, Bluetooth (BLE), WiFi/UDP
    o Hub (MQTT), que es la misma ruta que usa la versión web.
  - Muestra cada signo vital en una tarjeta con su estado (Normal / Vigilancia / Alerta)
    y un banner con el estado general del paciente.
  - Grafica las tendencias en vivo con el rango normal sombreado y marca los eventos.
  - Genera alertas (caída, SOS, signos fuera de rango), con cronómetro de atención y
    escalamiento si nadie las atiende a tiempo.
  - Muestra la calidad del enlace: paquetes recibidos y perdidos, último paquete.
  - Guarda todo en CSV (telemetría, eventos y bitácora de alertas) y arma un resumen.
  - Modo demostración (solo con el simulador): escenarios y botones de caída / SOS.

Cómo correrlo:
    pip install matplotlib
    python app_testbed.py
(bleak solo hace falta el día que se use la fuente Bluetooth con la pulsera real:
 pip install bleak; paho-mqtt solo para la fuente Hub (MQTT): pip install paho-mqtt)

Atajos: F2 simula caída, F3 simula SOS (solo simulador), F11 pantalla completa, Esc sale.
"""

import asyncio
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from bisect import bisect_left
from datetime import datetime
from tkinter import font as tkfont
from tkinter import ttk

import matplotlib

matplotlib.use("TkAgg")
import matplotlib.dates as mdates
import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from logica import (
    ESTADO_PACIENTE,
    NIVEL_ALERTA,
    NIVEL_NORMAL,
    NIVEL_VIGILANCIA,
    NOMBRE_NIVEL,
    UMBRALES,
    Sesion,
    a_numero,
    clasificar,
    estado_global,
    estimar_autonomia_min,
    etiqueta_severidad,
    formato_autonomia,
    formatear_resumen,
    formato_duracion,
    BATERIA_BAJA,
    BATERIA_CRITICA,
    nivel_bateria,
    texto_rango,
    titulo_alerta,
)
from registro import CARPETA_DATOS, RegistradorCSV
from transportes import ESCENARIOS, BLETransport, MQTTTransport, SimuladorTransport, UDPTransport
from datos import ARCHIVO_BD, SIGNOS, SIGNOS_MANUALES, BaseDatos, ahora as ahora_iso, texto_edad
from estilo import estilos_ttk
from pantallas import (DialogoAsignar, DialogoMedicion, PantallaConfig, PantallaPacientes,
                       PantallaPulseras, nombre_completo, ubicacion)

# ----------------------------------------------------------------------
# Configuración
# ----------------------------------------------------------------------

FUENTE_SIM = "Simulador"
FUENTE_BLE = "Bluetooth (BLE)"
FUENTE_UDP = "WiFi / UDP"
FUENTE_MQTT = "Hub (MQTT)"
FUENTES = [FUENTE_SIM, FUENTE_BLE, FUENTE_UDP, FUENTE_MQTT]

ESCALAMIENTO_S = 60  # una alerta sin atender tras este tiempo se marca como escalada
SIN_SENAL_S = 7.0  # sin paquetes por más de esto => "Sin señal" (telemetría llega cada 2 s)
MAX_TARJETAS_ALERTA = 40
ESCALAMIENTO_BATERIA_BAJA_S = 300  # el cambio de pulsera se puede programar con calma
ESCALAMIENTO_BATERIA_CRITICA_S = 120
PLAZO_RECUPERACION_S = 12.0  # tiempo máximo para recuperar un paquete faltante
REINTENTO_S = 4.0  # cada cuánto se vuelve a pedir un paquete que sigue faltando
BATERIA_REARME = 40  # al volver a subir de este valor (pulsera cambiada) las alertas se rearman
VENTANAS = [("2 min", 120), ("10 min", 600), ("Sesión", None)]
MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")

# Paleta
BG = "#EEF2F6"
CARD = "#FFFFFF"
NAVY = "#0F2A43"
TEXTO = "#16222E"
MUTED = "#5B6B7A"
BORDE = "#D5DEE7"
PRIMARIO = "#0B6BCB"
OK, OK_BG = "#1B8A5A", "#E3F4EB"
WARN, WARN_BG = "#B26A00", "#FFF1D6"
ALERTA, ALERTA_BG = "#C62828", "#FDE7E7"
GRIS, GRIS_BG = "#6B7B8A", "#E6EBF0"
SERIE = {"hr": "#0B6BCB", "spo2": "#0E8F8F", "temp": "#C2570C"}

COLOR_NIVEL = {  # nivel -> (color fuerte, fondo suave)
    None: (GRIS, GRIS_BG),
    NIVEL_NORMAL: (OK, OK_BG),
    NIVEL_VIGILANCIA: (WARN, WARN_BG),
    NIVEL_ALERTA: (ALERTA, ALERTA_BG),
}

CONEXION = {  # estado -> (fondo, texto, etiqueta)
    "desconectado": ("#3B5168", "#DCE5EE", "Desconectado"),
    "conectando": ("#B7791F", "#FFFFFF", "Conectando…"),
    "conectado": ("#1B8A5A", "#FFFFFF", "Conectado"),
    "sin_senal": ("#C25E00", "#FFFFFF", "Sin señal"),
    "error": ("#C62828", "#FFFFFF", "Error"),
}


def ip_local():
    """IP de esta PC en la red (la que debe usar el emisor). No envía nada a internet."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def elegir_familia(root):
    if sys.platform.startswith("win"):
        return "Segoe UI"
    if sys.platform == "darwin":
        return "Helvetica Neue"
    disponibles = set(tkfont.families(root))
    for candidata in ("Inter", "Noto Sans", "DejaVu Sans"):
        if candidata in disponibles:
            return candidata
    return "TkDefaultFont"


# ----------------------------------------------------------------------
# Puente asyncio -> Tkinter
# ----------------------------------------------------------------------
# Tkinter y asyncio no comparten loop. El transporte corre en un hilo aparte con su
# propio event loop; cada paquete que llega se coloca en una cola thread-safe que la
# ventana principal revisa varias veces por segundo con root.after(...).


class HiloTransporte(threading.Thread):
    def __init__(self, transporte, cola_salida):
        super().__init__(daemon=True)
        self.transporte = transporte
        self.cola_salida = cola_salida
        self._loop = None
        self._detenido = False

    def run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        if self._detenido:
            return

        def on_telemetry(paquete):
            self.cola_salida.put(("telemetry", paquete))

        def on_event(paquete):
            self.cola_salida.put(("event", paquete))

        try:
            self._loop.run_until_complete(
                self.transporte.recibir_paquetes(on_telemetry, on_event)
            )
            self.cola_salida.put(("fin", None))
        except Exception as exc:  # noqa: BLE001 - se reporta a la UI, no se silencia
            self.cola_salida.put(("error", str(exc) or exc.__class__.__name__))

    def detener(self):
        self._detenido = True
        if hasattr(self.transporte, "_detenido"):
            self.transporte._detenido = True  # cubre la carrera si el loop aún no arrancó
        if self._loop is not None and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.transporte.desconectar(), self._loop)


# ----------------------------------------------------------------------
# Componentes de la interfaz
# ----------------------------------------------------------------------


class TarjetaVital(tk.Frame):
    """Tarjeta con el valor actual de un signo vital y su estado."""

    def __init__(self, master, fam, titulo, unidad, rango_txt, fuente=30, al_pulsar=None):
        super().__init__(master, bg=CARD, highlightthickness=1, highlightbackground=BORDE, highlightcolor=BORDE)
        self.barra = tk.Frame(self, width=6, bg=GRIS)
        self.barra.pack(side=tk.LEFT, fill=tk.Y)
        cuerpo = tk.Frame(self, bg=CARD, padx=16, pady=7)
        cuerpo.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        fila = tk.Frame(cuerpo, bg=CARD)
        fila.pack(fill=tk.X)
        # El estado se empaqueta primero para que nunca se recorte; si falta espacio se acorta el título.
        self.chip = tk.Label(fila, text="Sin datos", font=(fam, 9, "bold"), padx=6, pady=1)
        self.chip.pack(side=tk.RIGHT)
        tk.Label(fila, text=titulo, bg=CARD, fg=MUTED, font=(fam, 9, "bold"), anchor="w").pack(
            side=tk.LEFT, fill=tk.X, expand=True)

        fila_valor = tk.Frame(cuerpo, bg=CARD)
        fila_valor.pack(fill=tk.X, pady=(4, 0))
        self.lbl_valor = tk.Label(fila_valor, text="--", bg=CARD, fg=TEXTO, font=(fam, fuente, "bold"))
        self.lbl_valor.pack(side=tk.LEFT)
        tk.Label(fila_valor, text=unidad, bg=CARD, fg=MUTED, font=(fam, 13)).pack(
            side=tk.LEFT, padx=(6, 0), pady=(10, 0)
        )
        self.lbl_rango = tk.Label(cuerpo, text=rango_txt, bg=CARD, fg=MUTED, font=(fam, 9))
        self.lbl_rango.pack(anchor="w")
        self.actualizar(None, None, "Sin datos")
        if al_pulsar is not None:
            self._hacer_pulsable(self, al_pulsar)

    def _hacer_pulsable(self, widget, al_pulsar):
        """Toda la tarjeta responde al clic, no solo el texto."""
        widget.bind("<Button-1>", lambda e: al_pulsar())
        try:
            widget.configure(cursor="hand2")
        except tk.TclError:
            pass
        for hijo in widget.winfo_children():
            self._hacer_pulsable(hijo, al_pulsar)

    def fijar_pie(self, texto):
        self.lbl_rango.config(text=texto)

    def actualizar(self, valor_txt, nivel, chip_txt):
        fuerte, suave = COLOR_NIVEL.get(nivel, COLOR_NIVEL[None])
        self.barra.config(bg=fuerte)
        self.lbl_valor.config(text=valor_txt if valor_txt is not None else "--",
                              fg=TEXTO if nivel in (None, NIVEL_NORMAL) else fuerte)
        self.chip.config(text=chip_txt, bg=suave, fg=fuerte)


RESOLUCIONES_BASE = ["Resuelta por enfermería", "Resuelta con indicación médica", "Falsa alarma",
                     "Paciente derivado o trasladado", "Otra"]
RESOLUCIONES_BATERIA = ["Pulsera reemplazada", "Pulsera puesta a cargar", "Falsa alarma (lectura errónea)", "Otra"]
RESOLUCIONES_DATOS = ["Datos recuperados por otra vía", "Pérdida confirmada y documentada", "Otra"]

# Frases rápidas por tipo de alerta: ahorran escritura, siempre se pueden editar.
FRASES = {
    "FALL": ["Se acudió y se evaluó al paciente, sin lesiones", "Se notificó al médico de guardia",
             "Se trasladó al paciente para valoración"],
    "SOS": ["Se acudió a la habitación y se atendió al paciente", "El paciente presionó el botón sin querer",
            "Se notificó al médico de guardia"],
    "HR": ["Se verificó el pulso de forma manual", "Paciente en reposo, se volvió a medir",
           "Se notificó al médico"],
    "SPO2": ["Se reposicionó el sensor y se volvió a medir", "Se administró oxígeno según indicación médica",
             "Se notificó al médico"],
    "TEMP": ["Se midió con termómetro clínico", "Se administró antipirético según indicación médica",
             "Se aplicaron medios físicos", "Se notificó al médico"],
    "BATERIA": ["Se reemplazó la pulsera por una cargada", "Se puso la pulsera a cargar"],
    "DATOS": ["Se revisó la conexión del receptor", "Se documentó el intervalo sin datos"],
}


def resoluciones_para(tipo):
    if str(tipo).startswith("BATERIA"):
        return RESOLUCIONES_BATERIA
    if tipo == "DATOS":
        return RESOLUCIONES_DATOS
    return RESOLUCIONES_BASE


def frases_para(tipo):
    for clave, frases in FRASES.items():
        if str(tipo).startswith(clave):
            return frases
    return []


class DialogoAtencion(tk.Toplevel):
    """
    Ventana para cerrar una alerta con detalle escrito por el personal: qué hizo, quién lo
    hizo, si hubo indicación médica, o si fue falsa alarma. No se cierra una alerta sin esto.
    """

    def __init__(self, app, tarjeta):
        super().__init__(app)
        self.app, self.tarjeta = app, tarjeta
        fam = app.fam
        self.title("Registrar atención de la alerta")
        self.configure(bg=BG)
        self.transient(app)
        self.resizable(False, False)
        marco = tk.Frame(self, bg=BG, padx=22, pady=16)
        marco.pack(fill=tk.BOTH, expand=True)

        tk.Label(marco, text=tarjeta.titulo, bg=BG, fg=TEXTO, font=(fam, 14, "bold")).pack(anchor="w")
        tk.Label(marco, text=tarjeta.detalle, bg=BG, fg=MUTED, font=(fam, 10), wraplength=500,
                 justify="left").pack(anchor="w")
        tk.Label(marco, text=f"Tiempo sin atender: {formato_duracion(tarjeta.segundos())}", bg=BG, fg=ALERTA,
                 font=(fam, 10, "bold")).pack(anchor="w", pady=(2, 10))

        def etiqueta(texto):
            tk.Label(marco, text=texto, bg=BG, fg=TEXTO, font=(fam, 10, "bold")).pack(anchor="w", pady=(8, 2))

        etiqueta("¿Cómo se resolvió?")
        self.var_res = tk.StringVar()
        self.combo = ttk.Combobox(marco, textvariable=self.var_res, values=resoluciones_para(tarjeta.tipo),
                                  state="readonly", width=46, font=(fam, 10))
        self.combo.pack(anchor="w")
        self.combo.bind("<<ComboboxSelected>>", lambda e: self._al_elegir())

        etiqueta("¿Qué se hizo? (descripción de la acción realizada)")
        self.txt = tk.Text(marco, width=58, height=5, wrap="word", font=(fam, 10), relief=tk.FLAT,
                           highlightthickness=1, highlightbackground=BORDE, highlightcolor=PRIMARIO)
        self.txt.pack(anchor="w")
        frases = frases_para(tarjeta.tipo)
        if frases:
            fila = tk.Frame(marco, bg=BG)
            fila.pack(anchor="w", pady=(6, 0), fill=tk.X)
            tk.Label(fila, text="Frases rápidas:", bg=BG, fg=MUTED, font=(fam, 9)).pack(anchor="w")
            for frase in frases:
                tk.Button(fila, text="+ " + frase, command=lambda f=frase: self._insertar(f), bg=GRIS_BG, fg=TEXTO,
                          activebackground="#D3DAE1", relief=tk.FLAT, bd=0, padx=8, pady=2, font=(fam, 9),
                          cursor="hand2", anchor="w").pack(anchor="w", pady=1)

        fila_med = tk.Frame(marco, bg=BG)
        fila_med.pack(anchor="w", fill=tk.X, pady=(10, 0))
        self.var_ind = tk.BooleanVar(value=False)
        tk.Checkbutton(fila_med, text="Hubo indicación médica", variable=self.var_ind, command=self._al_cambiar_ind,
                       bg=BG, fg=TEXTO, activebackground=BG, font=(fam, 10), selectcolor="white").pack(side=tk.LEFT)
        tk.Label(fila_med, text="Médico que indicó:", bg=BG, fg=MUTED, font=(fam, 9)).pack(side=tk.LEFT, padx=(14, 4))
        self.var_medico = tk.StringVar()
        self.ent_medico = tk.Entry(fila_med, textvariable=self.var_medico, width=24, relief=tk.FLAT, font=(fam, 10),
                                   highlightthickness=1, highlightbackground=BORDE, highlightcolor=PRIMARIO,
                                   state="disabled", disabledbackground="#E6EBF0")
        self.ent_medico.pack(side=tk.LEFT, ipady=2)

        etiqueta("Atendido por (enfermera o enfermero)")
        self.var_persona = tk.StringVar(value=app.ultimo_personal)
        self.ent_persona = tk.Entry(marco, textvariable=self.var_persona, width=40, relief=tk.FLAT, font=(fam, 10),
                                    highlightthickness=1, highlightbackground=BORDE, highlightcolor=PRIMARIO)
        self.ent_persona.pack(anchor="w", ipady=2)

        self.lbl_error = tk.Label(marco, text="", bg=BG, fg=ALERTA, font=(fam, 9, "bold"), wraplength=500,
                                  justify="left")
        self.lbl_error.pack(anchor="w", pady=(8, 0))

        botones = tk.Frame(marco, bg=BG)
        botones.pack(fill=tk.X, pady=(10, 0))
        tk.Button(botones, text="Guardar y cerrar la alerta", command=self._guardar, bg=OK, fg="white",
                  activebackground="#157049", activeforeground="white", relief=tk.FLAT, bd=0, padx=16, pady=6,
                  font=(fam, 10, "bold"), cursor="hand2").pack(side=tk.RIGHT)
        tk.Button(botones, text="Cancelar (la alerta sigue pendiente)", command=self.destroy, bg=GRIS_BG, fg=TEXTO,
                  activebackground="#D3DAE1", relief=tk.FLAT, bd=0, padx=14, pady=6, font=(fam, 10),
                  cursor="hand2").pack(side=tk.RIGHT, padx=(0, 8))

        self.update_idletasks()
        x = app.winfo_rootx() + max(0, (app.winfo_width() - self.winfo_width()) // 2)
        y = app.winfo_rooty() + max(0, (app.winfo_height() - self.winfo_height()) // 3)
        self.geometry(f"+{x}+{y}")
        try:
            self.wait_visibility()
            self.grab_set()
        except tk.TclError:
            pass
        self.txt.focus_set()

    def _insertar(self, frase):
        actual = self.txt.get("1.0", "end").strip()
        if actual:
            actual += " " if actual.endswith(".") else ". "
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", actual + frase)

    def _al_elegir(self):
        if "indicación médica" in self.var_res.get().lower():
            self.var_ind.set(True)
            self._al_cambiar_ind()

    def _al_cambiar_ind(self):
        self.ent_medico.config(state="normal" if self.var_ind.get() else "disabled")

    def datos(self):
        return {
            "resolucion": self.var_res.get().strip(),
            "accion_realizada": self.txt.get("1.0", "end").strip(),
            "atendido_por": self.var_persona.get().strip(),
            "indicacion_medica": bool(self.var_ind.get()),
            "medico": self.var_medico.get().strip() if self.var_ind.get() else "",
        }

    @staticmethod
    def validar(d):
        if not d["resolucion"]:
            return "Elige cómo se resolvió la alerta."
        if len(d["accion_realizada"]) < 8:
            return "Describe qué se hizo (incluso si fue falsa alarma, indica el motivo)."
        if len(d["atendido_por"]) < 2:
            return "Escribe quién atendió la alerta."
        if d["indicacion_medica"] and len(d["medico"]) < 2:
            return "Escribe el nombre del médico que dio la indicación."
        return None

    def _guardar(self):
        d = self.datos()
        error = self.validar(d)
        if error:
            self.lbl_error.config(text=error)
            return
        self.app.ultimo_personal = d["atendido_por"]
        try:
            self.destroy()
        finally:
            self.tarjeta.cerrar(d)


class TarjetaAlerta(tk.Frame):
    """Una alerta en el panel lateral, con cronómetro. Se cierra con el detalle escrito por el personal."""

    def __init__(self, master, app, titulo, detalle, gravedad, origen, tipo, seq, requiere_atencion=True,
                 escalamiento_s=ESCALAMIENTO_S, leve=False):
        super().__init__(master, bg=ALERTA_BG, highlightthickness=1, highlightbackground="#E8B4B4", highlightcolor="#E8B4B4")
        self.app = app
        self.titulo = titulo
        self.origen, self.tipo, self.seq, self.detalle = origen, tipo, seq, detalle
        self.requiere_atencion = requiere_atencion
        self.escalamiento_s = escalamiento_s
        self.leve = leve  # aviso preventivo (ámbar) en vez de alerta (rojo)
        self.creada = time.monotonic()
        self.atendida = False
        self.escalada = False
        fam = app.fam

        self.barra = tk.Frame(self, width=6, bg=ALERTA)
        self.barra.pack(side=tk.LEFT, fill=tk.Y)
        self.cuerpo = tk.Frame(self, bg=ALERTA_BG, padx=10, pady=5)
        self.cuerpo.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        fila1 = tk.Frame(self.cuerpo, bg=ALERTA_BG)
        fila1.pack(fill=tk.X)
        self.lbl_titulo = tk.Label(fila1, text=titulo, bg=ALERTA_BG, fg=TEXTO, font=(fam, 11, "bold"),
                                  anchor="w", justify="left", wraplength=165)
        self.lbl_titulo.pack(side=tk.LEFT)
        self.boton = tk.Button(
            fila1, text="Atender" if requiere_atencion else "Descartar", command=self.atender,
            bg=PRIMARIO, fg="white", activebackground="#0956A5", activeforeground="white",
            relief=tk.FLAT, bd=0, padx=10, pady=0, font=(fam, 9, "bold"), cursor="hand2",
        )
        self.boton.pack(side=tk.RIGHT)
        self.lbl_detalle = tk.Label(self.cuerpo, text=detalle, bg=ALERTA_BG, fg=MUTED, font=(fam, 9),
                                    anchor="w", justify="left", wraplength=235)
        self.lbl_detalle.pack(fill=tk.X)
        fila3 = tk.Frame(self.cuerpo, bg=ALERTA_BG)
        fila3.pack(fill=tk.X)
        self.lbl_estado = tk.Label(fila3, text="", bg=ALERTA_BG, fg=ALERTA, font=(fam, 9, "bold"),
                                   anchor="w", justify="left", wraplength=235)
        self.lbl_estado.pack(side=tk.LEFT)
        self.lbl_grav = tk.Label(fila3, text=f"Gravedad {gravedad}" if gravedad else "", bg=ALERTA_BG,
                                 fg=ALERTA, font=(fam, 8, "bold"))
        self.lbl_grav.pack(side=tk.RIGHT)
        self._fondos = [self, self.cuerpo, fila1, fila3, self.lbl_titulo, self.lbl_grav,
                        self.lbl_detalle, self.lbl_estado]
        self.refrescar()

    def _pintar(self, fondo, barra, texto_estado=None):
        for w in self._fondos:
            w.config(bg=fondo)
        self.barra.config(bg=barra)
        self.lbl_estado.config(fg=barra)
        self.lbl_grav.config(fg=barra)
        if texto_estado is not None:
            self.lbl_estado.config(text=texto_estado)

    def segundos(self):
        return time.monotonic() - self.creada

    def refrescar(self):
        if self.atendida:
            return
        if not self.requiere_atencion:
            self._pintar(WARN_BG, WARN, "Aviso del sistema")
            return
        s = self.segundos()
        if s >= self.escalamiento_s:
            if not self.escalada:
                self.escalada = True
                self.app.alerta_escalada(self)
            self._pintar("#F9D2D2", "#8E0000", f"ESCALADA · sin atender {formato_duracion(s)}")
        elif self.leve:
            self._pintar(WARN_BG, WARN, f"Pendiente · {formato_duracion(s)}")
        else:
            self._pintar(ALERTA_BG, ALERTA, f"Sin atender · {formato_duracion(s)}")

    def atender(self):
        if self.atendida:
            return
        if self.requiere_atencion:
            DialogoAtencion(self.app, self)
        else:
            self.atendida = True
            self.app.quitar_alerta(self)

    def cerrar(self, cierre):
        """Cierra la alerta con el detalle manual (cierre es el dict que arma DialogoAtencion)."""
        if self.atendida:
            return
        self.atendida = True
        respuesta = self.segundos()
        self._pintar(OK_BG, OK, f"Cerrada · {cierre['resolucion']} · {respuesta:.0f} s")
        nota = cierre["accion_realizada"]
        resumen = nota if len(nota) <= 110 else nota[:107] + "…"
        extra = f" (indicó: {cierre['medico']})" if cierre.get("indicacion_medica") and cierre.get("medico") else ""
        self.lbl_detalle.config(text=f"{cierre['atendido_por']}{extra}: {resumen}")
        self.lbl_grav.config(text="")
        try:
            self.boton.destroy()
        except tk.TclError:
            pass
        self.app.alerta_cerrada(self, respuesta, cierre)


# ----------------------------------------------------------------------
# Ventana principal
# ----------------------------------------------------------------------


class Aplicacion(tk.Tk):
    def __init__(self, intervalo_sim=2.0, autoiniciar=True, carpeta_datos=CARPETA_DATOS,
                 bd_ruta=ARCHIVO_BD, sembrar=True):
        super().__init__()
        self.title("VitalCare · Testbed de pulsera de signos vitales (ITT466)")
        ancho_pantalla, alto_pantalla = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{min(1360, ancho_pantalla)}x{min(860, alto_pantalla - 60)}+0+0")
        self.minsize(1180, 700)
        if alto_pantalla < 900:
            # En pantallas de 768 px de alto conviene aprovechar hasta el último pixel
            try:
                self.state("zoomed")          # Windows
            except tk.TclError:
                self.attributes("-zoomed", True)  # Linux
        self.configure(bg=BG)
        self.fam = elegir_familia(self)
        self.intervalo_sim = intervalo_sim
        self.carpeta_datos = carpeta_datos

        # Base de datos del centro: pacientes, pulseras, asignaciones y configuración.
        self.bd = BaseDatos(bd_ruta)
        if sembrar:
            self.bd.sembrar_demostracion()
        self.usuario = ""
        self.paciente_id = None
        self.paciente = None
        self.dispositivo_esperado = None
        self.umbrales = dict(UMBRALES)
        self.cfg = {}
        self._cargar_config()
        self.sesion_bd = None
        self._t_latido = 0.0
        self._ajenos = {}
        self._corte_pendiente = None
        self._niveles_vitales = {}
        self._causas_vitales = []
        self._nivel_manual = {}
        self._causas_manual = []

        # Estado de la conexión y de la sesión
        self.cola = queue.Queue()
        self.hilo = None
        self.transporte = None
        self.registrador = None
        self.sesion = Sesion()
        self.fuente_actual = FUENTE_SIM
        self.conexion = "desconectado"
        self._t_ultimo_paquete = None
        self._critico_activo = {"hr": False, "spo2": False, "temp": False, "batt_baja": False, "batt_critica": False}
        self.ultimo_personal = ""
        self._huecos = {}
        self.tarjetas_alerta = []
        self._artistas_marcas = []
        self._offset_local = datetime.now().astimezone().utcoffset().total_seconds()
        self._ultimo_estado = None

        self.var_fuente = tk.StringVar(value=FUENTE_SIM)
        self.var_param = tk.StringVar(value="")
        self.var_ventana = tk.IntVar(value=0)  # índice en VENTANAS
        self.var_sonido = tk.BooleanVar(value=True)
        self.var_escenario = tk.StringVar(value="Paciente estable")
        self._pantalla_completa = False

        self._estilos()
        self._construir_encabezado()
        self._construir_pestanas()
        self._construir_contexto()
        self._construir_banner()
        self._construir_tarjetas()
        self._construir_pie()
        self._construir_cuerpo()
        self._construir_gestion()
        self._retomar_ultimo_paciente()
        self._al_cambiar_fuente()
        self._redibujar()
        self._revisar_cortes_previos()

        self.bind("<F2>", lambda e: self._demo_evento("FALL"))
        self.bind("<F3>", lambda e: self._demo_evento("SOS"))
        self.bind("<F4>", lambda e: self.simular_corte_red())
        self.bind("<F5>", lambda e: self.simular_corte_energia())
        self.bind("<F8>", lambda e: self.capturar_evidencia())
        self.bind("<F11>", lambda e: self._alternar_pantalla_completa())
        self.bind("<Escape>", lambda e: self._salir_pantalla_completa())
        self.protocol("WM_DELETE_WINDOW", self._al_cerrar)

        self._job_cola = self.after(100, self._procesar_cola)
        self._job_tick = self.after(500, self._tick)
        if autoiniciar:
            self.after(200, self._conectar)

    # ---- construcción de la interfaz -----------------------------------

    def _estilos(self):
        estilos_ttk(self, self.fam)

    def _boton(self, master, texto, comando, color=PRIMARIO, fg="white", activo="#0956A5", **kw):
        return tk.Button(master, text=texto, command=comando, bg=color, fg=fg,
                         activebackground=activo, activeforeground=fg, relief=tk.FLAT, bd=0,
                         padx=14, pady=5, font=(self.fam, 10, "bold"), cursor="hand2", **kw)

    def _construir_encabezado(self):
        enc = tk.Frame(self, bg=NAVY, height=58)
        enc.pack(side=tk.TOP, fill=tk.X)
        enc.pack_propagate(False)

        marca = tk.Frame(enc, bg=NAVY)
        marca.pack(side=tk.LEFT, padx=(18, 0))
        tk.Label(marca, text="VitalCare", bg=NAVY, fg="white", font=(self.fam, 16, "bold")).pack(side=tk.LEFT)
        tk.Label(marca, text="Testbed de pulsera · ITT466", bg=NAVY, fg="#9FB3C8",
                 font=(self.fam, 10)).pack(side=tk.LEFT, padx=(12, 0), pady=(4, 0))

        derecha = tk.Frame(enc, bg=NAVY)
        derecha.pack(side=tk.RIGHT, padx=18)
        self.chip_conexion = tk.Label(derecha, text="", font=(self.fam, 10, "bold"), padx=12, pady=4)
        self.chip_conexion.pack(side=tk.RIGHT, padx=(14, 0))
        self.btn_conectar = self._boton(derecha, "Conectar", self._alternar_conexion,
                                        color="#2E9E6B", activo="#237F54")
        self.btn_conectar.pack(side=tk.RIGHT, padx=(14, 0))

        self.entrada_param = tk.Entry(derecha, textvariable=self.var_param, width=14, relief=tk.FLAT,
                                      font=(self.fam, 10), highlightthickness=1,
                                      highlightbackground=BORDE, highlightcolor=PRIMARIO,
                                      disabledbackground="#1B3A57", disabledforeground="#6F88A0")
        self.entrada_param.pack(side=tk.RIGHT, ipady=3)
        self.lbl_param = tk.Label(derecha, text="", bg=NAVY, fg="#9FB3C8", font=(self.fam, 9))
        self.lbl_param.pack(side=tk.RIGHT, padx=(14, 6))

        self.combo_fuente = ttk.Combobox(derecha, textvariable=self.var_fuente, values=FUENTES,
                                         state="readonly", width=16, font=(self.fam, 10))
        self.combo_fuente.pack(side=tk.RIGHT)
        self.combo_fuente.bind("<<ComboboxSelected>>", lambda e: self._al_cambiar_fuente())
        tk.Label(derecha, text="Fuente de datos", bg=NAVY, fg="#9FB3C8", font=(self.fam, 9)).pack(
            side=tk.RIGHT, padx=(0, 8))
        self._pintar_conexion()

    def _construir_pestanas(self):
        """Cinco pestañas: el monitoreo y las pantallas de gestión del centro."""
        self.pestanas = ttk.Notebook(self)
        self.pestanas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.tab_mon = tk.Frame(self.pestanas, bg=BG)
        self.pestanas.add(self.tab_mon, text="  Monitoreo  ")

    def _construir_gestion(self):
        self.tab_pacientes = PantallaPacientes(self.pestanas, self)
        self.tab_pulseras = PantallaPulseras(self.pestanas, self)
        self.tab_config = PantallaConfig(self.pestanas, self)
        self.pestanas.add(self.tab_pacientes, text="  Pacientes  ")
        self.pestanas.add(self.tab_pulseras, text="  Pulseras  ")
        self.pestanas.add(self.tab_config, text="  Configuración  ")
        self.pestanas.bind("<<NotebookTabChanged>>", self._al_cambiar_pestana)
        self._refrescar_combo_pacientes()

    def _al_cambiar_pestana(self, _evento=None):
        actual = self.pestanas.select()
        if actual == str(self.tab_pacientes):
            self.tab_pacientes.refrescar()
        elif actual == str(self.tab_pulseras):
            self.tab_pulseras.refrescar()

    def _construir_contexto(self):
        """Barra que dice a quién se está monitoreando y con qué pulsera."""
        barra = tk.Frame(self.tab_mon, bg=CARD, highlightthickness=1, highlightbackground=BORDE,
                         highlightcolor=BORDE)
        barra.pack(side=tk.TOP, fill=tk.X, padx=16, pady=(12, 0))
        der = tk.Frame(barra, bg=CARD)
        der.pack(side=tk.RIGHT, padx=14, pady=8)
        self.lbl_pulsera = tk.Label(der, text="Sin pulsera asignada", bg=GRIS_BG, fg=TEXTO,
                                    font=(self.fam, 10, "bold"), padx=10, pady=4)
        self.lbl_pulsera.pack(side=tk.RIGHT, padx=(10, 0))
        self._boton(der, "Signos a mano", self.registrar_signos_manuales, color=GRIS_BG, fg=TEXTO,
                    activo="#D3DAE1").pack(side=tk.RIGHT, padx=(8, 0))
        self._boton(der, "Cambiar pulsera", self.cambiar_pulsera, color=GRIS_BG, fg=TEXTO,
                    activo="#D3DAE1").pack(side=tk.RIGHT)

        izq = tk.Frame(barra, bg=CARD)
        izq.pack(side=tk.LEFT, padx=14, pady=6, fill=tk.X, expand=True)
        fila = tk.Frame(izq, bg=CARD)
        fila.pack(anchor="w", fill=tk.X)
        tk.Label(fila, text="Paciente en monitoreo", bg=CARD, fg=MUTED,
                 font=(self.fam, 9, "bold")).pack(side=tk.LEFT, padx=(0, 10))
        self.var_paciente = tk.StringVar(value="Sin paciente seleccionado")
        self.combo_paciente = ttk.Combobox(fila, textvariable=self.var_paciente, state="readonly",
                                           width=36, font=(self.fam, 10))
        self.combo_paciente.pack(side=tk.LEFT)
        self.combo_paciente.bind("<<ComboboxSelected>>", self._al_elegir_paciente)
        linea_ficha = tk.Frame(izq, bg=CARD)
        linea_ficha.pack(anchor="w", fill=tk.X, pady=(1, 0))
        self.lbl_ficha = tk.Label(linea_ficha, text="Elija un paciente ingresado o trabaje en modo libre.",
                                  bg=CARD, fg=MUTED, font=(self.fam, 9), anchor="w")
        self.lbl_ficha.pack(side=tk.LEFT)
        self.lbl_alergias = tk.Label(linea_ficha, text="", bg=CARD, fg=ALERTA,
                                     font=(self.fam, 9, "bold"), anchor="w")
        self.lbl_alergias.pack(side=tk.LEFT, padx=(8, 0))

    def _construir_banner(self):
        self.banner = tk.Frame(self.tab_mon, bg=GRIS_BG, height=48)
        self.banner.pack(side=tk.TOP, fill=tk.X, padx=16, pady=(12, 0))
        self.banner.pack_propagate(False)
        self.banner_barra = tk.Frame(self.banner, width=8, bg=GRIS)
        self.banner_barra.pack(side=tk.LEFT, fill=tk.Y)
        self.lbl_estado = tk.Label(self.banner, text="SIN DATOS", bg=GRIS_BG, fg=GRIS,
                                   font=(self.fam, 17, "bold"))
        self.lbl_estado.pack(side=tk.LEFT, padx=(16, 0))
        self.lbl_causas = tk.Label(self.banner, text="Esperando conexión con la fuente de datos",
                                   bg=GRIS_BG, fg=MUTED, font=(self.fam, 11))
        self.lbl_causas.pack(side=tk.LEFT, padx=(18, 0))
        self.lbl_integridad = tk.Label(self.banner, text="", bg=GRIS_BG, fg=MUTED,
                                       font=(self.fam, 10, "bold"))
        self.lbl_integridad.pack(side=tk.RIGHT, padx=16)

    def _construir_tarjetas(self):
        """Los seis signos juntos: los tres de la pulsera, los dos de enfermería y la batería."""
        fila = tk.Frame(self.tab_mon, bg=BG)
        fila.pack(side=tk.TOP, fill=tk.X, padx=16, pady=(10, 0))
        self.orden_tarjetas = ("hr", "spo2", "temp", "pa", "fr", "batt")
        for i in range(len(self.orden_tarjetas)):
            fila.columnconfigure(i, weight=1, uniform="tarjeta")
        u = self.umbrales
        abrir = self.registrar_signos_manuales
        self.tarjetas = {
            "hr": TarjetaVital(fila, self.fam, "FREC. CARDÍACA", u["hr"]["unidad"], texto_rango("hr", u)),
            "spo2": TarjetaVital(fila, self.fam, "OXÍGENO EN SANGRE", "%", texto_rango("spo2", u)),
            "temp": TarjetaVital(fila, self.fam, "TEMPERATURA", u["temp"]["unidad"],
                                 texto_rango("temp", u)),
            "pa": TarjetaVital(fila, self.fam, "PRESIÓN ARTERIAL", "mmHg", "la toma enfermería",
                               fuente=25, al_pulsar=abrir),
            "fr": TarjetaVital(fila, self.fam, "FREC. RESPIRATORIA", "rpm", "la toma enfermería",
                               fuente=25, al_pulsar=abrir),
            "batt": TarjetaVital(fila, self.fam, "BATERÍA", "%",
                                 f"Aviso bajo {self.cfg['bateria_baja']:g} %"),
        }
        ultimo = len(self.orden_tarjetas) - 1
        for i, clave in enumerate(self.orden_tarjetas):
            self.tarjetas[clave].grid(row=0, column=i, sticky="nsew",
                                      padx=(0 if i == 0 else 5, 0 if i == ultimo else 5))

    def _construir_cuerpo(self):
        cuerpo = tk.Frame(self.tab_mon, bg=BG)
        cuerpo.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=16, pady=(10, 8))

        # --- gráficas ---
        col_izq = tk.Frame(cuerpo, bg=BG)
        col_izq.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        panel = tk.Frame(col_izq, bg=CARD, highlightthickness=1, highlightbackground=BORDE,
                         highlightcolor=BORDE)
        panel.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        cab = tk.Frame(panel, bg=CARD)
        cab.pack(fill=tk.X, padx=14, pady=(10, 0))
        tk.Label(cab, text="Tendencias en vivo", bg=CARD, fg=TEXTO, font=(self.fam, 12, "bold")).pack(side=tk.LEFT)
        self.segmentos = []
        seg = tk.Frame(cab, bg=GRIS_BG)
        seg.pack(side=tk.RIGHT)
        for i, (texto, _) in enumerate(VENTANAS):
            lbl = tk.Label(seg, text=texto, font=(self.fam, 9, "bold"), padx=12, pady=3, cursor="hand2")
            lbl.pack(side=tk.LEFT, padx=1, pady=1)
            lbl.bind("<Button-1>", lambda e, idx=i: self._elegir_ventana(idx))
            self.segmentos.append(lbl)
        tk.Label(cab, text="Ventana:", bg=CARD, fg=MUTED, font=(self.fam, 9)).pack(side=tk.RIGHT, padx=(0, 8))
        self._pintar_segmentos()

        matplotlib.rcParams["font.family"] = [self.fam, "DejaVu Sans"]
        self.fig = Figure(figsize=(7, 4), dpi=100, facecolor=CARD)
        self.ax = {}
        self._bandas = {}
        self.lineas = {}
        self.puntos = {}
        config = (
            ("hr", "Frecuencia cardíaca (lpm)"),
            ("spo2", "SpO₂ (%)"),
            ("temp", "Temperatura estimada (°C)"),
        )
        for i, (clave, titulo) in enumerate(config):
            ax = self.fig.add_subplot(3, 1, i + 1, sharex=self.ax["hr"] if i else None)
            self.ax[clave] = ax
            lo, hi = self.umbrales[clave]["normal"]
            self._bandas[clave] = ax.axhspan(lo, hi, color=OK, alpha=0.10, lw=0)
            ax.set_title(titulo, loc="left", fontsize=10, color=TEXTO, fontweight="bold", pad=6)
            etiqueta = f"normal ≥ {lo:g}" if clave == "spo2" else f"normal {lo:g}–{hi:g}"
            ax.set_title(etiqueta, loc="right", fontsize=8, color=OK, pad=6)
            (linea,) = ax.plot([], [], color=SERIE[clave], linewidth=1.8)
            (punto,) = ax.plot([], [], "o", color=SERIE[clave], markersize=6)
            self.lineas[clave], self.puntos[clave] = linea, punto
            ax.grid(True, color="#E3E9EF", linewidth=0.8)
            ax.set_axisbelow(True)
            for lado in ("top", "right"):
                ax.spines[lado].set_visible(False)
            for lado in ("left", "bottom"):
                ax.spines[lado].set_color(BORDE)
            ax.tick_params(colors=MUTED, labelsize=8, length=3)
            ax.set_facecolor(CARD)
            if i < 2:
                ax.tick_params(labelbottom=False)
        self.ax["hr"].xaxis_date()
        formato = mdates.DateFormatter("%H:%M:%S")
        self.ax["temp"].xaxis.set_major_formatter(formato)
        self.ax["temp"].xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=7))
        # Reparto automático del alto: así los títulos no se montan en pantallas pequeñas
        self.fig.set_layout_engine("constrained", h_pad=0.12, hspace=0.10, w_pad=0.04)
        self.canvas = FigureCanvasTkAgg(self.fig, master=panel)
        self.canvas.get_tk_widget().configure(bg=CARD, highlightthickness=0)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))

        # --- columna derecha ---
        lateral = tk.Frame(cuerpo, bg=BG, width=404)
        lateral.pack(side=tk.RIGHT, fill=tk.Y, padx=(12, 0))
        lateral.pack_propagate(False)

        # Alertas
        self.panel_alertas = self._panel(lateral, "Alertas")
        self.lbl_titulo_alertas = self.panel_alertas.winfo_children()[0]
        self.lbl_sin_alertas = tk.Label(self.panel_alertas, text="Sin alertas por ahora", bg=CARD,
                                        fg=MUTED, font=(self.fam, 10))
        self.lbl_sin_alertas.pack(pady=18)
        zona = tk.Frame(self.panel_alertas, bg=CARD)
        zona.pack(fill=tk.BOTH, expand=True, padx=(8, 4), pady=(0, 8))
        self.canvas_alertas = tk.Canvas(zona, bg=CARD, highlightthickness=0, bd=0)
        self.scroll_alertas = ttk.Scrollbar(zona, orient="vertical", command=self.canvas_alertas.yview)
        self.canvas_alertas.configure(yscrollcommand=self.scroll_alertas.set)
        self.scroll_alertas.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas_alertas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.lista_alertas = tk.Frame(self.canvas_alertas, bg=CARD)
        ventana_lista = self.canvas_alertas.create_window((0, 0), window=self.lista_alertas, anchor="nw")
        self.lista_alertas.bind(
            "<Configure>", lambda e: self.canvas_alertas.configure(scrollregion=self.canvas_alertas.bbox("all")))
        self.canvas_alertas.bind(
            "<Configure>", lambda e: self.canvas_alertas.itemconfigure(ventana_lista, width=e.width))

        def rueda(evento):
            self.canvas_alertas.yview_scroll(int(-1 * (evento.delta / 120)) if evento.delta else 0, "units")

        self.canvas_alertas.bind("<Enter>", lambda e: self.canvas_alertas.bind_all("<MouseWheel>", rueda))
        self.canvas_alertas.bind("<Leave>", lambda e: self.canvas_alertas.unbind_all("<MouseWheel>"))

        # Enlace: tres cifras y una línea de detalle, para dejarle sitio a las alertas
        enlace = self._panel(lateral, "Enlace con la pulsera")
        # Se empaqueta antes que las alertas para que nunca quede cortado abajo.
        enlace.pack(side=tk.BOTTOM, fill=tk.X, pady=(10, 0))
        self.panel_alertas.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(10, 0))
        self.lbl_enlace = {}
        cuadro = tk.Frame(enlace, bg=CARD)
        cuadro.pack(fill=tk.X, padx=14)
        for c in range(3):
            cuadro.columnconfigure(c, weight=1, uniform="enlace")
        for i, (clave, texto) in enumerate((("paquetes", "Recibidos"), ("recuperados", "Recuperados"),
                                            ("perdidos", "Perdidos reales"))):
            celda = tk.Frame(cuadro, bg=CARD)
            celda.grid(row=0, column=i, sticky="w")
            tk.Label(celda, text=texto, bg=CARD, fg=MUTED, font=(self.fam, 8)).pack(anchor="w")
            lbl = tk.Label(celda, text="--", bg=CARD, fg=TEXTO, font=(self.fam, 12, "bold"))
            lbl.pack(anchor="w")
            self.lbl_enlace[clave] = lbl
        detalle = tk.Frame(enlace, bg=CARD)
        detalle.pack(fill=tk.X, padx=14, pady=(4, 10))
        for i, (clave, texto) in enumerate((("ultimo", "Último"), ("sensor", "Sensor"),
                                            ("espera", "Recuperando"))):
            if i:
                tk.Label(detalle, text="·", bg=CARD, fg=BORDE,
                         font=(self.fam, 9)).pack(side=tk.LEFT, padx=5)
            tk.Label(detalle, text=texto + ":", bg=CARD, fg=MUTED,
                     font=(self.fam, 8)).pack(side=tk.LEFT, pady=(2, 0))
            lbl = tk.Label(detalle, text="--", bg=CARD, fg=TEXTO, font=(self.fam, 9, "bold"))
            lbl.pack(side=tk.LEFT, padx=(3, 0))
            self.lbl_enlace[clave] = lbl
        # Solo con la fuente Hub (MQTT): estado del broker y del hub
        self.lbl_hub = tk.Label(enlace, text="", bg=CARD, fg=OK, font=(self.fam, 9, "bold"), anchor="w",
                                justify="left", wraplength=370)
        self._hub = {}

    def _panel(self, master, titulo):
        p = tk.Frame(master, bg=CARD, highlightthickness=1, highlightbackground=BORDE, highlightcolor=BORDE)
        tk.Label(p, text=titulo, bg=CARD, fg=TEXTO, font=(self.fam, 11, "bold")).pack(
            anchor="w", padx=14, pady=(10, 6))
        return p

    def _construir_pie(self):
        pie = tk.Frame(self.tab_mon, bg=BG)
        pie.pack(side=tk.BOTTOM, fill=tk.X, padx=16, pady=(0, 10))

        # Fila de archivos y utilidades
        fila_arch = tk.Frame(pie, bg=BG)
        fila_arch.pack(side=tk.BOTTOM, fill=tk.X, pady=(8, 0))
        # Los botones se empaquetan antes que los textos: un aviso largo no debe tapar ningún botón.
        self.lbl_csv = tk.Label(fila_arch, text="Datos: sin sesión activa", bg=BG, fg=MUTED, font=(self.fam, 9))
        self.lbl_aviso = tk.Label(fila_arch, text="", bg=BG, fg=PRIMARIO, font=(self.fam, 9, "bold"))
        self._boton(fila_arch, "Pantalla completa (F11)", self._alternar_pantalla_completa, color=GRIS_BG,
                    fg=TEXTO, activo="#D3DAE1").pack(side=tk.RIGHT, padx=(8, 0))
        self._boton(fila_arch, "Abrir carpeta de datos", self._abrir_carpeta, color=GRIS_BG, fg=TEXTO,
                    activo="#D3DAE1").pack(side=tk.RIGHT, padx=(8, 0))
        self._boton(fila_arch, "Evidencia (F8)", self.capturar_evidencia, color=GRIS_BG, fg=TEXTO,
                    activo="#D3DAE1").pack(side=tk.RIGHT, padx=(8, 0))
        self._boton(fila_arch, "Resumen de sesión", self._mostrar_resumen).pack(side=tk.RIGHT, padx=(8, 0))
        tk.Checkbutton(fila_arch, text="Sonido de alerta", variable=self.var_sonido, bg=BG, fg=TEXTO,
                       activebackground=BG, font=(self.fam, 9), selectcolor="white").pack(side=tk.RIGHT, padx=(8, 0))
        self.lbl_csv.pack(side=tk.LEFT)
        self.lbl_aviso.pack(side=tk.LEFT, padx=(16, 0))

        # Barra de demostración (solo activa con el simulador)
        self.panel_demo = tk.Frame(pie, bg=CARD, highlightthickness=1, highlightbackground=BORDE, highlightcolor=BORDE)
        self.panel_demo.pack(side=tk.BOTTOM, fill=tk.X)
        # Los botones se empaquetan primero para que no los recorte el selector de escenario.
        self.btn_sos = self._boton(self.panel_demo, "Simular SOS (F3)", lambda: self._demo_evento("SOS"),
                                   color=ALERTA, activo="#9E1F1F")
        self.btn_sos.pack(side=tk.RIGHT, padx=(6, 10), pady=6)
        self.btn_caida = self._boton(self.panel_demo, "Simular caída (F2)", lambda: self._demo_evento("FALL"),
                                     color="#C2570C", activo="#9E4509")
        self.btn_caida.pack(side=tk.RIGHT, padx=(6, 0), pady=6)
        self.btn_energia = self._boton(self.panel_demo, "Corte de energía (F5)", self.simular_corte_energia,
                                       color=GRIS_BG, fg=TEXTO, activo="#D3DAE1")
        self.btn_energia.pack(side=tk.RIGHT, padx=(6, 0), pady=6)
        self.btn_red = self._boton(self.panel_demo, "Caída de red (F4)", lambda: self.simular_corte_red(),
                                   color=GRIS_BG, fg=TEXTO, activo="#D3DAE1")
        self.btn_red.pack(side=tk.RIGHT, padx=(6, 0), pady=6)
        tk.Label(self.panel_demo, text="Modo demostración", bg=CARD, fg=TEXTO,
                 font=(self.fam, 10, "bold")).pack(side=tk.LEFT, padx=(14, 4), pady=8)
        tk.Label(self.panel_demo, text="(solo simulador)", bg=CARD, fg=MUTED,
                 font=(self.fam, 9)).pack(side=tk.LEFT, padx=(0, 14))
        tk.Label(self.panel_demo, text="Escenario", bg=CARD, fg=MUTED,
                 font=(self.fam, 9)).pack(side=tk.LEFT, padx=(0, 6))
        self.combo_escenario = ttk.Combobox(self.panel_demo, textvariable=self.var_escenario,
                                            values=list(ESCENARIOS), state="readonly", width=30,
                                            font=(self.fam, 10))
        self.combo_escenario.pack(side=tk.LEFT)
        self.combo_escenario.bind("<<ComboboxSelected>>", lambda e: self._aplicar_escenario())

    # ---- fuente, conexión ------------------------------------------------

    def _cargar_config(self):
        """Lee de la base los parámetros que el centro puede editar."""
        c = self.bd
        self.cfg = {
            "escalamiento_s": c.config("escalamiento_s", ESCALAMIENTO_S),
            "escalamiento_bateria_baja_s": c.config("escalamiento_bateria_baja_s", ESCALAMIENTO_BATERIA_BAJA_S),
            "escalamiento_bateria_critica_s": c.config("escalamiento_bateria_critica_s",
                                                       ESCALAMIENTO_BATERIA_CRITICA_S),
            "bateria_baja": c.config("bateria_baja", BATERIA_BAJA),
            "bateria_critica": c.config("bateria_critica", BATERIA_CRITICA),
            "bateria_rearme": c.config("bateria_rearme", BATERIA_REARME),
            "sin_senal_s": c.config("sin_senal_s", SIN_SENAL_S),
            "plazo_recuperacion_s": c.config("plazo_recuperacion_s", PLAZO_RECUPERACION_S),
            "reintento_s": c.config("reintento_s", REINTENTO_S),
            "centro": c.config("centro", "Centro de salud"),
            "mqtt_broker": str(c.config("mqtt_broker", "broker.hivemq.com")),
            "mqtt_puerto": c.config("mqtt_puerto", 1883),
            "mqtt_prefijo": str(c.config("mqtt_prefijo", "pucmm-pulsera-7f3a")),
            "mqtt_usuario": str(c.config("mqtt_usuario", "") or ""),
            "mqtt_clave": str(c.config("mqtt_clave", "") or ""),
        }

    def aplicar_configuracion(self):
        """La llaman las pantallas de configuración cuando el centro cambia un parámetro."""
        self._cargar_config()
        self.tarjetas["batt"].fijar_pie(f"Aviso a menos de {self.cfg['bateria_baja']:g} %")
        self.refrescar_umbrales()
        if self.var_fuente.get() == FUENTE_MQTT and self.hilo is None:
            self.var_param.set(self.cfg["mqtt_prefijo"])
        if self.hilo is not None and self.fuente_actual == FUENTE_MQTT:
            self.aviso("Detenga y vuelva a conectar para usar los datos nuevos del broker.")

    def refrescar_umbrales(self):
        """Arma los umbrales del paciente que se está monitoreando."""
        nuevos = {k: dict(v) for k, v in UMBRALES.items()}
        if self.paciente_id:
            desde_bd = self.bd.umbrales_de_paciente(self.paciente_id)
            for signo, datos in desde_bd.items():
                base = nuevos.get(signo, {})
                nombre, unidad, _ = SIGNOS[signo]
                base.setdefault("nombre", nombre)
                base.setdefault("corto", base.get("corto", nombre))
                base.setdefault("unidad", unidad)
                base["normal"] = datos["normal"]
                base["vigilancia"] = datos["vigilancia"]
                base["origen"] = datos["origen"]
                nuevos[signo] = base
        self.umbrales = nuevos
        if hasattr(self, "tarjetas"):
            for clave in ("hr", "spo2", "temp"):
                self.tarjetas[clave].fijar_pie(texto_rango(clave, self.umbrales))
            self._pintar_signos_manuales()
        if hasattr(self, "_bandas"):
            for clave, banda in self._bandas.items():
                lo, hi = self.umbrales[clave]["normal"]
                banda.remove()
                self._bandas[clave] = self.ax[clave].axhspan(lo, hi, color=OK, alpha=0.10, lw=0)
                etiqueta = f"normal ≥ {lo:g}" if clave == "spo2" else f"normal {lo:g}–{hi:g}"
                self.ax[clave].set_title(etiqueta, loc="right", fontsize=8, color=OK, pad=6)
            self._redibujar()

    # ---- paciente en monitoreo ------------------------------------------

    def _refrescar_combo_pacientes(self):
        self._opciones_paciente = {}
        etiquetas = ["Modo libre (sin paciente)"]
        for p in self.bd.listar_pacientes("", "Ingresado"):
            etiqueta = f"{nombre_completo(p)} · {ubicacion(p)} · {p['serie'] or 'sin pulsera'}"
            self._opciones_paciente[etiqueta] = p["id"]
            etiquetas.append(etiqueta)
        self.combo_paciente.configure(values=etiquetas)
        if self.paciente_id:
            for etiqueta, pid in self._opciones_paciente.items():
                if pid == self.paciente_id:
                    self.var_paciente.set(etiqueta)
                    break
        else:
            self.var_paciente.set(etiquetas[0])

    def _al_elegir_paciente(self, _evento=None):
        etiqueta = self.var_paciente.get()
        pid = self._opciones_paciente.get(etiqueta)
        if pid is None:
            self.monitorear_paciente(None)
        else:
            self.monitorear_paciente(pid)

    def monitorear_paciente(self, pid):
        """Deja la pantalla de monitoreo apuntando a este paciente y su pulsera."""
        self.paciente_id = pid
        self.bd.fijar_interno("ultimo_paciente", pid or "", "Paciente que se estaba monitoreando")
        self.paciente = self.bd.paciente(pid) if pid else None
        asignacion = self.bd.asignacion_de_paciente(pid) if pid else None
        self.dispositivo_esperado = asignacion["id_dispositivo"] if asignacion else None
        if isinstance(self.transporte, MQTTTransport):
            self.transporte.id_destino = self.dispositivo_esperado
        self._ajenos = {}
        self.refrescar_umbrales()
        self._refrescar_combo_pacientes()
        self._pintar_ficha()
        self._pintar_signos_manuales()
        self.pestanas.select(self.tab_mon)
        if pid and asignacion:
            self.aviso(f"Monitoreando a {nombre_completo(self.paciente)} con la pulsera "
                       f"{asignacion['serie']} ({asignacion['id_dispositivo']}).")

    def _retomar_ultimo_paciente(self):
        """Al abrir, vuelve al paciente que se estaba monitoreando, si sigue ingresado."""
        guardado = self.bd.config("ultimo_paciente", "")
        if not guardado:
            return
        try:
            pid = int(guardado)
        except (TypeError, ValueError):
            return
        p = self.bd.paciente(pid)
        if p and p["estado"] == "Ingresado" and self.bd.asignacion_de_paciente(pid):
            self.monitorear_paciente(pid)

    def refrescar_paciente_monitoreado(self):
        if self.paciente_id:
            self.paciente = self.bd.paciente(self.paciente_id)
        self._refrescar_combo_pacientes()
        self._pintar_ficha()

    def _pintar_ficha(self):
        if not self.paciente:
            self.lbl_ficha.configure(text="Modo libre: se muestran los datos de cualquier pulsera, con los "
                                          "umbrales de adulto en reposo.")
            self.lbl_pulsera.configure(text="Sin paciente", bg=GRIS_BG, fg=TEXTO)
            self.lbl_alergias.configure(text="")
            return
        p = self.paciente
        alergias = (p["alergias"] or "").strip()
        self.lbl_ficha.configure(
            text=f"{texto_edad(p['fecha_nac'])} · {p['sexo'].lower()} · perfil {p['perfil']} · "
                 f"{p['diagnostico'] or 'sin diagnóstico registrado'}", fg=MUTED)
        self.lbl_alergias.configure(
            text=f"alergias: {alergias}" if alergias and alergias.lower() != "ninguna conocida" else "")
        a = self.bd.asignacion_de_paciente(p["id"])
        if a:
            bat = "" if a["bateria"] is None else f" · {int(a['bateria'])} %"
            self.lbl_pulsera.configure(text=f"Pulsera {a['serie']} ({a['id_dispositivo']}){bat}",
                                       bg=OK_BG, fg=OK)
        else:
            self.lbl_pulsera.configure(text="Sin pulsera asignada", bg=WARN_BG, fg=WARN)

    def cambiar_pulsera(self):
        if not self.paciente_id:
            self.aviso("Elija primero un paciente para cambiarle la pulsera.")
            return
        d = DialogoAsignar(self, self.bd.paciente(self.paciente_id))
        self.wait_window(d)
        if d.resultado:
            self.pulsera_cambiada(self.paciente_id, d.resultado)

    def pulsera_cambiada(self, pid, info):
        """
        Llamada al asignar, cambiar o liberar la pulsera de un paciente. La serie de datos
        del paciente no se corta: se marca el cambio en la gráfica y se siguen recibiendo
        los paquetes del dispositivo nuevo.
        """
        if pid != self.paciente_id:
            self._refrescar_combo_pacientes()
            return
        if info is None:
            self.dispositivo_esperado = None
            self._pintar_ficha()
            self._refrescar_combo_pacientes()
            return
        self.dispositivo_esperado = info["dispositivo"]
        if isinstance(self.transporte, MQTTTransport):
            self.transporte.id_destino = info["dispositivo"]
        self._ajenos = {}
        self.sesion.seq.reiniciar_secuencia()
        self._huecos = {}
        if info.get("anterior"):
            self.sesion.marcar_cambio_pulsera(info["anterior"], info["serie"])
            self._nueva_alerta(
                "Pulsera cambiada",
                f"{info['anterior']} → {info['serie']} ({info['dispositivo']}). Motivo: "
                f"{info['motivo'].lower()}. El monitoreo del paciente continúa en la misma gráfica.",
                "MEDIA", "SISTEMA", "CAMBIO_PULSERA", None, requiere_atencion=False, leve=True)
        self._pintar_ficha()
        self._refrescar_combo_pacientes()
        self._redibujar()

    def paciente_dado_de_alta(self, pid):
        if pid == self.paciente_id:
            self.monitorear_paciente(None)
        else:
            self._refrescar_combo_pacientes()

    # ---- signos tomados a mano -------------------------------------------

    def registrar_signos_manuales(self):
        if not self.paciente_id:
            self.aviso("Los signos a mano se registran sobre un paciente. Elija uno primero.")
            return
        d = DialogoMedicion(self, self.bd.paciente(self.paciente_id))
        self.wait_window(d)
        if d.resultado:
            self.signos_manuales_registrados(self.paciente_id, d.resultado)

    def signos_manuales_registrados(self, pid, valores):
        """Evalúa los signos que acaba de registrar enfermería contra el rango del paciente."""
        if pid != self.paciente_id:
            return
        self._pintar_signos_manuales()
        if self.conexion == "conectado":
            self._actualizar_estado_global()
        # La presión se juzga completa, no sístole y diástole por separado.
        if "pas" in valores or "pad" in valores:
            ult = self.bd.ultimas_mediciones(pid)
            pas = valores.get("pas") or (ult["pas"]["valor"] if "pas" in ult else None)
            pad = valores.get("pad") or (ult["pad"]["valor"] if "pad" in ult else None)
            if pas is not None and pad is not None:
                n_s, d_s = clasificar("pas", pas, self.umbrales)
                n_d, d_d = clasificar("pad", pad, self.umbrales)
                niveles = [n for n in (n_s, n_d) if n is not None]
                nivel = max(niveles) if niveles else None
                if nivel is not None and nivel > NIVEL_NORMAL:
                    partes = []
                    if n_s and n_s > NIVEL_NORMAL:
                        partes.append(f"sistólica {d_s}")
                    if n_d and n_d > NIVEL_NORMAL:
                        partes.append(f"diastólica {d_d}")
                    u_s, u_d = self.umbrales["pas"], self.umbrales["pad"]
                    self._nueva_alerta(
                        "Presión arterial fuera de rango",
                        f"{pas:g}/{pad:g} mmHg ({' y '.join(partes)}). Rango normal del paciente: "
                        f"{u_s['normal'][0]:g} a {u_s['normal'][1]:g} sobre {u_d['normal'][0]:g} a "
                        f"{u_d['normal'][1]:g} mmHg. Valor tomado a mano.",
                        "ALTA" if nivel == NIVEL_ALERTA else "MEDIA", "MANUAL", "PA", None,
                        leve=(nivel == NIVEL_VIGILANCIA))
        if "fr" in valores:
            nivel, direccion = clasificar("fr", valores["fr"], self.umbrales)
            if nivel is not None and nivel > NIVEL_NORMAL:
                u = self.umbrales["fr"]
                self._nueva_alerta(
                    "Frecuencia respiratoria fuera de rango",
                    f"{valores['fr']:g} rpm ({direccion}). Rango normal del paciente: "
                    f"{u['normal'][0]:g} a {u['normal'][1]:g} rpm. Valor tomado a mano.",
                    "ALTA" if nivel == NIVEL_ALERTA else "MEDIA", "MANUAL", "FR", None,
                    leve=(nivel == NIVEL_VIGILANCIA))

    def _actualizar_estado_global(self, dispositivo=None):
        """El estado del paciente junta lo que mide la pulsera y lo que tomó enfermería."""
        niveles = dict(self._niveles_vitales)
        niveles.update(self._nivel_manual)
        causas = list(self._causas_vitales) + list(self._causas_manual)
        self._pintar_banner(estado_global(niveles.values()), causas, dispositivo)

    def _pintar_signos_manuales(self):
        """Pinta la presión arterial y la frecuencia respiratoria en sus propias tarjetas."""
        if not hasattr(self, "tarjetas") or "pa" not in self.tarjetas:
            return
        self._nivel_manual = {}
        self._causas_manual = []
        if not self.paciente_id:
            for clave in ("pa", "fr"):
                self.tarjetas[clave].actualizar(None, None, "Sin datos")
                self.tarjetas[clave].fijar_pie("la toma enfermería")
            return
        ult = self.bd.ultimas_mediciones(self.paciente_id)
        pas, pad = ult.get("pas"), ult.get("pad")
        if pas and pad:
            niveles = [n for n in (clasificar("pas", pas["valor"], self.umbrales)[0],
                                   clasificar("pad", pad["valor"], self.umbrales)[0]) if n is not None]
            nivel = max(niveles) if niveles else None
            self.tarjetas["pa"].actualizar(f"{pas['valor']:g}/{pad['valor']:g}", nivel,
                                           NOMBRE_NIVEL.get(nivel, "Registrada"))
            self.tarjetas["pa"].fijar_pie(f"{pas['hora'][11:16]} · {pas['tomado_por'] or 'sin registrar'}")
            if nivel is not None and nivel > NIVEL_NORMAL:
                self._nivel_manual["pa"] = nivel
                self._causas_manual.append(f"Presión arterial {pas['valor']:g}/{pad['valor']:g} mmHg")
        else:
            self.tarjetas["pa"].actualizar(None, None, "Sin tomar")
            self.tarjetas["pa"].fijar_pie("pulse aquí para registrarla")
        fr = ult.get("fr")
        if fr:
            nivel = clasificar("fr", fr["valor"], self.umbrales)[0]
            self.tarjetas["fr"].actualizar(f"{fr['valor']:g}", nivel, NOMBRE_NIVEL.get(nivel, "Registrada"))
            self.tarjetas["fr"].fijar_pie(f"{fr['hora'][11:16]} · {fr['tomado_por'] or 'sin registrar'}")
            if nivel is not None and nivel > NIVEL_NORMAL:
                self._nivel_manual["fr"] = nivel
                self._causas_manual.append(f"Frecuencia respiratoria {fr['valor']:g} rpm")
        else:
            self.tarjetas["fr"].actualizar(None, None, "Sin tomar")
            self.tarjetas["fr"].fijar_pie("pulse aquí para registrarla")

    # ---- avisos y evidencias ---------------------------------------------

    def aviso(self, texto):
        """Mensaje breve en el pie de la ventana."""
        if hasattr(self, "lbl_csv"):
            self.lbl_aviso.configure(text=texto)
            self.after(9000, lambda: self.lbl_aviso.configure(text=""))

    def capturar_evidencia(self, etiqueta=""):
        """Guarda una imagen de la ventana para los anexos del informe."""
        import datetime as _dt
        carpeta = os.path.join(self.carpeta_datos, "evidencias")
        os.makedirs(carpeta, exist_ok=True)
        marca = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        sufijo = re.sub(r"[^A-Za-z0-9_-]+", "_", etiqueta).strip("_")
        ruta = os.path.join(carpeta, f"evidencia_{marca}{'_' + sufijo if sufijo else ''}.png")
        try:
            from PIL import ImageGrab
            self.update_idletasks()
            x, y = self.winfo_rootx(), self.winfo_rooty()
            img = ImageGrab.grab(bbox=(x, y, x + self.winfo_width(), y + self.winfo_height()))
            img.save(ruta)
        except Exception as exc:  # sin Pillow o sin permiso de captura
            self.aviso(f"No se pudo capturar la pantalla ({exc}). Use la tecla Impr Pant.")
            return None
        self.bd.auditar(self.usuario, "Captura de evidencia", "sistema", None, os.path.basename(ruta))
        self.aviso(f"Evidencia guardada en {ruta}")
        return ruta

    # ---- escenarios críticos ---------------------------------------------

    def _revisar_cortes_previos(self):
        """
        Al arrancar: si quedó una sesión abierta, el programa se cerró de golpe
        (corte de energía, apagón del equipo o cierre forzado). Se avisa, se dice
        hasta qué hora hay datos y la alerta queda pendiente de reconocer.
        """
        abiertas = self.bd.sesiones_sin_cerrar()
        if not abiertas:
            return
        for s in abiertas:
            quien = f"{s['nombre']} {s['apellidos']}" if s["nombre"] else "sin paciente asociado"
            detalle = (f"La sesión del {s['inicio']} ({quien}, pulsera {s['serie'] or '--'}) no se cerró "
                       f"bien. Hay datos guardados hasta las {(s['latido'] or s['inicio'])[11:19]}. "
                       "Revise ese intervalo antes de continuar.")
            eid = self.bd.registrar_evento_sistema("CORTE_DETECTADO", detalle, s["id"])
            self.bd.cerrar_sesion(s["id"], limpio=False)
            self._nueva_alerta("Corte detectado en la sesión anterior", detalle, "ALTA", "SISTEMA",
                               "CORTE", None)
            self._corte_pendiente = eid

    def simular_corte_red(self, segundos=12):
        """Deja de llegar telemetría durante unos segundos, como en una caída de red."""
        if self.transporte is None or not hasattr(self.transporte, "cortar"):
            self.aviso("La simulación de caída de red funciona con la fuente Simulador.")
            return
        self.transporte.cortar(segundos)
        self.bd.registrar_evento_sistema("ENLACE_CAIDO", f"Caída de red simulada de {segundos} s",
                                         self.sesion_bd)
        self._nueva_alerta("Caída de red simulada",
                           f"Se interrumpió el enlace por {segundos} s. Los paquetes de ese intervalo "
                           "quedan marcados como faltantes y se piden de vuelta al restablecerse.",
                           "MEDIA", "SISTEMA", "SIMULACION", None, requiere_atencion=False, leve=True)
        self.aviso(f"Enlace interrumpido por {segundos} s. Observe el indicador de integridad.")

    def simular_corte_energia(self):
        """
        Reproduce lo que ocurre cuando se va la luz: la sesión queda abierta, los datos
        escritos hasta ese instante permanecen en disco y al volver el sistema lo avisa.
        """
        if self.sesion_bd is None:
            self.aviso("Conecte una sesión antes de simular el corte de energía.")
            return
        sid = self.sesion_bd
        self.bd.latido_sesion(sid)
        corte = self.bd.q1("SELECT latido FROM sesiones WHERE id=?", (sid,))["latido"]
        # La sesión se deja ABIERTA a propósito: eso es lo que ocurre cuando se va la luz
        # y el programa no alcanza a cerrarla. Al volver a abrir, el sistema lo detecta.
        self.sesion_bd = None
        self._detener()
        detalle = (f"Corte de energía simulado. La sesión quedó abierta con datos hasta las "
                   f"{corte[11:19]}. Los archivos de la sesión conservan todo lo recibido hasta ese "
                   "instante porque cada fila se escribe en disco al llegar.")
        eid = self.bd.registrar_evento_sistema("CORTE_ENERGIA", detalle, sid)
        self._corte_pendiente = eid
        self._nueva_alerta("Corte de energía", detalle, "ALTA", "SISTEMA", "CORTE", None)
        self.aviso("Corte simulado. Pulse Conectar para reanudar el monitoreo del paciente.")

    def _al_cambiar_fuente(self):
        fuente = self.var_fuente.get()
        self.entrada_param.pack_forget()
        self.lbl_param.pack_forget()
        if fuente == FUENTE_SIM:
            self.var_param.set("")
        else:
            if fuente == FUENTE_BLE:
                self.lbl_param.config(text="Nombre o MAC")
                self.var_param.set("W01")
                self.entrada_param.config(width=14)
            elif fuente == FUENTE_MQTT:
                self.lbl_param.config(text="Prefijo")
                self.var_param.set(self.cfg["mqtt_prefijo"])
                self.entrada_param.config(width=20)
            else:
                self.lbl_param.config(text="Puerto UDP")
                self.var_param.set("5005")
                self.entrada_param.config(width=14)
            self.entrada_param.config(state="normal")
            self.entrada_param.pack(side=tk.RIGHT, ipady=3, before=self.combo_fuente)
            self.lbl_param.pack(side=tk.RIGHT, padx=(14, 6), before=self.combo_fuente)
        self._estado_demo()

    def _estado_demo(self):
        activo = self.fuente_actual == FUENTE_SIM and self.hilo is not None
        estado = "normal" if activo else "disabled"
        for w in (self.btn_caida, self.btn_sos):
            w.config(state=estado)
        self.combo_escenario.config(state="readonly" if activo else "disabled")

    def _crear_transporte(self, fuente, param):
        if fuente == FUENTE_SIM:
            return SimuladorTransport(intervalo_s=self.intervalo_sim)
        if fuente == FUENTE_BLE:
            param = param.strip() or "W01"
            if MAC_RE.match(param):
                return BLETransport(direccion_mac=param)
            return BLETransport(nombre_dispositivo=param)
        if fuente == FUENTE_MQTT:
            prefijo = param.strip() or self.cfg["mqtt_prefijo"]
            if not prefijo:
                raise ValueError("Escriba el prefijo de los tópicos del hub (por ejemplo pucmm-pulsera-7f3a).")
            t = MQTTTransport(self.cfg["mqtt_broker"], self.cfg["mqtt_puerto"], prefijo, self.cfg["mqtt_usuario"],
                              self.cfg["mqtt_clave"], al_hub=lambda info: self.cola.put(("hub", info)))
            t.id_destino = self.dispositivo_esperado
            return t
        try:
            puerto = int(param.strip())
            if not 1 <= puerto <= 65535:
                raise ValueError
        except ValueError:
            raise ValueError("El puerto UDP debe ser un número entre 1 y 65535.") from None
        return UDPTransport(puerto=puerto)

    def _alternar_conexion(self):
        if self.hilo is None:
            self._conectar()
        else:
            self._detener()

    def _conectar(self):
        if self.hilo is not None:
            return
        fuente = self.var_fuente.get()
        try:
            transporte = self._crear_transporte(fuente, self.var_param.get())
        except ValueError as exc:
            self._cambiar_conexion("error")
            self._nueva_alerta("Configuración no válida", str(exc), None, "SISTEMA", "CONFIG", None,
                               requiere_atencion=False)
            return
        # Nueva sesión: gráficas, contadores y alertas empiezan de cero.
        self._cerrar_registro()
        self.sesion = Sesion()
        self.registrador = RegistradorCSV(self.carpeta_datos)
        self.lbl_csv.config(text=f"Guardando en: {self.registrador.ruta_telemetria}")
        asignacion = self.bd.asignacion_de_paciente(self.paciente_id) if self.paciente_id else None
        self.sesion_bd = self.bd.abrir_sesion(self.paciente_id,
                                              asignacion["pulsera_id"] if asignacion else None,
                                              fuente, self.registrador.marca)
        # Las alertas ya cerradas se quedan con la sesión anterior. Las que siguen sin atender
        # no desaparecen porque se reinicie el monitoreo: se arrastran y se vuelven a anotar en
        # la bitácora de la sesión nueva, porque un pendiente clínico no se pierde solo.
        arrastradas = 0
        for t in list(self.tarjetas_alerta):
            if t.requiere_atencion and not t.atendida:
                arrastradas += 1
                self.registrador.registrar_alerta("GENERADA", t.origen, t.tipo,
                                                  f"{t.detalle} (pendiente de la sesión anterior)", t.seq)
                continue
            t.destroy()
            self.tarjetas_alerta.remove(t)
        if not arrastradas:
            self._critico_activo = {k: False for k in self._critico_activo}
        self._huecos = {}
        self._ultimo_estado = None
        self._t_ultimo_paquete = None
        self.fuente_actual = fuente
        self.transporte = transporte
        self.cola = queue.Queue()
        self.hilo = HiloTransporte(transporte, self.cola)
        self.hilo.start()
        self.combo_fuente.config(state="disabled")
        self.entrada_param.config(state="disabled")
        self.btn_conectar.config(text="Detener", bg=ALERTA, activebackground="#9E1F1F")
        self._cambiar_conexion("conectando")
        self._limpiar_estado_visual()
        self._hub = {}
        self._pintar_hub()
        if fuente == FUENTE_MQTT:
            self.lbl_causas.config(text=f"Conectando con el broker {self.cfg['mqtt_broker']} · esperando los datos del hub")
        if fuente == FUENTE_UDP:
            self.lbl_causas.config(
                text=f"Escuchando UDP en el puerto {self.var_param.get().strip()} · IP de esta PC: {ip_local()} "
                     f"· esperando paquetes del emisor")
        self._estado_demo()
        self._refrescar_vacio()
        self._actualizar_titulo_alertas()
        if arrastradas:
            self.aviso(f"{arrastradas} alerta(s) sin atender siguen abiertas de la sesión anterior.")
        self._redibujar()

    def _detener(self):
        if self.sesion_bd is not None:
            self.bd.cerrar_sesion(self.sesion_bd, limpio=True)
            self.sesion_bd = None
        if self.hilo is not None:
            self.hilo.detener()
        self.hilo = None
        self.cola = queue.Queue()  # lo que quedó pendiente de la conexión anterior se descarta
        self.sesion.cerrar()
        self._cerrar_registro(conservar_ruta=True)
        self.combo_fuente.config(state="readonly")
        self._al_cambiar_fuente()
        self.btn_conectar.config(text="Conectar", bg="#2E9E6B", activebackground="#237F54")
        self._cambiar_conexion("desconectado")
        self._banner_obsoleto("SESIÓN DETENIDA")
        self._estado_demo()
        self._actualizar_enlace()
        self._pintar_hub()

    def _cerrar_registro(self, conservar_ruta=False):
        if self.registrador is not None:
            self.registrador.cerrar()
            if conservar_ruta:
                self.lbl_csv.config(text=f"Datos guardados en: {self.registrador.ruta_telemetria}")
        elif not conservar_ruta:
            self.lbl_csv.config(text="Datos: sin sesión activa")

    def _cambiar_conexion(self, estado):
        self.conexion = estado
        self._pintar_conexion()

    def _pintar_conexion(self):
        fondo, texto, etiqueta = CONEXION[self.conexion]
        self.chip_conexion.config(bg=fondo, fg=texto, text=etiqueta)

    # ---- hub (solo MQTT) ---------------------------------------------------

    def _al_hub(self, info):
        if self.fuente_actual != FUENTE_MQTT or self.hilo is None:
            return
        h = self._hub
        if info.get("tipo") == "broker":
            h["broker"] = info.get("texto")
        elif info.get("tipo") == "estado":
            antes = h.get("estado")
            h["estado"] = "en línea" if info.get("texto") == "online" else "apagado"
            h["id"] = info.get("hub")
            if antes and antes != h["estado"]:
                self.aviso(f"El hub {info.get('hub')} está {h['estado']}.")
        elif info.get("tipo") == "info":
            h["info"] = info.get("datos") or {}
            h["id"] = info.get("hub")
        self._pintar_hub()

    def _pintar_hub(self):
        if self.fuente_actual != FUENTE_MQTT or self.hilo is None:
            self.lbl_hub.pack_forget()
            return
        h = self._hub
        partes = [f"Broker: {h.get('broker') or 'conectando'}",
                  f"Hub{' ' + h['id'] if h.get('id') else ''}: {h.get('estado') or 'sin noticias'}"]
        datos = h.get("info")
        if datos:
            partes.append(f"pulsera conectada · {datos.get('pendientes', 0)} en cola"
                          if datos.get("pulsera") == "conectada" else "buscando la pulsera")
        mal = h.get("estado") == "apagado" or h.get("broker") == "sin conexión"
        self.lbl_hub.config(text=" · ".join(partes), fg=ALERTA if mal else OK)
        self.lbl_hub.pack(fill=tk.X, padx=14, pady=(0, 8))

    # ---- bucle de actualización ------------------------------------------

    def _procesar_cola(self):
        try:
            while True:
                tipo, paquete = self.cola.get_nowait()
                if tipo == "telemetry":
                    self._manejar_telemetria(paquete)
                elif tipo == "event":
                    self._manejar_evento(paquete)
                elif tipo == "error":
                    self._manejar_error(paquete)
                elif tipo == "hub":
                    self._al_hub(paquete)
                elif tipo == "fin" and self.hilo is not None:
                    self._manejar_fin()
        except queue.Empty:
            pass
        self._job_cola = self.after(100, self._procesar_cola)

    def _tick(self):
        """Una vez por segundo: relojes, enlace y escalamiento de alertas."""
        if self.sesion_bd is not None and time.monotonic() - self._t_latido > 10:
            self._t_latido = time.monotonic()
            self.bd.latido_sesion(self.sesion_bd)
        self._vigilar_huecos()
        self._actualizar_enlace()
        for t in list(self.tarjetas_alerta):
            t.refrescar()
        if self.hilo is not None and self._t_ultimo_paquete is not None:
            sin_paquetes = time.monotonic() - self._t_ultimo_paquete
            if sin_paquetes > self.cfg["sin_senal_s"] and self.conexion == "conectado":
                self._cambiar_conexion("sin_senal")
                self._banner_obsoleto("SIN SEÑAL", sin_senal=True)
                self._nueva_alerta("Sin señal de la pulsera",
                                   f"No llegan paquetes hace más de {self.cfg['sin_senal_s']:.0f} s.", None,
                                   "SISTEMA", "SIN_SENAL", None, requiere_atencion=False)
        self._job_tick = self.after(500, self._tick)

    def _manejar_telemetria(self, p):
        if not self._es_del_paciente(p):
            return
        if p.get("id"):
            self._t_latido = self._t_latido or 0
            self.bd.registrar_bateria(str(p["id"]), a_numero(p.get("batt")))
        if self.registrador is not None:
            self.registrador.registrar_telemetria(p)
        self.sesion.agregar_telemetria(p)
        self._revisar_integridad()
        self._t_ultimo_paquete = time.monotonic()
        if self.conexion in ("conectando", "sin_senal"):
            self._cambiar_conexion("conectado")

        niveles = {}
        causas = []
        for variable in ("hr", "spo2", "temp"):
            valor = a_numero(p.get(variable))
            nivel, direccion = clasificar(variable, valor, self.umbrales)
            niveles[variable] = nivel
            u = self.umbrales[variable]
            if valor is None:
                self.tarjetas[variable].actualizar(None, None, "Sin lectura")
                continue
            texto = f"{valor:.1f}" if variable == "temp" else f"{valor:.0f}"
            chip = NOMBRE_NIVEL[nivel] if nivel == NIVEL_NORMAL else f"{NOMBRE_NIVEL[nivel]} · {direccion}"
            self.tarjetas[variable].actualizar(texto, nivel, chip)
            if nivel and nivel > NIVEL_NORMAL:
                causas.append(f"{u['corto']} {texto} {u['unidad']} ({direccion})")
            self._evaluar_critico(variable, nivel, direccion, texto, p.get("seq"))

        bateria = a_numero(p.get("batt"))
        nb = nivel_bateria(bateria, self.cfg["bateria_baja"], self.cfg["bateria_critica"])
        if bateria is None:
            self.tarjetas["batt"].actualizar(None, None, "Sin lectura")
        else:
            chip = {NIVEL_NORMAL: "Carga OK", NIVEL_VIGILANCIA: "Baja", NIVEL_ALERTA: "Crítica"}[nb]
            self.tarjetas["batt"].actualizar(f"{bateria:.0f}", nb, chip)
            autonomia = self.sesion.autonomia_min()
            if autonomia is not None:
                pie = f"Autonomía estimada: {formato_autonomia(autonomia)}"
            elif self.sesion.paquetes_telemetria < 8:
                pie = "Autonomía: calculando…"
            else:
                pie = "Sin descarga medible"
            self.tarjetas["batt"].fijar_pie(pie)
            self._evaluar_bateria(bateria, autonomia, p.get("seq"))

        self._niveles_vitales = dict(niveles)
        self._causas_vitales = list(causas)
        self._actualizar_estado_global(p.get("id"))
        self._redibujar()
        self._actualizar_enlace()

    def _es_del_paciente(self, p):
        """
        Con un paciente seleccionado, solo se aceptan los paquetes de la pulsera que
        tiene asignada. Si llega otra, se avisa en lugar de mezclar datos de dos personas.
        """
        if not self.dispositivo_esperado:
            return True
        ident = str(p.get("id") or "").strip()
        if ident == self.dispositivo_esperado:
            return True
        if ident and ident not in self._ajenos:
            self._ajenos[ident] = True
            duena = self.bd.paciente_por_dispositivo(ident)
            de_quien = (f" Esa pulsera está asignada a {duena['nombre']} {duena['apellidos']}."
                        if duena else " Esa pulsera no está asignada a ningún paciente.")
            self._nueva_alerta(
                "Llegan datos de otra pulsera",
                f"Se reciben paquetes del dispositivo {ident}, pero este paciente tiene asignada la "
                f"{self.dispositivo_esperado}.{de_quien} Los datos ajenos se descartan para no mezclar "
                "información de dos pacientes.", "ALTA", "SISTEMA", "DISPOSITIVO_AJENO", None)
        return False

    def _evaluar_critico(self, variable, nivel, direccion, texto, seq):
        """Genera una alerta cuando un signo cruza a nivel Alerta (una vez por episodio)."""
        if nivel == NIVEL_ALERTA and not self._critico_activo[variable]:
            self._critico_activo[variable] = True
            u = self.umbrales[variable]
            self._nueva_alerta(titulo_alerta(variable, direccion),
                               f"{u['nombre']}: {texto} {u['unidad']} fuera del límite seguro.",
                               "ALTA", "UMBRAL", variable.upper(), seq)
        elif nivel is not None and nivel < NIVEL_ALERTA:
            self._critico_activo[variable] = False

    def _evaluar_bateria(self, bateria, autonomia, seq):
        """
        Dos avisos para cambiar la pulsera ANTES de que se apague:
          - batería baja (30 % o menos): aviso preventivo, hay que programar el cambio;
          - batería crítica (15 % o menos): hay que cambiarla ya.
        Se rearman cuando la batería vuelve a subir de 40 % (pulsera cambiada o recargada).
        """
        act = self._critico_activo
        if bateria >= self.cfg["bateria_rearme"]:
            act["batt_baja"] = act["batt_critica"] = False
            return
        resto = f"Autonomía estimada {formato_autonomia(autonomia)}. " if autonomia is not None else ""
        if bateria <= self.cfg["bateria_critica"] and not act["batt_critica"]:
            act["batt_critica"] = act["batt_baja"] = True
            for t in list(self.tarjetas_alerta):  # el aviso preventivo queda superado por el crítico
                if t.tipo == "BATERIA_BAJA" and not t.atendida:
                    t.cerrar({"resolucion": "Superada por alerta crítica", "atendido_por": "SISTEMA",
                              "accion_realizada": "Cierre automático: la batería llegó al nivel crítico.",
                              "indicacion_medica": False, "medico": ""})
            self._nueva_alerta("Batería crítica: cambiar la pulsera ya",
                               f"Queda {bateria:.0f} %. {resto}Reemplazarla antes de que se apague.",
                               "ALTA", "UMBRAL", "BATERIA_CRITICA", seq,
                               escalamiento_s=self.cfg["escalamiento_bateria_critica_s"])
        elif bateria <= self.cfg["bateria_baja"] and not act["batt_baja"]:
            act["batt_baja"] = True
            self._nueva_alerta("Batería baja: programar el cambio",
                               f"Queda {bateria:.0f} %. {resto}Preparar una pulsera cargada.",
                               "MEDIA", "UMBRAL", "BATERIA_BAJA", seq,
                               escalamiento_s=self.cfg["escalamiento_bateria_baja_s"], leve=True)

    def _manejar_evento(self, p):
        if self.registrador is not None:
            self.registrador.registrar_evento(p)
        ahora = time.time()
        self.sesion.agregar_evento(p, ahora)
        self._revisar_integridad()
        self._t_ultimo_paquete = time.monotonic()
        if self.conexion in ("conectando", "sin_senal"):
            self._cambiar_conexion("conectado")
        nombre = str(p.get("ev"))
        titulo = {"FALL": "Caída detectada", "SOS": "Botón SOS presionado"}.get(nombre, f"Evento {nombre}")
        detalle = f"Dispositivo {p.get('id', '--')} · seq {p.get('seq', '--')} · {datetime.now():%H:%M:%S}"
        self._nueva_alerta(titulo, detalle, etiqueta_severidad(p.get("sev")), "DISPOSITIVO", nombre, p.get("seq"))
        self._redibujar()
        self._actualizar_enlace()

    def _manejar_error(self, mensaje):
        self.hilo = None
        self.sesion.cerrar()
        self._cerrar_registro(conservar_ruta=True)
        self.combo_fuente.config(state="readonly")
        self._al_cambiar_fuente()
        self.btn_conectar.config(text="Conectar", bg="#2E9E6B", activebackground="#237F54")
        self._cambiar_conexion("error")
        self._banner_obsoleto("SIN CONEXIÓN", sin_senal=True)
        self._estado_demo()
        self._pintar_hub()
        self._nueva_alerta("Error de conexión", mensaje, None, "SISTEMA", "ERROR", None, requiere_atencion=False)

    def _manejar_fin(self):
        """El transporte terminó solo (por ejemplo, la pulsera se desconectó)."""
        self.hilo = None
        self.sesion.cerrar()
        self._cerrar_registro(conservar_ruta=True)
        self.combo_fuente.config(state="readonly")
        self._al_cambiar_fuente()
        self.btn_conectar.config(text="Conectar", bg="#2E9E6B", activebackground="#237F54")
        self._cambiar_conexion("desconectado")
        self._banner_obsoleto("CONEXIÓN FINALIZADA", sin_senal=True)
        self._estado_demo()
        self._pintar_hub()
        self._nueva_alerta("Conexión finalizada", "La fuente de datos se desconectó.", None, "SISTEMA",
                           "FIN", None, requiere_atencion=False)

    # ---- alertas ----------------------------------------------------------

    def _nueva_alerta(self, titulo, detalle, gravedad, origen, tipo, seq, requiere_atencion=True,
                      escalamiento_s=None, leve=False):
        escalamiento_s = escalamiento_s or self.cfg["escalamiento_s"]
        self.lbl_sin_alertas.pack_forget()
        tarjeta = TarjetaAlerta(self.lista_alertas, self, titulo, detalle, gravedad, origen, tipo, seq,
                                requiere_atencion, escalamiento_s, leve)
        if self.tarjetas_alerta:
            tarjeta.pack(side=tk.TOP, fill=tk.X, pady=(0, 6), before=self.tarjetas_alerta[0])
        else:
            tarjeta.pack(side=tk.TOP, fill=tk.X, pady=(0, 6))
        self.tarjetas_alerta.insert(0, tarjeta)
        self.after(60, lambda: self.canvas_alertas.yview_moveto(0))
        self.update_idletasks()
        self.canvas_alertas.yview_moveto(0)
        # Si hay demasiadas, se retiran primero las ya atendidas o las avisos del sistema;
        # una alerta pendiente nunca se oculta sola.
        while len(self.tarjetas_alerta) > MAX_TARJETAS_ALERTA:
            candidata = next((t for t in reversed(self.tarjetas_alerta)
                              if t.atendida or not t.requiere_atencion), None)
            if candidata is None:
                break
            self.tarjetas_alerta.remove(candidata)
            candidata.destroy()
        if requiere_atencion:
            self.sesion.alerta_generada()
            self._sonar()
        if self.registrador is not None:
            self.registrador.registrar_alerta("GENERADA", origen, tipo, detalle, seq)

    def alerta_cerrada(self, tarjeta, respuesta_s, cierre):
        self.sesion.alerta_atendida(respuesta_s, cierre.get("resolucion"))
        if self.registrador is not None:
            self.registrador.registrar_alerta("CERRADA", tarjeta.origen, tarjeta.tipo, tarjeta.detalle,
                                              tarjeta.seq, respuesta_s, cierre)
        self._actualizar_titulo_alertas()
        # Con el hub, al atender una caída o un SOS se avisa a la pulsera: "AYUDA EN CAMINO" en su pantalla.
        if tarjeta.tipo in ("FALL", "SOS") and self.hilo is not None and isinstance(self.transporte, MQTTTransport):
            ident = self.dispositivo_esperado or self.transporte.ultimo_id
            ok = self.transporte.enviar_comando(ident, {"cmd": "ack", "ev": tarjeta.tipo, "seq": tarjeta.seq})
            self.aviso(f"Aviso enviado a la pulsera {ident}: ayuda en camino." if ok
                       else "No se pudo avisar a la pulsera (sin conexión con el broker).")

    def alerta_escalada(self, tarjeta):
        if self.registrador is not None:
            self.registrador.registrar_alerta("ESCALADA", tarjeta.origen, tarjeta.tipo, tarjeta.detalle,
                                              tarjeta.seq, tarjeta.segundos())
        self._sonar()

    def quitar_alerta(self, tarjeta):
        if tarjeta in self.tarjetas_alerta:
            self.tarjetas_alerta.remove(tarjeta)
        tarjeta.destroy()
        if not self.tarjetas_alerta:
            self.lbl_sin_alertas.pack(pady=18, before=self.canvas_alertas.master)

    def _sonar(self):
        if not self.var_sonido.get():
            return

        def pitar():
            try:
                if sys.platform.startswith("win"):
                    import winsound

                    for _ in range(2):
                        winsound.Beep(1000, 160)
                        time.sleep(0.05)
                else:
                    self.bell()
            except Exception:  # noqa: BLE001 - el sonido nunca debe tumbar la app
                pass

        if sys.platform.startswith("win"):
            threading.Thread(target=pitar, daemon=True).start()
        else:
            pitar()

    # ---- demostración -----------------------------------------------------

    def _demo_evento(self, tipo):
        if self.fuente_actual == FUENTE_SIM and self.hilo is not None and hasattr(self.transporte, "disparar_evento"):
            self.transporte.disparar_evento(tipo)

    def _aplicar_escenario(self):
        if self.fuente_actual == FUENTE_SIM and self.transporte is not None and hasattr(self.transporte, "fijar_escenario"):
            self.transporte.fijar_escenario(self.var_escenario.get())

    # ---- vista --------------------------------------------------------------

    def _pintar_banner(self, nivel, causas, dispositivo):
        fuerte, suave = COLOR_NIVEL.get(nivel, COLOR_NIVEL[None])
        texto = ESTADO_PACIENTE.get(nivel, "SIN DATOS")
        if nivel == NIVEL_NORMAL:
            detalle = "Todos los signos vitales dentro del rango normal"
        elif causas:
            detalle = "  ·  ".join(causas)
        else:
            detalle = "Esperando lecturas válidas"
        for w in (self.banner, self.lbl_estado, self.lbl_causas, self.lbl_integridad):
            w.config(bg=suave)
        self.banner_barra.config(bg=fuerte)
        self.lbl_estado.config(text=f"PACIENTE {texto}" if nivel is not None else texto, fg=fuerte)
        self.lbl_causas.config(text=detalle, fg=TEXTO if nivel else MUTED)
        self._ultimo_estado = nivel

    def _banner_obsoleto(self, titulo, sin_senal=False):
        """El estado del paciente deja de ser en vivo: se muestra como último dato conocido."""
        fuerte, suave = (WARN, WARN_BG) if sin_senal else (GRIS, GRIS_BG)
        ultimo = ESTADO_PACIENTE.get(self._ultimo_estado)
        for w in (self.banner, self.lbl_estado, self.lbl_causas, self.lbl_integridad):
            w.config(bg=suave)
        self.banner_barra.config(bg=fuerte)
        self.lbl_estado.config(text=titulo, fg=fuerte)
        self.lbl_causas.config(
            text=f"Último estado recibido: {ultimo}" if ultimo else "Sin lecturas recibidas", fg=MUTED)

    def _limpiar_estado_visual(self):
        self._pintar_banner(None, [], None)
        self.lbl_causas.config(text="Conectando con la fuente de datos…")

    def _refrescar_vacio(self):
        """
        Deja en blanco lo que mide la pulsera, a la espera del primer paquete.
        Los signos tomados a mano no se borran: los registró el personal y siguen
        siendo el último valor conocido del paciente.
        """
        for clave in ("hr", "spo2", "temp", "batt"):
            self.tarjetas[clave].actualizar(None, None, "Sin datos")
        self._pintar_signos_manuales()
        if not self.tarjetas_alerta:
            self.lbl_sin_alertas.pack(pady=18, before=self.canvas_alertas.master)

    def _actualizar_titulo_alertas(self):
        pendientes = sum(1 for t in self.tarjetas_alerta if t.requiere_atencion and not t.atendida)
        if pendientes:
            self.lbl_titulo_alertas.config(text=f"Alertas · {pendientes} sin atender", fg=ALERTA)
        else:
            self.lbl_titulo_alertas.config(text="Alertas", fg=TEXTO)

    def _actualizar_enlace(self):
        self._actualizar_titulo_alertas()
        s = self.sesion
        sq = s.seq
        self.lbl_enlace["paquetes"].config(text=str(sq.recibidos))
        self.lbl_enlace["recuperados"].config(text=str(sq.recuperados), fg=TEXTO if not sq.recuperados else OK)
        self.lbl_enlace["perdidos"].config(text=str(len(sq.irrecuperables)),
                                           fg=TEXTO if not sq.irrecuperables else ALERTA)
        self.lbl_enlace["espera"].config(text=str(len(sq.faltantes)), fg=TEXTO if not sq.faltantes else WARN)
        if self._t_ultimo_paquete is None:
            ultimo = "--"
        else:
            ultimo = f"hace {time.monotonic() - self._t_ultimo_paquete:.0f} s"
        self.lbl_enlace["ultimo"].config(text=ultimo)
        st = s.estado_sensor
        self.lbl_enlace["sensor"].config(text=str(st) if st else "--", fg=(TEXTO if st in (None, "OK") else ALERTA))
        # Integridad: la pregunta clave es "¿tengo todos los datos?"
        if sq.recibidos == 0:
            self.lbl_integridad.config(text="")
        elif sq.irrecuperables:
            self.lbl_integridad.config(text=f"Integridad de datos: PÉRDIDA REAL ({len(sq.irrecuperables)})", fg=ALERTA)
        elif sq.faltantes:
            self.lbl_integridad.config(text=f"Integridad de datos: recuperando {len(sq.faltantes)}", fg=WARN)
        else:
            extra = f" ({sq.recuperados} recuperados)" if sq.recuperados else ""
            self.lbl_integridad.config(text=f"Integridad de datos: completa{extra}", fg=OK)

    # ---- integridad de datos: detectar, pedir reenvío, recuperar o declarar la pérdida ----

    def _revisar_integridad(self):
        sq = self.sesion.seq
        ahora = time.monotonic()
        if sq.nuevos:
            for seq in sq.nuevos:
                self._huecos[seq] = {"detectado": ahora, "pedido": ahora}
                if self.registrador is not None:
                    self.registrador.registrar_hueco(seq, "DETECTADO", "faltante en la secuencia")
            self._pedir_reenvio(list(sq.nuevos))
        if sq.recuperado_seq is not None:
            info = self._huecos.pop(sq.recuperado_seq, None)
            demora = f"{ahora - info['detectado']:.1f} s después de detectarse" if info else "llegó tarde"
            if self.registrador is not None:
                self.registrador.registrar_hueco(sq.recuperado_seq, "RECUPERADO", demora)

    def _pedir_reenvio(self, seqs):
        fuente = self.transporte
        if fuente is not None and getattr(fuente, "soporta_reenvio", False) and fuente.solicitar_reenvio(seqs):
            return
        self._declarar_no_recuperados(seqs, "esta fuente no permite pedir reenvío")

    def _vigilar_huecos(self):
        if not self._huecos:
            return
        ahora = time.monotonic()
        vencidos, reintentar = [], []
        for seq, info in self._huecos.items():
            if ahora - info["detectado"] > self.cfg["plazo_recuperacion_s"]:
                vencidos.append(seq)
            elif ahora - info["pedido"] > self.cfg["reintento_s"]:
                reintentar.append(seq)
                info["pedido"] = ahora
        if reintentar and self.transporte is not None and getattr(self.transporte, "soporta_reenvio", False):
            self.transporte.solicitar_reenvio(reintentar)
        if vencidos:
            self._declarar_no_recuperados(vencidos, f"no llegaron en {self.cfg['plazo_recuperacion_s']:.0f} s")

    def _declarar_no_recuperados(self, seqs, motivo):
        self.sesion.seq.marcar_irrecuperables(seqs)
        for seq in seqs:
            self._huecos.pop(seq, None)
            if self.registrador is not None:
                self.registrador.registrar_hueco(seq, "NO_RECUPERADO", motivo)
        rango = f"{min(seqs)}" if len(seqs) == 1 else f"{min(seqs)} a {max(seqs)}"
        self._nueva_alerta("Datos no recuperados",
                           f"Faltan {len(seqs)} paquete(s), seq {rango}: {motivo}. Documentar el incidente.",
                           "ALTA", "SISTEMA", "DATOS", None)

    def _elegir_ventana(self, idx):
        self.var_ventana.set(idx)
        self._pintar_segmentos()
        self._redibujar()

    def _pintar_segmentos(self):
        for i, lbl in enumerate(self.segmentos):
            if i == self.var_ventana.get():
                lbl.config(bg=PRIMARIO, fg="white")
            else:
                lbl.config(bg=GRIS_BG, fg=TEXTO)

    def _redibujar(self):
        s = self.sesion
        ventana = VENTANAS[self.var_ventana.get()][1]
        off = self._offset_local
        if s.t:
            ahora = s.t[-1]
        else:
            ahora = time.time()
        if ventana:
            idx = bisect_left(s.t, ahora - ventana)
            inicio_visible = max(ahora - ventana, s.t[0]) if s.t else ahora - 60
            x_lo = min(inicio_visible, ahora - 30)
            x_hi = ahora + 2
        else:
            idx = 0
            x_lo = s.t[0] if s.t else ahora - 60
            x_hi = max(ahora + 2, x_lo + 30)
        xs = (np.asarray(s.t[idx:], dtype=float) + off) / 86400.0

        for variable, ax in self.ax.items():
            ys = np.array([np.nan if v is None else v for v in getattr(s, variable)[idx:]], dtype=float)
            self.lineas[variable].set_data(xs, ys)
            if len(xs):
                self.puntos[variable].set_data(xs[-1:], ys[-1:])
            else:
                self.puntos[variable].set_data([], [])
            lo, hi = self.umbrales[variable]["normal"]
            validos = ys[~np.isnan(ys)]
            minimo = min(lo, validos.min()) if validos.size else lo
            maximo = max(hi, validos.max()) if validos.size else hi
            margen = {"hr": 8, "spo2": 1.5, "temp": 0.4}[variable]
            techo = maximo + margen
            if variable == "spo2":
                techo = min(techo, 101.5)
            ax.set_ylim(minimo - margen, techo)
        self.ax["hr"].set_xlim((x_lo + off) / 86400.0, (x_hi + off) / 86400.0)

        for artista in self._artistas_marcas:
            artista.remove()
        self._artistas_marcas.clear()
        for t_ev, nombre in s.eventos:
            if t_ev < x_lo:
                continue
            xe = (t_ev + off) / 86400.0
            etiqueta = {"FALL": "CAÍDA", "SOS": "SOS"}.get(nombre, nombre)
            for variable, ax in self.ax.items():
                self._artistas_marcas.append(ax.axvline(xe, color=ALERTA, linewidth=1.3, linestyle="--", alpha=0.85))
            texto = self.ax["hr"].text(xe, 0.93, etiqueta + " ", transform=self.ax["hr"].get_xaxis_transform(),
                                       ha="right", va="top", fontsize=8, color=ALERTA, fontweight="bold",
                                       bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.85))
            self._artistas_marcas.append(texto)
        for t_cambio, texto_cambio in getattr(s, "cambios", []):
            if t_cambio < x_lo:
                continue
            xc = (t_cambio + off) / 86400.0
            for variable, ax in self.ax.items():
                self._artistas_marcas.append(
                    ax.axvline(xc, color=PRIMARIO, linewidth=1.3, linestyle=":", alpha=0.9))
            etiqueta = self.ax["hr"].text(
                xc, 0.80, " PULSERA " + texto_cambio.split("→")[-1].strip(),
                transform=self.ax["hr"].get_xaxis_transform(), ha="left", va="top", fontsize=8,
                color=PRIMARIO, fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.85))
            self._artistas_marcas.append(etiqueta)
        self.canvas.draw_idle()

    # ---- utilidades -----------------------------------------------------------

    def _mostrar_resumen(self):
        res = self.sesion.resumen()
        top = tk.Toplevel(self)
        top.title("Resumen de sesión")
        top.configure(bg=BG)
        top.geometry("640x600")
        top.transient(self)
        fam = self.fam
        tk.Label(top, text="Resumen de sesión", bg=BG, fg=TEXTO, font=(fam, 15, "bold")).pack(anchor="w", padx=20, pady=(16, 2))
        sub = f"{self.fuente_actual} · dispositivo {res['dispositivo'] or '--'} · duración {formato_duracion(res['duracion_s'])}"
        tk.Label(top, text=sub, bg=BG, fg=MUTED, font=(fam, 10)).pack(anchor="w", padx=20)

        tabla = tk.Frame(top, bg=CARD, highlightthickness=1, highlightbackground=BORDE, highlightcolor=BORDE)
        tabla.pack(fill=tk.X, padx=20, pady=(14, 0))
        encabezados = ["Signo vital", "Mínimo", "Máximo", "Promedio", "Fuera de rango"]
        for c, h in enumerate(encabezados):
            tk.Label(tabla, text=h, bg=GRIS_BG, fg=MUTED, font=(fam, 9, "bold"), padx=10, pady=6, anchor="w").grid(
                row=0, column=c, sticky="ew")
            tabla.columnconfigure(c, weight=1)

        def f(v, d=1):
            return "--" if v is None else f"{v:.{d}f}"

        for r, variable in enumerate(("hr", "spo2", "temp"), start=1):
            u, v = self.umbrales[variable], res["variables"][variable]
            dec = 1 if variable == "temp" else 0
            fila = [f"{u['nombre']} ({u['unidad']})", f(v["min"], dec), f(v["max"], dec), f(v["media"], 1),
                    "--" if v["pct_fuera_rango"] is None else f"{v['pct_fuera_rango']:.0f} %"]
            for c, texto in enumerate(fila):
                tk.Label(tabla, text=texto, bg=CARD, fg=TEXTO, font=(fam, 10, "bold" if c == 0 else "normal"),
                         padx=10, pady=6, anchor="w").grid(row=r, column=c, sticky="ew")

        datos = [
            ("Paquetes recibidos", str(res["paquetes_recibidos"])),
            ("Faltantes detectados / recuperados", f"{res['paquetes_detectados']} / {res['paquetes_recuperados']}"),
            ("Pérdida real de datos", f"{res['paquetes_perdidos']} paquetes ({res['perdida_pct']:.1f} %)"),
            ("Batería", f"{f(res['bateria_inicial'], 0)} % al inicio → {f(res['bateria_final'], 0)} % al final"),
            ("Eventos de la pulsera", ", ".join(f"{k}: {n}" for k, n in sorted(res["eventos"].items())) or "ninguno"),
            ("Alertas generadas / atendidas", f"{res['alertas_generadas']} / {res['alertas_atendidas']}"),
            ("Tiempo de respuesta", f"promedio {f(res['respuesta_media_s'])} s · máximo {f(res['respuesta_max_s'])} s"),
        ]
        cuadro = tk.Frame(top, bg=CARD, highlightthickness=1, highlightbackground=BORDE, highlightcolor=BORDE)
        cuadro.pack(fill=tk.X, padx=20, pady=(12, 0))
        cuadro.columnconfigure(1, weight=1)
        for r, (k, v) in enumerate(datos):
            tk.Label(cuadro, text=k, bg=CARD, fg=MUTED, font=(fam, 10), padx=12, pady=5, anchor="w").grid(row=r, column=0, sticky="w")
            tk.Label(cuadro, text=v, bg=CARD, fg=TEXTO, font=(fam, 10, "bold"), padx=12, anchor="e").grid(row=r, column=1, sticky="e")
        tk.Label(top, text="Umbrales de referencia para adulto en reposo, pendientes de validar.", bg=BG, fg=MUTED,
                 font=(fam, 9)).pack(anchor="w", padx=20, pady=(10, 0))

        estado = tk.Label(top, text="", bg=BG, fg=OK, font=(fam, 9))
        estado.pack(anchor="w", padx=20, pady=(6, 0))

        def guardar():
            texto = formatear_resumen(res, self.fuente_actual, self.sesion.inicio)
            try:
                if self.registrador is not None:
                    ruta = self.registrador.guardar_resumen(texto)
                else:
                    os.makedirs(self.carpeta_datos, exist_ok=True)
                    ruta = os.path.join(self.carpeta_datos, f"resumen_{datetime.now():%Y%m%d_%H%M%S}.txt")
                    with open(ruta, "w", encoding="utf-8") as f:
                        f.write(texto)
            except OSError as exc:
                estado.config(text=f"No se pudo guardar: {exc}", fg=ALERTA)
                return
            estado.config(text=f"Guardado en {ruta}", fg=OK)

        botones = tk.Frame(top, bg=BG)
        botones.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=16)
        self._boton(botones, "Cerrar", top.destroy, color=GRIS_BG, fg=TEXTO, activo="#D3DAE1").pack(side=tk.RIGHT)
        self._boton(botones, "Guardar como .txt", guardar).pack(side=tk.RIGHT, padx=(0, 8))

    def _abrir_carpeta(self):
        carpeta = os.path.abspath(self.carpeta_datos)
        os.makedirs(carpeta, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(carpeta)  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", carpeta])
            else:
                subprocess.Popen(["xdg-open", carpeta])
        except Exception as exc:  # noqa: BLE001
            self.lbl_csv.config(text=f"No se pudo abrir la carpeta: {exc}")

    def _alternar_pantalla_completa(self):
        self._pantalla_completa = not self._pantalla_completa
        self.attributes("-fullscreen", self._pantalla_completa)

    def _salir_pantalla_completa(self):
        if self._pantalla_completa:
            self._pantalla_completa = False
            self.attributes("-fullscreen", False)

    def _al_cerrar(self):
        for job in ("_job_cola", "_job_tick"):
            try:
                self.after_cancel(getattr(self, job))
            except Exception:  # noqa: BLE001
                pass
        if self.hilo is not None:
            self.hilo.detener()
        self.sesion.cerrar()
        if self.sesion_bd is not None:
            self.bd.cerrar_sesion(self.sesion_bd, limpio=True)
            self.sesion_bd = None
        if self.registrador is not None:
            self.registrador.cerrar()
        self.bd.registrar_evento_sistema("CIERRE_LIMPIO", "El programa se cerró de forma normal")
        self.bd.cerrar()
        self.destroy()


if __name__ == "__main__":
    app = Aplicacion()
    app.mainloop()
