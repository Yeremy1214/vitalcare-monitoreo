"""
Pruebas automáticas del testbed. No necesitan hardware, Bluetooth ni internet.

    python pruebas_testbed.py

Revisa la lógica de umbrales, el conteo de paquetes perdidos, el resumen, el CSV, el
simulador, la recepción por UDP (con un emisor local) y por el hub MQTT (con un broker de mentira). Si hay pantalla disponible,
también abre la ventana unos segundos y comprueba que no falle.
"""

import asyncio
import csv
import json
import os
import socket
import sys
import tempfile
import time
import traceback

from logica import (
    ContadorSeq,
    estimar_autonomia_min,
    formato_autonomia,
    Sesion,
    clasificar,
    estado_global,
    formatear_resumen,
    nivel_bateria,
)
from datos import AMBOS, BaseDatos, meses_de_edad
from registro import RegistradorCSV
from transportes import ESCENARIOS, MQTTTransport, SimuladorTransport, UDPTransport, despachar_json

RESULTADOS = []


def prueba(funcion):
    RESULTADOS.append(funcion)
    return funcion


# ----------------------------------------------------------------------
# Lógica
# ----------------------------------------------------------------------


@prueba
def clasifica_signos_vitales():
    assert clasificar("hr", 75) == (0, None)
    assert clasificar("hr", 60) == (0, None) and clasificar("hr", 100) == (0, None)
    assert clasificar("hr", 110) == (1, "alta")
    assert clasificar("hr", 55) == (1, "baja")
    assert clasificar("hr", 132) == (2, "alta")
    assert clasificar("hr", 44) == (2, "baja")
    assert clasificar("spo2", 97) == (0, None)
    assert clasificar("spo2", 93) == (1, "baja")
    assert clasificar("spo2", 91) == (2, "baja")
    assert clasificar("temp", 36.7) == (0, None)
    assert clasificar("temp", 37.8) == (1, "alta")
    assert clasificar("temp", 38.4) == (2, "alta")
    assert clasificar("temp", 34.5) == (2, "baja")


@prueba
def valores_invalidos_no_rompen():
    for malo in (None, "abc", float("nan"), float("inf"), True):
        assert clasificar("hr", malo) == (None, None), malo
    assert clasificar("hr", "80") == (0, None)  # un número como texto sí se acepta
    assert nivel_bateria(None) is None


@prueba
def estado_global_toma_el_peor():
    assert estado_global([0, 1, None]) == 1
    assert estado_global([0, 2, 1]) == 2
    assert estado_global([None, None]) is None


@prueba
def bateria_por_niveles():
    assert nivel_bateria(80) == 0
    assert nivel_bateria(29) == 1
    assert nivel_bateria(10) == 2


@prueba
def contador_de_paquetes_perdidos():
    c = ContadorSeq()
    for s in (1, 2, 3, 7, 8):  # faltan 4, 5 y 6
        c.registrar(s)
    assert c.recibidos == 5 and c.perdidos == 3, (c.recibidos, c.perdidos)
    assert c.faltantes == {4, 5, 6} and c.detectados == 3
    assert abs(c.porcentaje_perdida - 37.5) < 1e-9
    c.registrar(8)  # duplicado
    assert c.duplicados == 1 and c.recibidos == 5
    assert c.registrar("x") == 0  # seq inválido se ignora
    for s in range(9, 60):
        c.registrar(s)
    c.registrar(1)  # el seq retrocede mucho: la pulsera se reinició
    assert c.reinicios == 1
    assert c.irrecuperables == {4, 5, 6}  # lo anterior ya no se puede pedir


