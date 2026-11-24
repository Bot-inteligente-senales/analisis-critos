"""Señales repetidas, etiqueta «Fuerza de la señal» y ventana de lectura del registro.

Importar app.py ejecuta el panel, así que se extraen sus funciones mediante AST (igual que
test_firebase_protections.py). vigia.py sí se puede importar: no depende de Streamlit.
"""

import ast
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def cargar_funciones(archivo, nombres, contexto):
    arbol = ast.parse((ROOT / archivo).read_text(encoding="utf-8"))
    nodos = [n for n in arbol.body if isinstance(n, ast.FunctionDef) and n.name in nombres]
    assert {n.name for n in nodos} == set(nombres)
    exec(compile(ast.Module(body=nodos, type_ignores=[]), archivo, "exec"), contexto)
    return contexto


def constante(archivo, nombre):
    """Lee una constante entera de app.py sin ejecutarlo."""
    arbol = ast.parse((ROOT / archivo).read_text(encoding="utf-8"))
    for n in arbol.body:
        if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == nombre for t in n.targets):
            return ast.literal_eval(n.value)
    raise AssertionError(f"No encontré {nombre}")


class SenalRepetidaTests(unittest.TestCase):
    def setUp(self):
        self.f = cargar_funciones("app.py", {"es_senal_repetida"}, {})["es_senal_repetida"]
        self.horas = constante("app.py", "HORAS_REPETIR_MISMA_SENAL")

    def test_sin_senal_previa_no_es_repetida(self):
        self.assertFalse(self.f("SELL", "", 0.0, 1_000_000.0, self.horas))
        self.assertFalse(self.f("SELL", None, None, 1_000_000.0, self.horas))

    def test_misma_direccion_antes_de_la_ventana_es_repetida(self):
        self.assertTrue(self.f("SELL", "SELL", 1000.0, 1000.0 + 15 * 60, self.horas))
        self.assertTrue(self.f("SELL", "SELL", 1000.0, 1000.0 + self.horas * 3600 - 1, self.horas))

    def test_misma_direccion_despues_de_la_ventana_ya_sale(self):
        self.assertFalse(self.f("SELL", "SELL", 1000.0, 1000.0 + self.horas * 3600, self.horas))

    def test_cambio_de_direccion_sale_enseguida(self):
        self.assertFalse(self.f("BUY", "SELL", 1000.0, 1001.0, self.horas))
        self.assertFalse(self.f("SELL", "BUY", 1000.0, 1001.0, self.horas))

    def test_una_racha_bajista_de_24h_manda_unas_pocas_senales_no_cien(self):
        """Simula el ciclo real: 5 s por ciclo, anti-spam de 180 ciclos y SELL todo el día."""
        enviadas, ultima_ciclo, tipo, ts = 0, 0, "", 0.0
        t0 = 1_800_000_000.0
        for ciclo in range(1, 24 * 3600 // 5 + 1):
            ahora = t0 + ciclo * 5
            if ciclo - ultima_ciclo > 180 and not self.f("SELL", tipo, ts, ahora, self.horas):
                enviadas += 1
                ultima_ciclo, tipo, ts = ciclo, "SELL", ahora
        self.assertLessEqual(enviadas, 24 // self.horas + 1)
        self.assertGreaterEqual(enviadas, 1)

    def test_el_cambio_a_compra_durante_la_racha_si_se_envia(self):
        tipo, ts = "SELL", 1000.0
        self.assertTrue(self.f("SELL", tipo, ts, 1000.0 + 3600, self.horas))   # sigue siendo repetida
        self.assertFalse(self.f("BUY", tipo, ts, 1000.0 + 3600, self.horas))   # cambió: sale


class EtiquetaFuerzaTests(unittest.TestCase):
    def test_telegram_dice_fuerza_de_la_senal_y_no_probabilidad(self):
        enviar = Mock(return_value=True)
        ctx = cargar_funciones(
            "app.py",
            {"calcular_probabilidad", "enviar_senal_telegram"},
            {"CANAL_CONFIGURADO": False, "enviar_telegram": enviar, "enviar_canal_telegram": Mock(),
             "datetime": datetime, "timezone": timezone, "timedelta": timedelta},
        )
        self.assertTrue(ctx["enviar_senal_telegram"](
            "BTC", "SELL", 1_500_000, "Señal de venta (-75.0)", 75.0, 30.0, -12.0, "BAJISTA"))
        texto = enviar.call_args[0][0]
        self.assertIn("Fuerza de la señal: 100.0%", texto)
        self.assertIn("no es probabilidad de acierto", texto)
        self.assertNotIn("Probabilidad:", texto)

    def test_el_resumen_del_vigia_dice_fuerza(self):
        import vigia
        datos = {"senales": {"horas": 24, "total": 1, "por_motor": {"actual": 1}, "por_tipo": {"SELL": 1},
                             "ultima": {"simbolo": "BTC", "tipo": "SELL", "motor": "actual",
                                        "fuerza_senal": 100.0, "hace_min": 1.0}},
                 "dca": {"nota": "sin datos"}, "tendencia": {"nota": "sin datos"}}
        texto = vigia.Vigia.formatear_hechos(datos, "2026-11-20")
        self.assertIn("BTC SELL fuerza 100.0%", texto)
        self.assertNotIn("probabilidad", texto.lower())


class VentanaDeLecturaTests(unittest.TestCase):
    """El panel lee las últimas MAX_REGISTROS_SENALES señales (antes eran 1000 fijas)."""

    def _leer(self, **kw):
        from types import SimpleNamespace
        respuesta = Mock(status_code=200)
        respuesta.json.return_value = {"-a": {"ts": 1}, "-b": {"ts": 2}}
        requests = SimpleNamespace(get=Mock(return_value=respuesta))
        ctx = cargar_funciones(
            "app.py", {"_leer_registros_senales"},
            {"st": SimpleNamespace(session_state={}), "time": __import__("time"), "requests": requests,
             "_url_registro": lambda ruta: f"https://fb/{ruta}.json", "SEGUNDOS_CACHE_ESTADISTICAS": 300,
             "MAX_REGISTROS_SENALES": constante("app.py", "MAX_REGISTROS_SENALES")})
        return ctx["_leer_registros_senales"](**kw), requests.get

    def test_la_ventana_ya_no_es_de_1000(self):
        self.assertGreater(constante("app.py", "MAX_REGISTROS_SENALES"), 1000)

    def test_pide_a_firebase_la_ventana_completa(self):
        registros, get = self._leer()
        self.assertEqual(len(registros), 2)
        self.assertEqual(get.call_args.kwargs["params"]["limitToLast"],
                         constante("app.py", "MAX_REGISTROS_SENALES"))

    def test_se_puede_pedir_otra_ventana(self):
        _, get = self._leer(maximo=50)
        self.assertEqual(get.call_args.kwargs["params"]["limitToLast"], 50)


class QuitarSenalesRepetidasTests(unittest.TestCase):
    """Las estadísticas cuentan una sola vez cada racha de señales iguales (misma regla de 4 h del bot)."""

    T0 = 1_800_000_000.0

    def setUp(self):
        self.horas = constante("app.py", "HORAS_REPETIR_MISMA_SENAL")
        ctx = cargar_funciones("app.py", {"es_senal_repetida", "quitar_senales_repetidas"},
                               {"HORAS_REPETIR_MISMA_SENAL": self.horas})
        self.f = ctx["quitar_senales_repetidas"]

    def senal(self, minutos, tipo="SELL", simbolo="BTC", **extra):
        return {"ts": self.T0 + minutos * 60, "tipo": tipo, "simbolo": simbolo, **extra}

    def test_una_racha_de_24h_cada_15_min_queda_en_una_por_ventana(self):
        racha = [self.senal(15 * i) for i in range(24 * 4)]          # 96 señales SELL en 24 h
        distintas = self.f(racha)
        self.assertEqual(len(distintas), 24 // self.horas)
        pasos = [b["ts"] - a["ts"] for a, b in zip(distintas, distintas[1:])]
        self.assertTrue(all(p >= self.horas * 3600 for p in pasos))

    def test_el_cambio_de_direccion_se_conserva(self):
        seq = [self.senal(0, "SELL"), self.senal(15, "SELL"), self.senal(30, "BUY"),
               self.senal(45, "BUY"), self.senal(60, "SELL")]
        self.assertEqual([r["tipo"] for r in self.f(seq)], ["SELL", "BUY", "SELL"])

    def test_cada_moneda_va_por_su_lado(self):
        seq = [self.senal(0, simbolo="BTC"), self.senal(1, simbolo="ETH"),
               self.senal(15, simbolo="BTC"), self.senal(16, simbolo="ETH")]
        self.assertEqual([r["simbolo"] for r in self.f(seq)], ["BTC", "ETH"])

    def test_cada_motor_va_por_su_lado_y_sin_campo_motor_cuenta_como_actual(self):
        seq = [self.senal(0), self.senal(5, motor="v2"), self.senal(10, motor="actual"), self.senal(15, motor="v2")]
        distintas = self.f(seq)
        self.assertEqual(len(distintas), 2)
        self.assertEqual([r.get("motor") for r in distintas], [None, "v2"])

    def test_separadas_por_la_ventana_o_mas_se_cuentan_todas(self):
        seq = [self.senal(i * self.horas * 60) for i in range(5)]
        self.assertEqual(len(self.f(seq)), 5)

    def test_no_depende_del_orden_ni_modifica_la_lista(self):
        seq = [self.senal(30), self.senal(0), self.senal(15)]
        copia = list(seq)
        self.assertEqual([r["ts"] for r in self.f(seq)], [self.T0])
        self.assertEqual(seq, copia)

    def test_un_registro_sin_tipo_no_se_pierde(self):
        raro = {"ts": self.T0 + 5, "simbolo": "BTC"}
        self.assertIn(raro, self.f([self.senal(0), raro]))

    def test_lista_vacia(self):
        self.assertEqual(self.f([]), [])

    def test_las_estadisticas_dejan_de_contar_la_misma_racha_varias_veces(self):
        ctx = cargar_funciones(
            "app.py", {"es_senal_repetida", "quitar_senales_repetidas", "calcular_estadisticas_senales"},
            {"HORAS_REPETIR_MISMA_SENAL": self.horas,
             "HORIZONTES_HORAS": constante("app.py", "HORIZONTES_HORAS"),
             "MUESTRA_MINIMA_CONFIABLE": constante("app.py", "MUESTRA_MINIMA_CONFIABLE")})
        resultado = {"1h": {"acierto": True, "retorno_pct": 0.5}}
        racha = [self.senal(15 * i, resultados=resultado, coinciden="SI") for i in range(16)]   # 4 h seguidas
        crudo = ctx["calcular_estadisticas_senales"](racha)[0]
        unico = ctx["calcular_estadisticas_senales"](ctx["quitar_senales_repetidas"](racha))[0]
        self.assertEqual(crudo[0]["Evaluadas"], 16)
        self.assertEqual(unico[0]["Evaluadas"], 1)
        self.assertEqual(unico[0]["Aciertos"], 1)

    def test_el_panel_el_centro_de_mando_y_el_resumen_usan_las_distintas(self):
        app = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn("calcular_estadisticas_senales(distintas)", app)
        self.assertIn("simular_tp_sl(distintas, velas)", app)
        self.assertIn('_par_acierto(distintas, "actual", "1h")', app)
        self.assertIn('_par_acierto(distintas, "actual", "4h")', app)
        resumen = (ROOT / "resumen_publico.py").read_text(encoding="utf-8")
        self.assertIn("calcular_estadisticas_senales(app.quitar_senales_repetidas(senales))", resumen)
        # el total de señales enviadas del día NO se toca: es lo que realmente salió por Telegram
        self.assertIn("enviadas = [r for r in del_motor_actual if reciente(r)]", resumen)


class AjustesDelPanelTests(unittest.TestCase):
    """Tope de lectura de bloqueadas, marcas por racha en la gráfica y texto honesto de la compra periódica."""

    T0 = 1_800_000_000.0

    def test_el_tope_de_bloqueadas_subio_y_es_el_de_lectura_por_defecto(self):
        from types import SimpleNamespace
        tope = constante("app.py", "MAX_REGISTROS_BLOQUEADAS")
        self.assertGreater(tope, 500)
        respuesta = Mock(status_code=200)
        respuesta.json.return_value = {"-a": {"ts": 1}}
        requests = SimpleNamespace(get=Mock(return_value=respuesta))
        ctx = cargar_funciones(
            "app.py", {"_leer_registros_bloqueadas"},
            {"st": SimpleNamespace(session_state={}), "time": __import__("time"), "requests": requests,
             "_url_registro": lambda ruta: f"https://fb/{ruta}.json", "RUTA_BLOQUEADAS": "senales_bloqueadas",
             "SEGUNDOS_CACHE_ESTADISTICAS": 300, "MAX_REGISTROS_BLOQUEADAS": tope})
        self.assertEqual(ctx["_leer_registros_bloqueadas"](), [{"ts": 1}])
        self.assertEqual(requests.get.call_args.kwargs["params"]["limitToLast"], tope)

    def _marcas(self):
        horas = constante("app.py", "HORAS_REPETIR_MISMA_SENAL")
        ctx = cargar_funciones(
            "app.py", {"es_senal_repetida", "quitar_senales_repetidas", "_marcas_grafica"},
            {"HORAS_REPETIR_MISMA_SENAL": horas})
        return ctx["quitar_senales_repetidas"], ctx["_marcas_grafica"]

    def test_una_racha_se_dibuja_con_una_sola_marca(self):
        quitar, marcar = self._marcas()
        racha = [{"ts": self.T0 + 900 * i, "tipo": "SELL", "simbolo": "BTC"} for i in range(8)]   # 2 h seguidas
        ahora = self.T0 + 24 * 3600
        self.assertEqual(len(marcar(racha, 96, ahora)), 8)            # sin quitar repetidas: ocho triángulos
        self.assertEqual(marcar(quitar(racha), 96, ahora), [[71, "sell"]])

    def test_compra_venta_otra_moneda_y_fuera_de_ventana(self):
        _, marcar = self._marcas()
        ahora = self.T0 + 200 * 3600
        senales = [
            {"ts": ahora, "tipo": "BUY", "simbolo": "BTC"},                    # ahora mismo: última vela
            {"ts": ahora - 10 * 3600, "tipo": "SELL", "simbolo": "BTC"},
            {"ts": ahora - 5 * 3600, "tipo": "BUY", "simbolo": "ETH"},         # otra moneda: no va
            {"ts": ahora - 100 * 3600, "tipo": "BUY", "simbolo": "BTC"},       # fuera de las 96 h: no va
        ]
        self.assertEqual(marcar(senales, 96, ahora), [[95, "buy"], [85, "sell"]])

    def test_el_texto_de_la_compra_periodica_ya_no_culpa_solo_a_las_comisiones(self):
        app = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("solo por las comisiones", app)
        self.assertIn("lo que haya hecho el precio desde la primera compra", app)

    def test_el_resumen_diario_lee_las_mismas_ventanas_que_el_panel(self):
        resumen = (ROOT / "resumen_publico.py").read_text(encoding="utf-8")
        self.assertIn("limitToLast=app.MAX_REGISTROS_SENALES", resumen)
        self.assertIn("limitToLast=app.MAX_REGISTROS_BLOQUEADAS", resumen)


if __name__ == "__main__":
    unittest.main()
