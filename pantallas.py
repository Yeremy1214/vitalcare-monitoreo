"""
Pantallas de gestión: pacientes, pulseras y configuración del centro.

La pantalla de monitoreo vive en app_testbed.py. Aquí está todo lo que rodea al
monitoreo: quién está ingresado, qué pulsera lleva puesta, qué pasa cuando hay que
cambiarla, qué signos se toman a mano y con qué umbrales se juzga cada paciente.
"""

import tkinter as tk
from tkinter import messagebox, ttk

from datos import (AMBOS, ESTADO_PULSERA, MOTIVOS_FIN, SEXOS, SIGNOS, SIGNOS_MANUALES,
                   meses_de_edad, texto_edad)
from estilo import (ALERTA, ALERTA_BG, BG, BORDE, CARD, GRIS_BG, MUTED, OK, OK_BG, PRIMARIO, TEXTO,
                    WARN, WARN_BG, DialogoBase, Tabla, boton, boton_claro, fila_campo, tarjeta,
                    titulo_panel)


def nombre_completo(p):
    return f"{p['nombre']} {p['apellidos']}".strip()


def texto_bateria(v):
    return "—" if v is None else f"{int(v)} %"


def ubicacion(p):
    hab, cama = (p["habitacion"] or "").strip(), (p["cama"] or "").strip()
    if hab and cama:
        return f"{hab} · {cama}"
    return hab or cama or "sin asignar"


# ======================================================================
# Diálogos
# ======================================================================

class DialogoPaciente(DialogoBase):
    """Ingreso de un paciente nuevo o edición de uno ya ingresado."""

    def __init__(self, app, paciente=None):
        titulo = "Editar datos del paciente" if paciente else "Ingreso de paciente"
        super().__init__(app, titulo, app.fam, ancho=620)
        self.app, self.bd, self.paciente = app, app.bd, paciente
        self.subtitulo("Los datos marcados son obligatorios. La fecha de nacimiento define el perfil "
                       "de umbrales clínicos que el sistema aplicará a este paciente.")
        g = tk.Frame(self.cuerpo, bg=BG)
        g.pack(fill=tk.BOTH, expand=True)
        p = paciente
        exp = p["expediente"] if p else f"EXP-{self.bd.q1('SELECT COUNT(*) c FROM pacientes')['c'] + 1:06d}"
        self.v_exp, _ = fila_campo(g, app.fam, "Expediente *", 0, 26, exp)
        self.v_nom, _ = fila_campo(g, app.fam, "Nombre *", 1, 26, p["nombre"] if p else "")
        self.v_ape, _ = fila_campo(g, app.fam, "Apellidos *", 2, 26, p["apellidos"] if p else "")
        self.v_fn, w_fn = fila_campo(g, app.fam, "Fecha de nacimiento *", 3, 26,
                                     p["fecha_nac"] if p else "", "formato AAAA-MM-DD")
        self.v_sexo, _ = fila_campo(g, app.fam, "Sexo *", 4, 26, p["sexo"] if p else SEXOS[0],
                                    opciones=SEXOS)
        self.v_hab, _ = fila_campo(g, app.fam, "Habitación", 5, 26, p["habitacion"] if p else "")
        self.v_cama, _ = fila_campo(g, app.fam, "Cama", 6, 26, p["cama"] if p else "")
        self.v_dx, _ = fila_campo(g, app.fam, "Diagnóstico de ingreso", 7, 26, p["diagnostico"] if p else "")
        self.v_ale, _ = fila_campo(g, app.fam, "Alergias", 8, 26, p["alergias"] if p else "")
        self.v_not, _ = fila_campo(g, app.fam, "Notas", 9, 26, p["notas"] if p else "")
        self.lbl_perfil = tk.Label(g, text="", bg=BG, fg=PRIMARIO, font=(app.fam, 9, "bold"),
                                   justify="left")
        self.lbl_perfil.grid(row=10, column=0, columnspan=3, sticky="w", pady=(10, 0))
        for v in (self.v_fn, self.v_sexo):
            v.trace_add("write", lambda *a: self._refrescar_perfil())
        self._refrescar_perfil()
        self.botones("Guardar", self._guardar)
        w_fn.focus_set()
        self.centrar()

    def _refrescar_perfil(self):
        fn = self.v_fn.get().strip()
        m = meses_de_edad(fn)
        if m is None:
            self.lbl_perfil.configure(text="Escriba la fecha de nacimiento para ver el perfil clínico.")
            return
        perfil = self.bd.perfil_de(fn, self.v_sexo.get())
        u = self.bd.umbrales_de_perfil(perfil, self.v_sexo.get())
        hr = u.get("hr")
        extra = f" · FC normal {hr['normal_min']:g} a {hr['normal_max']:g} lpm" if hr else ""
        self.lbl_perfil.configure(text=f"Edad: {texto_edad(fn)} · Perfil clínico: {perfil}{extra}")

    def _guardar(self):
        d = {"expediente": self.v_exp.get().strip(), "nombre": self.v_nom.get().strip(),
             "apellidos": self.v_ape.get().strip(), "fecha_nac": self.v_fn.get().strip(),
             "sexo": self.v_sexo.get(), "habitacion": self.v_hab.get().strip(),
             "cama": self.v_cama.get().strip(), "diagnostico": self.v_dx.get().strip(),
             "alergias": self.v_ale.get().strip(), "notas": self.v_not.get().strip()}
        if not d["expediente"] or not d["nombre"] or not d["apellidos"]:
            return self.error("Expediente, nombre y apellidos son obligatorios.")
        if meses_de_edad(d["fecha_nac"]) is None:
            return self.error("La fecha de nacimiento debe tener el formato AAAA-MM-DD, por ejemplo 1958-04-12.")
        otro = self.bd.paciente_por_expediente(d["expediente"])
        if otro and (not self.paciente or otro["id"] != self.paciente["id"]):
            return self.error(f"Ya hay un paciente con el expediente {d['expediente']}.")
        if self.paciente:
            self.bd.actualizar_paciente(self.paciente["id"], d, self.app.usuario)
            self.resultado = self.paciente["id"]
        else:
            self.resultado = self.bd.crear_paciente(d, self.app.usuario)
        self.destroy()