@prueba
def resumen_de_sesion():
    s = Sesion()
    t0 = time.time()
    paquetes = [
        {"id": "W01", "seq": 1, "hr": 70, "spo2": 98, "temp": 36.6, "batt": 90, "st": "OK"},
        {"id": "W01", "seq": 2, "hr": 130, "spo2": 91, "temp": 36.8, "batt": 89, "st": "OK"},
        {"id": "W01", "seq": 4, "hr": 80, "spo2": 97, "temp": 38.5, "batt": 88, "st": "OK"},
    ]
    for i, p in enumerate(paquetes):
        s.agregar_telemetria(p, t0 + i * 2)
    s.agregar_evento({"id": "W01", "seq": 5, "ev": "FALL", "sev": "HIGH"}, t0 + 7)
    s.alerta_generada()
    s.alerta_atendida(12.5)
    r = s.resumen()
    assert r["paquetes_perdidos"] == 1 and r["paquetes_recibidos"] == 4
    assert r["variables"]["hr"]["min"] == 70 and r["variables"]["hr"]["max"] == 130
    assert abs(r["variables"]["hr"]["media"] - 93.3333) < 1e-3
    assert round(r["variables"]["hr"]["pct_fuera_rango"]) == 33  # 1 de 3 fuera de normal
    assert r["eventos"] == {"FALL": 1}
    assert r["alertas_generadas"] == 1 and r["alertas_atendidas"] == 1
    assert r["respuesta_media_s"] == 12.5
    assert r["bateria_inicial"] == 90 and r["bateria_final"] == 88
    texto = formatear_resumen(r, "Simulador", t0)
    assert "RESUMEN DE SESIÓN" in texto and "FALL: 1" in texto


@prueba
def resumen_de_sesion_vacia():
    r = Sesion().resumen()
    assert r["paquetes_recibidos"] == 0 and r["variables"]["hr"]["media"] is None
    assert "--" in formatear_resumen(r)


# ----------------------------------------------------------------------
# CSV
# ----------------------------------------------------------------------


@prueba
def csv_escribe_y_deja_de_escribir_al_cerrar():
    with tempfile.TemporaryDirectory() as carpeta:
        reg = RegistradorCSV(carpeta)
        reg.registrar_telemetria({"id": "W01", "seq": 1, "ts": 10, "hr": 72, "spo2": 98, "temp": 36.7, "batt": 90, "st": "OK"})
        reg.registrar_evento({"id": "W01", "seq": 2, "ts": 20, "ev": "SOS", "sev": "HIGH"})
        reg.registrar_alerta("GENERADA", "DISPOSITIVO", "SOS", "detalle", 2)
        cierre = {"resolucion": "Falsa alarma", "accion_realizada": "Presionó el botón sin querer",
                  "atendido_por": "Enf. Pérez", "indicacion_medica": False, "medico": ""}
        reg.registrar_alerta("CERRADA", "DISPOSITIVO", "SOS", "detalle", 2, 8.4, cierre)
        reg.registrar_hueco(5, "DETECTADO", "faltante")
        reg.registrar_hueco(5, "RECUPERADO", "2 s")
        reg.cerrar()
        reg.registrar_telemetria({"id": "W01", "seq": 3})  # no debe fallar ni escribir
        with open(reg.ruta_telemetria, encoding="utf-8") as f:
            filas = f.read().splitlines()
        assert len(filas) == 2 and filas[0].startswith("hora_local,id,seq"), filas
        assert filas[1].split(",")[2:] == ["1", "10", "72", "98", "36.7", "90", "OK"]
        with open(reg.ruta_eventos, encoding="utf-8") as f:
            assert len(f.read().splitlines()) == 2
        with open(reg.ruta_alertas, encoding="utf-8", newline="") as f:
            filas_al = list(csv.reader(f))
        assert len(filas_al) == 3 and filas_al[0][-5:] == [
            "resolucion", "accion_realizada", "atendido_por", "indicacion_medica", "medico"]
        assert filas_al[2][1] == "CERRADA" and filas_al[2][6] == "8.4"
        assert filas_al[2][7:] == ["Falsa alarma", "Presionó el botón sin querer", "Enf. Pérez", "NO", ""]
        with open(reg.ruta_huecos, encoding="utf-8") as f:
            assert [l.split(",")[2] for l in f.read().splitlines()[1:]] == ["DETECTADO", "RECUPERADO"]


# ----------------------------------------------------------------------
# Transportes
# ----------------------------------------------------------------------


@prueba
def despachar_json_clasifica_y_descarta_basura():
    tel, ev = [], []
    n = despachar_json(
        b'{"id":"W01","seq":1,"hr":70}\n{"id":"W01","seq":2,"ev":"FALL","sev":"HIGH"}\nbasura\n\n[1,2]\n',
        tel.append, ev.append,
    )
    assert n == 2 and len(tel) == 1 and len(ev) == 1
    assert despachar_json(b"\xff\xfe", tel.append, ev.append) == 0  # bytes que no son UTF-8


