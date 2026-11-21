"""
Capa de transporte del testbed.

Esta capa separa "de dónde vienen los paquetes" de "qué hace el programa con ellos".
El testbed puede recibir los paquetes de tres fuentes, que se eligen desde la ventana:

  - SimuladorTransport: genera paquetes localmente, sin hardware (con escenarios y
                        botones para disparar caída / SOS en la demostración).
  - BLETransport:       se conecta por Bluetooth Low Energy a la pulsera real (bleak).
  - UDPTransport:       escucha datagramas JSON por WiFi (la ruta del receptor UDP).

Las tres entregan el mismo formato, acordado en Definicion_Formato_JSON_Bluetooth.docx:
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