class DialogoPulsera(DialogoBase):
    def __init__(self, app, pulsera=None):
        super().__init__(app, "Editar pulsera" if pulsera else "Agregar pulsera al inventario", app.fam)
        self.app, self.bd, self.pulsera = app, app.bd, pulsera
        self.subtitulo("El número de serie identifica al dispositivo físico. El identificador es el que "
                       "la pulsera envía dentro de cada paquete, y es el que permite saber de quién son "
                       "los datos que están llegando.")
        g = tk.Frame(self.cuerpo, bg=BG)
        g.pack(fill=tk.BOTH, expand=True)
        w = pulsera
        self.v_serie, _ = fila_campo(g, app.fam, "Número de serie *", 0, 24,
                                     w["serie"] if w else self.bd.siguiente_serie())
        self.v_dev, _ = fila_campo(g, app.fam, "Identificador en el paquete *", 1, 24,
                                   w["id_dispositivo"] if w else self.bd.siguiente_dispositivo(),
                                   'el campo "id" del JSON, por ejemplo W01')
        self.v_mac, _ = fila_campo(g, app.fam, "Dirección MAC (BLE)", 2, 24, w["mac"] if w else "")
        self.v_mod, _ = fila_campo(g, app.fam, "Modelo", 3, 24, w["modelo"] if w else "VitalCare v1")
        self.v_est, _ = fila_campo(g, app.fam, "Estado", 4, 24, w["estado"] if w else "Disponible",
                                   opciones=ESTADO_PULSERA)
        self.v_not, _ = fila_campo(g, app.fam, "Notas", 5, 24, w["notas"] if w else "")
        self.botones("Guardar", self._guardar)
        self.centrar()

    def _guardar(self):
        d = {"serie": self.v_serie.get().strip(), "id_dispositivo": self.v_dev.get().strip(),
             "mac": self.v_mac.get().strip(), "modelo": self.v_mod.get().strip(),
             "estado": self.v_est.get(), "notas": self.v_not.get().strip()}
        if not d["serie"] or not d["id_dispositivo"]:
            return self.error("El número de serie y el identificador son obligatorios.")
        existe = self.bd.q1("SELECT id FROM pulseras WHERE (serie=? OR id_dispositivo=?) AND id<>?",
                            (d["serie"], d["id_dispositivo"], self.pulsera["id"] if self.pulsera else -1))
        if existe:
            return self.error("Ya hay otra pulsera con ese número de serie o ese identificador.")
        if self.pulsera:
            self.bd.actualizar_pulsera(self.pulsera["id"], d, self.app.usuario)
            self.resultado = self.pulsera["id"]
        else:
            self.resultado = self.bd.crear_pulsera(d, self.app.usuario)
        self.destroy()


class DialogoAsignar(DialogoBase):
    """Asignar una pulsera al paciente, o cambiarla por otra sin perder la continuidad."""

    def __init__(self, app, paciente):
        self.actual = app.bd.asignacion_de_paciente(paciente["id"])
        titulo = "Cambiar la pulsera del paciente" if self.actual else "Asignar pulsera al paciente"
        super().__init__(app, titulo, app.fam, ancho=620)
        self.app, self.bd, self.paciente = app, app.bd, paciente
        if self.actual:
            self.subtitulo(f"{nombre_completo(paciente)} lleva puesta la {self.actual['serie']} "
                           f"({texto_bateria(self.actual['bateria'])}) desde {self.actual['inicio']}. "
                           "Al asignar otra, la anterior se libera y el monitoreo del paciente continúa "
                           "sin cortar la gráfica.")
        else:
            self.subtitulo(f"{nombre_completo(paciente)} · {ubicacion(paciente)}. "
                           "Elija una pulsera disponible del inventario.")
        cont = tk.Frame(self.cuerpo, bg=BG)
        cont.pack(fill=tk.BOTH, expand=True)
        tk.Label(cont, text="Pulseras disponibles", bg=BG, fg=TEXTO,
                 font=(app.fam, 10, "bold")).pack(anchor="w")
        self.tabla = Tabla(cont, [("serie", "Serie", 130), ("dev", "Identificador", 110),
                                  ("bat", "Batería", 80, "center"), ("est", "Estado", 110),
                                  ("mac", "MAC", 150)], app.fam, alto=6)
        self.tabla.pack(fill=tk.BOTH, expand=True, pady=(4, 10))
        self.disponibles = self.bd.pulseras_disponibles()
        for w in self.disponibles:
            bat = w["bateria"]
            tag = "alerta" if (bat is not None and bat <= 15) else ("aviso" if (bat is not None and bat <= 30) else "ok")
            self.tabla.agregar(w["id"], (w["serie"], w["id_dispositivo"], texto_bateria(bat),
                                         w["estado"], w["mac"] or "—"), (tag,))
        g = tk.Frame(cont, bg=BG)
        g.pack(fill=tk.X)
        if self.actual:
            self.v_motivo, _ = fila_campo(g, app.fam, "Motivo del cambio *", 0, 28, MOTIVOS_FIN[1],
                                          opciones=MOTIVOS_FIN)
        else:
            self.v_motivo = tk.StringVar(value="Asignación inicial")
        self.v_nota, _ = fila_campo(g, app.fam, "Nota", 1, 28, "")
        self.v_quien, _ = fila_campo(g, app.fam, "Asignada por *", 2, 28, app.usuario)
        if not self.disponibles:
            self.error("No hay pulseras disponibles en el inventario. Libere una o registre otra en la "
                       "pantalla de Pulseras.")
        self.botones("Asignar", self._guardar)
        self.centrar()

    def _guardar(self):
        wid = self.tabla.seleccion()
        if not wid:
            return self.error("Elija una pulsera de la lista.")
        quien = self.v_quien.get().strip()
        if len(quien) < 3:
            return self.error("Escriba el nombre de quien realiza la asignación.")
        try:
            aid, anterior = self.bd.asignar_pulsera(self.paciente["id"], wid, quien,
                                                    self.v_nota.get().strip(), self.v_motivo.get())
        except ValueError as e:
            return self.error(str(e))
        self.app.usuario = quien
        nueva = self.bd.pulsera(wid)
        self.resultado = {"pulsera_id": wid, "serie": nueva["serie"], "dispositivo": nueva["id_dispositivo"],
                          "anterior": anterior, "motivo": self.v_motivo.get()}
        self.destroy()


