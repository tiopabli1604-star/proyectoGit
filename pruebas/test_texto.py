# -*- coding: utf-8 -*-
"""Texto programado: escribirlo bien y no pisar el movimiento.

Lo importante es la segunda parte. Si al tocarle el turno hay una macro o un
guion en marcha, el texto NO se puede escribir en ese momento: hay que esperar
un hueco, porque si no las teclas se cuelan en medio del movimiento grabado.
"""
import os
import sys
import threading
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main as G

R = []


def check(nombre, ok, extra=""):
    R.append(ok)
    extra = str(extra) if extra else ""
    print(f"  {'OK  ' if ok else 'FALLO'} {nombre}{(' — ' + extra) if extra else ''}")


class TecladoEspia:
    """Apunta lo que se teclea, en orden, sin tocar el teclado de verdad."""

    def __init__(self):
        self.acciones = []

    def press(self, k):
        self.acciones.append(("press", str(k)))

    def release(self, k):
        self.acciones.append(("release", str(k)))

    def type(self, s):
        self.acciones.append(("type", s))

    @property
    def escrito(self):
        return "".join(a[1] for a in self.acciones if a[0] == "type")


# ------------------------------------------------------------ type_text
def p_escribe_letra_a_letra():
    print("--- teclea caracter a caracter, no de un volcado ---")
    kb = TecladoEspia()
    G.type_text(kb, "hola", tecla_antes=None, intro=False, delay_char=0.0)
    tipos = [a for a in kb.acciones if a[0] == "type"]
    check("una llamada por letra", len(tipos) == 4, f"{tipos}")
    check("y el texto sale entero", kb.escrito == "hola", kb.escrito)


def p_abre_el_chat_y_pulsa_intro():
    print("--- abre el chat, escribe y pulsa Intro ---")
    kb = TecladoEspia()
    G.type_text(kb, "hi", tecla_antes="t", intro=True, delay_char=0.0,
                delay_ui=0.0)
    ops = kb.acciones
    check("lo primero es pulsar la tecla del chat",
          ops[0] == ("press", "t") and ops[1] == ("release", "t"), str(ops[:2]))
    check("luego el texto", kb.escrito == "hi", kb.escrito)
    check("y lo ultimo Intro",
          ops[-2] == ("press", str(G.keyboard.Key.enter))
          and ops[-1] == ("release", str(G.keyboard.Key.enter)), str(ops[-2:]))


def p_sin_tecla_ni_intro():
    print("--- sin tecla previa y sin Intro no toca nada mas ---")
    kb = TecladoEspia()
    G.type_text(kb, "abc", tecla_antes=None, intro=False, delay_char=0.0)
    check("solo hay tecleo", all(a[0] == "type" for a in kb.acciones),
          str(kb.acciones))


def p_espera_a_que_aparezca_el_chat():
    print("--- espera tras abrir el chat antes de teclear ---")
    kb = TecladoEspia()
    t0 = time.perf_counter()
    G.type_text(kb, "x", tecla_antes="t", intro=False, delay_char=0.0,
                delay_ui=0.30)
    tardo = time.perf_counter() - t0
    check("hay una pausa de ~0.3 s", 0.28 < tardo < 0.55, f"{tardo:.3f} s")


def p_tecla_mala():
    print("--- una tecla que no existe se avisa, no se traga ---")
    kb = TecladoEspia()
    try:
        G.type_text(kb, "x", tecla_antes="teclainexistente", delay_char=0.0)
        check("lanza ValueError", False, "no lanzo nada")
    except ValueError as exc:
        check("lanza ValueError", True, str(exc))
    check("y no ha escrito nada", not kb.acciones, str(kb.acciones))


# ------------------------------------------------------------ programador
def abrir():
    if os.path.exists(G.CONFIG_PATH):
        os.remove(G.CONFIG_PATH)
    root = tk.Tk()
    root.withdraw()
    app = G.App(root)
    app._kb = TecladoEspia()
    return app, root


