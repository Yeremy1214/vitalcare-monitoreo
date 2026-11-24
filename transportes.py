"""
Capa de transporte del testbed.

Esta capa separa "de dónde vienen los paquetes" de "qué hace el programa con ellos".
El testbed puede recibir los paquetes de cuatro fuentes, que se eligen desde la ventana:

  - SimuladorTransport: genera paquetes localmente, sin hardware (con escenarios y
                        botones para disparar caída / SOS en la demostración).
  - BLETransport:       se conecta por Bluetooth Low Energy a la pulsera real (bleak).
  - UDPTransport:       escucha datagramas JSON por WiFi (la ruta del receptor UDP).
  - MQTTTransport:      recibe la pulsera real a través del hub y el broker MQTT (paho-mqtt),
                        igual que la versión web.

Las cuatro entregan el mismo formato, acordado en Definicion_Formato_JSON_Bluetooth.docx:
  Telemetry (cada 2 s):
    {"id":"W01","seq":145,"ts":123456,"hr":78,"spo2":97,"temp":36.8,"batt":82,"st":"OK"}
  Event (inmediato):
    {"id":"W01","seq":146,"ts":125460,"ev":"FALL","sev":"HIGH"}
    {"id":"W01","seq":146,"ts":125460,"ev":"SOS","sev":"HIGH"}
"""

import asyncio
import json
import queue
import random
import time


class Transporte:
    """Interfaz común: cualquier transporte debe saber conectar y recibir paquetes."""

    async def conectar(self):
        raise NotImplementedError

    async def recibir_paquetes(self, on_telemetry, on_event):
        """Debe invocar on_telemetry(dict) y on_event(dict) según vayan llegando."""
        raise NotImplementedError

    async def desconectar(self):
        pass

    # Recuperación de paquetes perdidos. BLE (notify) y UDP no confirman la entrega, así que
    # recuperar un paquete exige que la pulsera guarde un historial y acepte un comando de
    # reenvío. Mientras el firmware no lo tenga, estas fuentes solo pueden DETECTAR la pérdida.
    soporta_reenvio = False

    def solicitar_reenvio(self, seqs):
        """Pide a la fuente reenviar los seq indicados. Devuelve True si la fuente lo soporta."""
        return False


def despachar_json(datos, on_telemetry, on_event):
    """
    Interpreta bytes/str con uno o varios JSON (uno por línea) y los entrega.
    Un paquete con la clave "ev" es un evento; cualquier otro es telemetría.
    Los paquetes corruptos se descartan sin propagar el error.
    """
    if isinstance(datos, (bytes, bytearray)):
        try:
            datos = bytes(datos).decode("utf-8")
        except UnicodeDecodeError:
            return 0
    entregados = 0
    for linea in datos.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        try:
            paquete = json.loads(linea)
        except json.JSONDecodeError:
            continue
        if not isinstance(paquete, dict):
            continue
        if "ev" in paquete:
            on_event(paquete)
        else:
            on_telemetry(paquete)
        entregados += 1
    return entregados


# ----------------------------------------------------------------------
# Simulador
# ----------------------------------------------------------------------

# Valores hacia los que tiende cada escenario. El simulador se acerca poco a poco,
# como pasaría con un paciente real, en lugar de dar saltos.
ESCENARIOS = {
    "Paciente estable": {"hr": 75, "spo2": 97, "temp": 36.7},
    "Fiebre": {"hr": 98, "spo2": 96, "temp": 38.4},
    "SpO₂ baja": {"hr": 96, "spo2": 91, "temp": 36.8},
    "Taquicardia": {"hr": 132, "spo2": 96, "temp": 36.9},
    "Bradicardia": {"hr": 44, "spo2": 97, "temp": 36.5},
    "Señal inestable (pérdida de paquetes)": {"hr": 78, "spo2": 97, "temp": 36.7, "perdida": 0.4},
    "Señal inestable SIN reenvío (pérdida real)": {"hr": 78, "spo2": 97, "temp": 36.7, "perdida": 0.4,
                                                    "sin_reenvio": True},
    "Batería baja (descarga rápida)": {"hr": 75, "spo2": 97, "temp": 36.7, "drenaje": 1.5},
}
ESCENARIO_BATERIA = "Batería baja (descarga rápida)"


