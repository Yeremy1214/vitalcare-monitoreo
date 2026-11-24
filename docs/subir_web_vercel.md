# Subir la versión web a Vercel y probarla

Esta guía es para reemplazar lo que hoy está en https://vitalcare-pulsera.vercel.app por la versión
nueva de la carpeta `web/`, que hace lo mismo que el programa de escritorio. El enlace no cambia.

## 1. Averiguar cómo está publicado hoy

Entrar a https://vercel.com, abrir el proyecto **vitalcare-pulsera** y mirar
*Settings → Git*:

- Si aparece un repositorio de GitHub conectado, seguir el **camino A**.
- Si dice que no hay repositorio conectado, seguir el **camino C**.

El **camino B** es opcional y sirve para que, de aquí en adelante, la web se actualice sola cada vez
que se sube un cambio a `vitalcare-monitoreo`.

## 2. Publicar

### Camino A: el proyecto ya está conectado a un repositorio

1. Abrir en la PC la carpeta de ese repositorio (la que tiene `index.html`, `app.js`, `logica.js` y
   `estilo.css`) y hacer `git pull`.
2. Borrar esos cuatro archivos viejos.
3. Copiar ahí **todo lo que hay dentro de** `web/`: `index.html`, `estilo.css`, `README.md` y las
   carpetas `js/`, `vendor/` y `pruebas/`. Copiar el contenido, no la carpeta `web` entera.
4. Subirlo:

   ```
   git add -A
   git commit -m "VitalCare web igual a la versión de escritorio"
   git push
   ```

5. Vercel publica solo en un minuto más o menos. En *Deployments* debe aparecer como *Ready*.

### Camino B: conectar Vercel a este repositorio (opcional)

1. En *Settings → Git*, conectar el repositorio `Yeremy1214/vitalcare-monitoreo`. Si no aparece,
   Yeremy tiene que agregar a Luis como colaborador en GitHub.
2. En *Settings → Build and Deployment*:
   - *Root Directory*: `web`
   - *Framework Preset*: `Other`
   - *Build Command* y *Output Directory*: vacíos (no hay nada que compilar).
3. En *Deployments*, pulsar *Redeploy* sobre el último.

### Camino C: subirlo desde la PC sin GitHub

Hace falta [Node.js](https://nodejs.org) instalado.

```
cd vitalcare-monitoreo/web
npx vercel login
npx vercel --prod
```

Cuando pregunte, contestar que sí se quiere enlazar a un proyecto existente (*Link to existing
project*) y elegir **vitalcare-pulsera**. Si pregunta por el directorio, dejar el actual (`./`).

## 3. Comprobar que quedó bien

1. Abrir https://vitalcare-pulsera.vercel.app y recargar sin caché con **Ctrl + Shift + R**.
2. Debe verse igual que el programa de escritorio: encabezado azul oscuro, cuatro pestañas, seis
   tarjetas de signos y el simulador corriendo con datos. En *Configuración* tiene que aparecer la
   pestaña *Datos del navegador*.
3. Abrir https://vitalcare-pulsera.vercel.app/pruebas/ y esperar unos 15 segundos. Debe decir
   **32 de 32 pruebas correctas**.

La primera vez que se abre en un navegador, la web arranca con los mismos datos de ejemplo del
escritorio: María Pérez Gómez con la pulsera VC-W-0001 (W01), Carlos Martínez Díaz y cuatro pulseras.
Lo que se había registrado en la versión vieja de la web no pasa a la nueva.

## 4. Probar con la pulsera real

1. Cerrar el programa de escritorio si estaba conectado a la pulsera por Bluetooth: la pulsera
   acepta una sola conexión y la necesita el hub.
2. Encender el hub y la pulsera.
3. En la web, elegir en *Paciente en monitoreo* a **María Pérez Gómez**.
4. En *Fuente de datos* elegir **Hub (MQTT)**. El prefijo ya viene puesto (`pucmm-pulsera-7f3a`).
   Pulsar **Conectar**.
5. En el panel *Enlace con la pulsera* debe aparecer: *Broker: conectado · Hub ...: en línea ·
   pulsera conectada*. El número de *Recibidos* sube cada 2 segundos y las tarjetas muestran los
   valores de la pulsera.
6. Provocar una caída o pulsar el SOS de la pulsera. Sale la alerta; al atenderla y guardar, la web
   le manda al hub la orden de "ayuda en camino".

Si no llega nada:

| Lo que se ve | Qué revisar |
|---|---|
| *Broker: conectado* pero *Hub: sin noticias* | El hub no está publicando: WiFi del hub, broker y `MQTT_PREFIJO` en `FIRMWARE/hub/config.h` |
| *Hub: apagado* | El hub se desconectó del broker (su WiFi o su energía) |
| Hub en línea pero *buscando la pulsera* | La pulsera está apagada o conectada por Bluetooth a otra PC |
| Llega la alerta *Llegan datos de otra pulsera* | El `id` que manda la pulsera no es `W01`. Cambiar el identificador de la pulsera en la pestaña *Pulseras* para que coincida |
| *Error de conexión* con el broker | Internet de la PC, o la red bloquea el puerto 8884 (pasa en algunas redes de universidad; probar con los datos del celular) |

Para probar todo esto sin la pulsera ni el hub, en cualquier PC:

```
pip install paho-mqtt
python emisor_mqtt_prueba.py
```

Mientras corre, escribir `c` + Enter manda una caída y `s` + Enter un SOS.

## 5. Lo que el firmware del hub tiene que hacer

La web y el escritorio ya están listos. Para que todo funcione con el hardware, el hub debe cumplir
lo que dice [contrato_pulsera.md](contrato_pulsera.md), sección *Hub por MQTT*:

- Publicar la telemetría y los eventos en `pucmm-pulsera-7f3a/pulsera/W01/telemetria` y `.../evento`.
- Publicar `online` en `pucmm-pulsera-7f3a/hub/<hub>/estado` al conectarse, retenido, con `offline`
  como último deseo (LWT).
- Escuchar `pucmm-pulsera-7f3a/pulsera/W01/comando` y pasarle a la pulsera el `ack` (pantalla "AYUDA
  EN CAMINO") y el `resend` (reenvío de paquetes).

Si el hub todavía no atiende el `resend`, no se rompe nada: los paquetes que falten se declaran como
pérdida real a los 12 segundos, igual que en el escritorio.

## 6. El día de la presentación

- Usar Google Chrome o Microsoft Edge actualizados y tener **una sola pestaña** de VitalCare abierta.
- Hacer un clic en la página antes de empezar, para que el navegador deje sonar las alertas.
- **F11** pone la pantalla completa. **F5** en esta página es el *corte de energía* de la
  demostración, no recargar. Para recargar usar el botón del navegador o **Ctrl + R**.
- Si se quieren en la web los pacientes registrados en el escritorio: cerrar el programa de
  escritorio, abrir la web en *Configuración → Datos del navegador → Importar base de datos* y
  elegir el `vitalcare.db` de la carpeta del proyecto.
