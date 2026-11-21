"""
Paleta y piezas de interfaz que comparten todas las pantallas.

Está aparte para que la ventana de monitoreo, la de pacientes y la de configuración
se vean como un solo programa y no haya que repetir colores en cada archivo.
"""

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# Paleta
BG = "#EEF2F6"
CARD = "#FFFFFF"
NAVY = "#0F2A43"
TEXTO = "#16222E"
MUTED = "#5B6B7A"
BORDE = "#D5DEE7"
PRIMARIO = "#0B6BCB"
PRIMARIO_HOVER = "#0956A5"
OK, OK_BG = "#1B8A5A", "#E3F4EB"
WARN, WARN_BG = "#B26A00", "#FFF1D6"
ALERTA, ALERTA_BG = "#C62828", "#FDE7E7"
GRIS, GRIS_BG = "#6B7B8A", "#E6EBF0"

COLOR_ESTADO_PULSERA = {
    "Disponible": (OK, OK_BG),
    "En uso": (PRIMARIO, "#E4F0FB"),
    "Cargando": (WARN, WARN_BG),
    "Fuera de servicio": (ALERTA, ALERTA_BG),
}


def elegir_familia(root):
    """La primera tipografía disponible de la lista, para que se vea igual en Windows y Linux."""
    disponibles = set(tkfont.families(root))
    for f in ("Segoe UI", "Inter", "Noto Sans", "DejaVu Sans", "Helvetica", "Arial"):
        if f in disponibles:
            return f
    return "TkDefaultFont"


def boton(master, texto, comando, fam, color=PRIMARIO, fg="white", activo=PRIMARIO_HOVER, **kw):
    return tk.Button(master, text=texto, command=comando, bg=color, fg=fg,
                     activebackground=activo, activeforeground=fg, relief=tk.FLAT, bd=0,
                     padx=13, pady=5, font=(fam, 10, "bold"), cursor="hand2", **kw)


def boton_claro(master, texto, comando, fam, **kw):
    return boton(master, texto, comando, fam, color=GRIS_BG, fg=TEXTO, activo="#D3DAE1", **kw)


def tarjeta(master, **kw):
    return tk.Frame(master, bg=CARD, highlightthickness=1, highlightbackground=BORDE,
                    highlightcolor=BORDE, **kw)


def titulo_panel(master, texto, fam, sub=""):
    cab = tk.Frame(master, bg=CARD)
    cab.pack(fill=tk.X, padx=14, pady=(12, 6))
    tk.Label(cab, text=texto, bg=CARD, fg=TEXTO, font=(fam, 12, "bold")).pack(side=tk.LEFT)
    if sub:
        tk.Label(cab, text=sub, bg=CARD, fg=MUTED, font=(fam, 9)).pack(side=tk.LEFT, padx=(10, 0))
    return cab


def estilos_ttk(root, fam):
    est = ttk.Style(root)
    try:
        est.theme_use("clam")
    except tk.TclError:
        pass
    est.configure("TCombobox", fieldbackground="white", background="white",
                  foreground=TEXTO, arrowcolor=NAVY, padding=4)
    est.map("TCombobox", fieldbackground=[("readonly", "white"), ("disabled", "#C9D3DD")],
            foreground=[("disabled", MUTED)])
    est.configure("Tabla.Treeview", background="white", fieldbackground="white", foreground=TEXTO,
                  rowheight=27, font=(fam, 10), borderwidth=0)
    est.configure("Tabla.Treeview.Heading", background="#E7EDF3", foreground=TEXTO,
                  font=(fam, 10, "bold"), relief="flat", padding=5)
    est.map("Tabla.Treeview", background=[("selected", "#CFE3F7")], foreground=[("selected", TEXTO)])
    est.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(8, 6, 8, 0))
    est.configure("TNotebook.Tab", background="#DCE4EC", foreground=TEXTO, padding=(18, 8),
                  font=(fam, 10, "bold"))
    est.map("TNotebook.Tab", background=[("selected", CARD)], foreground=[("selected", PRIMARIO)])
    root.option_add("*TCombobox*Listbox.font", (fam, 10))
    return est