class DialogoMedicion(DialogoBase):
    """Signos que la pulsera no mide y el personal toma a mano."""

    def __init__(self, app, paciente):
        super().__init__(app, "Registrar signos tomados a mano", app.fam, ancho=620)
        self.app, self.bd, self.paciente = app, app.bd, paciente
        self.umbrales = self.bd.umbrales_de_paciente(paciente["id"])
        self.subtitulo(f"{nombre_completo(paciente)} · {texto_edad(paciente['fecha_nac'])} · "
                       f"{paciente['sexo'].lower()} · perfil {paciente['perfil']}. "
                       "La pulsera no mide presión arterial ni frecuencia respiratoria, así que estos "
                       "valores se registran a mano y el sistema los compara con el rango del paciente.")
        g = tk.Frame(self.cuerpo, bg=BG)
        g.pack(fill=tk.BOTH, expand=True)
        ult = self.bd.ultimas_mediciones(paciente["id"])
        self.vars = {}
        for i, signo in enumerate(SIGNOS_MANUALES):
            nombre, unidad, _ = SIGNOS[signo]
            u = self.umbrales.get(signo)
            rango = f"normal {u['normal'][0]:g} a {u['normal'][1]:g} {unidad}" if u else ""
            anterior = ult.get(signo)
            if anterior is not None:
                rango += f"   ·   último: {anterior['valor']:g} ({anterior['hora'][11:16]})"
            v, _ = fila_campo(g, app.fam, f"{nombre} ({unidad})", i, 12, "", rango)
            self.vars[signo] = v
        self.v_quien, _ = fila_campo(g, app.fam, "Tomado por *", len(SIGNOS_MANUALES), 24, app.usuario)
        self.v_nota, _ = fila_campo(g, app.fam, "Observación", len(SIGNOS_MANUALES) + 1, 24, "")
        self.botones("Registrar", self._guardar)
        self.centrar()

    def _guardar(self):
        quien = self.v_quien.get().strip()
        if len(quien) < 3:
            return self.error("Escriba quién tomó los signos.")
        valores = {}
        for signo, var in self.vars.items():
            txt = var.get().strip().replace(",", ".")
            if not txt:
                continue
            try:
                valores[signo] = float(txt)
            except ValueError:
                return self.error(f"El valor de {SIGNOS[signo][0].lower()} debe ser un número.")
        if not valores:
            return self.error("Escriba al menos un valor.")
        if "pas" in valores and "pad" in valores and valores["pad"] >= valores["pas"]:
            return self.error("La presión diastólica no puede ser mayor o igual que la sistólica.")
        for signo, valor in valores.items():
            self.bd.registrar_medicion(self.paciente["id"], signo, valor, quien, self.v_nota.get().strip())
        self.app.usuario = quien
        self.resultado = valores
        self.destroy()


class DialogoUmbral(DialogoBase):
    def __init__(self, app, perfil, sexo, signo, fila):
        nombre, unidad, _ = SIGNOS[signo]
        super().__init__(app, f"Umbrales de {nombre.lower()}", app.fam)
        self.app, self.bd = app, app.bd
        self.perfil, self.sexo, self.signo = perfil, sexo, signo
        self.subtitulo(f"Perfil {perfil} ({sexo.lower()}). Dentro del rango normal no hay aviso. "
                       "Entre el rango normal y el de vigilancia el signo se marca en ámbar. "
                       "Fuera del rango de vigilancia se genera una alerta.")
        g = tk.Frame(self.cuerpo, bg=BG)
        g.pack(fill=tk.BOTH, expand=True)
        self.v_nmin, _ = fila_campo(g, app.fam, f"Normal, mínimo ({unidad})", 0, 12, f"{fila['normal_min']:g}")
        self.v_nmax, _ = fila_campo(g, app.fam, f"Normal, máximo ({unidad})", 1, 12, f"{fila['normal_max']:g}")
        self.v_vmin, _ = fila_campo(g, app.fam, f"Vigilancia, mínimo ({unidad})", 2, 12, f"{fila['vig_min']:g}")
        self.v_vmax, _ = fila_campo(g, app.fam, f"Vigilancia, máximo ({unidad})", 3, 12, f"{fila['vig_max']:g}")
        self.v_quien, _ = fila_campo(g, app.fam, "Autorizado por *", 4, 24, app.usuario)
        self.botones("Guardar", self._guardar)
        self.centrar()

    def _guardar(self):
        try:
            v = [float(x.get().replace(",", ".")) for x in
                 (self.v_nmin, self.v_nmax, self.v_vmin, self.v_vmax)]
        except ValueError:
            return self.error("Todos los umbrales deben ser números.")
        if not (v[2] <= v[0] < v[1] <= v[3]):
            return self.error("Debe cumplirse: vigilancia mínima ≤ normal mínima < normal máxima ≤ "
                              "vigilancia máxima.")
        quien = self.v_quien.get().strip()
        if len(quien) < 3:
            return self.error("Escriba quién autoriza el cambio. Queda registrado en la auditoría.")
        self.bd.fijar_umbral_perfil(self.perfil, self.sexo, self.signo, v, quien)
        self.app.usuario = quien
        self.resultado = v
        self.destroy()


class DialogoHistorial(tk.Toplevel):
    def __init__(self, app, titulo, columnas, filas):
        super().__init__(app)
        self.title(titulo)
        self.configure(bg=BG)
        self.transient(app)
        m = tk.Frame(self, bg=BG, padx=16, pady=14)
        m.pack(fill=tk.BOTH, expand=True)
        tk.Label(m, text=titulo, bg=BG, fg=TEXTO, font=(app.fam, 12, "bold")).pack(anchor="w", pady=(0, 8))
        t = Tabla(m, columnas, app.fam, alto=14)
        t.pack(fill=tk.BOTH, expand=True)
        for i, f in enumerate(filas):
            t.agregar(i, f)
        boton_claro(m, "Cerrar", self.destroy, app.fam).pack(anchor="e", pady=(10, 0))
        self.update_idletasks()
        self.grab_set()


