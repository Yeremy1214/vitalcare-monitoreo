"""
Hub de prueba para la fuente "Hub (MQTT)", en la web y en el programa de escritorio.

Hace lo mismo que el hub real (ESP32-S3) pero sin hardware: publica en el broker los paquetes
de una pulsera, con los mismos tópicos y el mismo JSON del contrato, y atiende las órdenes que
le manda la estación (reenvío de paquetes y aviso de "ayuda en camino"). Sirve para comprobar
que la estación recibe por el hub desde cualquier PC, aunque la pulsera no esté a mano.

Necesita:  pip install paho-mqtt

Cómo usarlo (con la estación abierta, fuente "Hub (MQTT)" y pulsado Conectar):

    python emisor_mqtt_prueba.py                        # broker público, prefijo del proyecto
    python emisor_mqtt_prueba.py --perdida 0.2          # pierde 1 de cada 5 paquetes (para ver el reenvío)
    python emisor_mqtt_prueba.py --id W02               # otra pulsera
    python emisor_mqtt_prueba.py --broker 127.0.0.1     # un broker en esta misma red

Mientras corre, escribe en esta ventana:  c + Enter = caída,  s + Enter = SOS,  q + Enter = salir

Tópicos (iguales que FIRMWARE/hub):
    <prefijo>/pulsera/<id>/telemetria   telemetría cada 2 s
    <prefijo>/pulsera/<id>/evento       FALL / SOS al momento
    <prefijo>/hub/<hub>/estado          "online" / "offline" (retenido)
    <prefijo>/hub/<hub>/info            {"pulsera":"conectada","pendientes":0}
    <prefijo>/pulsera/<id>/comando      lo que manda la estación: {"cmd":"ack"} o {"cmd":"resend","seq":[...]}
"""

import argparse
import json
import random
import threading
import time

try:
    import paho.mqtt.client as mqtt
except ImportError:
    raise SystemExit("Falta paho-mqtt. Ejecuta:  pip install paho-mqtt")

ap = argparse.ArgumentParser(description="Hub de prueba que publica una pulsera simulada por MQTT")
ap.add_argument("--broker", default="broker.hivemq.com")
ap.add_argument("--puerto", type=int, default=1883)
ap.add_argument("--prefijo", default="pucmm-pulsera-7f3a")
ap.add_argument("--id", default="W01", help="identificador de la pulsera (campo id del JSON)")
ap.add_argument("--hub", default="HUB-PRUEBA")
ap.add_argument("--perdida", type=float, default=0.0, help="fracción de paquetes que no se publican (0 a 1)")
ap.add_argument("--usuario", default="")
ap.add_argument("--clave", default="")
ap.add_argument("--sin-teclado", action="store_true", help="no leer el teclado (para correrlo en segundo plano)")
ap.add_argument("--segundos", type=float, default=0, help="terminar solo después de estos segundos (0 = nunca)")
ap.add_argument("--caida-a-los", type=float, default=0, help="mandar una caída (FALL) a los N segundos (0 = no)")
args = ap.parse_args()

P = args.prefijo
T_TEL = f"{P}/pulsera/{args.id}/telemetria"
T_EV = f"{P}/pulsera/{args.id}/evento"
T_CMD = f"{P}/pulsera/{args.id}/comando"
T_ESTADO = f"{P}/hub/{args.hub}/estado"
T_INFO = f"{P}/hub/{args.hub}/info"

historial = {}  # seq -> (tópico, json): lo que la pulsera guarda para reenviar
pendientes = []
salir = False
lock = threading.Lock()


def al_conectar(cliente, _u, _f, rc, *_):
    print(f"Conectado al broker {args.broker}:{args.puerto} (código {rc}). Publicando en {P}/...")
    cliente.publish(T_ESTADO, "online", qos=1, retain=True)
    cliente.subscribe(T_CMD, qos=1)