async def _correr_simulador(sim, segundos, tras=None):
    tel, ev = [], []
    tarea = asyncio.create_task(sim.recibir_paquetes(tel.append, ev.append))
    if tras:
        await asyncio.sleep(0.12)
        tras(sim)
    await asyncio.sleep(segundos)
    await sim.desconectar()
    await asyncio.wait_for(tarea, 2)
    return tel, ev


@prueba
def simulador_formato_y_eventos_inmediatos():
    sim = SimuladorTransport(intervalo_s=0.05)
    tel, ev = asyncio.run(_correr_simulador(sim, 0.5, tras=lambda s: s.disparar_evento("FALL")))
    assert len(tel) >= 5, len(tel)
    claves = {"id", "seq", "ts", "hr", "spo2", "temp", "batt", "st"}
    assert all(set(p) == claves for p in tel)
    assert len(ev) == 1 and ev[0]["ev"] == "FALL" and ev[0]["sev"] == "HIGH"
    seqs = [p["seq"] for p in tel] + [ev[0]["seq"]]
    assert len(set(seqs)) == len(seqs)  # el contador se comparte y no repite


@prueba
def simulador_escenarios_llegan_al_objetivo():
    sim = SimuladorTransport(intervalo_s=0.03)
    sim.fijar_escenario("Taquicardia")
    tel, _ = asyncio.run(_correr_simulador(sim, 1.2))
    ultimos = [p["hr"] for p in tel[-5:]]
    assert min(ultimos) >= 115, ultimos  # objetivo 132
    for nombre in ESCENARIOS:
        sim.fijar_escenario(nombre)  # todos los nombres son válidos


@prueba
def simulador_perdida_genera_huecos_de_seq():
    sim = SimuladorTransport(intervalo_s=0.02)
    sim.fijar_escenario("Señal inestable (pérdida de paquetes)")
    tel, _ = asyncio.run(_correr_simulador(sim, 1.5))
    c = ContadorSeq()
    for p in tel:
        c.registrar(p["seq"])
    assert c.perdidos > 0 and c.recibidos == len(tel), (c.perdidos, c.recibidos)


@prueba
def simulador_se_puede_detener_antes_de_arrancar():
    sim = SimuladorTransport(intervalo_s=0.05)
    sim._detenido = True
    tel, _ = asyncio.run(_correr_simulador(sim, 0.2))
    assert tel == []


async def _correr_udp():
    udp = UDPTransport(puerto=0, host="127.0.0.1")
    tel, ev = [], []
    tarea = asyncio.create_task(udp.recibir_paquetes(tel.append, ev.append))
    for _ in range(50):
        if udp.puerto_real:
            break
        await asyncio.sleep(0.02)
    assert udp.puerto_real, "el socket UDP no abrió"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    destino = ("127.0.0.1", udp.puerto_real)
    s.sendto(json.dumps({"id": "W01", "seq": 1, "ts": 1, "hr": 77, "spo2": 98, "temp": 36.7, "batt": 80, "st": "OK"}).encode(), destino)
    s.sendto(json.dumps({"id": "W01", "seq": 2, "ts": 2, "ev": "SOS", "sev": "HIGH"}).encode(), destino)
    s.sendto(b"esto no es json", destino)
    await asyncio.sleep(0.2)
    await udp.desconectar()
    await asyncio.wait_for(tarea, 2)
    s.close()
    return tel, ev


@prueba
def udp_recibe_telemetria_y_eventos():
    tel, ev = asyncio.run(_correr_udp())
    assert len(tel) == 1 and tel[0]["hr"] == 77
    assert len(ev) == 1 and ev[0]["ev"] == "SOS"


@prueba
def udp_puerto_ocupado_da_error_claro():
    async def main():
        a = UDPTransport(puerto=0, host="127.0.0.1")
        t = asyncio.create_task(a.recibir_paquetes(lambda p: None, lambda p: None))
        for _ in range(50):
            if a.puerto_real:
                break
            await asyncio.sleep(0.02)
        b = UDPTransport(puerto=a.puerto_real, host="127.0.0.1")
        try:
            await b.recibir_paquetes(lambda p: None, lambda p: None)
            raise AssertionError("debió fallar por puerto ocupado")
        except OSError:
            pass
        await a.desconectar()
        await t

    asyncio.run(main())