class SimuladorTransport(Transporte):
    soporta_reenvio = True

    """
    Genera paquetes localmente, sin BLE ni hardware, con el mismo formato que usará
    la pulsera real. Sirve para desarrollar y demostrar el testbed mientras llega el
    sensor óptico nuevo (MAX30101).

    - hr, spo2 y temp tienden hacia los valores del escenario activo, con ruido.
    - temp ya viene con el offset +2.1 °C aplicado del lado del firmware.
    - batt baja lentamente con el tiempo.
    - cada `intervalo_s` se envía una Telemetry.
    - disparar_evento("FALL" | "SOS") envía un Event de inmediato (se puede llamar
      desde cualquier hilo). prob_evento_por_ciclo > 0 agrega eventos al azar.
    """

    def __init__(self, device_id="W01", intervalo_s=2.0, prob_evento_por_ciclo=0.0):
        self.device_id = device_id
        self.intervalo_s = intervalo_s
        self.prob_evento_por_ciclo = prob_evento_por_ciclo
        self._seq = 0
        self._hr = 75.0
        self._spo2 = 97.0
        self._temp = 36.7
        self._batt = 95.0
        self._t0 = time.monotonic()
        self._activo = False
        self._detenido = False
        self._pendientes = queue.SimpleQueue()
        self._reenvios = queue.SimpleQueue()
        self._historial = {}  # seq -> ("t" | "e", paquete): lo que la "pulsera" guarda para reenviar
        self.escenario = "Paciente estable"
        self._objetivo = dict(ESCENARIOS[self.escenario])
        self._corte_hasta = 0.0  # hasta cuándo dura una caída de enlace simulada

    async def conectar(self):
        self._activo = not self._detenido

    async def desconectar(self):
        self._detenido = True
        self._activo = False

    # ---- control desde la interfaz ----------------------------------------

    def fijar_escenario(self, nombre):
        if nombre not in ESCENARIOS:
            raise ValueError(f"Escenario desconocido: {nombre}")
        if nombre == ESCENARIO_BATERIA:
            self._batt = min(self._batt, 34.0)  # arranca cerca del primer umbral para la demostración
        elif self.escenario == ESCENARIO_BATERIA:
            self._batt = 100.0  # al salir del escenario se simula el cambio de pulsera
        self.escenario = nombre
        self._objetivo = dict(ESCENARIOS[nombre])
        # Con este escenario la "pulsera" no guarda historial: muestra qué pasa sin soporte de reenvío.
        self.soporta_reenvio = not self._objetivo.get("sin_reenvio", False)

    def solicitar_reenvio(self, seqs):
        if not self.soporta_reenvio:
            return False
        for s in seqs:
            self._reenvios.put(int(s))
        return True

    def cambiar_pulsera(self, device_id):
        """Simula que al paciente le pusieron otra pulsera: cargada y con su propia numeración."""
        self.device_id = device_id
        self._seq = 0
        self._historial = {}
        self._batt = 100.0

    def disparar_evento(self, tipo):
        """Encola un evento FALL o SOS para enviarlo de inmediato."""
        if tipo not in ("FALL", "SOS"):
            raise ValueError("tipo debe ser 'FALL' o 'SOS'")
        self._pendientes.put(tipo)

    # ---- generación de paquetes -------------------------------------------

    def _siguiente_seq(self):
        self._seq += 1
        return self._seq

    def _ts_ms(self):
        return int((time.monotonic() - self._t0) * 1000)

    def _paso_fisiologico(self):
        o = self._objetivo
        # Tira hacia el objetivo del escenario y agrega ruido pequeño.
        self._hr += (o["hr"] - self._hr) * 0.35 + random.uniform(-1.2, 1.2)
        self._spo2 += (o["spo2"] - self._spo2) * 0.35 + random.uniform(-0.35, 0.35)
        self._temp += (o["temp"] - self._temp) * 0.20 + random.uniform(-0.04, 0.04)
        self._hr = min(180, max(35, self._hr))
        self._spo2 = min(100, max(80, self._spo2))
        self._temp = min(41.0, max(33.0, self._temp))
        self._batt = max(0.0, self._batt - 0.01 - o.get("drenaje", 0.0))

    def _nueva_telemetria(self):
        self._paso_fisiologico()
        estado = "OK" if self._spo2 >= 90 else "ERR"
        return {
            "id": self.device_id,
            "seq": self._siguiente_seq(),
            "ts": self._ts_ms(),
            "hr": round(self._hr),
            "spo2": round(self._spo2),
            "temp": round(self._temp, 1),
            "batt": round(self._batt),
            "st": estado,
        }

    def _nuevo_evento(self, tipo=None):
        return {
            "id": self.device_id,
            "seq": self._siguiente_seq(),
            "ts": self._ts_ms(),
            "ev": tipo or random.choice(["FALL", "SOS"]),
            "sev": "HIGH",
        }

    def cortar(self, segundos=12.0):
        """
        Simula una caída del enlace: la pulsera sigue midiendo y numerando, pero nada
        sale al aire durante ese tiempo. Los paquetes quedan en el historial, así que al
        volver el enlace el testbed los puede pedir de vuelta y la gráfica se completa.
        """
        self._corte_hasta = time.monotonic() + float(segundos)

    def enlace_caido(self):
        return time.monotonic() < self._corte_hasta

    def _guardar(self, tipo, paquete):
        self._historial[paquete["seq"]] = (tipo, paquete)
        if len(self._historial) > 2000:
            self._historial.pop(next(iter(self._historial)))

    async def recibir_paquetes(self, on_telemetry, on_event):
        await self.conectar()
        proximo = time.monotonic() + self.intervalo_s
        while self._activo:
            await asyncio.sleep(0.05)
            if not self._activo:
                break
            while True:  # eventos pedidos desde la interfaz: salen de inmediato
                try:
                    tipo = self._pendientes.get_nowait()
                except queue.Empty:
                    break
                evento = self._nuevo_evento(tipo)
                self._guardar("e", evento)
                if not self.enlace_caido():
                    on_event(evento)
            if not self.enlace_caido():
                while True:  # reenvíos pedidos por el testbed para llenar huecos
                    try:
                        seq = self._reenvios.get_nowait()
                    except queue.Empty:
                        break
                    guardado = self._historial.get(seq)
                    if guardado:
                        (on_telemetry if guardado[0] == "t" else on_event)(guardado[1])
            if time.monotonic() < proximo:
                continue
            proximo += self.intervalo_s
            if self._batt <= 0 and self._objetivo.get("drenaje"):
                continue  # la pulsera se quedó sin batería: deja de transmitir
            paquete = self._nueva_telemetria()
            self._guardar("t", paquete)
            if self.enlace_caido():
                continue  # enlace interrumpido: el paquete queda guardado para reenviarlo
            if random.random() >= self._objetivo.get("perdida", 0.0):
                on_telemetry(paquete)  # si se "pierde", el seq queda con un hueco
            if random.random() < self.prob_evento_por_ciclo:
                evento = self._nuevo_evento()
                self._guardar("e", evento)
                on_event(evento)