class Tabla(tk.Frame):
    """Treeview con barra de desplazamiento y colores por fila."""

    def __init__(self, master, columnas, fam, alto=12, al_elegir=None, al_doble=None):
        super().__init__(master, bg=CARD)
        self.fam = fam
        ids = [c[0] for c in columnas]
        self.tree = ttk.Treeview(self, columns=ids, show="headings", height=alto, style="Tabla.Treeview",
                                 selectmode="browse")
        for i, (cid, titulo, ancho, *resto) in enumerate(columnas):
            self.tree.heading(cid, text=titulo)
            # la última columna se estira para que la tabla ocupe todo el ancho disponible
            self.tree.column(cid, width=ancho, anchor=(resto[0] if resto else "w"),
                             stretch=(i == len(columnas) - 1))
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.tag_configure("alerta", background=ALERTA_BG)
        self.tree.tag_configure("aviso", background=WARN_BG)
        self.tree.tag_configure("ok", background=OK_BG)
        self.tree.tag_configure("gris", foreground=MUTED)
        if al_elegir:
            self.tree.bind("<<TreeviewSelect>>", lambda e: al_elegir())
        if al_doble:
            self.tree.bind("<Double-1>", lambda e: al_doble())

    def limpiar(self):
        for i in self.tree.get_children():
            self.tree.delete(i)

    def agregar(self, iid, valores, tags=()):
        self.tree.insert("", tk.END, iid=str(iid), values=valores, tags=tags)

    def seleccion(self):
        s = self.tree.selection()
        return int(s[0]) if s else None

    def seleccionar(self, iid):
        try:
            self.tree.selection_set(str(iid))
            self.tree.see(str(iid))
        except tk.TclError:
            pass


class DialogoBase(tk.Toplevel):
    """Ventana modal con cuerpo, pie de botones y mensaje de error."""

    def __init__(self, padre, titulo, fam, ancho=560):
        super().__init__(padre)
        self.fam = fam
        self.resultado = None
        self.title(titulo)
        self.configure(bg=BG)
        self.transient(padre)
        self.resizable(False, False)
        self.marco = tk.Frame(self, bg=BG, padx=20, pady=16)
        self.marco.pack(fill=tk.BOTH, expand=True)
        tk.Label(self.marco, text=titulo, bg=BG, fg=TEXTO, font=(fam, 13, "bold")).pack(anchor="w")
        self.sub = tk.Label(self.marco, text="", bg=BG, fg=MUTED, font=(fam, 9), justify="left",
                            wraplength=ancho)
        self.sub.pack(anchor="w", pady=(2, 10))
        self.cuerpo = tk.Frame(self.marco, bg=BG)
        self.cuerpo.pack(fill=tk.BOTH, expand=True)
        self.lbl_error = tk.Label(self.marco, text="", bg=BG, fg=ALERTA, font=(fam, 9, "bold"),
                                  justify="left", wraplength=ancho)
        self.lbl_error.pack(anchor="w", pady=(8, 0))
        self.pie = tk.Frame(self.marco, bg=BG)
        self.pie.pack(fill=tk.X, pady=(12, 0))

    def subtitulo(self, texto):
        self.sub.configure(text=texto)

    def error(self, texto):
        self.lbl_error.configure(text=texto)

    def botones(self, texto_ok, al_guardar, texto_cancelar="Cancelar"):
        boton_claro(self.pie, texto_cancelar, self.destroy, self.fam).pack(side=tk.RIGHT)
        boton(self.pie, texto_ok, al_guardar, self.fam).pack(side=tk.RIGHT, padx=(0, 8))
        self.bind("<Escape>", lambda e: self.destroy())

    def centrar(self):
        self.update_idletasks()
        p = self.master
        x = p.winfo_rootx() + (p.winfo_width() - self.winfo_width()) // 2
        y = p.winfo_rooty() + max(30, (p.winfo_height() - self.winfo_height()) // 3)
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.grab_set()


def fila_campo(master, fam, etiqueta, fila, ancho=30, valor="", ayuda="", opciones=None, col=0):
    """Etiqueta + entrada (o combo) en una grilla. Devuelve la variable y el widget."""
    tk.Label(master, text=etiqueta, bg=BG, fg=TEXTO, font=(fam, 10)).grid(
        row=fila, column=col, sticky="w", pady=4, padx=(0, 8))
    var = tk.StringVar(value=valor)
    if opciones is None:
        w = tk.Entry(master, textvariable=var, font=(fam, 10), width=ancho, relief=tk.FLAT,
                     highlightthickness=1, highlightbackground=BORDE, highlightcolor=PRIMARIO, bg="white")
    else:
        w = ttk.Combobox(master, textvariable=var, values=opciones, state="readonly",
                         font=(fam, 10), width=ancho - 2)
    w.grid(row=fila, column=col + 1, sticky="w", pady=4)
    if ayuda:
        tk.Label(master, text=ayuda, bg=BG, fg=MUTED, font=(fam, 8)).grid(
            row=fila, column=col + 2, sticky="w", padx=(8, 0))
    return var, w
