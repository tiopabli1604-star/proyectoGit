# MacroPro

Grabador y reproductor de macros para Windows (estilo TinyTask, pero sin que la
grabación se corrompa) con un módulo extra de **Auto-Captcha**: detecta una zona
verde en pantalla y hace clic en su centro automáticamente.

## Instalación rápida (sin Python)

1. Descarga `MacroPro.exe` de este repositorio (botón **Download raw file**).
2. Ponlo en una carpeta cualquiera, por ejemplo el Escritorio.
3. Doble clic.

La primera vez Windows puede mostrar el aviso de SmartScreen ("Windows protegió
tu PC") porque el ejecutable no está firmado digitalmente. Se resuelve con
**Más información → Ejecutar de todas formas**.

Junto al ejecutable se creará un `macropro_config.json` con tus ajustes.

## Atajos de teclado globales

| Tecla | Acción |
|-------|--------|
| F6    | Empezar / parar grabación |
| F7    | Reproducir / parar reproducción |
| F8    | Cuentagotas: capturar el color bajo el ratón y calibrarse solo |
| F9    | Activar / desactivar Auto-Captcha |

Funcionan aunque la ventana del programa no esté en primer plano.

## Grabador de macros

Captura todos los eventos de entrada con sus tiempos exactos: movimientos del
ratón (unas 125 muestras por segundo), clics, arrastres, rueda y teclado. La
reproducción respeta la cadencia original y admite velocidad (0.5x – 4x) y
repeticiones (0 = bucle infinito).

Si cortas la reproducción a mitad, el programa suelta automáticamente cualquier
tecla o botón que hubiera quedado pulsado.

Las macros se guardan como archivos `.macro.json`, así que puedes tener varias.

## Auto-Captcha

Escanea la pantalla cada X segundos buscando la mancha verde más grande que
supere un área mínima, y hace clic en su centro. No depende de coordenadas
fijas: funciona esté donde esté la zona verde.

Para evitar clics indeseados exige ver la zona en **dos escaneos seguidos**
antes de actuar, y luego respeta un tiempo de espera (cooldown) configurable.

### Calibración en dos pasos

**1. Cuentagotas.** Deja en pantalla el objeto que hay que clicar, pon el ratón
justo encima y pulsa **F8**. El programa lee el color exacto de esos píxeles
(mediana de un cuadro de 5×5, para no tragarse un borde o una sombra) y ajusta
solo el tono, la saturación y el brillo con un margen generoso. En el registro
verás el RGB y el rango que ha fijado.

Esto es lo que evita adivinar números: un cristal translúcido de Minecraft sale
con tono ~45 y saturación baja, muy lejos de los valores de un verde puro.

**2. Probar detección.** Pulsa **Probar detección (3 s)**, deja la pantalla como
cuando aparece el aviso y mira el registro: lista todos los candidatos con sus
coordenadas y su área, **sin hacer clic**. El nº 1 es el que se clicaría.

También guarda un `debug_deteccion.png` junto al ejecutable, con los píxeles
detectados en rojo y cada candidato recuadrado (verde = aceptado, amarillo =
descartado por área). El botón **Ver imagen de depuración** lo abre.

### Si detecta el objeto equivocado

Cuando en el registro aparece más de un candidato y el correcto no es el nº 1
(típico si tu inventario tiene otros objetos del mismo color), acota la búsqueda:

- **Buscar desde / hasta (% alto)** — limita la franja vertical de pantalla que
  se analiza. Por ejemplo `0` a `65` ignora la mitad inferior, donde suele estar
  tu propio inventario.
- **Área mín. / máx. (px²)** — el objeto de una casilla ronda unos cientos de
  px²; súbele el mínimo para descartar motas de color y bájale el máximo para
  descartar paredes o fondos grandes del mismo tono.
- **± tolerancia** — bájala para exigir un color más parecido al capturado.

Los dos modos conviven: el Auto-Captcha se pausa solo mientras grabas o
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
pyinstaller --onefile --windowed --name MacroPro main.py
```

El ejecutable aparece en `dist/MacroPro.exe`.

## Notas

Windows necesita permisos normales de usuario para capturar entrada global; no
hace falta ejecutarlo como administrador salvo que la aplicación de destino sí
lo sea (en ese caso ejecuta MacroPro también como administrador, porque Windows
no permite enviar entrada de un proceso de menor privilegio a uno mayor).

Usar automatización de entrada puede ir contra las normas de algunos servicios o
juegos, aunque no exista detección automática. Úsalo bajo tu criterio.