# ----------------------------------------------------------------------
# WiFi / UDP
# ----------------------------------------------------------------------


class _ProtocoloUDP(asyncio.DatagramProtocol):
    def __init__(self, on_datos):
        self._on_datos = on_datos

    def datagram_received(self, data, addr):
        self._on_datos(data)


class UDPTransport(Transporte):
    """
    Escucha datagramas UDP con el mismo JSON que el contrato BLE (uno o varios por
    datagrama, separados por salto de línea). Sirve para el plan B de la demostración:
    pulsera -> WiFi -> laptop, sin depender del Bluetooth.

    puerto=0 deja que el sistema elija uno libre; el valor real queda en `puerto_real`.
    """

    def __init__(self, puerto=5005, host="0.0.0.0"):
        self.puerto = puerto
        self.host = host
        self.puerto_real = None
        self._transport = None
        self._fin = None
        self._detenido = False

    async def conectar(self):
        loop = asyncio.get_running_loop()
        self._fin = loop.create_future()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _ProtocoloUDP(self._entregar), local_addr=(self.host, self.puerto)
        )
        self.puerto_real = self._transport.get_extra_info("sockname")[1]

    def _entregar(self, datos):
        despachar_json(datos, self._on_telemetry, self._on_event)

    async def recibir_paquetes(self, on_telemetry, on_event):
        self._on_telemetry = on_telemetry
        self._on_event = on_event
        if self._detenido:
            return
        await self.conectar()
        await self._fin

    async def desconectar(self):
        self._detenido = True
        if self._transport is not None:
            self._transport.close()
        if self._fin is not None and not self._fin.done():
            self._fin.set_result(None)


# ----------------------------------------------------------------------
# Bluetooth Low Energy
# ----------------------------------------------------------------------


