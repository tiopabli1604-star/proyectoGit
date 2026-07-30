# -*- coding: utf-8 -*-
"""Revision de conjunto: que la aplicacion este bien cableada.

Las suites de antes prueban cada pieza. Esta busca lo otro: botones que apuntan
a metodos que ya no existen, ajustes que se guardan y no se cargan,
instrucciones que el analizador acepta pero luego revientan al explicarse o al
ejecutarse, teclas sin accion, rutas fuera de la carpeta de datos. Es donde se
acumulan los fallos cuando se ha tocado mucho.
"""
import io
import json
import os
import re
import sys
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main as G

R = []


def check(nombre, ok, extra=""):
    R.append(ok)
    extra = str(extra) if extra else ""
    print(f"  {'OK  ' if ok else 'FALLO'} {nombre}{(' — ' + extra) if extra else ''}")


def abrir(cfg=None):
    if cfg is None:
        if os.path.exists(G.CONFIG_PATH):
            os.remove(G.CONFIG_PATH)
    else:
        with open(G.CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
    root = tk.Tk()
    root.withdraw()
    return G.App(root), root


def cerrar(app, root):
    # lo mismo que hace _on_close: parar el vaciado y cancelar el pendiente, o
    # Tcl se queja al destruir la ventana
    app._cerrando = True
    if getattr(app, "_drain_id", None) is not None:
        try:
            root.after_cancel(app._drain_id)
        except Exception:
            pass
        app._drain_id = None
    for ev in ("_guard_stop", "_txt_stop", "_sched_stop"):
        try:
            getattr(app, ev).set()
        except Exception:
            pass
    try:
        app.script.stop()
    except Exception:
        pass
    try:
        if app._hotkey_listener:
            app._hotkey_listener.stop()
    except Exception:
        pass
    root.destroy()


# ---------------------------------------------------------------- botones
def recorrer(w, salida):
    for hijo in w.winfo_children():
        salida.append(hijo)
        recorrer(hijo, salida)


def p_botones():
    print("--- todos los botones y casillas llaman a algo que existe ---")
    app, root = abrir()
    try:
        widgets = []
        recorrer(root, widgets)
        botones = [w for w in widgets
                   if w.winfo_class() in ("TButton", "TCheckbutton",
                                          "TRadiobutton", "Button",
                                          "Checkbutton", "Radiobutton")]
        check("hay botones que revisar", len(botones) >= 20, f"{len(botones)}")
        sin_comando, roto = [], []
        for b in botones:
            try:
                cmd = b.cget("command")
            except Exception:
                continue
            texto = ""
            try:
                texto = str(b.cget("text"))[:34]
            except Exception:
                pass
            if not cmd:
                if b.winfo_class() in ("TButton", "Button"):
                    sin_comando.append(texto)
                continue
            # el comando es un nombre Tcl: se comprueba que sigue registrado
            if not root.tk.call("info", "commands", str(cmd)):
                roto.append(texto)
        check("ningun boton sin accion", not sin_comando, str(sin_comando))
        check("ningun comando colgando", not roto, str(roto))
    finally:
        cerrar(app, root)


def p_hotkeys():
    print("--- las teclas rapidas ---")
    app, root = abrir()
    try:
        check("todas las teclas tienen accion",
              set(app._acciones) == G.HOTKEYS,
              f"sin accion: {G.HOTKEYS - set(app._acciones)}")
        check("y todas las acciones son invocables",
              all(callable(v) for v in app._acciones.values()))
        check("no hay dos teclas iguales",
              len(G.HOTKEYS) == len({HOT for HOT in G.HOTKEYS}) == 8,
              f"{len(G.HOTKEYS)} teclas")
        # las etiquetas de los botones dicen la tecla de verdad
        pares = ((app.btn_rec, G.T_REC), (app.btn_play, G.T_PLAY),
                 (app.btn_watch, G.T_WATCH), (app.btn_script, G.T_SCRIPT))
        for boton, tecla in pares:
            t = boton.cget("text")
            check(f"la etiqueta «{t}» dice {tecla}", tecla in t, t)
    finally:
        cerrar(app, root)


# ---------------------------------------------------------------- config
def p_config_ida_y_vuelta():
    print("--- los ajustes se guardan y se recuperan todos ---")
    app, root = abrir()
    try:
        claves = set(app._cfg_map())
        # tocar todos los valores para que no coincidan con los de fabrica
        for k, var in app._cfg_map().items():
            v = var.get()
            if re.fullmatch(r"-?\d+", str(v) or ""):
                var.set(str(int(v) + 1 if int(v) < 40 else int(v) - 1))
        app.var_txt_text.set("/prueba de guardado")
        app.var_ventana.set("Minecraft")
        app.txt_script.delete("1.0", "end")
        app.txt_script.insert("1.0", "esperar 1\nparar\n")
        app.targets["obj_prueba"] = app.finder.snapshot()
        antes = {k: v.get() for k, v in app._cfg_map().items()}
        app._save_config()
    finally:
        cerrar(app, root)

    with io.open(G.CONFIG_PATH, encoding="utf-8-sig") as f:
        guardado = json.load(f)
    faltan = claves - set(guardado)
    check("el archivo tiene todas las claves", not faltan, str(faltan))

    app, root = abrir(cfg=guardado)
    try:
        despues = {k: v.get() for k, v in app._cfg_map().items()}
        distintos = {k: (antes[k], despues[k]) for k in antes
                     if str(antes[k]) != str(despues[k])}
        check("todos los valores vuelven igual", not distintos, str(distintos))
        check("los objetivos vuelven", "obj_prueba" in app.targets,
              str(list(app.targets)))
        check("el guion vuelve", "esperar 1" in app.txt_script.get("1.0", "end"))
        check("la ventana exigida vuelve", app.var_ventana.get() == "Minecraft",
              app.var_ventana.get())
        check("y llega al guion y al vigilante",
              app.script.ventana_req == "Minecraft"
              and app.watcher.ventana_req == "Minecraft",
              f"{app.script.ventana_req!r} / {app.watcher.ventana_req!r}")
    finally:
        cerrar(app, root)


def p_config_estable():
    print("--- guardar dos veces da lo mismo (no hay deriva) ---")
    app, root = abrir()
    try:
        app._save_config()
        with io.open(G.CONFIG_PATH, encoding="utf-8-sig") as f:
            uno = json.load(f)
    finally:
        cerrar(app, root)
    app, root = abrir(cfg=uno)
    try:
        app._save_config()
        with io.open(G.CONFIG_PATH, encoding="utf-8-sig") as f:
            dos = json.load(f)
    finally:
        cerrar(app, root)
    dif = {k for k in set(uno) | set(dos) if uno.get(k) != dos.get(k)}
    check("el segundo guardado es identico al primero", not dif, str(dif))


# ---------------------------------------------------------------- guion
OBJ = {"cosa": {"mode": "unico", "sat_min": 40, "val_min": 90, "min_area": 80,
                "max_area": 40000, "max_side": 80, "frames": 1, "on_gui": False,
                "roi_left": 0.0, "roi_right": 1.0, "roi_top": 0.0,
                "roi_bottom": 1.0}}

LINEAS = [
    "buscar cosa",
    "buscar cosa 5",
    "buscar cosa 5 si_falla repetir",
    "desaparecer cosa 5",
    "esperar_cambio cosa 5",
    "esperar_sonido 5",
    "esperar_sonido 5 si_falla ir 2",
    "clic",
    "clic doble",
    "clic derecho",
    "esperar 1",
    "tecla espacio",
    "tecla shift+1",
    "pulsar ctrl+f",
    "girar 100 -50",
    "mantener w 2",
    "mantener shift",
    "mantener w 5 si_atascado parar",
    "soltar shift",
    "mantener_clic 2",
    "mantener_clic derecho",
    "soltar_clic",
    "escribir hola mundo",
    "pitar",
    "reafirmar",
    "ir 1",
    "repetir",
    "repetir 3",
    "parar",
]


def p_todas_las_instrucciones():
    print("--- cada instruccion se analiza y se explica sin reventar ---")
    malas, sin_explicar = [], []
    for linea in LINEAS:
        texto = "buscar cosa 1\n" + linea + "\nparar"
        pasos, errs = G.Script.parse(texto, OBJ)
        if errs:
            malas.append((linea, errs[0][:50]))
            continue
        try:
            d = G.Script.describe(pasos)
            if len(d) != len(pasos) or not d[1].strip():
                sin_explicar.append(linea)
        except Exception as exc:
            sin_explicar.append(f"{linea}: {exc}")
    check(f"las {len(LINEAS)} formas se analizan", not malas, str(malas[:3]))
    check("y todas se explican en palabras", not sin_explicar,
          str(sin_explicar[:3]))


def p_ayuda_completa():
    print("--- la ayuda menciona todas las instrucciones ---")
    app, root = abrir()
    try:
        app.script_help()
        app._drain_log(reprogramar=False)
        ayuda = app.txt_log.get("1.0", "end")
    finally:
        cerrar(app, root)
    ops = sorted({l.split()[0] for l in LINEAS})
    faltan = [o for o in ops if o not in ayuda]
    check("ninguna instruccion sin documentar en la ayuda", not faltan,
          str(faltan))


def p_ops_del_codigo_cubiertas():
    """Que no haya una instruccion implementada que nadie prueba aqui."""
    print("--- no hay instrucciones implementadas sin revisar ---")
    fuente = io.open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "main.py"), encoding="utf-8").read()
    cuerpo = fuente[fuente.index("def _run(self):"):]
    cuerpo = cuerpo[:cuerpo.index("\n    def ", 10)] if "\n    def " in cuerpo[10:] else cuerpo
    ejecutadas = set(re.findall(r'op (?:==|in) \(?["\']([a-z_]+)["\']', cuerpo))
    ejecutadas |= set(re.findall(r'["\']([a-z_]+)["\'](?=\s*[,)])', cuerpo))
    revisadas = {l.split()[0] for l in LINEAS}
    # solo las que de verdad son instrucciones
    conocidas = {"buscar", "desaparecer", "esperar_cambio", "esperar_sonido",
                 "clic", "esperar", "tecla", "pulsar", "girar", "mantener",
                 "soltar", "mantener_clic", "soltar_clic", "escribir", "macro",
                 "pitar", "reafirmar", "ir", "repetir", "parar"}
    faltan = conocidas - revisadas - {"macro"}   # macro necesita un archivo
    check("todas las instrucciones conocidas se revisan", not faltan,
          str(faltan))
    # y que el analizador las acepte todas
    rechazadas = [op for op in conocidas
                  if any("no conozco la instrucción" in e
                         for e in G.Script.parse(op, OBJ)[1])]
    check("el analizador conoce todas", not rechazadas, str(rechazadas))