def cerrar(app, root):
    try:
        app._txt_stop.set()
        app._sched_stop.set()
        app.script.stop()
        if app._hotkey_listener:
            app._hotkey_listener.stop()
    except Exception:
        pass
    root.destroy()


def registro(app, root):
    """El registro tal como lo vera la ventana: se vacia la cola y se lee."""
    try:
        app._drain_log(reprogramar=False)
    except Exception:
        pass
    return app.txt_log.get("1.0", "end")


def p_no_arranca_sin_texto():
    print("--- no arranca si no le has puesto texto ---")
    app, root = abrir()
    try:
        app.var_txt_text.set("   ")
        app.var_txt_on.set(True)
        app._toggle_text_scheduler()
        check("se desmarca solo", app.var_txt_on.get() is False)
        check("y lo dice", "Escribe primero el texto" in registro(app, root))
    finally:
        cerrar(app, root)


def p_valida_minutos_y_tecla():
    print("--- valida los minutos y la tecla ---")
    app, root = abrir()
    try:
        app.var_txt_text.set("hola")
        app.var_txt_min.set("cero")
        app.var_txt_on.set(True)
        app._toggle_text_scheduler()
        check("minutos no numericos: no arranca", app.var_txt_on.get() is False)
        check("y lo dice", "no son un número válido" in registro(app, root))

        app.var_txt_min.set("31")
        app.var_txt_key.set("teclamala")
        app.var_txt_on.set(True)
        app._toggle_text_scheduler()
        check("tecla mala: no arranca", app.var_txt_on.get() is False)
        check("y dice cual",
              "tecla para abrir el chat no vale" in registro(app, root))
    finally:
        cerrar(app, root)


def p_escribe_de_verdad():
    print("--- el envio manual escribe la secuencia completa ---")
    app, root = abrir()
    try:
        app.var_txt_text.set("/afk")
        app.var_txt_key.set("t")
        app.var_txt_enter.set(True)
        ok = app._enviar_texto()
        check("dice que lo escribio", ok is True)
        check("con el texto entero", app._kb.escrito == "/afk", app._kb.escrito)
        check("abriendo con la t", app._kb.acciones[0] == ("press", "t"),
              str(app._kb.acciones[:2]))
        check("y contando el envio", app._txt_count == 1, str(app._txt_count))
    finally:
        cerrar(app, root)


def p_espera_hueco():
    print("--- si hay una macro en marcha, ESPERA en vez de pisarla ---")
    app, root = abrir()
    try:
        app.var_txt_text.set("hola")
        app.var_txt_key.set("")
        app.var_txt_enter.set(False)
        app.player.playing = True          # como si estuviera reproduciendo
        # periodo de 0.3 s: da para varios turnos con la macro en marcha
        hilo = threading.Thread(target=app._txt_run, args=(0.3 / 60,),
                                daemon=True)
        hilo.start()
        time.sleep(0.9)
        check("no ha escrito nada mientras la macro corre",
              not app._kb.acciones, str(app._kb.acciones))
        check("y avisa de que espera un hueco",
              "espero un hueco" in registro(app, root))

        app.player.playing = False         # se libera
        t0 = time.perf_counter()
        while app._txt_count == 0 and time.perf_counter() - t0 < 4.0:
            time.sleep(0.02)
        app._txt_stop.set()                # parar antes de que repita el turno
        tardo = time.perf_counter() - t0
        check("en cuanto queda libre, escribe",
              app._txt_count >= 1 and app._kb.escrito.startswith("hola"),
              f"{app._txt_count} envio(s) en {tardo:.2f} s, "
              f"escrito={app._kb.escrito!r}")
        check("y escribe el texto entero, sin cortarlo",
              app._kb.escrito.replace("hola", "") == "",
              repr(app._kb.escrito))
    finally:
        app._txt_stop.set()
        cerrar(app, root)