class BLETransport(Transporte):
    """
    Transporte real: se conecta por Bluetooth Low Energy a la pulsera física usando
    bleak, siguiendo el contrato de la sección 7. Requiere `pip install bleak` y que
    la pulsera esté encendida y anunciándose.

    Uso:
        BLETransport(direccion_mac="XX:XX:XX:XX:XX:XX")
    o, si no se conoce la dirección de antemano:
        BLETransport(nombre_dispositivo="W01")
    """

    SERVICE_UUID = "81540fdd-3371-4ebf-b228-7363bed623e7"
    TELEMETRY_CHAR_UUID = "3e9ad104-fdf4-4e20-a4f1-e5d67040c7a6"
    EVENT_CHAR_UUID = "72058786-0707-460c-9eda-342769fe84b6"

    def __init__(self, direccion_mac=None, nombre_dispositivo=None):
        self.direccion_mac = direccion_mac
        self.nombre_dispositivo = nombre_dispositivo
        self._cliente = None
        self._detenido = False

    @staticmethod
    def _importar_bleak():
        try:
            import bleak  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "Falta instalar bleak para usar Bluetooth. Ejecuta: pip install bleak"
            ) from exc

    async def _encontrar_direccion(self):
        from bleak import BleakScanner

        if self.direccion_mac:
            return self.direccion_mac
        dispositivos = await BleakScanner.discover(timeout=5.0)
        for d in dispositivos:
            if d.name == self.nombre_dispositivo:
                return d.address
        raise RuntimeError(
            f"No se encontró ningún dispositivo BLE anunciándose como "
            f"'{self.nombre_dispositivo}'. Verifica que la pulsera esté encendida."
        )

    async def conectar(self):
        self._importar_bleak()
        from bleak import BleakClient

        direccion = await self._encontrar_direccion()
        if self._detenido:
            return
        self._cliente = BleakClient(direccion)
        await self._cliente.connect()

    async def desconectar(self):
        self._detenido = True
        if self._cliente is not None:
            await self._cliente.disconnect()

    async def recibir_paquetes(self, on_telemetry, on_event):
        await self.conectar()
        if self._cliente is None:
            return

        def _callback_telemetry(_, datos: bytearray):
            despachar_json(datos, on_telemetry, on_event)

        def _callback_event(_, datos: bytearray):
            despachar_json(datos, on_telemetry, on_event)

        await self._cliente.start_notify(self.TELEMETRY_CHAR_UUID, _callback_telemetry)
        await self._cliente.start_notify(self.EVENT_CHAR_UUID, _callback_event)

        # Mantener la tarea viva mientras el cliente siga conectado.
        while self._cliente.is_connected and not self._detenido:
            await asyncio.sleep(1.0)


# ----------------------------------------------------------------------
# Hub por MQTT (la misma ruta que usa la versión web)
# ----------------------------------------------------------------------


def topicos_mqtt(prefijo):
    """Tópicos que publica el hub (FIRMWARE/hub)."""
    return [f"{prefijo}/pulsera/+/telemetria", f"{prefijo}/pulsera/+/evento",
            f"{prefijo}/hub/+/estado", f"{prefijo}/hub/+/info"]