def al_mensaje(cliente, _u, msg):
    try:
        orden = json.loads(msg.payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return
    if orden.get("cmd") == "resend":
        seqs = [int(s) for s in orden.get("seq", [])]
        with lock:
            reenviados = [s for s in seqs if s in historial]
            for s in reenviados:
                topico, texto = historial[s]
                cliente.publish(topico, texto, qos=1)
        print(f"  la estación pidió reenviar {seqs}: reenviados {reenviados}")
    elif orden.get("cmd") == "ack":
        print(f"  >>> AYUDA EN CAMINO (la estación atendió {orden.get('ev')} seq {orden.get('seq')})")


try:
    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"hub-prueba-{random.randint(0, 99999)}")
except AttributeError:  # paho-mqtt 1.x
    cliente = mqtt.Client(client_id=f"hub-prueba-{random.randint(0, 99999)}")
if args.usuario:
    cliente.username_pw_set(args.usuario, args.clave)
if args.puerto == 8883:
    cliente.tls_set()
cliente.will_set(T_ESTADO, "offline", qos=1, retain=True)
cliente.on_connect = al_conectar
cliente.on_message = al_mensaje
cliente.connect(args.broker, args.puerto, keepalive=30)
cliente.loop_start()


def teclado():
    global salir
    while not salir:
        try:
            tecla = input().strip().lower()
        except EOFError:
            return
        if tecla == "c":
            pendientes.append("FALL")
        elif tecla == "s":
            pendientes.append("SOS")
        elif tecla == "q":
            salir = True


if not args.sin_teclado:
    threading.Thread(target=teclado, daemon=True).start()
    print("c = caída, s = SOS, q = salir")

seq = 0
t0 = time.monotonic()
hr, spo2, temp, batt = 75.0, 97.0, 36.7, 90.0
ultima_info = 0.0
caida_hecha = False
while not salir:
    if args.caida_a_los and not caida_hecha and time.monotonic() - t0 > args.caida_a_los:
        caida_hecha = True
        pendientes.append("FALL")
    if args.segundos and time.monotonic() - t0 > args.segundos:
        break
    hr = min(120, max(50, hr + random.uniform(-1.5, 1.5)))
    spo2 = min(100, max(93, spo2 + random.uniform(-0.4, 0.4)))
    temp = min(37.4, max(36.2, temp + random.uniform(-0.05, 0.05)))
    batt = max(0, batt - 0.01)
    seq += 1
    paquete = {"id": args.id, "seq": seq, "ts": int((time.monotonic() - t0) * 1000), "hr": round(hr),
               "spo2": round(spo2), "temp": round(temp, 1), "batt": round(batt), "st": "OK"}
    texto = json.dumps(paquete, separators=(",", ":"))
    with lock:
        historial[seq] = (T_TEL, texto)
        if len(historial) > 512:
            historial.pop(next(iter(historial)))
    if random.random() >= args.perdida:
        cliente.publish(T_TEL, texto, qos=1)
        print("telemetría", texto)
    else:
        print("(perdido a propósito)", texto)
    if time.monotonic() - ultima_info > 5:
        ultima_info = time.monotonic()
        cliente.publish(T_INFO, json.dumps({"pulsera": "conectada", "pendientes": 0}), qos=0)
    for _ in range(20):  # 2 s, pero atento a los eventos del teclado
        while pendientes:
            seq += 1
            evento = {"id": args.id, "seq": seq, "ts": int((time.monotonic() - t0) * 1000), "ev": pendientes.pop(0), "sev": "HIGH"}
            texto = json.dumps(evento, separators=(",", ":"))
            with lock:
                historial[seq] = (T_EV, texto)
            cliente.publish(T_EV, texto, qos=1)
            print("EVENTO", texto)
        if salir:
            break
        time.sleep(0.1)

cliente.publish(T_ESTADO, "offline", qos=1, retain=True)
time.sleep(0.3)
cliente.loop_stop()
cliente.disconnect()
print("Listo.")
