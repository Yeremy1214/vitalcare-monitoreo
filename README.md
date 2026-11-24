# VitalCare · Estación de monitoreo (versión 3)

Aplicación en Python que recibe los datos de la pulsera de signos vitales, los muestra en vivo,
genera las alertas y lleva el registro del centro: quién está ingresado, qué pulsera tiene puesta
cada paciente y qué hizo el personal con cada alerta.

Es el programa que se presenta a la asesora y al jurado en la etapa de Anteproyecto.

La estación existe en dos versiones que hacen lo mismo y usan la misma base de datos:

| Versión | Dónde | Cómo se abre |
|---|---|---|
| **Escritorio** | esta carpeta (Python) | `python app_testbed.py` |
| **Web** | carpeta [`web/`](web/) | https://vitalcare-pulsera.vercel.app en Chrome o Edge, sin instalar nada ([web/README.md](web/README.md)) |

## Qué se necesita

- Python 3.10 o superior, con Tkinter (viene incluido en el instalador de python.org para Windows).
- Una sola librería obligatoria:

```
pip install matplotlib
```

- Solo el día que se use Bluetooth con la pulsera real: `pip install bleak`
- Solo para recibir la pulsera a través del hub (fuente *Hub (MQTT)*): `pip install paho-mqtt`

La base de datos es SQLite y viene incluida con Python, así que no hay nada más que instalar.

## Cómo correrlo

```
python app_testbed.py
```

Arranca conectado al **simulador**, con dos pacientes y cuatro pulseras de ejemplo, así que se ve
vivo sin hardware. Para comprobar que todo funciona en tu PC:

```
python pruebas_testbed.py
```

Debe terminar con `33 de 33 pruebas correctas`.

## Las cuatro pestañas

| Pestaña | Para qué sirve |
|---|---|
| **Monitoreo** | Los seis signos en tarjetas, las tendencias, las alertas en vivo y el estado del enlace, con el paciente y su pulsera en la cabecera |
| **Pacientes** | Ingreso, edición, asignación y cambio de pulsera, signos tomados a mano, alta y eliminación |
| **Pulseras** | Inventario por número de serie, con su estado, su batería y a quién está asignada |
| **Configuración** | Umbrales clínicos por perfil, parámetros de alerta del centro (incluida la conexión con el hub) y auditoría |

## Ingreso del paciente y asignación de pulsera

1. En **Pacientes**, pulsar *Ingresar paciente*. Se piden expediente, nombre, fecha de nacimiento,
   sexo, habitación, cama, diagnóstico y alergias. Con la fecha de nacimiento el sistema calcula la
   edad y elige el **perfil clínico** (neonato, lactante, niño pequeño, preescolar, escolar,
   adolescente, adulto o adulto mayor), que define los rangos normales de ese paciente.
2. Pulsar *Asignar o cambiar pulsera* y elegir una de las disponibles. La pulsera se identifica por
   su **número de serie** (VC-W-0001) y por el identificador que viaja en cada paquete (W01).
   Ninguna pulsera está atada a una cama.
3. Pulsar *Monitorear a este paciente*. La pestaña de Monitoreo queda apuntando a esa persona y solo
   acepta los paquetes de la pulsera que tiene asignada.

**Cambio de pulsera sin interrumpir el monitoreo.** Si la batería se está agotando, se asigna otra
pulsera cargada desde el mismo botón. La anterior se libera y queda *Cargando*, la nueva empieza a
enviar, la gráfica del paciente continúa y queda una marca vertical con el cambio. El historial
guarda qué pulsera envió cada tramo de datos y quién hizo el cambio.

**Alta del paciente.** El botón *Dar de alta* saca al paciente del monitoreo y libera su pulsera,
que vuelve al inventario como *Disponible*.

**Qué no se puede borrar.** Un paciente con historial no se elimina, se da de alta; una pulsera que
ya se usó no se elimina, se marca *Fuera de servicio*. El botón *Eliminar* solo funciona con
registros que no tienen historial, por ejemplo uno creado por equivocación.

## Seguridad de los datos del paciente

Con un paciente seleccionado, si llegan paquetes de otra pulsera el sistema los **descarta** y
genera la alerta *Llegan datos de otra pulsera*, diciendo a quién pertenece esa pulsera. Así no se
mezclan los datos de dos personas.

## Signos que la pulsera no mide