@prueba
def contador_recupera_faltantes_y_cuenta_la_perdida_real():
    c = ContadorSeq()
    for s in (1, 2, 5):
        c.registrar(s)
    assert c.nuevos == [3, 4] and c.perdidos == 2 and c.detectados == 2
    c.registrar(3)  # llega reenviado
    assert c.recuperado_ahora and c.recuperado_seq == 3 and c.recuperados == 1 and c.perdidos == 1
    c.marcar_irrecuperables([4])  # venció el plazo
    assert c.faltantes == set() and c.irrecuperables == {4} and c.perdidos == 1
    c.registrar(4)  # llega tarde, pero llega: la pérdida deja de serlo
    assert c.perdidos == 0 and c.recuperados == 2


@prueba
def sesion_coloca_el_paquete_recuperado_en_su_hora():
    s = Sesion()
    t0 = 1_000_000.0
    for seq, ts, hr in ((1, 0, 70), (2, 2000, 71), (4, 6000, 73)):  # falta el seq 3
        s.agregar_telemetria({"id": "W01", "seq": seq, "ts": ts, "hr": hr, "spo2": 97, "temp": 36.7, "batt": 90}, t0 + ts / 1000 + 0.1)
    s.agregar_telemetria({"id": "W01", "seq": 3, "ts": 4000, "hr": 72, "spo2": 97, "temp": 36.7, "batt": 90}, t0 + 20)
    assert s.hr == [70, 71, 72, 73], s.hr  # el 72 quedó entre el 71 y el 73
    assert s.t == sorted(s.t) and s.seq.perdidos == 0 and s.seq.recuperados == 1


@prueba
def autonomia_de_bateria():
    t0 = 0.0
    bajando = [(t0 + i * 60, 100 - i) for i in range(10)]  # 1 % por minuto
    minutos = estimar_autonomia_min(bajando)
    assert minutos is not None and abs(minutos - 91) < 1, minutos
    assert estimar_autonomia_min([(t0 + i * 60, 80) for i in range(10)]) is None  # no baja
    assert estimar_autonomia_min([(0, 90), (1, 90)]) is None  # pocos datos
    assert formato_autonomia(None) == "calculando…"
    assert formato_autonomia(45) == "~45 min" and formato_autonomia(130) == "~2 h 10 min"


@prueba
def cierre_de_alerta_exige_detalle_manual():
    try:
        from app_testbed import DialogoAtencion
    except Exception:  # noqa: BLE001 - sin tkinter instalado no se puede importar la ventana
        print("    (sin tkinter: se omite esta prueba)")
        return
    ok = {"resolucion": "Resuelta con indicación médica", "accion_realizada": "Se administró paracetamol",
          "atendido_por": "Enf. Pérez", "indicacion_medica": True, "medico": "Dr. Gómez"}
    assert DialogoAtencion.validar(ok) is None
    assert DialogoAtencion.validar({**ok, "resolucion": ""}) is not None
    assert DialogoAtencion.validar({**ok, "accion_realizada": "ok"}) is not None  # sin descripción real
    assert DialogoAtencion.validar({**ok, "atendido_por": ""}) is not None
    assert DialogoAtencion.validar({**ok, "medico": ""}) is not None  # dice que hubo indicación médica
    assert DialogoAtencion.validar({**ok, "indicacion_medica": False, "medico": ""}) is None


@prueba
def simulador_recupera_lo_perdido_con_reenvio():
    async def main():
        sim = SimuladorTransport(intervalo_s=0.02)
        sim.fijar_escenario("Señal inestable (pérdida de paquetes)")
        tel, ev = [], []
        tarea = asyncio.create_task(sim.recibir_paquetes(tel.append, ev.append))
        await asyncio.sleep(1.5)
        c = ContadorSeq()
        for p in tel:
            c.registrar(p["seq"])
        faltan = sorted(c.faltantes)
        assert faltan, "el escenario debía perder paquetes"
        assert sim.solicitar_reenvio(faltan) is True
        await asyncio.sleep(0.3)
        sim.fijar_escenario("Paciente estable")  # deja de perder para cerrar limpio
        for p in tel[len(tel) - 0:]:
            pass
        await sim.desconectar()
        await asyncio.wait_for(tarea, 2)
        c2 = ContadorSeq()
        for p in tel:
            c2.registrar(p["seq"])
        pendientes = [s for s in faltan if s in c2.faltantes]
        assert not pendientes, pendientes
        assert c2.recuperados >= len(faltan) - 1  # el último paquete puede no haber generado hueco aún

    asyncio.run(main())