def p_ocupado_cuenta_los_tres():
    print("--- 'ocupado' mira grabacion, reproduccion y guion ---")
    app, root = abrir()
    try:
        check("libre al empezar", app._ocupado() is False)
        app.recorder.recording = True
        check("grabando cuenta", app._ocupado() is True)
        app.recorder.recording = False
        app.player.playing = True
        check("reproduciendo cuenta", app._ocupado() is True)
        app.player.playing = False
        app.script.running = True
        check("el guion cuenta", app._ocupado() is True)
        app.script.running = False
        check("y libre otra vez", app._ocupado() is False)
    finally:
        cerrar(app, root)


def p_probarlo_ahora_respeta_lo_que_corre():
    print("--- 'Probarlo ahora' no pisa una macro en marcha ---")
    app, root = abrir()
    try:
        app.var_txt_text.set("hola")
        app.player.playing = True
        app.send_text_now()
        time.sleep(0.3)
        check("no escribe", not app._kb.acciones, str(app._kb.acciones))
        check("y lo explica", "para eso primero" in registro(app, root))
    finally:
        cerrar(app, root)


def p_panic_lo_para():
    print("--- F12 para el texto programado ---")
    app, root = abrir()
    try:
        app.var_txt_text.set("hola")
        app.var_txt_min.set("31")
        app.var_txt_on.set(True)
        app._toggle_text_scheduler()
        check("arranco", app.var_txt_on.get() is True)
        app.panic()
        check("F12 lo desmarca", app.var_txt_on.get() is False)
        txt = registro(app, root)
        check("y lo nombra en la parada total", "texto programado" in txt,
              [l for l in txt.splitlines() if "PARADA" in l][:1])
    finally:
        cerrar(app, root)


def p_persistencia():
    print("--- el texto y los minutos se guardan ---")
    app, root = abrir()
    try:
        app.var_txt_text.set("/afk on")
        app.var_txt_min.set("31")
        app.var_txt_key.set("t")
        app.var_txt_enter.set(False)
        app._save_config()
    finally:
        cerrar(app, root)
    root = tk.Tk()
    root.withdraw()
    app = G.App(root)
    try:
        check("vuelve el texto", app.var_txt_text.get() == "/afk on",
              app.var_txt_text.get())
        check("vuelven los minutos", app.var_txt_min.get() == "31",
              app.var_txt_min.get())
        check("vuelve la tecla", app.var_txt_key.get() == "t",
              app.var_txt_key.get())
        check("vuelve el Intro", app.var_txt_enter.get() is False)
        check("pero arranca desactivado", app.var_txt_on.get() is False)
    finally:
        cerrar(app, root)


if __name__ == "__main__":
    respaldo = None
    if os.path.exists(G.CONFIG_PATH):
        with open(G.CONFIG_PATH, encoding="utf-8-sig") as f:
            respaldo = f.read()
    try:
        for fn in (p_escribe_letra_a_letra, p_abre_el_chat_y_pulsa_intro,
                   p_sin_tecla_ni_intro, p_espera_a_que_aparezca_el_chat,
                   p_tecla_mala, p_no_arranca_sin_texto,
                   p_valida_minutos_y_tecla, p_escribe_de_verdad,
                   p_espera_hueco, p_ocupado_cuenta_los_tres,
                   p_probarlo_ahora_respeta_lo_que_corre, p_panic_lo_para,
                   p_persistencia):
            try:
                fn()
            except Exception:
                import traceback
                traceback.print_exc()
                R.append(False)
            print()
    finally:
        if respaldo is not None:
            with open(G.CONFIG_PATH, "w", encoding="utf-8") as f:
                f.write(respaldo)
        elif os.path.exists(G.CONFIG_PATH):
            os.remove(G.CONFIG_PATH)
    fallos = R.count(False)
    print(f"{len(R)} comprobaciones, "
          + ("TODO OK" if not fallos else f"{fallos} FALLO(S)"))
    sys.exit(1 if fallos else 0)