La pulsera mide frecuencia cardíaca, SpO₂ y temperatura. La **presión arterial** y la **frecuencia
respiratoria** las toma el personal, así que tienen su propia tarjeta junto a los demás signos: se
pulsa sobre ella (o en *Signos a mano*) y se abre el formulario, que guarda el valor con la hora y
el nombre de quien lo tomó. El sistema los compara con el rango del paciente, los pinta en ámbar o
rojo igual que los otros signos, y si están fuera de rango generan alerta y cambian el estado
general que muestra el banner.

## Umbrales por edad y sexo

Cada perfil clínico trae sus propios rangos para los seis signos. Por ejemplo, un escolar tiene
frecuencia cardíaca normal de 70 a 120 lpm y presión sistólica de 90 a 120 mmHg, mientras que un
adulto tiene 60 a 100 lpm y 90 a 129 mmHg.

En **Configuración → Umbrales clínicos** el centro puede:

- cambiar cualquier rango, indicando quién autoriza el cambio;
- crear una **variante por sexo** de un perfil, que copia los valores para ajustarlos aparte;
- y en la ficha del paciente se puede fijar un **ajuste individual** con su motivo, que pesa más que
  el perfil (por ejemplo, SpO₂ ≥ 92 % en un paciente con EPOC).

Todo cambio queda en la **auditoría** con la fecha, el usuario y el valor anterior.

## Alertas de batería

- **Batería baja (30 % o menos):** aviso ámbar, "programar el cambio". Escala a los 5 minutos.
- **Batería crítica (15 % o menos):** alerta roja, "cambiar la pulsera ya". Escala a los 2 minutos y
  cierra el aviso ámbar.
- **Autonomía estimada** en la tarjeta de batería, calculada con la descarga de los últimos 15 min.
- Las alertas se rearman cuando la batería vuelve a subir de 40 %.

Los tres porcentajes y los dos tiempos se editan en Configuración.

## Cero pérdida de datos

Una radio no puede garantizar por sí sola que nada se pierda, así que el sistema trabaja en capas:

1. **Detecta** cada paquete que falta por el campo `seq` y guarda cuáles son (`huecos_*.csv`).
2. **Pide el reenvío** y coloca el dato recuperado en su hora original en la gráfica. Reintenta cada 4 s.
3. **Declara la pérdida** si en 12 s no llega: integridad en rojo y alerta *Datos no recuperados*,
   que el personal debe documentar.
4. **Guarda en disco** cada fila en el momento en que llega, con `fsync`.

El reenvío funciona con el simulador y con el hub de prueba (`emisor_mqtt_prueba.py`). Con la
pulsera real, la estación ya manda la orden `{"cmd":"resend","seq":[...]}` por el hub; falta que el
firmware guarde el historial por número de secuencia y la atienda (ver
[docs/contrato_pulsera.md](docs/contrato_pulsera.md)). Mientras tanto, lo que no llega se declara
como pérdida real a los 12 s.

## Escenarios críticos: corte de red y corte de energía

En la barra de demostración hay dos botones para provocarlos en vivo:

- **Caída de red (F4):** el enlace se interrumpe 12 segundos. Los paquetes de ese intervalo se
  marcan como faltantes y, al volver, se piden y se recuperan. El indicador de integridad lo muestra.
- **Corte de energía (F5):** reproduce lo que pasa cuando se va la luz. La sesión queda abierta, los
  datos escritos siguen en disco y se levanta una alerta con la hora del último dato.

**Al arrancar**, si encuentra una sesión que quedó abierta, el programa avisa que hubo un corte, dice
hasta qué hora hay datos y deja la alerta pendiente de atender. Cada arranque y cada cierre quedan en
la tabla de eventos del sistema. Para demostrarlo de verdad, cerrar el programa desde el
Administrador de tareas y volver a abrirlo.

## Cierre de alertas con detalle manual

Al pulsar **Atender** se abre un formulario: cómo se resolvió, la descripción de lo que se hizo,
quién atendió y, si hubo indicación médica, qué médico la dio. Hay frases rápidas por tipo de
alerta. No se puede guardar sin esos datos, y si se cancela la alerta sigue pendiente con su
cronómetro corriendo. Todo queda en `alertas_*.csv`.

## Evidencias para el informe

El botón **Evidencia (F8)**, en la barra de abajo, guarda una captura de la ventana con fecha y hora en
`datos_sesion/evidencias/`, y la deja anotada en la auditoría. Sirve para los anexos del informe
final sin tener que recortar pantallazos a mano.

## Las cuatro fuentes de datos

1. **Simulador**: no necesita nada. Escenarios: paciente estable, fiebre, SpO₂ baja, taquicardia,
   bradicardia, señal inestable (pierde y recupera), señal inestable sin reenvío (pérdida real) y
   batería baja (descarga rápida).
