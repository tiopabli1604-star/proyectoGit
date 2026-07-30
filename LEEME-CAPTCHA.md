# Golem Captcha

Vigila una zona de la pantalla y clica el aviso cuando aparece. Nada más.

Es la mitad de [Golem](README.md) que resuelve el captcha, separada: **no graba
macros, no reproduce movimiento, no toca el teclado y no mueve la cámara.** Solo
lee la pantalla y da un clic.

## Descarga

- `GolemCaptcha.exe` — un solo archivo, 66 MB.
- `GolemCaptcha-carpeta.zip` — descomprimir y abrir el `.exe` de dentro. Da menos
  problemas con los antivirus (ver el apartado del [README](README.md)).

**Cópialo a una carpeta de verdad antes de abrirlo**, no lo ejecutes desde la
descarga. Si lo haces, te avisa y guarda sus ajustes en `%LOCALAPPDATA%\Golem`
para que no se pierdan.

## Cómo se usa

1. Deja el aviso en pantalla, con el objeto visible.
2. Ratón en una esquina de la zona → **F2**; ratón en la esquina opuesta →
   **F2**. Marca solo donde puede aparecer: en un cofre, la rejilla y **no** la
   fila de tu inventario.
3. **Probar detección (3 s)**. Tiene que salir **un solo candidato**, en las
   coordenadas del objeto. Si sale más de uno, aprieta la zona.
4. **F6** y ya se queda esperando. Puedes minimizar la ventana.

No hay que calibrar ningún color: el modo por defecto busca **lo único que tenga
color** dentro de la zona, y las casillas vacías de un cofre son gris puro.

## Teclas

| Tecla | Acción |
|-------|--------|
| F2    | Marcar la zona (una esquina por pulsación) |
| F8    | Cuentagotas, solo si usas el modo de un color concreto |
| F6    | Activar / desactivar la vigilancia |
| F12   | **Parada total** |

Son las mismas que en Golem y significan lo mismo, así que si algún día tienes
los dos abiertos ninguna hace algo distinto de lo que esperas.

## Si no detecta

Pulsa **Probar detección** y lee el registro: cuando no encuentra nada te dice
qué veía dentro de la zona —el tono, la saturación y el brillo del fondo y de lo
más coloreado que hubiera—. Con eso se sabe si el problema es la zona o un umbral.

**Comprobar todo** te dice de una vez dónde guarda los archivos, qué ventana hay
delante, si la zona está marcada y cuánto tarda un escaneo.

Y cada clic automático guarda una captura con una cruz roja donde ha clicado
(`captcha_clic_1.png` … `_3.png`, rotando). El botón **Ver último clic** la abre.

## Ajustes que importan

- **Solo sobre una interfaz (fondo gris)** — exige que alrededor del objetivo
  haya gris claro, como el panel de un cofre. Es lo que evita que el paisaje se
  lleve el clic. Desmárcalo si tu objetivo no está dentro de un menú.
- **Fotogramas unidos** — si el objeto está encantado, su brillo lo rompe en
  trozos distintos en cada instante. Uniendo 3 capturas se vuelve a ver entero.
- **Espera tras clicar** — el tiempo que se queda quieto después de un clic.
- **Actuar solo si la ventana de delante contiene…** — pon un trozo del título
  del juego. Así, si te vas al navegador, deja de clicar.

## El resto de Golem

Todo lo demás —grabador de macros, movimiento relativo para juegos en primera
persona, guion de varios pasos, detección de atascos, sonido— sigue en
`Golem.exe`, aparte. Ver el [README](README.md).