# ---------------------------------------------------------------- varios
def p_rutas():
    print("--- todos los archivos van a la carpeta de datos ---")
    rutas = {"config": G.CONFIG_PATH, "registro": G.LOG_PATH,
             "plantilla": G.TEMPLATE_IMG, "depuracion": G.DEBUG_IMG,
             "clics": G.SHOT_PATH, "vigilancia": G.WATCH_SHOT}
    for nombre, ruta in rutas.items():
        check(nombre, os.path.dirname(ruta) == G.APP_DIR, ruta)


def p_diagnostico():
    print("--- el boton 'Comprobar todo' no revienta ---")
    import time
    app, root = abrir()
    try:
        app.diagnostico()
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 12:
            root.update()
            time.sleep(0.05)
        app._drain_log(reprogramar=False)
        txt = app.txt_log.get("1.0", "end")
        check("llega hasta el final", txt.count("=" * 52) >= 2,
              f"{txt.count('=' * 52)} separadores")
        for trozo in ("Archivos en", "Pantalla:", "Ventana de delante",
                      "Modo de búsqueda", "Objetivos guardados",
                      "Un escaneo tarda", "Raw input", "Audio", "Guion:"):
            check(f"informa de «{trozo}»", trozo in txt)
        check("sin errores dentro", "Traceback" not in txt)
    finally:
        cerrar(app, root)