@prueba
def simulador_sin_reenvio_lo_dice():
    sim = SimuladorTransport(intervalo_s=0.02)
    sim.fijar_escenario("Señal inestable SIN reenvío (pérdida real)")
    assert sim.solicitar_reenvio([1, 2]) is False
    assert UDPTransport(puerto=0).solicitar_reenvio([1]) is False  # UDP y BLE no tienen reenvío todavía


@prueba
def mqtt_recibe_por_el_hub_y_le_manda_ordenes():
    """La fuente Hub (MQTT) con un broker de mentira: no hace falta internet ni paho-mqtt instalado."""
    import threading
    import types

    publicados, clientes = [], []

    class ClienteFalso:
        def __init__(self, *a, **kw):
            self.on_connect = self.on_message = self.on_disconnect = None
            self.suscrito = None
            clientes.append(self)

        def username_pw_set(self, *a): pass
        def ws_set_options(self, **kw): pass
        def tls_set(self, *a, **kw): pass
        def reconnect_delay_set(self, *a): pass
        def connect_async(self, host, puerto, keepalive=60): self.destino = (host, puerto)
        def loop_start(self): threading.Timer(0.05, lambda: self.on_connect(self, None, None, 0)).start()
        def loop_stop(self): pass
        def disconnect(self): pass
        def is_connected(self): return True
        def subscribe(self, lista): self.suscrito = lista

        def publish(self, tema, texto, qos=0, retain=False):
            publicados.append((tema, json.loads(texto)))
            return types.SimpleNamespace(rc=0)

    falso = types.ModuleType("paho.mqtt.client")
    falso.Client = ClienteFalso
    falso.CallbackAPIVersion = types.SimpleNamespace(VERSION2=2)
    guardados = {k: sys.modules.get(k) for k in ("paho", "paho.mqtt", "paho.mqtt.client")}
    sys.modules["paho"] = types.ModuleType("paho")
    sys.modules["paho.mqtt"] = types.ModuleType("paho.mqtt")
    sys.modules["paho.mqtt"].client = falso
    sys.modules["paho"].mqtt = sys.modules["paho.mqtt"]
    sys.modules["paho.mqtt.client"] = falso
    try:
        hub, tel, ev = [], [], []
        t = MQTTTransport("broker.prueba", 1883, "pre", al_hub=hub.append)

        def mensaje(tema, datos):
            texto = datos if isinstance(datos, str) else json.dumps(datos)
            clientes[0].on_message(clientes[0], None, types.SimpleNamespace(topic=tema, payload=texto.encode()))

        async def main():
            tarea = asyncio.create_task(t.recibir_paquetes(tel.append, ev.append))
            await asyncio.sleep(0.2)
            assert clientes and clientes[0].destino == ("broker.prueba", 1883)
            assert ("pre/pulsera/+/telemetria", 1) in clientes[0].suscrito
            mensaje("pre/pulsera/W01/telemetria", {"id": "W01", "seq": 1, "ts": 1, "hr": 77, "spo2": 98,
                                                    "temp": 36.7, "batt": 80, "st": "OK"})
            mensaje("pre/pulsera/W01/evento", {"id": "W01", "seq": 2, "ts": 2, "ev": "SOS", "sev": "HIGH"})
            mensaje("pre/pulsera/W01/telemetria", "esto no es json")
            mensaje("pre/hub/H1/estado", "online")
            t.id_destino = "W01"
            assert t.solicitar_reenvio(list(range(5, 30))) is True
            assert t.enviar_comando("W01", {"cmd": "ack", "ev": "SOS", "seq": 2}) is True
            await t.desconectar()
            await asyncio.wait_for(tarea, 2)

        asyncio.run(main())
        assert len(tel) == 1 and tel[0]["hr"] == 77
        assert len(ev) == 1 and ev[0]["ev"] == "SOS"
        assert any(h.get("tipo") == "estado" and h.get("texto") == "online" for h in hub)
        reenvios = [m["seq"] for tema, m in publicados if m.get("cmd") == "resend"]
        assert reenvios == [list(range(5, 25)), list(range(25, 30))], reenvios  # en tandas de 20
        assert ("pre/pulsera/W01/comando", {"cmd": "ack", "ev": "SOS", "seq": 2}) in publicados
    finally:
        for k, v in guardados.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


# ----------------------------------------------------------------------
# Ventana (solo si hay pantalla)
# ----------------------------------------------------------------------


