# AutoCaptcha

Vigila una zona de la pantalla y clica el aviso cuando aparece. Nada más.

No graba macros, no reproduce movimiento, no toca el teclado y no mueve la cámara.
Solo mira y clica.

## Instalación

1. Descarga **`AutoCaptcha-carpeta.zip`** (botón *Download raw file*), descomprime
   y abre el `AutoCaptcha.exe` de dentro. También está `AutoCaptcha.exe` suelto
   si prefieres un solo archivo.
2. **Ponlo en una carpeta de verdad** —el Escritorio, por ejemplo— antes de
   abrirlo. Si lo ejecutas desde la descarga, Windows lo lanza desde una carpeta
   temporal que luego borra; el programa lo detecta, te avisa y guarda sus
   ajustes en `%LOCALAPPDATA%\AutoCaptcha` para que no se pierdan.

La primera vez puede salir el aviso de SmartScreen porque el ejecutable no está
firmado: **Más información → Ejecutar de todas formas**.

## Cómo se usa

1. Deja el aviso en pantalla, con el objeto visible.
2. Ratón en una esquina de la zona → **F2**; ratón en la esquina opuesta →
   **F2**. Marca solo donde puede aparecer: en un cofre, la rejilla, y **no** la
   fila de tu inventario.
3. **Probar detección (3 s)**. Tiene que salir **un solo candidato**, en las
   coordenadas del objeto. Si sale más de uno, aprieta la zona.
4. **F6** y ya está. Puedes minimizar la ventana.

No hay que calibrar ningún color: el modo por defecto busca **lo único que tenga
color** dentro de la zona, y las casillas vacías de un cofre son gris puro.

## Teclas

| Tecla | Acción |
|-------|--------|
| F2    | Marcar la zona (una esquina por pulsación) |
| F8    | Cuentagotas, solo si usas el modo de un color concreto |
| F6    | Activar / desactivar la vigilancia |
| F12   | **Parada total** |

## Si no detecta

Pulsa **Probar detección** y lee el registro: cuando no encuentra nada te dice qué
veía dentro de la zona —el tono, la saturación y el brillo del fondo y de lo más
coloreado que hubiera—. Con eso se sabe si el problema es la zona o un umbral.

**Comprobar todo** informa de una vez de dónde guarda los archivos, qué ventana
hay delante, si la zona está marcada y cuánto tarda un escaneo.

Cada clic guarda además una captura con una cruz roja donde ha clicado
(`captcha_clic_1.png` … `_3.png`, rotando). **Ver último clic** la abre.

## Ajustes que importan

- **Solo sobre una interfaz (fondo gris)** — exige que alrededor del objetivo haya
  gris claro, como el panel de un cofre. Es lo que evita que el paisaje se lleve
  el clic. Desmárcalo si tu objetivo no está dentro de un menú.
- **Fotogramas unidos** — si el objeto está encantado, su brillo lo rompe en
  trozos distintos en cada instante; uniendo 3 capturas se ve entero.
- **Espera tras clicar** — el tiempo que se queda quieto después de un clic.
- **Actuar solo si la ventana de delante contiene…** — pon un trozo del título del
  juego y, si te vas al navegador, deja de clicar.

## Cómo detecta

`motor.py` es el buscador. Dos modos:

- **Lo único con color** — no mira el tono, solo si el píxel está saturado. Dentro
  de un cofre las casillas vacías son gris puro, así que el objeto es lo único que
  puede salir. No hay nada que calibrar, y el brillo morado de un objeto encantado
  suma en vez de estorbar.
- **Un color concreto** — máscara HSV de un tono. Solo hace falta cuando dentro de
  la zona hay varias cosas con color.

Antes de clicar exige ver el objetivo en **dos escaneos seguidos**, y después
vuelve a mirar: si sigue ahí, lo dice en el registro.

## Ejecutar desde el código

Requiere Python 3.10 o superior.

```bash
pip install -r requirements.txt
python captcha.py
```

Y para compilarlo:

```bash
pip install pyinstaller
pyinstaller --onedir --windowed --name AutoCaptcha captcha.py
```

## Si el antivirus se queja

Puede pasar: el programa lee la pantalla y sintetiza clics, y eso se parece a lo
que hace un programa espía. La versión en carpeta se marca mucho menos que la de
un solo archivo, porque esa se autoextrae en una carpeta temporal y se ejecuta
desde ahí, que es la técnica de los *droppers*.

Si aun así te lo marca: ejecútalo desde el código fuente, o repórtalo como falso
positivo. Y no te fíes de mi palabra — el código está aquí entero y son dos
archivos legibles.

## Pruebas

```bash
python pruebas/test_captcha_app.py
```
