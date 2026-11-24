// Almacenamiento permanente en el navegador (IndexedDB).
//
// Hace el papel del disco en la versión de escritorio:
//   - "base":       el archivo vitalcare.db completo (pacientes, pulseras, umbrales, auditoría...)
//   - "filas":      cada renglón de los CSV de cada sesión, escrito en el momento en que llega
//   - "sesiones":   la lista de sesiones guardadas (la "carpeta datos_sesion")
//   - "evidencias": las capturas de pantalla (F8)
// Todo queda en el navegador de esta computadora y sobrevive a recargas y apagones.

const NOMBRE = "vitalcare";
const VERSION = 1;

function promesa(req) {
  return new Promise((ok, mal) => { req.onsuccess = () => ok(req.result); req.onerror = () => mal(req.error); });
}

export class Almacen {
  static async abrir() {
    const req = indexedDB.open(NOMBRE, VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains("base")) db.createObjectStore("base");
      if (!db.objectStoreNames.contains("filas")) {
        const s = db.createObjectStore("filas", { autoIncrement: true });
        s.createIndex("marca_archivo", ["marca", "archivo"]);
        s.createIndex("marca", "marca");
      }
      if (!db.objectStoreNames.contains("sesiones")) db.createObjectStore("sesiones", { keyPath: "marca" });
      if (!db.objectStoreNames.contains("evidencias")) db.createObjectStore("evidencias", { keyPath: "nombre" });
    };
    const a = new Almacen();
    a.db = await promesa(req);
    a._guardando = null;
    a._pendiente = null;
    return a;
  }

  _tx(stores, modo = "readonly") { return this.db.transaction(stores, modo); }

  // ---- archivo de la base de datos ----
  async leerBase() { return (await promesa(this._tx("base").objectStore("base").get("vitalcare.db"))) || null; }

  /** Guarda la base. Si llegan varias escrituras seguidas, se guarda la última sin perder ninguna. */
  guardarBase(bytes) {
    this._pendiente = bytes;
    if (this._guardando) return this._guardando;
    this._guardando = (async () => {
      while (this._pendiente) {
        const b = this._pendiente;
        this._pendiente = null;
        const tx = this._tx("base", "readwrite");
        tx.objectStore("base").put(b, "vitalcare.db");
        await new Promise((ok, mal) => { tx.oncomplete = ok; tx.onerror = () => mal(tx.error); });
      }
      this._guardando = null;
    })();
    return this._guardando;
  }

  async borrarTodo() {
    const tx = this._tx(["base", "filas", "sesiones", "evidencias"], "readwrite");
    ["base", "filas", "sesiones", "evidencias"].forEach((s) => tx.objectStore(s).clear());
    await new Promise((ok, mal) => { tx.oncomplete = ok; tx.onerror = () => mal(tx.error); });
  }

  // ---- archivos de sesión (CSV) ----
  registrarSesion(info) {
    this._tx("sesiones", "readwrite").objectStore("sesiones").put(info);
  }

  actualizarSesion(marca, cambios) {
    const tx = this._tx("sesiones", "readwrite");
    const s = tx.objectStore("sesiones");
    const r = s.get(marca);
    r.onsuccess = () => { if (r.result) s.put({ ...r.result, ...cambios }); };
  }

  agregarFila(marca, archivo, linea) {
    this._tx("filas", "readwrite").objectStore("filas").add({ marca, archivo, linea });
  }

  async listarSesiones() {
    const todas = await promesa(this._tx("sesiones").objectStore("sesiones").getAll());
    return todas.sort((a, b) => (a.marca < b.marca ? 1 : -1));
  }

  async lineas(marca, archivo) {
    const idx = this._tx("filas").objectStore("filas").index("marca_archivo");
    const filas = await promesa(idx.getAll(IDBKeyRange.only([marca, archivo])));
    return filas.map((f) => f.linea);
  }

  async contarFilas(marca, archivo) {
    const idx = this._tx("filas").objectStore("filas").index("marca_archivo");
    return promesa(idx.count(IDBKeyRange.only([marca, archivo])));
  }

  // ---- evidencias ----
  guardarEvidencia(nombre, blob) {
    this._tx("evidencias", "readwrite").objectStore("evidencias").put({ nombre, blob, hora: Date.now() });
  }

  async listarEvidencias() { return promesa(this._tx("evidencias").objectStore("evidencias").getAll()); }
}

/** Descarga un archivo al equipo (queda en la carpeta Descargas). */
export function descargar(nombre, contenido, tipo = "text/plain;charset=utf-8") {
  const blob = contenido instanceof Blob ? contenido : new Blob([contenido], { type: tipo });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = nombre;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 2000);
}