# ======================================================================
# Pantalla de pacientes
# ======================================================================

class PantallaPacientes(tk.Frame):
    def __init__(self, master, app):
        super().__init__(master, bg=BG)
        self.app, self.bd, self.fam = app, app.bd, app.fam
        self.v_buscar = tk.StringVar()
        self.v_estado = tk.StringVar(value="Ingresado")

        barra = tarjeta(self)
        barra.pack(fill=tk.X, padx=14, pady=(12, 8))
        fila = tk.Frame(barra, bg=CARD)
        fila.pack(fill=tk.X, padx=14, pady=10)
        tk.Label(fila, text="Buscar", bg=CARD, fg=TEXTO, font=(self.fam, 10)).pack(side=tk.LEFT)
        e = tk.Entry(fila, textvariable=self.v_buscar, font=(self.fam, 10), width=26, relief=tk.FLAT,
                     highlightthickness=1, highlightbackground=BORDE, highlightcolor=PRIMARIO, bg="white")
        e.pack(side=tk.LEFT, padx=(8, 14))
        e.bind("<KeyRelease>", lambda ev: self.refrescar())
        tk.Label(fila, text="Estado", bg=CARD, fg=TEXTO, font=(self.fam, 10)).pack(side=tk.LEFT)
        cb = ttk.Combobox(fila, textvariable=self.v_estado, values=["Ingresado", "Alta", "Todos"],
                          state="readonly", width=12, font=(self.fam, 10))
        cb.pack(side=tk.LEFT, padx=(8, 14))
        cb.bind("<<ComboboxSelected>>", lambda ev: self.refrescar())
        boton(fila, "Ingresar paciente", self.nuevo, self.fam).pack(side=tk.RIGHT)
        boton_claro(fila, "Editar", self.editar, self.fam).pack(side=tk.RIGHT, padx=(0, 8))
        boton_claro(fila, "Eliminar", self.eliminar, self.fam).pack(side=tk.RIGHT, padx=(0, 8))

        cuerpo = tarjeta(self)
        cuerpo.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 12))
        titulo_panel(cuerpo, "Pacientes", self.fam,
                     "doble clic para editar · la columna Pulsera indica qué dispositivo envía sus datos")
        self.tabla = Tabla(cuerpo, [
            ("exp", "Expediente", 120), ("nom", "Paciente", 210), ("edad", "Edad", 90),
            ("sexo", "Sexo", 90), ("perfil", "Perfil clínico", 120), ("ubi", "Habitación", 110),
            ("serie", "Pulsera", 120), ("bat", "Batería", 80, "center"), ("est", "Estado", 90),
            ("ing", "Ingreso", 130)], self.fam, alto=13,
            al_elegir=self._al_elegir, al_doble=self.editar)
        self.tabla.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 10))

        acciones = tk.Frame(cuerpo, bg=CARD)
        acciones.pack(fill=tk.X, padx=14, pady=(0, 12))
        boton(acciones, "Monitorear a este paciente", self.monitorear, self.fam).pack(side=tk.LEFT)
        boton_claro(acciones, "Asignar o cambiar pulsera", self.asignar, self.fam).pack(side=tk.LEFT, padx=8)
        boton_claro(acciones, "Signos manuales", self.medicion, self.fam).pack(side=tk.LEFT, padx=(0, 8))
        boton_claro(acciones, "Historial de pulseras", self.historial, self.fam).pack(side=tk.LEFT)
        boton(acciones, "Dar de alta", self.dar_alta, self.fam, color=WARN,
              activo="#8F5500").pack(side=tk.RIGHT)
        boton_claro(acciones, "Liberar pulsera", self.liberar, self.fam).pack(side=tk.RIGHT, padx=8)

        self.detalle = tk.Label(cuerpo, text="", bg=CARD, fg=MUTED, font=(self.fam, 9), justify="left",
                                anchor="w", wraplength=1150)
        self.detalle.pack(fill=tk.X, padx=14, pady=(0, 12))
        self.refrescar()

    # ---- datos ----
    def refrescar(self):
        sel = self.tabla.seleccion()
        self.tabla.limpiar()
        for p in self.bd.listar_pacientes(self.v_buscar.get().strip(), self.v_estado.get()):
            bat = p["bateria_pulsera"]
            tag = ""
            if p["estado"] == "Alta":
                tag = "gris"
            elif not p["serie"]:
                tag = "aviso"
            elif bat is not None and bat <= 15:
                tag = "alerta"
            self.tabla.agregar(p["id"], (
                p["expediente"], nombre_completo(p), texto_edad(p["fecha_nac"]), p["sexo"],
                p["perfil"] or "—", ubicacion(p), p["serie"] or "sin pulsera", texto_bateria(bat),
                p["estado"], (p["ingreso"] or "")[:16]), (tag,) if tag else ())
        if sel:
            self.tabla.seleccionar(sel)
        self._al_elegir()

    def _al_elegir(self):
        pid = self.tabla.seleccion()
        if not pid:
            self.detalle.configure(text="Seleccione un paciente para ver su detalle.")
            return
        p = self.bd.paciente(pid)
        u = self.bd.umbrales_de_paciente(pid)
        partes = [f"Diagnóstico: {p['diagnostico'] or 'sin registrar'}",
                  f"Alergias: {p['alergias'] or 'ninguna registrada'}"]
        for signo in ("hr", "spo2", "temp", "pas"):
            if signo in u:
                nombre, unidad, _ = SIGNOS[signo]
                n = u[signo]["normal"]
                partes.append(f"{nombre}: {n[0]:g} a {n[1]:g} {unidad} ({u[signo]['origen']})")
        ult = self.bd.ultimas_mediciones(pid)
        if ult:
            manual = ", ".join(f"{SIGNOS[s][0].split()[-1].lower()} {m['valor']:g}" for s, m in ult.items())
            partes.append(f"Últimos signos manuales: {manual}")
        self.detalle.configure(text="   ·   ".join(partes))

    def _seleccionado(self, exigir_ingresado=True):
        pid = self.tabla.seleccion()
        if not pid:
            messagebox.showinfo("Sin selección", "Elija primero un paciente de la lista.", parent=self.app)
            return None
        p = self.bd.paciente(pid)
        if exigir_ingresado and p["estado"] != "Ingresado":
            messagebox.showinfo("Paciente dado de alta",
                                "Este paciente ya fue dado de alta. Solo puede consultar su historial.",
                                parent=self.app)
            return None
        return p

    # ---- acciones ----
    def nuevo(self):
        d = DialogoPaciente(self.app)
        self.app.wait_window(d)
        if d.resultado:
            self.refrescar()
            self.tabla.seleccionar(d.resultado)
            self.app.aviso("Paciente ingresado. Ahora asígnele una pulsera.")

    def editar(self):
        pid = self.tabla.seleccion()
        if not pid:
            return
        d = DialogoPaciente(self.app, self.bd.paciente(pid))
        self.app.wait_window(d)
        if d.resultado:
            self.refrescar()
            self.app.refrescar_paciente_monitoreado()

    def eliminar(self):
        pid = self.tabla.seleccion()
        if not pid:
            return
        p = self.bd.paciente(pid)
        ok, usos, meds = self.bd.puede_eliminar_paciente(pid)
        if not ok:
            messagebox.showwarning(
                "No se puede eliminar",
                f"{nombre_completo(p)} tiene {usos} asignación(es) de pulsera y {meds} medición(es) "
                "registradas.\n\nEn un sistema clínico ese historial no se borra. Use Dar de alta para "
                "sacarlo del monitoreo y liberar su pulsera.", parent=self.app)
            return
        if messagebox.askyesno("Eliminar paciente",
                               f"¿Eliminar a {nombre_completo(p)}?\n\nNo tiene historial registrado, "
                               "así que la eliminación es definitiva.", parent=self.app):
            self.bd.eliminar_paciente(pid, self.app.usuario)
            self.refrescar()
            self.app.aviso("Paciente eliminado del sistema.")

    def asignar(self):
        p = self._seleccionado()
        if not p:
            return
        d = DialogoAsignar(self.app, p)
        self.app.wait_window(d)
        if d.resultado:
            r = d.resultado
            self.refrescar()
            self.app.pulsera_cambiada(p["id"], r)
            if r["anterior"]:
                self.app.aviso(f"Pulsera cambiada: {r['anterior']} → {r['serie']}. "
                               "El monitoreo del paciente continúa.")
            else:
                self.app.aviso(f"Pulsera {r['serie']} asignada a {nombre_completo(p)}.")

    def liberar(self):
        p = self._seleccionado()
        if not p:
            return
        a = self.bd.asignacion_de_paciente(p["id"])
        if not a:
            messagebox.showinfo("Sin pulsera", "Este paciente no tiene pulsera asignada.", parent=self.app)
            return
        if messagebox.askyesno("Liberar pulsera",
                               f"¿Liberar la pulsera {a['serie']} de {nombre_completo(p)}?\n\n"
                               "El paciente queda sin monitoreo hasta que se le asigne otra.",
                               parent=self.app):
            self.bd.cerrar_asignacion(a["id"], self.app.usuario, "Otro", "liberada a mano")
            self.refrescar()
            self.app.pulsera_cambiada(p["id"], None)
            self.app.aviso(f"Pulsera {a['serie']} liberada.")

    def dar_alta(self):
        p = self._seleccionado()
        if not p:
            return
        a = self.bd.asignacion_de_paciente(p["id"])
        texto = f"¿Dar de alta a {nombre_completo(p)}?"
        if a:
            texto += (f"\n\nLa pulsera {a['serie']} quedará disponible para otro paciente y el monitoreo "
                      "de este se detiene.")
        if not messagebox.askyesno("Alta del paciente", texto, parent=self.app):
            return
        liberada = self.bd.dar_alta(p["id"], self.app.usuario)
        self.refrescar()
        self.app.paciente_dado_de_alta(p["id"])
        self.app.aviso(f"{nombre_completo(p)} dado de alta." +
                       (f" Pulsera {liberada} liberada." if liberada else ""))

    def medicion(self):
        p = self._seleccionado()
        if not p:
            return
        d = DialogoMedicion(self.app, p)
        self.app.wait_window(d)
        if d.resultado:
            self.refrescar()
            self.app.signos_manuales_registrados(p["id"], d.resultado)

    def historial(self):
        pid = self.tabla.seleccion()
        if not pid:
            return
        p = self.bd.paciente(pid)
        filas = [(h["serie"], h["id_dispositivo"], h["inicio"], h["fin"] or "en uso",
                  h["motivo_fin"] or "—", h["asignado_por"] or "—", h["nota"] or "")
                 for h in self.bd.historial_asignaciones(pid=pid)]
        DialogoHistorial(self.app, f"Historial de pulseras de {nombre_completo(p)}",
                         [("serie", "Serie", 120), ("dev", "Identificador", 110),
                          ("ini", "Desde", 150), ("fin", "Hasta", 150), ("mot", "Motivo", 160),
                          ("quien", "Asignada por", 150), ("nota", "Nota", 220)], filas)

    def monitorear(self):
        p = self._seleccionado()
        if not p:
            return
        a = self.bd.asignacion_de_paciente(p["id"])
        if not a:
            messagebox.showinfo("Sin pulsera",
                                f"{nombre_completo(p)} no tiene pulsera asignada. Asígnele una primero.",
                                parent=self.app)
            return
        self.app.monitorear_paciente(p["id"])


