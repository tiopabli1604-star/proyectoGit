# -*- coding: utf-8 -*-
"""¿Detecta AutoCaptcha exactamente igual que Golem?

motor.py se saco de main.py copiando los bloques tal cual. Esta prueba lo
verifica de dos formas independientes:

  1. comparando el TEXTO de las clases Finder y Watcher en los dos archivos:
     tienen que ser identicas linea por linea;
  2. pasando las mismas escenas por los dos motores y comparando lo que
     devuelven: mismos candidatos, mismas coordenadas, mismas areas, mismos
     motivos de descarte.

Si algun dia alguien toca uno y no el otro, esto se pone rojo.
"""
import io
import os
import re
import sys

import cv2
import numpy as np

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
import main as G
import motor as M

W, H = 1920, 1080
MON = {"left": 0, "top": 0, "width": W, "height": H}
GUI = (787, 310, 1133, 748)
COFRE = (800, 340, 1125, 560)
CRISTAL = (1032, 539)

R = []


def check(nombre, ok, extra=""):
    R.append(ok)
    extra = str(extra) if extra else ""
    print(f"  {'OK  ' if ok else 'FALLO'} {nombre}{(' — ' + extra) if extra else ''}")


# ------------------------------------------------------------ 1. el texto
def extraer_clase(ruta, nombre):
    """El codigo de una clase, desde 'class X' hasta la siguiente de nivel 0."""
    txt = io.open(ruta, encoding="utf-8").read()
    m = re.search(rf"^class {nombre}\b.*?(?=^\S|\Z)", txt, re.S | re.M)
    return m.group(0).rstrip() if m else None


def p_mismo_codigo():
    print("--- el codigo de las clases es identico ---")
    for clase in ("Finder", "Watcher"):
        a = extraer_clase(os.path.join(RAIZ, "main.py"), clase)
        b = extraer_clase(os.path.join(RAIZ, "motor.py"), clase)
        check(f"{clase} existe en los dos", a and b,
              f"main={bool(a)} motor={bool(b)}")
        if a and b:
            check(f"{clase}: mismo texto, linea por linea", a == b,
                  f"{len(a.splitlines())} vs {len(b.splitlines())} lineas")
    for fn in ("click_at", "ventana_activa"):
        a = re.search(rf"^def {fn}\b.*?(?=^\S|\Z)",
                      io.open(os.path.join(RAIZ, "main.py"),
                              encoding="utf-8").read(), re.S | re.M)
        b = re.search(rf"^def {fn}\b.*?(?=^\S|\Z)",
                      io.open(os.path.join(RAIZ, "motor.py"),
                              encoding="utf-8").read(), re.S | re.M)
        check(f"{fn}: mismo texto", a and b
              and a.group(0).rstrip() == b.group(0).rstrip())


# ------------------------------------------------------ 2. el comportamiento
def col(hsv):
    return cv2.cvtColor(np.uint8([[hsv]]), cv2.COLOR_HSV2BGR)[0, 0].tolist()


