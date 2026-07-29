# -*- coding: utf-8 -*-
"""Regresion de Golem: color, zona rectangular, area/lado, tono circular,
plantilla, brillo animado, paisaje y suelta de teclas.

Lo que mas importa aqui: _band devuelve el offset en x de la zona, asi que se
comprueba que las coordenadas devueltas siguen siendo absolutas cuando la zona
no empieza en (0, 0) — tambien en modo plantilla y en un monitor cuyo origen no
es (0, 0).
"""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main as G

W, H = 1200, 800
MON = {"left": 0, "top": 0, "width": W, "height": H}
MON_OFF = {"left": 1920, "top": -200, "width": W, "height": H}   # 2º monitor


def col(hsv):
    return cv2.cvtColor(np.uint8([[hsv]]), cv2.COLOR_HSV2BGR)[0, 0].tolist()


def instalar(escenas, mon=MON):
    est = {"i": 0}

    def grab():
        img = escenas[est["i"] % len(escenas)]
        est["i"] += 1
        return img, mon
    G.Finder.grab_screen = staticmethod(grab)


def panel(img, cx, cy, lado=200):
    """Fondo gris de interfaz, para pasar el filtro de contexto."""
    cv2.rectangle(img, (cx - lado, cy - lado), (cx + lado, cy + lado),
                  (200, 200, 200), -1)


