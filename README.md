# Golem

Un gólem es un autómata al que le enseñas una tarea y la repite por ti. Esto es
eso, en tres piezas:

- un **grabador de macros** para Windows (estilo TinyTask, pero sin que la
  grabación se corrompa si mueves el ratón);
- un **vigilante de pantalla** que encuentra un objeto y hace clic en él
  automáticamente, esté donde esté;
- un **guion** de varios pasos, con esperas, condiciones y bucles, para tareas
  que no se resuelven con un solo clic.

## Instalación rápida (sin Python)

1. Descarga `Golem.exe` de este repositorio (botón **Download raw file**).
2. Ponlo en una carpeta cualquiera, por ejemplo el Escritorio.
3. Doble clic.

La primera vez Windows puede mostrar el aviso de SmartScreen ("Windows protegió
tu PC") porque el ejecutable no está firmado digitalmente. Se resuelve con
**Más información → Ejecutar de todas formas**.

Junto al ejecutable se crean sus archivos de trabajo: `golem_config.json` (tus
ajustes), `golem_log.txt` (registro), `golem_plantilla.png` (la imagen de
referencia) y `golem_debug.png` (la última prueba de detección).

**Ponlo en una carpeta de verdad antes de abrirlo.** Si lo ejecutas pinchando
directamente en la descarga del navegador, Windows lo lanza desde una carpeta
temporal que luego borra, y con ella se irían tus ajustes y la zona marcada en
cada arranque. Golem detecta ese caso, te avisa en el registro y guarda sus
archivos en `%LOCALAPPDATA%\Golem` para que no se pierdan — pero lo cómodo es
copiar el `.exe` al Escritorio y abrirlo desde ahí.

## Atajos de teclado globales

| Tecla | Acción |
|-------|--------|
| F6    | Empezar / parar grabación |
| F7    | Reproducir / parar reproducción |
| F2    | Marcar la zona de búsqueda (una esquina por pulsación) |
| F8    | Cuentagotas: capturar el color bajo el ratón y calibrarse solo |
| F4    | Capturar la imagen bajo el ratón como referencia |
| F9    | Activar / desactivar el vigilante |
| F10   | Ejecutar / parar el guion de varios pasos |
| F12   | **Parada total de emergencia** |

Funcionan aunque la ventana del programa no esté en primer plano. **F12** corta
de golpe la reproducción, la vigilancia, la grabación y la repetición
programada: es el botón del pánico si algo se descontrola con el ratón preso.

## Grabador de macros

Captura todos los eventos de entrada con sus tiempos exactos: movimientos del
ratón (unas 125 muestras por segundo), clics, arrastres, rueda y teclado. La
reproducción respeta la cadencia original y admite velocidad (0.5x – 4x) y
repeticiones (0 = bucle infinito).

### Movimiento relativo (juegos en 1ª persona)

En un juego que captura el ratón —Minecraft en primera persona, o cualquier
FPS— el cursor del sistema **no se mueve**. El juego pide *raw input* y lee
cuánto se ha desplazado el ratón, no dónde está el puntero. Por eso una macro
normal, que guarda posiciones y las restaura, no gira la cámara.

Marca **Movimiento relativo** antes de grabar y Golem cambia las dos mitades:

- **al grabar**, se registra como receptor de raw input y guarda el
  desplazamiento (`dx`, `dy`) de cada informe del ratón, sin agruparlos ni
  filtrarlos;
- **al reproducir**, los inyecta con `SendInput` en modo relativo, que es lo
  único que llega al juego.

Lo que se graba es lo que se reproduce, número por número: la prueba de ida y
vuelta comprueba que la secuencia de deltas sale idéntica a la que entró. Y va
por debajo de la aceleración del puntero de Windows, así que el juego recibe los
valores crudos del ratón — sin deformar. (En el escritorio verás el cursor
moverse más de la cuenta, porque ahí sí se aplica la aceleración; al juego le
llega exacto.)

En este modo un clic **no** recoloca el cursor: en primera persona el clic va
donde apunta la mira, y moverlo rompería la cámara.

No hay que marcar nada para reproducir. El modo se deduce del contenido de la
macro, así que las macros que ya tuvieras siguen funcionando igual que antes.

Si cortas la reproducción a mitad, el programa suelta automáticamente cualquier
tecla o botón que hubiera quedado pulsado. Eso es lo que evita quedarte con el
Shift o el clic izquierdo "enganchados".

Las macros se guardan como archivos `.macro.json`, así que puedes tener varias.

También puedes dejar una macro **repitiéndose sola cada N minutos** sin tenerla
en bucle continuo: marca "Repetir la macro sola cada …". Si en ese momento ya se
estaba grabando o reproduciendo algo, se salta el turno en vez de pisarlo.

### Escribir un texto cada N minutos

Marca "Escribir un texto solo cada …", pon el texto y listo. Sirve para lo que
hay que mandar cada cierto tiempo por el chat: un comando, un aviso, lo que sea.

- **Abrir el chat con** — la tecla que abre el chat antes de escribir (en
  Minecraft, la `t`). Déjalo vacío si no hace falta abrir nada.
- **Intro al final** — envía el mensaje al terminar.
- **Probarlo ahora** — lo escribe una vez, para ver que sale bien sin esperar.

Dos detalles que importan. Se teclea **carácter a carácter** y con una pausa
después de abrir el chat: volcando la cadena de golpe, un juego a 60 fps se salta
letras.

Y sobre todo: **nunca escribe encima de una macro o un guion en marcha**. Si al
tocarle el turno hay algo moviendo el ratón, espera a que acabe y escribe
entonces — no se salta el turno, porque si la macro dura casi todo el intervalo
eso significaría no escribir nunca. Así que no hace falta que las cuentas cuadren
al minuto: si tu grabación dura 30 minutos y pones el texto cada 31, cuadra; y si
un día se desfasa, el texto simplemente espera el hueco.

## Vigilante (clic automático)

Escanea la pantalla cada X segundos buscando el objetivo y hace clic en su
centro. No depende de coordenadas fijas. Para evitar clics indeseados exige
verlo en **dos escaneos seguidos** antes de actuar, y luego respeta un tiempo de
espera (cooldown) configurable. Puede devolver el ratón a donde estaba y pitar
al clicar, y la barra de estado lleva la cuenta de clics y la hora del último.

Además exige que el objetivo esté **sobre el gris claro de una interfaz**: mira
un anillo alrededor de la mancha y comprueba que el fondo está casi sin
saturación **y es claro**, como el panel de un cofre. Los dos requisitos hacen
falta por separado:

- la **saturación** descarta el paisaje: sin ese filtro, las hojas o el césped
  iluminados forman manchas del tamaño exacto de una casilla y se llevan el clic;
- el **brillo** descarta el texto de colores del HUD (el nombre del bioma, los
  marcadores del servidor), que también está sobre un fondo poco saturado, pero
  oscuro.

Si tu objetivo no está dentro de una interfaz, desmarca "Solo sobre una
interfaz".

### Marcar la zona de búsqueda (F2)

El filtro anterior no lo arregla todo. Si en tu propio inventario hay otro objeto
del mismo tono, los dos están sobre gris y el vigilante clicará el más grande de
los dos, que no tiene por qué ser el bueno. Y si el paisaje del fondo es
justamente de ese color — la hierba seca del bioma *Plains* es casi del mismo
tono que un cristal verde lima — el color por sí solo no puede separarlos.

La solución es decirle **dónde** mirar: pon el ratón en una esquina del área que
te interesa y pulsa **F2**, lleva el ratón a la esquina opuesta y pulsa **F2**
otra vez. A partir de ahí no mira nada de fuera de ese rectángulo. El botón
**Toda la pantalla** lo deshace.

Son dos pulsaciones en vez de un arrastre a propósito: así funciona igual de bien
sobre un juego a pantalla completa, donde no se puede dibujar un recuadro encima.

Cuanto más ajustada sea la zona, menos se puede equivocar. En el caso del cofre
de Minecraft, marcar solo la rejilla del cofre — **sin incluir la fila de tu
inventario** — deja el problema resuelto sin depender del color, porque dentro no
hay nada más.

La zona se guarda en porcentajes enteros de la pantalla, así que en 1920 px cada
paso son unos 19 px. Al redondear crece siempre hacia fuera, nunca hacia dentro,
para no recortar justo lo que acabas de marcar; el registro te dice los píxeles
marcados y los que se han guardado. Por eso no merece la pena marcar un
rectángulo mucho más pequeño que eso.

Cada clic automático guarda además una captura marcada con una cruz roja donde ha
clicado (`golem_clic_1.png` … `_3.png`, rotando). Es la forma de auditar un clic
que ocurrió mientras no mirabas: el botón **Ver último clic** abre la más
reciente.

Hay tres formas de decirle qué buscar:

### Modo Lo único con color (el de por defecto)

No mira el tono: solo si el píxel tiene color. Las casillas vacías de un cofre
son gris puro, así que dentro de esa zona el objeto es lo único que puede salir,
sea verde, rojo o rosa. **No hay nada que calibrar.**

Es más fiable que buscar un tono concreto por tres razones:

- no depende de acertar con el color, que con un cristal translúcido cambia según
  lo que haya detrás;
- el brillo morado de un objeto encantado también es color, así que en vez de
  romper la mancha se le suma — el problema se convierte en ayuda;
- funciona igual si mañana el servidor cambia el objeto por otro de otro color.

A cambio exige que la zona esté bien puesta, porque en un juego casi todo tiene
color. Si activas la vigilancia sin zona marcada y sin el filtro de interfaz, el
programa se niega y te lo dice, en vez de ponerse a clicar el paisaje.

### Objetos encantados

Si el objetivo es un objeto **encantado**, lleva encima el brillo morado animado
que barre el sprite. En cada instante tapa una parte distinta, así que una sola
captura ve el color roto en trozos que cambian de forma y de tamaño.

Por eso el vigilante **une varios fotogramas** en cada escaneo (ajuste
"Fotogramas unidos", 3 por defecto): un píxel cuenta si tenía el color buscado en
*alguna* de las capturas, y luego un cierre morfológico vuelve a pegar los trozos
en una sola mancha. El cuentagotas hace lo mismo en el tiempo — cinco lecturas y
se queda con la mediana — y te avisa en el registro si ve que el color
parpadeaba.

Con objetos encantados **no uses el modo Imagen de referencia**: la plantilla
guardaría el brillo en una posición que no se repite nunca.

### Modo Un color concreto

Máscara de color HSV. Solo hace falta cuando dentro de la zona hay varias cosas
con color y hay que quedarse con una.

**1. Cuentagotas.** Deja en pantalla el objeto que hay que clicar, pon el ratón
justo encima y pulsa **F8**. El programa lee el color de esos píxeles (mediana de
un cuadro de 5×5 y de cinco lecturas seguidas, para no tragarse un borde, una
sombra ni un brillo animado) y ajusta solo el tono, la saturación y el brillo con
un margen generoso. En el registro verás el RGB y el rango que ha fijado.

Esto es lo que evita adivinar números: un cristal translúcido de Minecraft sale
con tono ~45 y saturación baja, muy lejos de los valores de un verde puro.

**2. Probar detección.** Pulsa **Probar detección (3 s)**, deja la pantalla como
cuando aparece el aviso y mira el registro: lista todos los candidatos con sus
coordenadas y su área, **sin hacer clic**. El nº 1 es el que se clicaría.

Los descartados salen con el motivo escrito: demasiado pequeño, demasiado grande,
demasiado ancho para una casilla o "no está sobre una interfaz gris". Eso te dice
qué ajuste tocar en vez de tener que adivinarlo.

También guarda `golem_debug.png` junto al ejecutable, con los píxeles detectados
en rojo y cada candidato recuadrado (verde = aceptado, amarillo = descartado). El
botón **Ver imagen de depuración** lo abre.

### Modo Imagen de referencia

Cuando el color no basta porque hay otras cosas del mismo tono, pon el ratón
sobre el objetivo y pulsa **F4**: recorta un cuadro (por defecto 40×40 px) y lo
usa como plantilla, buscándolo por correlación en toda la pantalla. Es mucho más
selectivo, a cambio de exigir que el objetivo se vea siempre igual — mismo
tamaño, misma resolución, mismo zoom del juego.

Si no lo encuentra, el registro te dice el **mejor parecido** que ha logrado, así
que sabes si el problema es el umbral (bájalo) o la plantilla (recaptúrala).

### Si detecta el objeto equivocado

Cuando en el registro aparece más de un candidato y el correcto no es el nº 1
(típico si tu inventario tiene otros objetos del mismo color):

- **Marcar zona (F2)** — es lo que más ayuda, y con diferencia. Encierra el área
  donde aparece el objetivo y todo lo de fuera deja de existir.
- **Zona: alto / ancho de % a %** — lo mismo a mano, si prefieres teclear los
  números. Por ejemplo `0` a `65` de alto ignora la mitad inferior, donde suele
  estar tu propio inventario.
- **Área mín. / máx. (px²)** — el objeto de una casilla ronda unos cientos de
  px²; súbele el mínimo para descartar motas de color y bájale el máximo para
  descartar paredes o fondos grandes del mismo tono.
- **Lado máx. (px)** — descarta manchas más anchas o más altas que una casilla,
  como una pared o el césped del fondo. Es el filtro que más falsos positivos
  quita, porque una mancha inmensa puede tener un área dentro del límite si es
  delgada.
- **± tolerancia** — bájala para exigir un color más parecido al capturado.
- **Solo sobre una interfaz** — déjalo marcado si el objetivo aparece dentro de
  un menú o un cofre.
- O cambia al **modo Imagen de referencia**, que distingue la textura y no solo
  el tono (salvo con objetos encantados).

### Receta corta

Si lo que quieres es clicar un objeto que aparece en una casilla de un menú:

1. Deja en pantalla el aviso, con el objeto visible.
2. Ratón en una esquina de la rejilla → **F2**; ratón en la esquina opuesta →
   **F2**. Deja fuera tu propio inventario.
3. **Probar detección (3 s)** y mira el registro: debería salir **un solo
   candidato**, en las coordenadas del objeto. Si sale más de uno, aprieta la
   zona.
4. **F9** para activar la vigilancia, y ya se queda esperando.

No hay paso de calibración: el modo por defecto no la necesita. Si no detecta
nada, el registro te dice qué veía dentro de la zona (el tono, la saturación y el
brillo del fondo y de lo más coloreado que hubiera), que es lo que hace falta
para saber si el problema es la zona o un umbral.

Los dos módulos conviven: el vigilante se pausa solo mientras grabas o
reproduces una macro.

## Guion (varios pasos)

El vigilante sabe hacer una cosa: *veo esto → clico esto*. El guion encadena
pasos y puede volver atrás, que es lo que hace falta para una tarea con estados:
espera algo, actúa, comprueba el resultado, vuelve a empezar.

### Objetivos con nombre

Un guion puede mirar sitios distintos con criterios distintos, así que primero se
guardan los objetivos. En la pestaña **Vigilante** dejas puesto lo que quieres
buscar (modo, zona con F2, umbrales), y en la pestaña **Guion** le pones un
nombre y pulsas **Guardar objetivo**. Queda una copia de todos esos ajustes,
zona incluida, y se guarda con la configuración.

El nombre va sin espacios, porque en el guion se escribe suelto.

### Instrucciones

Una por línea. Todo lo que vaya tras `#` es un comentario.

| Instrucción | Qué hace |
|---|---|
| `buscar <objetivo> [segundos] [si_falla …]` | Espera a que aparezca. Sin segundos, espera indefinidamente. Deja apuntada su posición. |
| `desaparecer <objetivo> [segundos]` | Espera a que deje de verse. |
| `clic [doble\|derecho\|medio]` | Clica donde se vio el último objetivo. |
| `esperar <segundos>` | Pausa. |
| `tecla <nombre>` | Pulsa una tecla: `esc`, `intro`, `espacio`, `f`, `1`… |
| `escribir <texto>` | Teclea el texto tal cual. |
| `macro <archivo.macro.json>` | Reproduce una macro grabada y espera a que acabe. |
| `pitar` | Un pitido, para saber por dónde va sin mirar. |
| `ir <nº>` / `repetir` / `parar` | Salta a un paso, vuelve al 1, o termina. |

En `si_falla` puedes poner `parar` (lo que hace por defecto), `seguir`, `repetir`
o `ir <nº>`. Solo tiene sentido con un límite de segundos: sin él la búsqueda no
falla nunca porque espera para siempre.

El caso del captcha queda así:

```
buscar cristal
clic
esperar 2
desaparecer cristal 30
repetir
```

### Comprobar antes de ejecutar

**Comprobar** analiza el guion sin ejecutarlo y te lo cuenta en palabras, paso
por paso y numerado:

```
1. espera a ver 'cristal' (esperando lo que haga falta); si no aparece, para el guion
2. clic donde se vio el último objetivo
3. espera 2 s
4. espera a que 'cristal' desaparezca (hasta 30 s)
5. vuelve al paso 1
```

Los números son los que usan `ir` y `si_falla ir`. Si algo está mal, te dice la
línea y qué le pasa, en vez de fallar a medias con el ratón en marcha.

Hay dos comprobaciones que evitan estropicios: un `clic` sin un `buscar` antes se
rechaza (no sabría dónde clicar), y un bucle que no espera nada tampoco se acepta,
porque se dispararía sin freno.

### Ejecutar

**F10** lo lanza y lo para. No arranca si está grabando, reproduciendo o con la
vigilancia activa, porque los dos querrían mover el ratón. **F12** lo corta como
todo lo demás.

Cada paso se ve en el registro con su número y lo que ha hecho, y la barra de
estado dice en qué paso va. Igual que el vigilante, `buscar` exige ver el
objetivo en **dos escaneos seguidos** antes de darlo por bueno.

## Ejecutar desde el código fuente

Requiere Python 3.10 o superior.

```bash
pip install -r requirements.txt
python main.py
```

## Compilar el .exe

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name Golem main.py
```

El ejecutable aparece en `dist/Golem.exe`.

## Notas

La ventana **no** se pone encima del resto por defecto, precisamente para no
molestar sobre un juego a pantalla completa. Si la quieres siempre visible,
marca "Ventana siempre visible".

Windows necesita permisos normales de usuario para capturar entrada global; no
hace falta ejecutarlo como administrador salvo que la aplicación de destino sí
lo sea (en ese caso ejecuta Golem también como administrador, porque Windows no
permite enviar entrada de un proceso de menor privilegio a uno mayor).

Usar automatización de entrada puede ir contra las normas de algunos servicios o
juegos, aunque no exista detección automática. Úsalo bajo tu criterio.
