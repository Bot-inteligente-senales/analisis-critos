"""Señales repetidas y etiqueta «Fuerza de la señal».

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


if __name__ == "__main__":
    unittest.main()