@prueba
def ventana_arranca_recibe_y_cierra_sin_errores():
    try:
        import tkinter

        raiz = tkinter.Tk()
        raiz.destroy()
    except Exception:  # noqa: BLE001
        print("    (sin pantalla disponible: se omite la prueba de la ventana)")
        return
    import app_testbed as A
    from app_testbed import DialogoAtencion

    errores = []
    tkinter.Tk.report_callback_exception = lambda self, e, v, tb: errores.append("".join(traceback.format_exception(e, v, tb)))
    with tempfile.TemporaryDirectory() as carpeta:
        app = A.Aplicacion(intervalo_sim=0.1, carpeta_datos=carpeta,
                           bd_ruta=os.path.join(carpeta, 'prueba.db'))
        tipos = set()

        def accion(t, fn):
            app.after(int(t * 1000), fn)

        cierre = {"resolucion": "Falsa alarma", "accion_realizada": "El paciente presionó sin querer",
                  "atendido_por": "Enf. Pérez", "indicacion_medica": False, "medico": ""}
        accion(0.8, lambda: app._demo_evento("FALL"))
        accion(1.0, lambda: app._demo_evento("SOS"))
        accion(1.2, lambda: (app.var_escenario.set("Señal inestable (pérdida de paquetes)"), app._aplicar_escenario()))
        accion(2.8, lambda: [t.cerrar(cierre) for t in list(app.tarjetas_alerta) if t.requiere_atencion and t.tipo in ("FALL", "SOS")])
        accion(3.0, lambda: (app.var_escenario.set("Batería baja (descarga rápida)"), app._aplicar_escenario()))
        accion(3.2, lambda: app._elegir_ventana(2))
        accion(7.5, lambda: tipos.update(t.tipo for t in app.tarjetas_alerta))
        accion(7.6, lambda: DialogoAtencion(app, next(t for t in app.tarjetas_alerta if t.tipo.startswith("BATERIA") and not t.atendida)).destroy())
        accion(8.0, app._detener)
        accion(8.2, app._conectar)
        accion(9.2, app._al_cerrar)
        app.mainloop()
        assert not errores, errores[0]
        assert any(f.startswith("telemetria_") for f in os.listdir(carpeta))
        assert app.sesion.paquetes_telemetria > 0
        assert {"BATERIA_BAJA", "BATERIA_CRITICA"} <= tipos, tipos
        assert any(f.startswith("huecos_") for f in os.listdir(carpeta))


# ----------------------------------------------------------------------
# Gestión de pacientes, pulseras y configuración del centro
# ----------------------------------------------------------------------


def _bd():
    carpeta = tempfile.mkdtemp()
    bd = BaseDatos(os.path.join(carpeta, "prueba.db"))
    bd.sembrar_demostracion("pruebas")
    return bd


@prueba
def perfil_clinico_sale_de_la_edad():
    bd = _bd()
    nino = bd.paciente_por_expediente("EXP-2026-0002")
    adulta = bd.paciente_por_expediente("EXP-2026-0001")
    assert nino["perfil"] == "Escolar", nino["perfil"]
    assert adulta["perfil"] == "Adulto mayor", adulta["perfil"]
    u_nino = bd.umbrales_de_paciente(nino["id"])
    u_adulta = bd.umbrales_de_paciente(adulta["id"])
    # un niño de 9 años tolera una frecuencia cardíaca más alta que una adulta mayor
    assert u_nino["hr"]["normal"][1] > u_adulta["hr"]["normal"][1]
    # y el sistema trae rangos de presión arterial distintos para cada uno
    assert u_nino["pas"]["normal"] != u_adulta["pas"]["normal"]
    bd.cerrar()


@prueba
def variante_por_sexo_y_ajuste_individual():
    bd = _bd()
    pid = bd.crear_paciente({"expediente": "E-9", "nombre": "Ana", "apellidos": "López",
                             "fecha_nac": "1990-05-05", "sexo": "Femenino"}, "pruebas")
    bd.crear_variante_sexo("Adulto", "Femenino", "supervisora")
    bd.fijar_umbral_perfil("Adulto", "Femenino", "hr", (62, 104, 52, 124), "supervisora")
    u = bd.umbrales_de_paciente(pid)
    assert u["hr"]["normal"] == (62, 104), u["hr"]
    assert "femenino" in u["hr"]["origen"]
    # el ajuste individual pesa más que el perfil
    bd.fijar_umbral_paciente(pid, "spo2", (92, 100, 88, 100), "EPOC conocida", "Dr. Gómez")
    u = bd.umbrales_de_paciente(pid)
    assert u["spo2"]["normal"] == (92, 100)
    assert u["spo2"]["origen"] == "ajuste individual"
    assert any("EPOC" in (a["detalle"] or "") for a in bd.listar_auditoria())
    bd.cerrar()


