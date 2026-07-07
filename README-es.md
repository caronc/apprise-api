# Apprise API

Aprovecha [Informar](https://github.com/caronc/apprise) a través de su red con una API fácil de usar.

*   Envía notificaciones a más de 80 servicios.
*   Una puerta de entrada increíblemente ligera a Apprise.
*   Un microservicio listo para la producción a su disposición.

Apprise API fue diseñado para adaptarse fácilmente a los ecosistemas existentes (y nuevos) que buscan una solución de notificación simple.

[![Paypal](https://img.shields.io/badge/paypal-donate-green.svg)](https://www.paypal.com/donate/?hosted_button_id=CR6YF7KLQWQ5E)
[![Follow](https://img.shields.io/twitter/follow/l2gnux)](https://twitter.com/l2gnux/)<br/>
[![Discord](https://img.shields.io/discord/558793703356104724.svg?colorB=7289DA\&label=Discord\&logo=Discord\&logoColor=7289DA\&style=flat-square)](https://discord.gg/EGg4rhmpC2)
[![Build Status](https://travis-ci.org/caronc/apprise-api.svg?branch=master)](https://travis-ci.org/caronc/apprise-api)
[![CodeCov Status](https://codecov.io/github/caronc/apprise-api/branch/master/graph/badge.svg)](https://codecov.io/github/caronc/apprise-api)
[![Docker Pulls](https://img.shields.io/docker/pulls/caronc/apprise.svg?style=flat-square)](https://hub.docker.com/r/caronc/apprise)

## Capturas de pantalla

Hay un pequeño incorporado *Administrador de configuración* a los que se puede acceder opcionalmente a través de su navegador web, lo que le permite crear y guardar tantas configuraciones como desee. Cada configuración se diferencia por una `{KEY}` que usted decida:<br/>
![Screenshot of GUI - Using Keys](https://raw.githubusercontent.com/caronc/apprise-api/master/Screenshot-1.png)<br/>

A continuación se muestra una captura de pantalla de cómo puede asignar sus URL de Apprise a su `{KEY}`. Puede definir text o YAML [Configuraciones de Apprise](https://appriseit.com/config/).<br/>
![Screenshot of GUI - Configuration](https://raw.githubusercontent.com/caronc/apprise-api/master/Screenshot-2.png)

Una vez que haya guardado su configuración, podrá usar el *Notificación* para enviar sus mensajes a uno o más de los servicios que definió en su configuración. Puedes usar la etiqueta `all` para notificar a todos sus servicios, independientemente de la etiqueta que se les haya asignado de otra manera.
![Screenshot of GUI - Notifications](https://raw.githubusercontent.com/caronc/apprise-api/master/Screenshot-3.png)

Al final del día, la GUI simplemente ofrece una interfaz fácil de usar para la misma API con la que los desarrolladores pueden interactuar directamente si lo desean.

## Instalación

Las siguientes opciones deberían permitirle acceder a la API en: `http://localhost:8000/` desde su navegador.

Usando [dockerhub](https://hub.docker.com/r/caronc/apprise) puede hacer lo siguiente:

```bash
# Retrieve container
docker pull caronc/apprise:latest

# Start it up:
# /config is used for a persistent store, you do not have to mount
#         this if you don't intend to use it.
docker run --name apprise \
   -p 8000:8000 \
   -v /var/lib/apprise/config:/config \
   -d caronc/apprise:latest
```

Un `docker-compose.yml` el archivo ya está configurado para otorgarle un entorno simulado listo para la producción instantánea:

```bash
# Docker Compose
docker-compose up
```

### Permisos de directorio de configuración

Bajo el capó, un servicio NginX está leyendo/escribiendo sus archivos de configuración como usuario (y grupo) `www-data` que generalmente tiene el identificador de `33`.  En preparación para que no reciba el error: `An error occured saving configuration.` Considere también la posibilidad de configurar su local `/var/lib/apprise/config` permisos como:

```bash
# Create a user/group (if one doesn't already exist) owned
# by the user and group id of 33
id 33 &>/dev/null || sudo useradd \
   --system --no-create-home --shell /bin/false \
    -u 33 -g 33 www-data

# Securely set the directory limiting access to only those who
# are part of the www-data group:
sudo chmod 770 -R /var/lib/apprise/config
sudo chown 33:33 -R /var/lib/apprise/config

# Now optionally add yourself to the group if you wish to be able to view
# contents.
sudo usermod -a -G 33 $(whoami)

# You may need to log out and back in again for the above usermod
# to reflect on you.  Alternatively you can just type the following
# and it will work as a temporary solution:
sudo su - $(whoami)
```

Alternativamente, una solución sucia es simplemente establecer el directorio con permisos completos de lectura / escritura (lo cual no es ideal en un entorno de producción):

```bash
# Grant full permission to the local directory you're saving your
# Apprise configuration to:
chmod 777 /var/lib/apprise/config
```

## Detalles de Dockerfile

Se admiten las siguientes arquitecturas: `386`, `amd64`, `arm/v6`, `arm/v7`y `arm64`. Se pueden utilizar las siguientes etiquetas:

*   `latest`: Apunta a la última compilación estable.
*   `edge`: Apunta al último empuje a la rama maestra.

## URL de Apprise

📣 Para activar una notificación, primero debe definir una o más [URL de Apprise](https://appriseit.com) para respaldar los servicios que desea aprovechar. ¡Apprise admite más de 80 servicios de notificación hoy en día y siempre se está expandiendo para agregar soporte para más! Visite https://appriseit.com para ver la lista cada vez mayor de los servicios admitidos en la actualidad.

## Detalles de la API

### Solución apátrida

Es posible que algunas personas deseen tener solo una solución de sidecar que requiera el uso de cualquier almacenamiento persistente.  El siguiente punto de enlace de api se puede utilizar para enviar directamente una notificación de su elección a cualquiera de los [servicios soportados por Apprise](https://appriseit.com/services/) sin ningún requisito basado en almacenamiento:

| | de ruta Método | Descripción |
|------------- | ------ | ----------- |
| `/notify/` |  PUBLICAR | Envía una o más notificaciones a las direcciones URL identificadas como parte de la carga útil o a las identificadas en la variable de entorno `APPRISE_STATELESS_URLS`. <br/>*Parámetros de carga útil*<br/>📌 **direcciones URL**: Una o más URL que identifiquen a dónde se debe enviar la notificación. Si este campo no se especifica, asume automáticamente el `settings.APPRISE_STATELESS_URLS` valor o `APPRISE_STATELESS_URLS` variable de entorno.<br/>📌 **cuerpo**: El cuerpo de su mensaje. Este es un campo obligatorio.<br/>📌 **título**: Opcionalmente, defina un título para que vaya junto con el *cuerpo*.<br/>📌 **tipo**: define el tipo de mensaje que desea enviar.  Las opciones válidas son `info`, `success`, `warning`y `failure`. Si no *tipo* se especifica a continuación `info` es el valor predeterminado utilizado.<br/>📌 **formato**: Opcionalmente, identifique el formato de texto de los datos que está alimentando Apprise. Las opciones válidas son `text`, `markdown`, `html`. El valor predeterminado si no se especifica nada es `text`.

Aquí hay un *apátrida* Ejemplo de cómo se podría enviar una notificación (utilizando `/notify/`):

```bash
# Send your notifications directly
curl -X POST -d 'urls=mailto://user:pass@gmail.com&body=test message' \
    http://localhost:8000/notify

# Send your notifications directly using JSON
curl -X POST -d '{"urls": "mailto://user:pass@gmail.com", "body":"test message"}' \
    -H "Content-Type: application/json" \
    http://localhost:8000/notify
```

### Solución de almacenamiento persistente

Puede guardar previamente toda la configuración de Apprise y/o el conjunto de URL de Apprise y asociarlas con un `{KEY}` de su elección. Una vez establecida, la configuración persiste para su recuperación por el `apprise` [Herramienta CLI](https://appriseit.com/guides/) o cualquier otra integración personalizada que haya configurado. El sitio web incorporado viene con una interfaz de usuario que también puede usar para aprovechar estas llamadas a la API. Aquellos que deseen crear su propia aplicación en torno a esto pueden usar los siguientes puntos finales de la API:

| | de ruta Método | Descripción |
|------------- | ------ | ----------- |
| `/add/{KEY}` |  PUBLICAR | Guarda la configuración de Apprise (o el conjunto de direcciones URL) en el almacén persistente.<br/>*Parámetros de carga útil*<br/>📌 **direcciones URL**: Defina una o más URL de Apprise aquí. Utilice una coma y/o espacio para separar una URL de la siguiente.<br/>📌 **configuración**: Proporcione el contenido de una configuración de Apprise basada en YAML o TEXT.<br/>📌 **formato**: Este campo solo es obligatorio si ha especificado el *configuración* parámetro. Se utiliza para indicar al servidor cuál de los tipos de configuración admitidos (Apprise) está pasando. Las opciones válidas son *Mensaje de texto* y *yaml*.
| `/del/{KEY}` |  PUBLICAR | Quita la configuración de Apprise del almacén persistente.
| `/get/{KEY}` |  PUBLICAR | Devuelve la configuración de Apprise del almacén persistente.  Esto se puede utilizar directamente con el *Apprise CLI* y/o el *AppriseConfig()* objeto ([ver aquí para más detalles](https://appriseit.com/config/)).
| `/notify/{KEY}` |  PUBLICAR | Envía notificaciones a todos los puntos finales que haya configurado previamente asociados a un *{CLAVE}*.<br/>*Parámetros de carga útil*<br/>📌 **cuerpo**: El cuerpo de su mensaje. Este es el *solamente* campo obligatorio.<br/>📌 **título**: Opcionalmente, defina un título para que vaya junto con el *cuerpo*.<br/>📌 **tipo**: define el tipo de mensaje que desea enviar.  Las opciones válidas son `info`, `success`, `warning`y `failure`. Si no *tipo* se especifica a continuación `info` es el valor predeterminado utilizado.<br/>📌 **etiqueta**: Opcionalmente notifique solo a los etiquetados en consecuencia.<br/>📌 **formato**: Opcionalmente, identifique el formato de texto de los datos que está alimentando Apprise. Las opciones válidas son `text`, `markdown`, `html`. El valor predeterminado si no se especifica nada es `text`.
| `/json/urls/{KEY}` |  HAZTE | Devuelve un objeto de respuesta JSON que contiene todas las direcciones URL y etiquetas asociadas a la clave especificada.

A modo de ejemplo, el `/json/urls/{KEY}` la respuesta podría devolver algo como esto:

```json
{
   "tags": ["devops", "admin", "me"],
   "urls": [
      {
         "url": "slack://TokenA/TokenB/TokenC",
         "tags": ["devops", "admin"]
      },
      {
         "url": "discord://WebhookID/WebhookToken",
         "tags": ["devops"]
      },
      {
         "url": "mailto://user:pass@gmail.com",
         "tags": ["me"]
      }
   ]
}
```

Puede pasar atributos a la `/json/urls/{KEY}` como `privacy=1` que oculta las contraseñas y los tokens secretos al devolver la respuesta.  También puede configurar `tag=` y filtre los resultados devueltos en función de un conjunto de etiquetas separadas por comas. si no `tag=` se especifica, a continuación `tag=all` se utiliza como valor predeterminado.

Aquí hay un ejemplo usando `curl` en cuanto a cómo alguien podría enviar una notificación a todos los asociados con la etiqueta `abc123` (usando `/notify/{key}`):

```bash
# Send notification(s) to a {key} defined as 'abc123'
curl -X POST -d "body=test message" \
    http://localhost:8000/notify/abc123

# Here is the same request but using JSON instead:
curl -X POST -d '{"body":"test message"}' \
    -H "Content-Type: application/json" \
    http://localhost:8000/notify/abc123
```

🏷️ También puede aprovechar *Etiquetado* lo que le permite asociar una o más etiquetas con sus URL de Apprise.  Al hacer esto, las notificaciones solo necesitan ser referidas por su nombre de etiqueta de notificación fácil de recordar, como `devops`, `admin`, `family`etc. Puede agrupar muy fácilmente más de un servicio de notificaciones en el mismo *etiqueta* permitiéndole notificar a un grupo de servicios a la vez.  Esto se logra a través de archivos de configuración ([documentado aquí](https://appriseit.com/guides/#leverage-tagging/)) que se puede guardar en el almacenamiento persistente previamente asociado a un `{KEY}`.

```bash
# Send notification(s) to a {KEY} defined as 'abc123'
# but only notify the URLs associated with the 'devops' tag
curl -X POST -d 'tag=devops&body=test message' \
    http://localhost:8000/notify/abc123

# Here is the same request but using JSON instead:
curl -X POST -d '{"tag":"devops", "body":"test message"}' \
    -H "Content-Type: application/json" \
    http://localhost:8000/notify/abc123
```

### Notas de la API

*   `{KEY}` debe tener una longitud de 1-64 caracteres alfanuméricos. Además de esto, el subrayado (`_`) y guión (`-`) también se aceptan.
*   Especifique el `Content-Type` de `application/json` Para utilizar la compatibilidad con JSON, de lo contrario, el formato esperado predeterminado es `application/x-www-form-urlencoded` (ya sea que se especifique o no).
*   No se requiere autenticación (o cifrado SSL) para usar esta API; esto es por diseño. La intención aquí es ser un microservicio ligero y rápido.
*   No hay dependencias adicionales (como requisitos de base de datos, etc.) si decide utilizar el almacén persistente opcional (montado como `/config`).

### Variables de entorno

El uso de variables de entorno le permite proporcionar anulaciones a la configuración predeterminada.

| | variable Descripción |
|--------------------- | ----------- |
| `APPRISE_CONFIG_DIR` | Define una ubicación de almacén persistente (opcional) de todos los archivos de configuración guardados. Por defecto:<br/> - La configuración se escribe en el `apprise_api/var/config` cuando se utiliza el *Django* `manage runserver` Guión. Sin embargo, para la ruta de acceso para el contenedor es `/config`.
| `APPRISE_STATELESS_URLS` | Para una solución no persistente, puede aprovechar esta variable global. Use esto para definir un conjunto predeterminado de URL de Apprise para notificar cuando se usan llamadas a la API a `/notify`.  Si no `{KEY}` se define al llamar `/notify` a continuación, se utilizan las URL definidas aquí en su lugar. De forma predeterminada, no se define nada para esta variable.
| `APPRISE_STATEFUL_MODE` | Esto se puede configurar en los siguientes modos posibles:<br/>📌 **picadillo**: Este es también el valor predeterminado.  Almacena la configuración del servidor en un formato hash que se puede indexar y comprimir fácilmente.<br/>📌 **sencillo**: La configuración se escribe directamente en el disco utilizando el comando `{KEY}.cfg` (si `TEXT` basado) y `{KEY}.yml` (si `YAML` basado).<br/>📌 **Deshabilitado**: Denegar directamente cualquier consulta de lectura/escritura al almacén con estado de los servidores.  Desactive efectivamente la función Apprise Stateful por completo.
| `APPRISE_CONFIG_LOCK` | Bloquea el alojamiento de su API para que ya no pueda eliminar/actualizar/acceder a información con estado. Se sigue haciendo referencia a la configuración cuando se realizan llamadas con estado a `/notify`.  La idea de este cambio es permitir que alguien establezca su configuración (Apprise) y luego, como táctica de seguridad adicional, puede optar por bloquear su configuración (en un estado de solo lectura). Aquellos que usan la herramienta APprise CLI aún pueden hacerlo, sin embargo, el `--config` (`-c`) ya no hará referencia correctamente a este punto de acceso. Sin embargo, puede utilizar el `apprise://` plugin sin ningún problema ([ver aquí para más detalles](https://appriseit.com/services/apprise_api/)). De forma predeterminada, el valor predeterminado es `no` y, sin embargo, se puede establecer en `yes` simplemente definiendo la variable global como tal.
| `APPRISE_DENY_SERVICES` | Un conjunto de entradas separadas por comas que identifican a qué plugins denegar el acceso. Solo necesita identificar una entrada de esquema asociada con un complemento para, a su vez, deshabilitarlo todo.  Por lo tanto, si desea deshabilitar el `glib` plugin, no es necesario incluir adicionalmente `qt` también ya que se incluye como parte de la (`dbus`) paquete; en consecuencia, especificando `qt` a su vez deshabilitaría el `glib` módulo también (otra forma de realizar la misma tarea).  Para excluir/deshabilitar más el único servicio ascendente, simplemente especifique entradas adicionales separadas por un `,` (coma) o ` ` (espacio). El `APPRISE_DENY_SERVICES` Las entradas se omiten si el `APPRISE_ALLOW_SERVICES` está identificado. De forma predeterminada, esto se inicializa en `windows, dbus, gnome, macosx, syslog` (bloqueando la emisión de acciones locales dentro del contenedor docker)
| `APPRISE_ALLOW_SERVICES` | Un conjunto de entradas separadas por comas que identifican a qué plugins permitir el acceso. De todos modos, solo puede usar caracteres alfanuméricos, como lo es la restricción de los esquemas de Apprise (schema://).  Para incluir exclusivamente más el único servicio ascendente, simplemente especifique entradas adicionales separadas por un `,` (coma) o ` ` (espacio). El `APPRISE_DENY_SERVICES` Las entradas se omiten si el `APPRISE_ALLOW_SERVICES` está identificado.
| `SECRET_KEY`       | Una variable de Django que actúa como un *sal* para la mayoría de las cosas que requieren seguridad. Esta API lo utiliza para las secuencias hash al escribir los archivos de configuración en el disco (`hash` modo solamente).
| `ALLOWED_HOSTS`    | Una lista de cadenas que representan los nombres de host/dominio que esta API puede servir. Esta es una medida de seguridad para evitar ataques de encabezado de host HTTP, que son posibles incluso en muchas configuraciones de servidor web aparentemente seguras. De forma predeterminada, se establece en `*` permitiendo cualquier host. Utilice el espacio para delimitar más de un host.
| `APPRISE_RECURSION_MAX` | Esto define el número de veces que un servidor api de Apprise puede (recursivamente) llamar a otro.  Esto es para apoyar y mitigar el abuso a través de [el `apprise://` esquema](https://appriseit.com/services/apprise_api/) para aquellos que eligen usarlo. Cuando se aprovecha correctamente, puede aumentar este valor (máximo de recursividad) y equilibrar con éxito la carga del manejo de muchas solicitudes de notificación a través de muchos servidores API adicionales.  De forma predeterminada, este valor se establece en `1` (uno).
| `BASE_URL`    | Aquellos que alojan la API detrás de un proxy que requiere una subruta para obtener acceso a esta API también deben especificar esta ruta aquí.  De forma predeterminada, esto no está configurado en absoluto.
| `LOG_LEVEL`    | Ajuste el nivel de registro a la consola. Los valores posibles son `CRITICAL`, `ERROR`, `WARNING`, `INFO`y `DEBUG`.
| `DEBUG`            | De forma predeterminada, el valor predeterminado es `no` y, sin embargo, se puede establecer en `yes` simplemente definiendo la variable global como tal.

## Entorno de desarrollo

Lo siguiente debería proporcionarle un entorno de desarrollo de trabajo para probar:

```bash
# Create a virtual environment in the same directory you
# cloned this repository to:
python -m venv .

# Activate it now:
. ./bin/activate

# install dependencies
pip install -r dev-requirements.txt -r requirements.txt

# Run a dev server (debug mode) accessible from your browser at:
# -> http://localhost:8000/
./manage.py runserver
```

Algunas otras notas de desarrollo útiles:

```bash
# Check for any lint errors
flake8 apprise_api

# Run unit tests
pytest apprise_api
```

## Integración Apprise

Primero tendrás que tenerlo instalado:

```bash
# install apprise into your environment
pip install apprise
```

### Ejemplo de extracción de CLI de Apprise

Un escenario en el que desea sondear la API para su configuración:

```bash
# A simple example of the Apprise CLI
# pulling down previously stored configuration
apprise -vvv --body="test message" --config=http://localhost:8000/get/{KEY}
```

También puede aprovechar el `import` parámetro admitido en los archivos de configuración de Apprise si `APPRISE_CONFIG_LOCK` no está configurado en el servidor al que está accediendo:

```nginx
# Linux users can place this in ~/.apprise
# Windows users can place this info in %APPDATA%/Apprise/apprise

# Swap {KEY} with your apprise key you configured on your API
import http://localhost:8000/get/{KEY}
```

Ahora solo obtendrá automáticamente el archivo de configuración sin la necesidad de la `--config` interruptor:

```bash
# Configuration is automatically loaded from our server.
apprise -vvv --body="my notification"
```

Si utilizó el etiquetado, puede notificar al servicio específico de la siguiente manera:

```bash
# Configuration is automatically loaded from our server.
apprise -vvv --tag=devops --body="Tell James GitLab is down again."
```

Si su servidor tiene el `APPRISE_CONFIG_LOCK` set, aún puede aprovechar [el `apprise://` plugin](https://appriseit.com/services/apprise_api/) para activar nuestras notificaciones guardadas previamente:

```bash
# Swap {KEY} with your apprise key you configured on your API
apprise -vvv --body="There are donut's in the front hall if anyone wants any" \
   apprise://localhost:8000/{KEY}
```

Alternativamente, podemos configurar esto en un archivo de configuración e incluso vincular nuestras etiquetas locales a nuestras etiquetas ascendentes de la siguiente manera:

```nginx
# Linux users can place this in ~/.apprise
# Windows users can place this info in %APPDATA%/Apprise/apprise

# Swap {KEY} with your apprise key you configured on your API
devteam=apprise://localhost:8000/{KEY}?tags=devteam

# the only catch is you need to map your tags on the local server to the tags
# you want to pass upstream to your Apprise server using this method.
# In the above we tied the local keyword `friends` to the apprise server using the tag `friends`
```

Podríamos activar nuestra notificación a nuestros amigos ahora como:

```bash
# Trigger our service:
apprise -vvv --tag=devteam --body="Guys, don't forget about the audit tomorrow morning."
```

### Ejemplo de extracción de AppriseConfig()

Uso del [Biblioteca de Python de Apprise](https://github.com/caronc/apprise), puede acceder y cargar fácilmente su configuración guardada fuera de esta API para usarla en futuras notificaciones.

```python
import apprise

# Point our configuration to this API server:
config = apprise.AppriseConfig()
config.add('http://localhost:8000/get/{KEY}')

# Create our Apprise Instance
a = apprise.Apprise()

# Store our new configuration
a.add(config)

# Send a test message
a.notify('test message')
```