def base(gris=True):
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = (30, 30, 30)
    if gris:
        panel(img, W // 2, H // 2, 260)
    return img


def cuadro(img, cx, cy, lado, hsv):
    cv2.rectangle(img, (cx - lado // 2, cy - lado // 2),
                  (cx + lado // 2, cy + lado // 2), col(hsv), -1)


def finder(hue=48, tol=12, **kw):
    # estas pruebas son del modo de tono concreto, no del de "lo unico con
    # color" (que es el de fabrica), asi que hay que fijarlo explicitamente
    f = G.Finder()
    f.mode = "color"
    f.hue, f.hue_tol = hue, tol
    f.sat_min, f.val_min = 40, 90
    f.frames, f.frame_gap = 1, 0.0
    for k, v in kw.items():
        setattr(f, k, v)
    return f


def cerca(c, x, y, tol=14):
    return bool(c) and abs(c[0] - x) < tol and abs(c[1] - y) < tol


R = []


def check(nombre, ok, extra=""):
    R.append(ok)
    print(f"  {'OK  ' if ok else 'FALLO'} {nombre}{(' — ' + extra) if extra else ''}")


# --------------------------------------------------------------- 1. color
def p_tonos():
    print("--- cuatro tonos de lima distintos ---")
    for hsv in ([40, 120, 200], [45, 90, 230], [50, 200, 180], [55, 60, 240]):
        img = base()
        cuadro(img, 600, 400, 26, hsv)
        instalar([img])
        f = finder(hue=hsv[0])
        c = f.candidates()
        check(f"tono {hsv[0]} sat {hsv[1]} val {hsv[2]}",
              cerca(c[0], 600, 400) if c else False,
              f"{len(c)} cand., {c[0][:3] if c else 'nada'}")


def p_tono_circular():
    print("--- el rango de tono cruza el 0 (rojo) ---")
    img = base()
    cuadro(img, 600, 400, 26, [2, 220, 220])
    instalar([img])
    f = finder(hue=176, tol=12)          # 176+12 = 188 -> da la vuelta
    c = f.candidates()
    check("rojo con rango 164..188", cerca(c[0], 600, 400) if c else False,
          f"{len(c)} cand.")
    img2 = base()
    cuadro(img2, 600, 400, 26, [90, 220, 220])   # cian: no debe entrar
    instalar([img2])
    f = finder(hue=176, tol=12)
    check("el cian no se cuela en ese rango", len(f.candidates()) == 0)


# ------------------------------------------------- 2. zona rectangular
def p_zona_vertical():
    print("--- zona vertical (roi_top/roi_bottom) ---")
    img = base()
    panel(img, 600, 150, 120)
    panel(img, 600, 650, 120)
    cuadro(img, 600, 150, 26, [48, 150, 220])    # arriba
    cuadro(img, 600, 650, 30, [48, 150, 220])    # abajo, mas grande
    instalar([img])
    f = finder()
    c = f.candidates()
    check("sin zona ve los dos", len(c) == 2, f"{[x[:2] for x in c]}")
    f = finder(roi_top=0.0, roi_bottom=0.4)
    c = f.candidates()
    check("zona 0-40% solo ve el de arriba",
          len(c) == 1 and cerca(c[0], 600, 150), f"{[x[:2] for x in c]}")
    f = finder(roi_top=0.6, roi_bottom=1.0)
    c = f.candidates()
    check("zona 60-100% solo ve el de abajo",
          len(c) == 1 and cerca(c[0], 600, 650), f"{[x[:2] for x in c]}")


def p_zona_horizontal():
    print("--- zona horizontal (roi_left/roi_right): el offset en x ---")
    img = base()
    panel(img, 200, 400, 120)
    panel(img, 1000, 400, 120)
    cuadro(img, 200, 400, 26, [48, 150, 220])
    cuadro(img, 1000, 400, 26, [48, 150, 220])
    instalar([img])
    f = finder()
    check("sin zona ve los dos", len(f.candidates()) == 2)
    f = finder(roi_left=0.0, roi_right=0.4)
    c = f.candidates()
    check("zona izquierda: coordenada absoluta correcta",
          len(c) == 1 and cerca(c[0], 200, 400), f"{[x[:2] for x in c]}")
    f = finder(roi_left=0.6, roi_right=1.0)
    c = f.candidates()
    check("zona derecha: x NO se devuelve relativa al recorte",
          len(c) == 1 and cerca(c[0], 1000, 400),
          f"{[x[:2] for x in c]} (relativa seria ~{1000 - int(W * 0.6)})")


def p_zona_recuadro():
    print("--- zona rectangular en las dos direcciones a la vez ---")
    img = base()
    for (x, y) in ((250, 200), (950, 200), (250, 620), (950, 620), (600, 400)):
        panel(img, x, y, 100)
        cuadro(img, x, y, 26, [48, 150, 220])
    instalar([img])
    f = finder()
    check("sin zona ve los cinco", len(f.candidates()) == 5)
    f = finder(roi_left=0.4, roi_right=0.6, roi_top=0.4, roi_bottom=0.6)
    c = f.candidates()
    check("el recuadro central deja solo el del centro",
          len(c) == 1 and cerca(c[0], 600, 400), f"{[x[:2] for x in c]}")


def p_zona_degenerada():
    print("--- zona invalida: debe caer a pantalla completa, no romperse ---")
    img = base()
    cuadro(img, 600, 400, 26, [48, 150, 220])
    instalar([img])
    f = finder(roi_left=0.5, roi_right=0.5001, roi_top=0.5, roi_bottom=0.5001)
    c = f.candidates()
    check("zona de 0 px -> pantalla completa", cerca(c[0], 600, 400) if c else False)


def p_monitor_desplazado():
    print("--- monitor con origen distinto de (0,0) ---")
    img = base()
    panel(img, 1000, 400, 120)
    cuadro(img, 1000, 400, 26, [48, 150, 220])
    instalar([img], mon=MON_OFF)
    f = finder(roi_left=0.6, roi_right=1.0)
    c = f.candidates()
    esp = (1920 + 1000, -200 + 400)
    check("suma el origen del monitor Y el offset de la zona",
          cerca(c[0], *esp) if c else False,
          f"{c[0][:2] if c else 'nada'} esperado ~{esp}")


# ------------------------------------------------- 3. area y lado
def p_area():
    print("--- filtros de area y de lado ---")
    img = base()
    panel(img, 600, 400, 260)
    cuadro(img, 480, 400, 4, [48, 150, 220])     # mota (5x5 = 25 px^2)
    cuadro(img, 600, 400, 26, [48, 150, 220])    # casilla
    instalar([img])
    f = finder(min_area=80, max_side=200)
    c = f.candidates()
    check("la mota se descarta por area",
          len(c) == 1 and cerca(c[0], 600, 400), f"{[x[:3] for x in c]}")

    img = base(gris=False)
    cv2.rectangle(img, (100, 300), (1100, 340), col([48, 150, 220]), -1)
    instalar([img])
    f = finder(max_side=80, on_gui=False, max_area=10_000_000)
    c = f.candidates()
    lado = any("de lado" in r for r in f.rejects)
    check("una franja de 1000 px de ancho se descarta por lado",
          len(c) == 0 and lado, f"{f.rejects[:1]}")


# ------------------------------------------------- 4. brillo animado
def escena_encantada(fase):
    img = base()
    cx, cy, lado = 600, 400, 26
    cuadro(img, cx, cy, lado, [45, 105, 228])
    capa = img.copy()
    d = int((fase % 4) * lado / 4) - lado // 2
    pts = np.array([[cx - lado // 2 + d, cy - lado // 2],
                    [cx - lado // 2 + d + 9, cy - lado // 2],
                    [cx - lado // 2 + d + 18, cy + lado // 2],
                    [cx - lado // 2 + d + 9, cy + lado // 2]], np.int32)
    cv2.fillPoly(capa, [pts], col([140, 190, 250]))
    m = np.zeros((H, W), np.uint8)
    cv2.rectangle(m, (cx - lado // 2, cy - lado // 2),
                  (cx + lado // 2, cy + lado // 2), 255, -1)
    img = np.where(m[:, :, None] > 0, cv2.addWeighted(img, 0.3, capa, 0.7, 0), img)
    return np.ascontiguousarray(img)


def p_brillo():
    print("--- brillo del encantamiento: 1 fotograma vs union de 3 ---")
    areas1, areas3 = [], []
    for fase in range(4):
        instalar([escena_encantada(fase)])
        c = finder(hue=45, frames=1).candidates()
        areas1.append(c[0][2] if c else 0)
        instalar([escena_encantada((fase + k) % 4) for k in range(3)])
        f = finder(hue=45, frames=3)
        c = f.candidates()
        areas3.append(c[0][2] if c else 0)
        check(f"fase {fase} localizada con la union",
              cerca(c[0], 600, 400) if c else False,
              f"area {c[0][2] if c else 0}")
    v1 = max(areas1) - min(areas1)
    v3 = max(areas3) - min(areas3)
    check("la union es mas estable que un solo fotograma", v3 <= v1,
          f"variacion 1 fotograma {v1} px^2 ({min(areas1)}..{max(areas1)}), "
          f"union {v3} px^2 ({min(areas3)}..{max(areas3)})")


# ------------------------------------------------- 5. paisaje
def escena_paisaje():
    """Hojas iluminadas sobre tierra: manchas del tamano exacto de una casilla."""
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = col([15, 140, 110])                      # tierra
    rng = np.random.RandomState(3)
    for _ in range(100):
        x, y = rng.randint(0, W - 40), rng.randint(0, H - 40)
        s = rng.randint(20, 32)
        cv2.rectangle(img, (x, y), (x + s, y + s),
                      col([48 + rng.randint(-3, 4), 150 + rng.randint(-30, 40),
                           210 + rng.randint(-30, 40)]), -1)
    return img


def p_paisaje():
    print("--- paisaje sin interfaz: el contexto debe dejarlo en 0 ---")
    instalar([escena_paisaje()])
    f = finder(on_gui=False, max_area=10_000_000, max_side=10_000)
    n_sin = len(f.candidates())
    f = finder(on_gui=True, max_area=10_000_000, max_side=10_000)
    n_con = len(f.candidates())
    check("el filtro de interfaz borra el paisaje", n_sin > 20 and n_con == 0,
          f"sin filtro {n_sin} candidatos, con filtro {n_con}")


# ------------------------------------------------- 6. plantilla
def p_plantilla():
    print("--- modo plantilla, con la zona desplazada en x ---")
    img = base()
    panel(img, 1000, 400, 120)
    cuadro(img, 1000, 400, 30, [48, 150, 220])
    cv2.circle(img, (1000, 400), 6, (20, 20, 20), -1)   # textura distintiva
    tpl = np.ascontiguousarray(img[380:420, 980:1020])

    instalar([img])
    f = finder(mode="plantilla", template=tpl, tpl_thr=0.85)
    c = f.candidates()
    check("la encuentra en pantalla completa", cerca(c[0], 1000, 400) if c else False,
          f"score {f.last_score:.3f}")

    instalar([img])
    f = finder(mode="plantilla", template=tpl, tpl_thr=0.85,
               roi_left=0.6, roi_right=1.0, roi_top=0.3, roi_bottom=0.7)
    c = f.candidates()
    check("con zona desplazada devuelve la coordenada absoluta",
          cerca(c[0], 1000, 400) if c else False,
          f"{c[0][:2] if c else 'nada'} score {f.last_score:.3f}")

    instalar([base()])
    f = finder(mode="plantilla", template=tpl, tpl_thr=0.85)
    c = f.candidates()
    check("sin el objetivo no inventa nada", len(c) == 0,
          f"mejor parecido {f.last_score:.3f}")

    instalar([img])
    f = finder(mode="plantilla", template=tpl,
               roi_left=0.0, roi_right=0.02, roi_top=0.0, roi_bottom=0.02)
    try:
        f.candidates()
        check("plantilla mayor que la zona: avisa", False, "no lanzo error")
    except RuntimeError as e:
        check("plantilla mayor que la zona: avisa", True, str(e))


# ------------------------------------------------- 7. suelta de teclas
def p_release():
    print("--- suelta de teclas y botones al cortar ---")
    p = G.Player()
    sueltas = {"k": [], "b": []}
    p.keyboard = type("K", (), {"release": lambda s, k: sueltas["k"].append(k)})()
    p.mouse = type("M", (), {"release": lambda s, b: sueltas["b"].append(b)})()
    # el formato real es el de key_to_str: "k:<nombre>", "c:<char>", "v:<vk>"
    p._release_all([
        {"e": "kd", "k": "k:shift"},
        {"e": "kd", "k": "c:a"},
        {"e": "ku", "k": "c:a"},
        {"e": "mc", "b": "left", "d": True},
        {"e": "mc", "b": "right", "d": True},
        {"e": "mc", "b": "right", "d": False},
    ])
    check("suelta el shift que quedo pulsado",
          sueltas["k"] == [G.Key.shift], f"{sueltas['k']}")
    check("suelta el clic izquierdo y no el derecho", len(sueltas["b"]) == 1,
          f"{len(sueltas['b'])} boton(es)")

    # _release_all se traga las excepciones, asi que un cambio en el formato
    # dejaria teclas pegadas en silencio. Aqui se comprueba el ida y vuelta.
    print("--- ida y vuelta de key_to_str / str_to_key ---")
    for k in (G.Key.shift, G.Key.ctrl_l, G.Key.f6, G.Key.space, G.Key.enter):
        s = G.key_to_str(k)
        check(f"{s} -> {k}", G.str_to_key(s) == k)
    kc = G.KeyCode.from_char("a")
    check("c:a vuelve como algo que pynput acepta",
          G.str_to_key(G.key_to_str(kc)) == "a", G.key_to_str(kc))
    kv = G.KeyCode.from_vk(65)
    check("v:65 vuelve como KeyCode",
          G.str_to_key(G.key_to_str(kv)) == kv, G.key_to_str(kv))


if __name__ == "__main__":
    for fn in (p_tonos, p_tono_circular, p_zona_vertical, p_zona_horizontal,
               p_zona_recuadro, p_zona_degenerada, p_monitor_desplazado,
               p_area, p_brillo, p_paisaje, p_plantilla, p_release):
        fn()
        print()
    fallos = R.count(False)
    print(f"{len(R)} comprobaciones, "
          + ("TODO OK" if not fallos else f"{fallos} FALLO(S)"))
    sys.exit(1 if fallos else 0)
