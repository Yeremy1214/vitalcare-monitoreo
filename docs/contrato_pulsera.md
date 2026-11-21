# Contrato de comunicación entre la pulsera y la estación

Este documento es el acuerdo entre el firmware de la pulsera y el programa de la estación. Mientras
los dos lados respeten lo que está aquí, cada uno puede cambiar por dentro sin romper al otro.

## Formato de los paquetes

Los dos tipos de paquete son JSON en una sola línea, codificados en UTF-8.

### Telemetría (cada 2 segundos)

```json
{"id":"W01","seq":1234,"ts":1763400000000,"hr":76,"spo2":97,"temp":36.7,"batt":88,"st":"OK"}
```

| Campo | Tipo | Significado |
|---|---|---|
| `id` | texto | Identificador del dispositivo. Es el que la estación usa para saber de qué paciente son los datos |
| `seq` | entero | Número de secuencia, compartido por telemetría y eventos. Se incrementa de uno en uno |
| `ts` | entero | Marca de tiempo del dispositivo en milisegundos desde el arranque o desde la época |
| `hr` | número | Frecuencia cardíaca en latidos por minuto |
| `spo2` | número | Saturación de oxígeno en porcentaje |
| `temp` | número | Temperatura en °C, **con el ajuste de +2.1 °C ya aplicado por el firmware** |
| `batt` | entero | Carga de la batería en porcentaje, de 0 a 100 |
| `st` | texto | Estado del sensor: `OK`, `NO_FINGER`, `SENSOR_ERR` |

### Evento (en el momento en que ocurre)

```json
{"id":"W01","seq":1235,"ts":1763400002000,"ev":"FALL","sev":"HIGH"}
```

| Campo | Valores |
|---|---|
| `ev` | `FALL` (caída detectada) o `SOS` (botón presionado) |
| `sev` | `HIGH`, `MEDIUM` o `LOW` |

Los eventos **no esperan** al ciclo de telemetría: salen de inmediato.

## La regla del número de secuencia

`seq` es un solo contador para los dos tipos de paquete. Si la estación recibe el 120 y luego el
122, sabe que falta exactamente el 121 y lo pide de vuelta. De ahí sale la garantía de no perder
datos, así que el firmware **nunca debe repetir ni saltarse un número** por su cuenta.

Cuando la pulsera se reinicia, el contador vuelve a empezar. La estación lo detecta y lo registra
como reinicio, no como pérdida.

## Reenvío de paquetes faltantes

Para que la recuperación funcione con hardware real, el firmware necesita dos cosas:

1. **Historial en memoria.** Guardar los últimos 512 paquetes transmitidos, indexados por `seq`.
   A una muestra cada 2 segundos eso cubre unos 17 minutos.
2. **Comando de reenvío.** Atender una solicitud de la estación y volver a enviar esos paquetes
   tal como se generaron, sin cambiarles el `seq` ni el `ts`.

Formato de la solicitud que envía la estación:

```json
{"cmd":"resend","seq":[121,122,123]}
```

La pulsera responde reenviando cada paquete pedido en su formato normal. Si alguno ya no está en el
historial, simplemente no lo manda, y la estación lo declara como pérdida real a los 12 segundos.

La estación reintenta cada 4 segundos mientras el paquete siga faltando.

## Bluetooth (BLE)

| Elemento | UUID |
|---|---|
| Servicio | `81540fdd-3371-4ebf-b228-7363bed623e7` |
| Telemetría (notify) | `3e9ad104-fdf4-4e20-a4f1-e5d67040c7a6` |
| Eventos (notify) | `72058786-0707-460c-9eda-342769fe84b6` |

Parámetros del enlace que asume el diseño:

- Nombre de anuncio: el mismo `id` del dispositivo, por ejemplo `W01`.
- Unidad máxima de transmisión: negociar **185 bytes** para que cada paquete viaje en una sola
  notificación. Con el valor por defecto de 23 bytes habría que fragmentar.
- Intervalo de conexión 100 ms, latencia de esclavo 9, tiempo de supervisión 6 s.

## WiFi / UDP

La estación escucha en el puerto **5005** por defecto y acepta el mismo JSON, uno o varios
paquetes por datagrama separados por salto de línea. Para probar sin la pulsera está
`emisor_udp_prueba.py`, que simula el envío desde otra máquina de la misma red.

## Qué hace la estación con lo que recibe

1. Comprueba que el `id` corresponda a la pulsera asignada al paciente en monitoreo. Si no, descarta
   el paquete y avisa, para no mezclar datos de dos personas.
2. Revisa la continuidad de `seq`, pide los faltantes y coloca los recuperados en la hora que indica
   su `ts`, no en la hora en que llegaron.
3. Escribe cada fila en disco en el momento en que llega.
4. Compara los valores con los umbrales del perfil clínico del paciente y levanta alertas.