2. **Bluetooth (BLE)**: nombre con el que se anuncia la pulsera (por defecto `W01`) o su MAC. UUID
   del contrato: servicio `81540fdd-3371-4ebf-b228-7363bed623e7`, telemetría
   `3e9ad104-fdf4-4e20-a4f1-e5d67040c7a6`, eventos `72058786-0707-460c-9eda-342769fe84b6`.
3. **WiFi / UDP**: escucha en un puerto (por defecto `5005`) y acepta el mismo JSON. Para probar sin
   pulsera está `emisor_udp_prueba.py`.
4. **Hub (MQTT)**: la pulsera le manda los datos al hub por Bluetooth y el hub los publica en el
   broker MQTT (`broker.hivemq.com`, prefijo `pucmm-pulsera-7f3a`). La estación se suscribe, pide el
   reenvío de lo que falte y, al atender una caída o un SOS, le avisa a la pulsera "ayuda en camino".
   Es la misma ruta que usa la versión web, así que las dos pueden mirar a la misma pulsera a la vez.
   Los datos del broker se cambian en Configuración → Parámetros de alerta (`mqtt_*`). Para probar
   sin el hub está `emisor_mqtt_prueba.py`. Los tópicos están en
   [docs/contrato_pulsera.md](docs/contrato_pulsera.md).

Una pulsera acepta una sola conexión Bluetooth a la vez: o la tiene el hub, o la tiene una PC con la
fuente Bluetooth. Para usar el hub, la fuente Bluetooth de la PC tiene que estar desconectada.

## Archivos que se guardan

Cada vez que se pulsa **Conectar** se crea una sesión nueva en `datos_sesion/`:

| Archivo | Contenido |
|---|---|
| `telemetria_<fecha>.csv` | Un renglón por paquete de telemetría |
| `eventos_<fecha>.csv` | Un renglón por evento de la pulsera (FALL, SOS) |
| `alertas_<fecha>.csv` | Bitácora de alertas con el detalle que escribió el personal |
| `huecos_<fecha>.csv` | Cada paquete que faltó: detectado, recuperado o no recuperado |
| `resumen_<fecha>.txt` | Solo si se pulsa Guardar como .txt en el resumen de sesión |
| `evidencias/` | Capturas de pantalla con fecha y hora |

Los pacientes, las pulseras, las asignaciones, los umbrales, la configuración y la auditoría viven
en `vitalcare.db`, junto al programa.

## Archivos del programa

| Archivo | Qué hace |
|---|---|
| `app_testbed.py` | Ventana principal y pestaña de monitoreo |
| `pantallas.py` | Pestañas de pacientes, pulseras y configuración, con sus formularios |
| `datos.py` | Base de datos: pacientes, pulseras, asignaciones, umbrales, auditoría |
| `logica.py` | Clasificación de signos, conteo de paquetes perdidos y resumen (sin interfaz) |
| `transportes.py` | Simulador, BLE, UDP y hub por MQTT; todos entregan el mismo formato |
| `registro.py` | Escritura de los CSV de la sesión |
| `estilo.py` | Colores y piezas de interfaz compartidas |
| `emisor_udp_prueba.py` | Emisor de prueba para la fuente WiFi/UDP |
| `emisor_mqtt_prueba.py` | Hub de prueba: publica una pulsera simulada por MQTT (sirve para la web y para el escritorio) |
| `pruebas_testbed.py` | 33 pruebas automáticas |
| `web/` | La versión web de la estación: misma lógica, mismas pantallas, misma base de datos |

## Atajos

| Tecla | Qué hace |
|---|---|
| F2 / F3 | Simular caída / SOS (solo con el simulador) |
| F4 | Simular caída de red |
| F5 | Simular corte de energía |
| F8 | Guardar captura de evidencia |
| F11 | Pantalla completa |

## Cosas que hay que saber

- Los umbrales de referencia son valores para población general y se ajustan desde Configuración
  según el criterio clínico del centro. El programa no emite diagnósticos.
- La temperatura llega con el ajuste de +2.1 °C aplicado por el firmware.
- El mismo archivo `vitalcare.db` sirve en las dos versiones: la web lo exporta y lo importa desde
  Configuración → Datos del navegador. Para copiarlo desde el escritorio, cerrar antes el programa.
- Esta estación monitorea a un paciente a la vez. La vista de todas las camas a la vez corresponde
  al panel web del Proyecto de Grado.
- La ventana está pensada para pantallas de 1366×768 o mayores.
