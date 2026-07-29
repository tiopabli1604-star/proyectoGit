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

### Calibración

Usa el botón **Probar detección ahora**: te da 3 segundos para dejar la
pantalla como cuando aparece el aviso y luego te dice en el registro si lo
detecta y en qué coordenadas, **sin hacer clic**.

Si no lo detecta, ajusta:

- **Tono verde (0-179)** — en OpenCV el verde puro es 60; los verdes lima suelen
  caer entre 40 y 70.
- **± tolerancia** — súbela para aceptar un rango de verdes más amplio.
- **Saturación mín. / Brillo mín.** — bájalos si el verde es apagado u oscuro.
- **Área mínima (px²)** — súbela para ignorar verdes pequeños (hierba, iconos) y
  quedarte solo con la zona grande del aviso.

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