@prueba
def cambio_de_pulsera_conserva_el_hilo_del_paciente():
    bd = _bd()
    p = bd.paciente_por_expediente("EXP-2026-0001")
    assert bd.paciente_por_dispositivo("W01")["id"] == p["id"]
    w2 = bd.pulsera_por_dispositivo("W02")
    _, anterior = bd.asignar_pulsera(p["id"], w2["id"], "enf. Santos", "batería al 12 %")
    assert anterior == "VC-W-0001"
    # los datos nuevos son del mismo paciente, ahora por la otra pulsera
    assert bd.paciente_por_dispositivo("W02")["id"] == p["id"]
    assert bd.paciente_por_dispositivo("W01") is None
    assert bd.pulsera_por_dispositivo("W01")["estado"] == "Cargando"
    # el historial deja ver los dos tramos
    historial = bd.historial_asignaciones(pid=p["id"])
    assert len(historial) == 2 and historial[0]["fin"] is None
    bd.cerrar()


@prueba
def una_pulsera_no_se_asigna_a_dos_pacientes():
    bd = _bd()
    otro = bd.paciente_por_expediente("EXP-2026-0002")
    w1 = bd.pulsera_por_dispositivo("W01")
    try:
        bd.asignar_pulsera(otro["id"], w1["id"], "enf. Santos")
    except ValueError:
        bd.cerrar()
        return
    raise AssertionError("permitió asignar la misma pulsera a dos pacientes")


@prueba
def alta_libera_la_pulsera_y_protege_el_historial():
    bd = _bd()
    p = bd.paciente_por_expediente("EXP-2026-0001")
    liberada = bd.dar_alta(p["id"], "enf. Santos")
    assert liberada == "VC-W-0001"
    assert bd.pulsera_por_dispositivo("W01")["estado"] == "Disponible"
    assert bd.paciente(p["id"])["estado"] == "Alta"
    assert bd.paciente_por_dispositivo("W01") is None
    try:
        bd.eliminar_paciente(p["id"], "admin")
    except ValueError:
        bd.cerrar()
        return
    raise AssertionError("borró un paciente que tenía historial")


@prueba
def configuracion_del_centro_queda_auditada():
    bd = _bd()
    assert bd.config("escalamiento_s") == 60
    bd.fijar_config("escalamiento_s", 45, "supervisora Reyes")
    assert bd.config("escalamiento_s") == 45
    ultima = bd.listar_auditoria(1)[0]
    assert ultima["usuario"] == "supervisora Reyes"
    assert "60 → 45" in ultima["detalle"]
    bd.cerrar()


@prueba
def corte_de_energia_deja_la_sesion_abierta():
    bd = _bd()
    p = bd.paciente_por_expediente("EXP-2026-0001")
    sid = bd.abrir_sesion(p["id"], 1, "Simulador", "20261121_0100")
    bd.latido_sesion(sid)
    assert len(bd.sesiones_sin_cerrar()) == 1
    # al arrancar de nuevo se detecta y se registra el corte
    pendiente = bd.sesiones_sin_cerrar()[0]
    bd.registrar_evento_sistema("CORTE_DETECTADO", "prueba", pendiente["id"])
    bd.cerrar_sesion(pendiente["id"], limpio=False)
    assert bd.sesiones_sin_cerrar() == []
    assert bd.eventos_sistema(solo_pendientes=True)[0]["tipo"] == "CORTE_DETECTADO"
    bd.cerrar()


@prueba
def signos_manuales_se_comparan_con_el_rango_del_paciente():
    bd = _bd()
    p = bd.paciente_por_expediente("EXP-2026-0001")
    bd.registrar_medicion(p["id"], "pas", 165, "enf. Santos", "brazo derecho")
    bd.registrar_medicion(p["id"], "pad", 70, "enf. Santos")
    u = bd.umbrales_de_paciente(p["id"])
    umbrales = {"pas": {"normal": u["pas"]["normal"], "vigilancia": u["pas"]["vigilancia"]},
                "pad": {"normal": u["pad"]["normal"], "vigilancia": u["pad"]["vigilancia"]}}
    assert clasificar("pas", 165, umbrales)[0] == 2
    assert clasificar("pad", 70, umbrales)[0] == 0
    ultimas = bd.ultimas_mediciones(p["id"])
    assert ultimas["pas"]["valor"] == 165 and ultimas["pas"]["tomado_por"] == "enf. Santos"
    bd.cerrar()