# ======================================================================
# Pantalla de pulseras
# ======================================================================

class PantallaPulseras(tk.Frame):
    def __init__(self, master, app):
        super().__init__(master, bg=BG)
        self.app, self.bd, self.fam = app, app.bd, app.fam
        self.v_buscar = tk.StringVar()
        self.v_estado = tk.StringVar(value="Todos")

        barra = tarjeta(self)
        barra.pack(fill=tk.X, padx=14, pady=(12, 8))
        fila = tk.Frame(barra, bg=CARD)
        fila.pack(fill=tk.X, padx=14, pady=10)
        tk.Label(fila, text="Buscar", bg=CARD, fg=TEXTO, font=(self.fam, 10)).pack(side=tk.LEFT)
        e = tk.Entry(fila, textvariable=self.v_buscar, font=(self.fam, 10), width=24, relief=tk.FLAT,
                     highlightthickness=1, highlightbackground=BORDE, highlightcolor=PRIMARIO, bg="white")
        e.pack(side=tk.LEFT, padx=(8, 14))
        e.bind("<KeyRelease>", lambda ev: self.refrescar())
        tk.Label(fila, text="Estado", bg=CARD, fg=TEXTO, font=(self.fam, 10)).pack(side=tk.LEFT)
        cb = ttk.Combobox(fila, textvariable=self.v_estado, values=["Todos"] + ESTADO_PULSERA,
                          state="readonly", width=16, font=(self.fam, 10))
        cb.pack(side=tk.LEFT, padx=(8, 14))
        cb.bind("<<ComboboxSelected>>", lambda ev: self.refrescar())
        boton(fila, "Agregar pulsera", self.nueva, self.fam).pack(side=tk.RIGHT)
        boton_claro(fila, "Editar", self.editar, self.fam).pack(side=tk.RIGHT, padx=(0, 8))
        boton_claro(fila, "Eliminar", self.eliminar, self.fam).pack(side=tk.RIGHT, padx=(0, 8))

        cuerpo = tarjeta(self)
        cuerpo.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 12))
        titulo_panel(cuerpo, "Inventario de pulseras", self.fam,
                     "cada pulsera tiene su número de serie, no está atada a una cama")
        self.tabla = Tabla(cuerpo, [
            ("serie", "Serie", 130), ("dev", "Identificador", 120), ("mac", "MAC", 160),
            ("mod", "Modelo", 130), ("est", "Estado", 130), ("bat", "Batería", 90, "center"),
            ("vista", "Último dato", 150), ("pac", "Asignada a", 230)], self.fam, alto=14,
            al_doble=self.editar)
        self.tabla.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 10))
        acciones = tk.Frame(cuerpo, bg=CARD)
        acciones.pack(fill=tk.X, padx=14, pady=(0, 14))
        for estado in ESTADO_PULSERA:
            boton_claro(acciones, f"Marcar {estado.lower()}", lambda e=estado: self.cambiar_estado(e),
                        self.fam).pack(side=tk.LEFT, padx=(0, 8))
        boton_claro(acciones, "Historial", self.historial, self.fam).pack(side=tk.RIGHT)
        self.refrescar()

    def refrescar(self):
        sel = self.tabla.seleccion()
        self.tabla.limpiar()
        for w in self.bd.listar_pulseras(self.v_buscar.get().strip(), self.v_estado.get()):
            bat = w["bateria"]
            tag = ""
            if w["estado"] == "Fuera de servicio":
                tag = "gris"
            elif bat is not None and bat <= 15:
                tag = "alerta"
            elif bat is not None and bat <= 30:
                tag = "aviso"
            elif w["estado"] == "Disponible":
                tag = "ok"
            paciente = (f"{w['p_nombre']} {w['p_apellidos']} · {w['p_habitacion'] or 's/h'}"
                        if w["paciente_id"] else "—")
            self.tabla.agregar(w["id"], (w["serie"], w["id_dispositivo"], w["mac"] or "—",
                                         w["modelo"] or "—", w["estado"], texto_bateria(bat),
                                         (w["ultima_vista"] or "nunca")[:16], paciente),
                               (tag,) if tag else ())
        if sel:
            self.tabla.seleccionar(sel)

    def nueva(self):
        d = DialogoPulsera(self.app)
        self.app.wait_window(d)
        if d.resultado:
            self.refrescar()
            self.app.aviso("Pulsera agregada al inventario.")

    def editar(self):
        wid = self.tabla.seleccion()
        if not wid:
            return
        d = DialogoPulsera(self.app, self.bd.pulsera(wid))
        self.app.wait_window(d)
        if d.resultado:
            self.refrescar()

    def eliminar(self):
        wid = self.tabla.seleccion()
        if not wid:
            return
        w = self.bd.pulsera(wid)
        ok, usos = self.bd.puede_eliminar_pulsera(wid)
        if not ok:
            messagebox.showwarning("No se puede eliminar",
                                   f"La pulsera {w['serie']} tiene {usos} asignación(es) en su historial "
                                   "y ese registro no se borra.\n\nUse el estado Fuera de servicio para "
                                   "retirarla del inventario.", parent=self.app)
            return
        if messagebox.askyesno("Eliminar pulsera", f"¿Eliminar la pulsera {w['serie']} del inventario?",
                               parent=self.app):
            self.bd.eliminar_pulsera(wid, self.app.usuario)
            self.refrescar()

    def cambiar_estado(self, estado):
        wid = self.tabla.seleccion()
        if not wid:
            messagebox.showinfo("Sin selección", "Elija una pulsera de la lista.", parent=self.app)
            return
        w = self.bd.pulsera(wid)
        asig = self.bd.asignacion_de_pulsera(wid)
        if asig and estado != "En uso":
            messagebox.showwarning("Pulsera en uso",
                                   f"La {w['serie']} está puesta a {asig['nombre']} {asig['apellidos']}. "
                                   "Libérela desde la pantalla de Pacientes antes de cambiar su estado.",
                                   parent=self.app)
            return
        self.bd.actualizar_estado_pulsera(wid, estado, self.app.usuario)
        self.refrescar()

    def historial(self):
        wid = self.tabla.seleccion()
        if not wid:
            return
        w = self.bd.pulsera(wid)
        filas = [(f"{h['nombre']} {h['apellidos']}", h["expediente"], h["inicio"], h["fin"] or "en uso",
                  h["motivo_fin"] or "—", h["asignado_por"] or "—")
                 for h in self.bd.historial_asignaciones(wid=wid)]
        DialogoHistorial(self.app, f"Historial de la pulsera {w['serie']}",
                         [("pac", "Paciente", 220), ("exp", "Expediente", 130), ("ini", "Desde", 150),
                          ("fin", "Hasta", 150), ("mot", "Motivo", 170), ("quien", "Asignada por", 150)],
                         filas)