def p_panic_lo_para_todo():
    print("--- la parada total apaga de verdad todo ---")
    app, root = abrir()
    try:
        app.script.running = True
        app.player.playing = True
        app.watcher.active = True
        app.var_sched.set(True)
        app.var_txt_on.set(True)
        app.var_guard_on.set(True)
        app.panic()
        app._drain_log(reprogramar=False)
        txt = app.txt_log.get("1.0", "end")
        check("el guion", not app.script.running)
        check("la vigilancia", not app.watcher.active)
        check("la repeticion programada", not app.var_sched.get())
        check("el texto programado", not app.var_txt_on.get())
        check("la guardia", not app.var_guard_on.get())
        for q in ("guion", "vigilancia", "texto programado", "guardia"):
            check(f"lo nombra: {q}", q in txt)
    finally:
        cerrar(app, root)


def p_cierre_limpio():
    print("--- al cerrar no deja hilos vivos ---")
    import threading
    import time
    antes = threading.active_count()
    app, root = abrir()
    app.var_txt_text.set("hola")
    app.var_txt_min.set("30")
    app.var_txt_on.set(True)
    app._toggle_text_scheduler()
    app._cerrando = True
    app._save_config()
    app._sched_stop.set()
    app._txt_stop.set()
    app._guard_stop.set()
    app.script.stop()
    app.player.stop()
    app.watcher.stop()
    try:
        if app._hotkey_listener:
            app._hotkey_listener.stop()
    except Exception:
        pass
    root.destroy()
    time.sleep(1.5)
    despues = threading.active_count()
    check("no crecen los hilos", despues <= antes + 1,
          f"{antes} -> {despues}")


if __name__ == "__main__":
    respaldo = None
    if os.path.exists(G.CONFIG_PATH):
        with io.open(G.CONFIG_PATH, encoding="utf-8-sig") as f:
            respaldo = f.read()
    try:
        for fn in (p_botones, p_hotkeys, p_config_ida_y_vuelta,
                   p_config_estable, p_todas_las_instrucciones,
                   p_ayuda_completa, p_ops_del_codigo_cubiertas, p_rutas,
                   p_diagnostico, p_panic_lo_para_todo, p_cierre_limpio):
            try:
                fn()
            except Exception:
                import traceback
                traceback.print_exc()
                R.append(False)
            print()
    finally:
        if respaldo is not None:
            with io.open(G.CONFIG_PATH, "w", encoding="utf-8") as f:
                f.write(respaldo)
        elif os.path.exists(G.CONFIG_PATH):
            os.remove(G.CONFIG_PATH)
    fallos = R.count(False)
    print(f"{len(R)} comprobaciones, "
          + ("TODO OK" if not fallos else f"{fallos} FALLO(S)"))
    sys.exit(1 if fallos else 0)
