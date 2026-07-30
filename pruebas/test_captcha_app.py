# -*- coding: utf-8 -*-
"""La aplicacion del captcha, sola.

Reutiliza el motor de Golem, asi que aqui no se vuelve a probar la deteccion:
se comprueba que la aplicacion esta bien cableada, que no pisa los archivos de
Golem, que no arrastra nada de macros ni de teclado, y que detecta y clica sobre
la escena real del cofre.
"""
import os
import sys
import threading
import time
import tkinter as tk

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import motor as G
import captcha as C
import main as Golem

W, H = 1920, 1080
MON = {"left": 0, "top": 0, "width": W, "height": H}
GUI = (787, 310, 1133, 748)
COFRE = (800, 340, 1125, 560)
CRISTAL = (1032, 539)
LADO = 22

R = []


def check(nombre, ok, extra=""):
    R.append(ok)
    extra = str(extra) if extra else ""
    print(f"  {'OK  ' if ok else 'FALLO'} {nombre}{(' — ' + extra) if extra else ''}")


def col(hsv):
    return cv2.cvtColor(np.uint8([[hsv]]), cv2.COLOR_HSV2BGR)[0, 0].tolist()


def escena(con_cristal=True):
    """La captura real: Plains de fondo, cofre vacio, cristal encantado."""
    img = np.zeros((H, W, 3), np.uint8)
    img[:H // 3, :] = (235, 190, 120)
    img[H // 3:, :] = col([42, 130, 200])
    rng = np.random.RandomState(7)
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
        cv2.rectangle(img, (cx - LADO // 2, cy - LADO // 2),
                      (cx + LADO // 2, cy + LADO // 2), col([45, 105, 228]), -1)
    return np.ascontiguousarray(img)


def instalar(secuencia):
    est = {"i": 0}

    def grab():
        img = secuencia[min(est["i"], len(secuencia) - 1)]
        est["i"] += 1
        return img, MON
    G.Finder.grab_screen = staticmethod(grab)


def abrir():
    if os.path.exists(C.CONFIG_PATH):
        os.remove(C.CONFIG_PATH)
    root = tk.Tk()
    root.withdraw()
    return C.App(root), root


def cerrar(app, root):
    app._cerrando = True
    if getattr(app, "_drain_id", None) is not None:
        try:
            root.after_cancel(app._drain_id)
        except Exception:
            pass
        app._drain_id = None
    app.watcher.stop()
    try:
        if app._hotkey_listener:
            app._hotkey_listener.stop()
    except Exception:
        pass
    root.destroy()


def registro(app):
    app._drain_log(reprogramar=False)
    return app.txt_log.get("1.0", "end")


def zona_cofre(app):
    app.var_roi_left.set(str(int(COFRE[0] * 100 / W)))
    app.var_roi_right.set(str(int(COFRE[2] * 100 / W) + 1))
    app.var_roi_top.set(str(int(COFRE[1] * 100 / H)))
    app.var_roi_bottom.set(str(int(COFRE[3] * 100 / H) + 1))
    app._apply()


def p_no_pisa_a_golem():
    print("--- no comparte archivos con Golem ---")
    check("config distinto", os.path.basename(C.CONFIG_PATH)
          != os.path.basename(Golem.CONFIG_PATH),
          f"{os.path.basename(C.CONFIG_PATH)} vs {os.path.basename(Golem.CONFIG_PATH)}")
    check("registro distinto", "captcha" in os.path.basename(C.LOG_PATH))
    check("imagen de depuracion distinta", "captcha" in os.path.basename(G.DEBUG_IMG))
    check("capturas de clic distintas", "captcha" in os.path.basename(G.SHOT_PATH))
    check("y todos en la carpeta de datos",
          all(os.path.dirname(p) == G.APP_DIR
              for p in (C.CONFIG_PATH, C.LOG_PATH, G.DEBUG_IMG)))


def p_solo_lo_del_captcha():
    print("--- no arrastra nada de macros ni de teclado ---")
    app, root = abrir()
    try:
        for sobra in ("recorder", "player", "script", "ancla_img"):
            check(f"no tiene {sobra}", not hasattr(app, sobra))
        check("tiene el buscador", hasattr(app, "finder"))
        check("y el vigilante", hasattr(app, "watcher"))
        check("cuatro teclas, ni una mas", len(C.HOTKEYS) == 4,
              str(sorted(k.name for k in C.HOTKEYS)))
        # si alguien tiene los dos abiertos, una tecla no puede significar dos
        # cosas distintas: F9 aqui no puede ponerse a grabar una macro alli
        check("ninguna choca con grabar o reproducir de Golem",
              Golem.HOTKEY_RECORD not in C.HOTKEYS
              and Golem.HOTKEY_PLAY not in C.HOTKEYS)
        equivalencias = {C.HOTKEY_ZONE: Golem.HOTKEY_ZONE,
                         C.HOTKEY_PICK: Golem.HOTKEY_PICK,
                         C.HOTKEY_WATCH: Golem.HOTKEY_WATCH,
                         C.HOTKEY_PANIC: Golem.HOTKEY_PANIC}
        check("y cada una significa lo mismo que en Golem",
              all(a == b for a, b in equivalencias.items()),
              str({a.name: b.name for a, b in equivalencias.items()}))
    finally:
        cerrar(app, root)


def p_detecta_el_cristal():
    print("--- detecta el cristal en la escena real ---")
    app, root = abrir()
    try:
        instalar([escena()])
        zona_cofre(app)
        app.finder.frames = 1
        c = app.finder.candidates()
        check("un solo candidato", len(c) == 1, f"{len(c)}: {[x[:2] for x in c]}")
        check("y es el cristal",
              bool(c) and abs(c[0][0] - CRISTAL[0]) < 14
              and abs(c[0][1] - CRISTAL[1]) < 14, str(c[0][:2] if c else None))
    finally:
        cerrar(app, root)


def p_clica_de_verdad():
    print("--- el vigilante lo clica ---")
    app, root = abrir()
    clics = []
    orig = G.click_at
    G.click_at = lambda m, x, y, **kw: clics.append((x, y))
    try:
        instalar([escena()])
        zona_cofre(app)
        app.var_frames.set("1")
        app.var_interval.set("0.2")
        app.var_shots.set(False)
        app.var_sound.set(False)
        app._apply()
        app.toggle_watch()
        check("arranca", app.watcher.active)
        t0 = time.perf_counter()
        while not clics and time.perf_counter() - t0 < 8:
            time.sleep(0.02)
        app.watcher.stop()
        check("ha clicado", bool(clics), str(clics[:2]))
        check("en el cristal",
              bool(clics) and abs(clics[0][0] - CRISTAL[0]) < 14
              and abs(clics[0][1] - CRISTAL[1]) < 14, str(clics[:1]))
    finally:
        G.click_at = orig
        cerrar(app, root)


def p_no_clica_sin_cristal():
    print("--- y no clica cuando no esta ---")
    app, root = abrir()
    clics = []
    orig = G.click_at
    G.click_at = lambda m, x, y, **kw: clics.append((x, y))
    try:
        instalar([escena(con_cristal=False)])
        zona_cofre(app)
        app.var_frames.set("1")
        app.var_interval.set("0.2")
        app.var_sound.set(False)
        app._apply()
        app.toggle_watch()
        time.sleep(2.0)
        app.watcher.stop()
        check("no ha clicado nada", not clics, str(clics))
    finally:
        G.click_at = orig
        cerrar(app, root)


def p_guarda_sin_zona():
    print("--- se niega a vigilar sin zona y sin filtro ---")
    app, root = abrir()
    try:
        app.var_on_gui.set(False)
        app.reset_zone()
        app._apply()
        app.toggle_watch()
        check("no arranca", not app.watcher.active)
        check("y lo explica", "Marca la zona" in registro(app))
    finally:
        cerrar(app, root)


def p_config():
    print("--- los ajustes van y vuelven ---")
    app, root = abrir()
    try:
        app.var_sat.set("77")
        app.var_interval.set("2.5")
        app.var_ventana.set("Minecraft")
        zona_cofre(app)
        app._save_config()
        antes = {k: v.get() for k, v in app._cfg_map().items()}
    finally:
        cerrar(app, root)
    root = tk.Tk()
    root.withdraw()
    app = C.App(root)
    try:
        despues = {k: v.get() for k, v in app._cfg_map().items()}
        dif = {k: (antes[k], despues[k]) for k in antes
               if str(antes[k]) != str(despues[k])}
        check("todo vuelve igual", not dif, str(dif))
        check("y llega al vigilante",
              app.watcher.ventana_req == "Minecraft", app.watcher.ventana_req)
    finally:
        cerrar(app, root)


def p_diagnostico():
    print("--- 'Comprobar todo' funciona ---")
    app, root = abrir()
    try:
        instalar([escena()])
        app.diagnostico()
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 8:
            root.update()
            time.sleep(0.05)
        txt = registro(app)
        for trozo in ("Archivos en", "Pantalla:", "Ventana de delante", "Zona:",
                      "Un escaneo tarda"):
            check(f"informa de «{trozo}»", trozo in txt)
        check("sin errores", "Traceback" not in txt)
    finally:
        cerrar(app, root)


if __name__ == "__main__":
    respaldo = None
    if os.path.exists(C.CONFIG_PATH):
        with open(C.CONFIG_PATH, encoding="utf-8-sig") as f:
            respaldo = f.read()
    try:
        for fn in (p_no_pisa_a_golem, p_solo_lo_del_captcha,
                   p_detecta_el_cristal, p_clica_de_verdad,
                   p_no_clica_sin_cristal, p_guarda_sin_zona, p_config,
                   p_diagnostico):
            try:
                fn()
            except Exception:
                import traceback
                traceback.print_exc()
                R.append(False)
            print()
    finally:
        if respaldo is not None:
            with open(C.CONFIG_PATH, "w", encoding="utf-8") as f:
                f.write(respaldo)
        elif os.path.exists(C.CONFIG_PATH):
            os.remove(C.CONFIG_PATH)
    fallos = R.count(False)
    print(f"{len(R)} comprobaciones, "
          + ("TODO OK" if not fallos else f"{fallos} FALLO(S)"))
    sys.exit(1 if fallos else 0)
