# Golem

Un gólem es un autómata al que le enseñas una tarea y la repite por ti. Esto es
eso: un grabador de macros para Windows (estilo TinyTask, pero sin que la
grabación se corrompa) más un **vigilante de pantalla** que detecta un objeto por
su color o por su imagen y hace clic en él automáticamente, esté donde esté.

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

## Atajos de teclado globales

| Tecla | Acción |
|-------|--------|
| F6    | Empezar / parar grabación |
| F7    | Reproducir / parar reproducción |
| F8    | Cuentagotas: capturar el color bajo el ratón y calibrarse solo |
| F4    | Capturar la imagen bajo el ratón como referencia |
| F9    | Activar / desactivar el vigilante |
| F12   | **Parada total de emergencia** |

Funcionan aunque la ventana del programa no esté en primer plano. **F12** corta
de golpe la reproducción, la vigilancia, la grabación y la repetición
programada: es el botón del pánico si algo se descontrola con el ratón preso.

## Grabador de macros

Captura todos los eventos de entrada con sus tiempos exactos: movimientos del
ratón (unas 125 muestras por segundo), clics, arrastres, rueda y teclado. La
reproducción respeta la cadencia original y admite velocidad (0.5x – 4x) y
repeticiones (0 = bucle infinito).

Si cortas la reproducción a mitad, el programa suelta automáticamente cualquier
tecla o botón que hubiera quedado pulsado. Eso es lo que evita quedarte con el
Shift o el clic izquierdo "enganchados".

Las macros se guardan como archivos `.macro.json`, así que puedes tener varias.

También puedes dejar una macro **repitiéndose sola cada N minutos** sin tenerla
en bucle continuo: marca "Repetir la macro sola cada …". Si en ese momento ya se
estaba grabando o reproduciendo algo, se salta el turno en vez de pisarlo.

## Vigilante (clic automático)

Escanea la pantalla cada X segundos buscando el objetivo y hace clic en su
centro. No depende de coordenadas fijas. Para evitar clics indeseados exige
verlo en **dos escaneos seguidos** antes de actuar, y luego respeta un tiempo de
espera (cooldown) configurable. Puede devolver el ratón a donde estaba y pitar
al clicar, y la barra de estado lleva la cuenta de clics y la hora del último.

Además exige que el objetivo esté **sobre el gris de una interfaz**: mira un
anillo alrededor de la mancha y comprueba que el fondo está casi sin saturación,
como el panel de un cofre. Eso es lo que distingue un objeto en una casilla de un
trozo de paisaje del mismo color — sin ese filtro, las hojas o el césped
iluminados forman manchas del tamaño exacto de una casilla y se llevan el clic.
Si tu objetivo no está dentro de una interfaz, desmarca "Solo sobre una
interfaz".

Cada clic automático guarda además una captura marcada con una cruz roja donde ha
clicado (`golem_clic_1.png` … `_3.png`, rotando). Es la forma de auditar un clic
que ocurrió mientras no mirabas: el botón **Ver último clic** abre la más
reciente.

Hay dos formas de decirle qué buscar:

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

### Modo Color

Máscara de color HSV: rápido, y funciona aunque el objeto cambie de tamaño.

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

- **Buscar desde / hasta (% alto)** — limita la franja vertical de pantalla que
  se analiza. Por ejemplo `0` a `65` ignora la mitad inferior, donde suele estar
  tu propio inventario.
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

Los dos módulos conviven: el vigilante se pausa solo mientras grabas o
reproduces una macro.

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