def escena(con_cristal=True, semilla=7):
    img = np.zeros((H, W, 3), np.uint8)
    img[:H // 3, :] = (235, 190, 120)
    img[H // 3:, :] = col([42, 130, 200])
    rng = np.random.RandomState(semilla)
    for _ in range(400):
        x, y = rng.randint(0, W - 40), rng.randint(H // 3, H - 30)
        s = rng.randint(14, 34)
        img[y:y + s, x:x + s] = col([42 + rng.randint(-4, 5),
                                     130 + rng.randint(-30, 30),
                                     200 + rng.randint(-40, 30)])
    x0, y0, x1, y1 = GUI
    cv2.rectangle(img, (x0, y0), (x1, y1), (198, 198, 198), -1)
    for sy in range(340, 560, 36):
        for sx in range(800, 1120, 36):
            cv2.rectangle(img, (sx, sy), (sx + 32, sy + 32), (139, 139, 139), -1)
    for i, hsv in enumerate(([120, 200, 200], [10, 220, 210], [90, 180, 190],
                             [45, 190, 215])):
        cv2.rectangle(img, (810 + i * 40, 600), (836 + i * 40, 626),
                      col(hsv), -1)
    if con_cristal:
        cx, cy = CRISTAL
        cv2.rectangle(img, (cx - 11, cy - 11), (cx + 11, cy + 11),
                      col([45, 105, 228]), -1)
    return np.ascontiguousarray(img)


def instalar(img):
    def grab():
        return np.ascontiguousarray(img), MON
    G.Finder.grab_screen = staticmethod(grab)
    M.Finder.grab_screen = staticmethod(grab)


def configurar(f, zona, **kw):
    f.frames = 1
    if zona:
        x0, y0, x1, y1 = COFRE
        f.roi_left, f.roi_right = x0 / W, x1 / W
        f.roi_top, f.roi_bottom = y0 / H, y1 / H
    for k, v in kw.items():
        setattr(f, k, v)
    return f


def p_mismos_resultados():
    print("--- los dos motores ven exactamente lo mismo ---")
    casos = [
        ("cofre con cristal, zona marcada", escena(True), True, {}),
        ("cofre vacio, zona marcada", escena(False), True, {}),
        ("sin zona, con filtro de interfaz", escena(True), False, {}),
        ("sin zona ni filtro", escena(True), False, {"on_gui": False}),
        ("modo de un color concreto", escena(True), True,
         {"mode": "color", "hue": 45, "hue_tol": 12}),
        ("umbrales apretados", escena(True), True,
         {"min_area": 400, "max_side": 30}),
        ("otra semilla de paisaje", escena(True, semilla=21), True, {}),
    ]
    for nombre, img, zona, kw in casos:
        instalar(img)
        a = configurar(G.Finder(), zona, **kw).candidates()
        ra = list(configurar(G.Finder(), zona, **kw).rejects)
        instalar(img)
        fa = configurar(G.Finder(), zona, **kw)
        a = fa.candidates()
        instalar(img)
        fb = configurar(M.Finder(), zona, **kw)
        b = fb.candidates()
        check(nombre, a == b and fa.rejects == fb.rejects,
              f"Golem {[x[:3] for x in a]} / AutoCaptcha {[x[:3] for x in b]}")


def p_mismo_informe():
    print("--- y el informe de la zona tambien ---")
    instalar(escena(True))
    a = configurar(G.Finder(), True).zone_report()
    instalar(escena(True))
    b = configurar(M.Finder(), True).zone_report()
    check("mismas lineas", a == b, f"{len(a)} vs {len(b)} lineas")
    if a and b:
        check("y dicen lo mismo", a[1] == b[1], f"{a[1][:50]}…")


def p_mismos_ajustes_de_fabrica():
    print("--- y arrancan con los mismos ajustes ---")
    a, b = G.Finder(), M.Finder()
    claves = ("mode", "hue", "hue_tol", "sat_min", "val_min", "min_area",
              "max_area", "max_side", "frames", "frame_gap", "on_gui",
              "tpl_thr", "roi_left", "roi_right", "roi_top", "roi_bottom")
    dif = {k: (getattr(a, k), getattr(b, k)) for k in claves
           if getattr(a, k) != getattr(b, k)}
    check("ni un ajuste distinto", not dif, str(dif))
    wa = G.Watcher(a, lambda m: None)
    wb = M.Watcher(b, lambda m: None)
    cw = ("interval", "cooldown", "double_click", "restore_mouse", "sound",
          "shots", "move_delay", "verificar")
    difw = {k: (getattr(wa, k), getattr(wb, k)) for k in cw
            if getattr(wa, k) != getattr(wb, k)}
    check("y el vigilante igual", not difw, str(difw))


if __name__ == "__main__":
    for fn in (p_mismo_codigo, p_mismos_resultados, p_mismo_informe,
               p_mismos_ajustes_de_fabrica):
        try:
            fn()
        except Exception:
            import traceback
            traceback.print_exc()
            R.append(False)
        print()
    fallos = R.count(False)
    print(f"{len(R)} comprobaciones, "
          + ("TODO OK" if not fallos else f"{fallos} FALLO(S)"))
    sys.exit(1 if fallos else 0)