# ======================================================================
# Pantalla de configuración
# ======================================================================

class PantallaConfig(tk.Frame):
    def __init__(self, master, app):
        super().__init__(master, bg=BG)
        self.app, self.bd, self.fam = app, app.bd, app.fam
        nb = ttk.Notebook(self)
        nb.pack(fill=tk.BOTH, expand=True, padx=14, pady=12)
        self.t_umbrales = tk.Frame(nb, bg=BG)
        self.t_param = tk.Frame(nb, bg=BG)
        self.t_audit = tk.Frame(nb, bg=BG)
        nb.add(self.t_umbrales, text="Umbrales clínicos")
        nb.add(self.t_param, text="Parámetros de alerta")
        nb.add(self.t_audit, text="Auditoría")
        nb.bind("<<NotebookTabChanged>>", lambda e: self.refrescar())
        self._construir_umbrales()
        self._construir_parametros()
        self._construir_auditoria()
        self.refrescar()

    # ---- umbrales ----
    def _construir_umbrales(self):
        c = tarjeta(self.t_umbrales)
        c.pack(fill=tk.BOTH, expand=True)
        titulo_panel(c, "Umbrales por perfil clínico", self.fam,
                     "el perfil se asigna por la edad del paciente; cada centro puede ajustarlo")
        fila = tk.Frame(c, bg=CARD)
        fila.pack(fill=tk.X, padx=14, pady=(0, 8))
        tk.Label(fila, text="Perfil", bg=CARD, fg=TEXTO, font=(self.fam, 10)).pack(side=tk.LEFT)
        self.v_perfil = tk.StringVar()
        self.cb_perfil = ttk.Combobox(fila, textvariable=self.v_perfil, state="readonly", width=18,
                                      font=(self.fam, 10))
        self.cb_perfil.pack(side=tk.LEFT, padx=(8, 16))
        self.cb_perfil.bind("<<ComboboxSelected>>", lambda e: self._cargar_umbrales())
        tk.Label(fila, text="Variante", bg=CARD, fg=TEXTO, font=(self.fam, 10)).pack(side=tk.LEFT)
        self.v_sexo = tk.StringVar(value=AMBOS)
        self.cb_sexo = ttk.Combobox(fila, textvariable=self.v_sexo, state="readonly", width=14,
                                    font=(self.fam, 10))
        self.cb_sexo.pack(side=tk.LEFT, padx=(8, 16))
        self.cb_sexo.bind("<<ComboboxSelected>>", lambda e: self._cargar_umbrales())
        boton(fila, "Editar umbral", self._editar_umbral, self.fam).pack(side=tk.RIGHT)
        boton_claro(fila, "Crear variante por sexo", self._crear_variante, self.fam).pack(
            side=tk.RIGHT, padx=(0, 8))
        self.tabla_u = Tabla(c, [("signo", "Signo", 240), ("mide", "Lo mide", 110),
                                 ("normal", "Rango normal", 180), ("vig", "Rango de vigilancia", 200),
                                 ("alerta", "Genera alerta", 260)], self.fam, alto=8,
                             al_doble=self._editar_umbral)
        self.tabla_u.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 10))
        tk.Label(c, text="Cada cambio queda registrado en la pestaña de Auditoría con el nombre de quien "
                         "lo autorizó. El sistema admite variantes por sexo: se crean copiando el perfil "
                         "general y ajustando los valores que el centro decida.",
                 bg=CARD, fg=MUTED, font=(self.fam, 9), justify="left", wraplength=1100).pack(
            anchor="w", padx=14, pady=(0, 14))

    def _cargar_umbrales(self):
        perfil, sexo = self.v_perfil.get(), self.v_sexo.get()
        variantes = [r["sexo"] for r in self.bd.q("SELECT DISTINCT sexo FROM perfiles WHERE nombre=?",
                                                  (perfil,))]
        self.cb_sexo.configure(values=variantes or [AMBOS])
        if sexo not in variantes:
            sexo = AMBOS
            self.v_sexo.set(sexo)
        self.tabla_u.limpiar()
        u = self.bd.umbrales_de_perfil(perfil, sexo)
        for signo, (nombre, unidad, origen) in SIGNOS.items():
            f = u.get(signo)
            if not f:
                continue
            self.tabla_u.agregar(
                list(SIGNOS).index(signo),
                (f"{nombre} ({unidad})", "la pulsera" if origen == "pulsera" else "a mano",
                 f"{f['normal_min']:g} a {f['normal_max']:g}",
                 f"{f['vig_min']:g} a {f['vig_max']:g}",
                 f"menos de {f['vig_min']:g} o más de {f['vig_max']:g}"))

    def _signo_seleccionado(self):
        i = self.tabla_u.seleccion()
        if i is None:
            messagebox.showinfo("Sin selección", "Elija un signo de la tabla.", parent=self.app)
            return None
        return list(SIGNOS)[i]

    def _editar_umbral(self):
        signo = self._signo_seleccionado()
        if not signo:
            return
        perfil, sexo = self.v_perfil.get(), self.v_sexo.get()
        fila = self.bd.umbrales_de_perfil(perfil, sexo)[signo]
        d = DialogoUmbral(self.app, perfil, sexo, signo, fila)
        self.app.wait_window(d)
        if d.resultado:
            self._cargar_umbrales()
            self.app.refrescar_umbrales()
            self.app.aviso(f"Umbral de {SIGNOS[signo][0].lower()} actualizado para el perfil {perfil}.")

    def _crear_variante(self):
        perfil = self.v_perfil.get()
        d = DialogoBase(self.app, "Crear variante por sexo", self.fam)
        d.subtitulo(f"Copia los umbrales del perfil {perfil} para un sexo, de modo que el centro pueda "
                    "ajustarlos sin tocar el perfil general.")
        g = tk.Frame(d.cuerpo, bg=BG)
        g.pack(fill=tk.BOTH, expand=True)
        v_sexo, _ = fila_campo(g, self.fam, "Sexo", 0, 18, SEXOS[0], opciones=SEXOS)
        v_quien, _ = fila_campo(g, self.fam, "Autorizado por *", 1, 24, self.app.usuario)

        def guardar():
            if len(v_quien.get().strip()) < 3:
                return d.error("Escriba quién autoriza la creación de la variante.")
            n = self.bd.crear_variante_sexo(perfil, v_sexo.get(), v_quien.get().strip())
            self.app.usuario = v_quien.get().strip()
            d.resultado = n
            d.destroy()

        d.botones("Crear", guardar)
        d.centrar()
        self.app.wait_window(d)
        if d.resultado:
            self._cargar_umbrales()
            self.app.aviso(f"Variante creada con {d.resultado} signos. Ahora puede ajustar cada valor.")

    # ---- parámetros ----
    def _construir_parametros(self):
        c = tarjeta(self.t_param)
        c.pack(fill=tk.BOTH, expand=True)
        titulo_panel(c, "Parámetros de alerta del centro", self.fam,
                     "doble clic sobre un parámetro para cambiarlo")
        self.tabla_p = Tabla(c, [("clave", "Parámetro", 280), ("valor", "Valor", 110, "center"),
                                 ("desc", "Qué controla", 560), ("act", "Último cambio", 190)],
                             self.fam, alto=11, al_doble=self._editar_param)
        self.tabla_p.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 10))
        f = tk.Frame(c, bg=CARD)
        f.pack(fill=tk.X, padx=14, pady=(0, 14))
        boton(f, "Cambiar parámetro", self._editar_param, self.fam).pack(side=tk.LEFT)
        tk.Label(f, text="Los cambios se aplican de inmediato al monitoreo en curso. Los parámetros mqtt_ son los "
                         "de la conexión con el hub.",
                 bg=CARD, fg=MUTED, font=(self.fam, 9)).pack(side=tk.LEFT, padx=12)

    def _cargar_parametros(self):
        self.tabla_p.limpiar()
        self.claves = []
        for i, f in enumerate(self.bd.listar_config()):
            self.claves.append(f["clave"])
            if f["unidad"] == "clave":
                valor = "••••••" if f["valor"] else "(vacía)"
            else:
                valor = f"{f['valor']} {'' if f['unidad'] == 'puerto' else (f['unidad'] or '')}".strip()
            self.tabla_p.agregar(i, (f["clave"], valor,
                                     f["descripcion"], f"{(f['actualizado'] or '')[:16]} · "
                                                       f"{f['actualizado_por'] or ''}"))

    def _editar_param(self):
        i = self.tabla_p.seleccion()
        if i is None:
            messagebox.showinfo("Sin selección", "Elija un parámetro de la tabla.", parent=self.app)
            return
        clave = self.claves[i]
        f = self.bd.q1("SELECT * FROM config WHERE clave=?", (clave,))
        d = DialogoBase(self.app, f"Parámetro: {clave}", self.fam)
        d.subtitulo(f["descripcion"] or "")
        g = tk.Frame(d.cuerpo, bg=BG)
        g.pack(fill=tk.BOTH, expand=True)
        unidad = "texto" if f["unidad"] in (None, "", "clave") else f["unidad"]
        v_val, w_val = fila_campo(g, self.fam, f"Valor ({unidad})", 0, 18, f["valor"])
        if f["unidad"] == "clave":
            w_val.configure(show="•")
        v_quien, _ = fila_campo(g, self.fam, "Autorizado por *", 1, 24, self.app.usuario)

        def guardar():
            if len(v_quien.get().strip()) < 3:
                return d.error("Escriba quién autoriza el cambio.")
            valor = v_val.get().strip()
            if f["unidad"] in ("s", "%", "min", "puerto"):
                try:
                    n = float(valor)
                except ValueError:
                    return d.error("Este parámetro debe ser un número.")
                if n <= 0:
                    return d.error("El valor debe ser mayor que cero.")
                valor = int(n) if n == int(n) else n
            self.bd.fijar_config(clave, valor, v_quien.get().strip())
            self.app.usuario = v_quien.get().strip()
            d.resultado = valor
            d.destroy()

        d.botones("Guardar", guardar)
        d.centrar()
        self.app.wait_window(d)
        if d.resultado is not None:
            self._cargar_parametros()
            self.app.aplicar_configuracion()
            self.app.aviso(f"Parámetro {clave} actualizado" + ("." if f["unidad"] == "clave" else f" a {d.resultado}."))

    # ---- auditoría ----
    def _construir_auditoria(self):
        c = tarjeta(self.t_audit)
        c.pack(fill=tk.BOTH, expand=True)
        titulo_panel(c, "Auditoría del sistema", self.fam,
                     "quién hizo qué y cuándo: ingresos, altas, cambios de pulsera y de umbrales")
        self.tabla_a = Tabla(c, [("hora", "Fecha y hora", 160), ("usr", "Usuario", 170),
                                 ("acc", "Acción", 230), ("det", "Detalle", 620)], self.fam, alto=16)
        self.tabla_a.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 10))
        f = tk.Frame(c, bg=CARD)
        f.pack(fill=tk.X, padx=14, pady=(0, 14))
        boton_claro(f, "Actualizar", self._cargar_auditoria, self.fam).pack(side=tk.LEFT)
        boton_claro(f, "Exportar a CSV", self._exportar, self.fam).pack(side=tk.LEFT, padx=8)

    def _cargar_auditoria(self):
        self.tabla_a.limpiar()
        for f in self.bd.listar_auditoria(400):
            self.tabla_a.agregar(f["id"], (f["hora"], f["usuario"], f["accion"], f["detalle"] or ""))

    def _exportar(self):
        import csv
        import os
        ruta = os.path.join(self.app.carpeta_datos, "auditoria.csv")
        os.makedirs(self.app.carpeta_datos, exist_ok=True)
        with open(ruta, "w", newline="", encoding="utf-8") as fh:
            esc = csv.writer(fh)
            esc.writerow(["hora", "usuario", "accion", "entidad", "detalle"])
            for f in self.bd.listar_auditoria(5000):
                esc.writerow([f["hora"], f["usuario"], f["accion"], f["entidad"], f["detalle"]])
        self.app.aviso(f"Auditoría exportada a {ruta}")

    def refrescar(self):
        perfiles = [r["nombre"] for r in self.bd.q("SELECT DISTINCT nombre FROM perfiles"
                                                   " ORDER BY meses_min")]
        self.cb_perfil.configure(values=perfiles)
        if self.v_perfil.get() not in perfiles:
            self.v_perfil.set("Adulto" if "Adulto" in perfiles else perfiles[0])
        self._cargar_umbrales()
        self._cargar_parametros()
        self._cargar_auditoria()
