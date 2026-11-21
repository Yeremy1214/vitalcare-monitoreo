"""
Emisor de prueba para la fuente WiFi / UDP del testbed.

Simula a la pulsera enviando paquetes JSON por UDP, con el mismo formato del contrato.
Sirve para comprobar que el testbed recibe por WiFi sin necesitar el hardware.

Cómo usarlo (con el testbed ya abierto, fuente "WiFi / UDP" y pulsado Conectar):

    python emisor_udp_prueba.py                 # envía a esta misma PC, puerto 5005
    python emisor_udp_prueba.py 192.168.1.50    # envía a otra PC (la IP que muestra el testbed)
    python emisor_udp_prueba.py 127.0.0.1 6000  # otro puerto

Mientras corre, escribe en esta ventana:  c + Enter = caída,  s + Enter = SOS,  q + Enter = salir
"""

import json
import random
import socket
import sys
import threading
import time

host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
puerto = int(sys.argv[2]) if len(sys.argv) > 2 else 5005

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
seq = 0
t0 = time.monotonic()
salir = False
pendientes = []


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


threading.Thread(target=teclado, daemon=True).start()
print(f"Enviando a {host}:{puerto} cada 2 s.  c = caída, s = SOS, q = salir")

hr, spo2, temp, batt = 75.0, 97.0, 36.7, 90.0
while not salir:
    hr = min(120, max(50, hr + random.uniform(-1.5, 1.5)))
    spo2 = min(100, max(93, spo2 + random.uniform(-0.4, 0.4)))
    temp = min(37.4, max(36.2, temp + random.uniform(-0.05, 0.05)))
    batt = max(0, batt - 0.01)
    seq += 1
    paquete = {"id": "W01", "seq": seq, "ts": int((time.monotonic() - t0) * 1000),
               "hr": round(hr), "spo2": round(spo2), "temp": round(temp, 1), "batt": round(batt), "st": "OK"}
    sock.sendto(json.dumps(paquete).encode("utf-8"), (host, puerto))
    print("telemetría", paquete)
    while pendientes:
        seq += 1
        evento = {"id": "W01", "seq": seq, "ts": int((time.monotonic() - t0) * 1000),
                  "ev": pendientes.pop(0), "sev": "HIGH"}
        sock.sendto(json.dumps(evento).encode("utf-8"), (host, puerto))
        print("EVENTO", evento)
    for _ in range(20):  # 2 s, pero atento a los eventos del teclado
        if salir or pendientes:
            break
        time.sleep(0.1)
print("Listo.")