@prueba
def simulador_corta_el_enlace_y_luego_recupera():
    sim = SimuladorTransport(intervalo_s=0.05)
    recibidos, eventos = [], []

    async def correr():
        tarea = asyncio.ensure_future(sim.recibir_paquetes(recibidos.append, eventos.append))
        await asyncio.sleep(0.25)
        sim.cortar(0.3)
        await asyncio.sleep(0.35)
        antes = len(recibidos)
        await asyncio.sleep(0.2)
        # durante el corte no llegó nada nuevo, pero la pulsera siguió numerando
        faltantes = [s for s in range(1, recibidos[-1]["seq"]) if s not in {p["seq"] for p in recibidos}]
        for seq in faltantes:
            sim.solicitar_reenvio([seq])
        await asyncio.sleep(0.3)
        await sim.desconectar()
        await tarea
        return antes, faltantes

    antes, faltantes = asyncio.new_event_loop().run_until_complete(correr())
    assert faltantes, "el corte debía dejar huecos de secuencia"
    llegados = {p["seq"] for p in recibidos}
    assert all(s in llegados for s in faltantes), "los paquetes del corte no se recuperaron"


@prueba
def ventana_con_paciente_no_mezcla_datos_de_otra_pulsera():
    """Con un paciente seleccionado, los paquetes de otra pulsera se descartan y se avisa."""
    try:
        import tkinter

        raiz = tkinter.Tk()
        raiz.destroy()
    except Exception:  # noqa: BLE001
        print("    (sin pantalla disponible: se omite la prueba de la ventana)")
        return
    import app_testbed as A

    with tempfile.TemporaryDirectory() as carpeta:
        app = A.Aplicacion(intervalo_sim=0.1, autoiniciar=False, carpeta_datos=carpeta,
                           bd_ruta=os.path.join(carpeta, "prueba.db"))
        try:
            paciente = app.bd.paciente_por_expediente("EXP-2026-0001")
            app.monitorear_paciente(paciente["id"])
            app.update()
            assert app.dispositivo_esperado == "W01"
            assert app.umbrales["hr"]["normal"] == (60.0, 100.0)
            propio = {"id": "W01", "seq": 1, "ts": 1000, "hr": 78, "spo2": 97, "temp": 36.6,
                      "batt": 90, "st": "OK"}
            ajeno = dict(propio, id="W03")
            app._manejar_telemetria(propio)
            app.update()
            assert app.sesion.paquetes_telemetria == 1
            app._manejar_telemetria(ajeno)
            app.update()
            assert app.sesion.paquetes_telemetria == 1, "aceptó datos de una pulsera ajena"
            assert any(t.tipo == "DISPOSITIVO_AJENO" for t in app.tarjetas_alerta)
            # al cambiar de pulsera, el paciente sigue y la gráfica queda marcada
            w3 = app.bd.pulsera_por_dispositivo("W03")
            _, anterior = app.bd.asignar_pulsera(paciente["id"], w3["id"], "enf. Santos")
            app.pulsera_cambiada(paciente["id"], {"pulsera_id": w3["id"], "serie": w3["serie"],
                                                  "dispositivo": "W03", "anterior": anterior,
                                                  "motivo": "Cambio por batería"})
            app.update()
            assert app.dispositivo_esperado == "W03"
            assert app.sesion.cambios and "VC-W-0003" in app.sesion.cambios[0][1]
            app._manejar_telemetria(dict(propio, id="W03", seq=1))
            app.update()
            assert app.sesion.paquetes_telemetria == 2, "no aceptó los datos de la pulsera nueva"
        finally:
            app._al_cerrar()


# ----------------------------------------------------------------------


def main():
    fallos = 0
    for f in RESULTADOS:
        try:
            f()
            print(f"  OK   {f.__name__}")
        except Exception:  # noqa: BLE001
            fallos += 1
            print(f"  FALLA {f.__name__}")
            traceback.print_exc()
    print(f"\n{len(RESULTADOS) - fallos} de {len(RESULTADOS)} pruebas correctas")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
