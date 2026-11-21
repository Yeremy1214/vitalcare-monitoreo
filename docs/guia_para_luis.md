# Cómo trabajamos con este repositorio

Guía corta para que los dos trabajemos sobre el mismo código sin pisarnos.

## La primera vez

1. Instalar [Git](https://git-scm.com/downloads) y [Python 3.10 o superior](https://www.python.org/downloads/).
   En el instalador de Python, marcar **Add Python to PATH**.
2. Abrir una terminal en la carpeta donde quieras tener el proyecto y clonar:

   ```
   git clone https://github.com/<usuario>/vitalcare-monitoreo.git
   cd vitalcare-monitoreo
   ```

3. Instalar la única librería obligatoria:

   ```
   pip install -r requirements.txt
   ```

4. Comprobar que todo funciona en tu máquina:

   ```
   python pruebas_testbed.py
   ```

   Debe terminar con `32 de 32 pruebas correctas`.

5. Abrir la aplicación:

   ```
   python app_testbed.py
   ```

## Cada vez que te sientes a trabajar

Antes de tocar nada, traer lo último:

```
git pull
```

Eso actualiza tu copia con los cambios que haya subido el otro. Si no lo haces, trabajas sobre
una versión vieja y después cuesta unir las dos.

## Cuando termines un cambio

```
git add .
git commit -m "Describe en una línea qué cambiaste"
git push
```

Si al hacer `push` te dice que el remoto tiene cambios, haz primero `git pull`, resuelve lo que
marque y vuelve a subir.

## Probar con la pulsera real

La pulsera física la tienes tú, así que esta parte te toca:

1. En la aplicación, elegir la fuente **Bluetooth (BLE)** y escribir el nombre con el que se anuncia
   la pulsera (por defecto `W01`) o su dirección MAC.
2. Pulsar **Conectar**.
3. Si no aparece nada, revisar que el firmware use los UUID y el formato de
   [contrato_pulsera.md](contrato_pulsera.md). Es el acuerdo entre tu firmware y mi programa.

Para probar por WiFi sin Bluetooth, correr en otra terminal:

```
python emisor_udp_prueba.py <ip-de-la-pc-que-recibe> 5005
```

## Qué no se sube al repositorio

El archivo `.gitignore` deja fuera la base de datos (`vitalcare.db`), la carpeta `datos_sesion/` y
las capturas de evidencia. Son datos que cada uno genera al correr el programa, así que no tiene
sentido compartirlos y además ensuciarían el historial. El código y la documentación sí se suben.

## Si algo se rompe

`git status` dice en qué estado está tu copia. `git log --oneline -10` muestra los últimos cambios.
Si quieres volver a como estaba el archivo antes de tocarlo: `git checkout -- nombre_del_archivo`.

Y si se complica, me escribes antes de forzar nada.