class MQTTTransport(Transporte):
    """
    Recibe la pulsera real a través del hub: pulsera -> BLE -> hub (ESP32-S3) -> WiFi ->
    broker MQTT -> esta PC. Así la pulsera no tiene que estar conectada por Bluetooth a la
    computadora de la estación, y la web y el programa de escritorio pueden mirar al mismo
    hub a la vez. Requiere `pip install paho-mqtt`.

    Tópicos:
      <prefijo>/pulsera/<id>/telemetria   JSON de telemetría
      <prefijo>/pulsera/<id>/evento       JSON de evento (FALL, SOS)
      <prefijo>/hub/<hub>/estado          "online" / "offline" (retenido)
      <prefijo>/hub/<hub>/info            {"pulsera":"conectada","pendientes":N}
      <prefijo>/pulsera/<id>/comando      lo que manda la estación: {"cmd":"ack",...} o {"cmd":"resend","seq":[...]}

    Pedir el reenvío no cuesta nada: si el firmware todavía no lo atiende, el paquete se
    declara perdido a los 12 s, como con cualquier otra fuente.
    """

    soporta_reenvio = True
    MAX_REENVIO_POR_CICLO = 120

    def __init__(self, broker="broker.hivemq.com", puerto=1883, prefijo="pucmm-pulsera-7f3a",
                 usuario="", clave="", al_hub=None):
        self.broker, self.puerto, self.prefijo = broker, int(puerto), prefijo
        self.usuario, self.clave = usuario, clave
        self.al_hub = al_hub
        self.id_destino = None  # la pulsera del paciente en monitoreo
        self.ultimo_id = None
        self._cliente = None
        self._detenido = False
        self._fin = None
        self._loop = None

    @staticmethod
    def _importar_paho():
        try:
            import paho.mqtt.client  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("Falta instalar paho-mqtt para usar el hub. Ejecuta: pip install paho-mqtt") from exc

    def _avisar_hub(self, info):
        if self.al_hub:
            try:
                self.al_hub(info)
            except Exception:  # noqa: BLE001 - un aviso nunca debe cortar la recepción
                pass

    async def recibir_paquetes(self, on_telemetry, on_event):
        self._importar_paho()
        import paho.mqtt.client as mqtt

        if self._detenido:
            return
        self._loop = asyncio.get_running_loop()
        self._fin = self._loop.create_future()
        conectado = self._loop.create_future()
        loop = self._loop

        def en_loop(fn, *a):
            loop.call_soon_threadsafe(fn, *a)

        def al_conectar(cliente, _u, _f, rc, *_):
            codigo = getattr(rc, "value", rc)
            if codigo == 0:
                cliente.subscribe([(t, 1) for t in topicos_mqtt(self.prefijo)])
                self._avisar_hub({"tipo": "broker", "texto": "conectado"})
                en_loop(lambda: conectado.done() or conectado.set_result(True))
            else:
                en_loop(lambda: conectado.done() or conectado.set_exception(
                    RuntimeError(f"El broker rechazó la conexión (código {rc}). Revise usuario y clave.")))

        def al_desconectar(_c, _u, *_a):
            self._avisar_hub({"tipo": "broker", "texto": "sin conexión" if not self._detenido else "desconectado"})

        def al_mensaje(_c, _u, msg):
            tema = msg.topic
            partes = tema.split("/")
            if tema.endswith("/estado"):
                texto = msg.payload.decode("utf-8", "replace").strip()
                self._avisar_hub({"tipo": "estado", "hub": partes[-2], "texto": texto})
                return
            if tema.endswith("/info"):
                try:
                    datos = json.loads(msg.payload.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    return
                self._avisar_hub({"tipo": "info", "hub": partes[-2], "datos": datos})
                return

            def tel(p):
                self.ultimo_id = p.get("id") or self.ultimo_id
                on_telemetry(p)

            def ev(p):
                self.ultimo_id = p.get("id") or self.ultimo_id
                on_event(p)

            despachar_json(msg.payload, tel, ev)

        ident = f"vitalcare-escritorio-{random.randint(0, 0xFFFFFF):06x}"
        # 1883 y 8883 son MQTT normal (8883 con cifrado); 8884, 443, 8000, 8080 y 9001 son por WebSocket.
        via = "websockets" if self.puerto in (8884, 443, 8000, 8080, 9001) else "tcp"
        try:
            cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=ident, transport=via)
        except AttributeError:  # paho-mqtt 1.x
            cliente = mqtt.Client(client_id=ident, transport=via)
        if via == "websockets":
            cliente.ws_set_options(path="/mqtt")
        if self.puerto in (8883, 8884, 443):
            cliente.tls_set()
        if self.usuario:
            cliente.username_pw_set(self.usuario, self.clave)
        cliente.on_connect = al_conectar
        cliente.on_disconnect = al_desconectar
        cliente.on_message = al_mensaje
        cliente.reconnect_delay_set(1, 5)
        self._cliente = cliente
        try:
            cliente.connect_async(self.broker, self.puerto, keepalive=30)
            cliente.loop_start()
            await asyncio.wait_for(conectado, timeout=15)
        except asyncio.TimeoutError:
            self._cerrar_cliente()
            raise RuntimeError(f"No se pudo conectar al broker {self.broker}:{self.puerto}. Revise la conexión a "
                               "internet y los datos del broker en Configuración.") from None
        except Exception:
            self._cerrar_cliente()
            raise
        await self._fin

    def _cerrar_cliente(self):
        c, self._cliente = self._cliente, None
        if c is not None:
            try:
                c.loop_stop()
                c.disconnect()
            except Exception:  # noqa: BLE001
                pass

    def enviar_comando(self, id_pulsera, comando):
        """Publica una orden para la pulsera (el hub se la pasa por Bluetooth). True si salió."""
        c = self._cliente
        if c is None or not id_pulsera or not c.is_connected():
            return False
        info = c.publish(f"{self.prefijo}/pulsera/{id_pulsera}/comando", json.dumps(comando), qos=1)
        return info.rc == 0

    def solicitar_reenvio(self, seqs):
        ident = self.id_destino or self.ultimo_id
        lista = [int(s) for s in seqs][: self.MAX_REENVIO_POR_CICLO]
        for i in range(0, len(lista), 20):  # en tandas: el hub y la pulsera tienen poca memoria por orden
            self.enviar_comando(ident, {"cmd": "resend", "seq": lista[i:i + 20]})
        return True

    async def desconectar(self):
        self._detenido = True
        self._cerrar_cliente()
        if self._fin is not None and not self._fin.done():
            self._fin.set_result(None)
