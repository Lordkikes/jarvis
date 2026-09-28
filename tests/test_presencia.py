"""Salir y llegar: la presencia, y las rutinas que dispara.

Una rutina de mañana sabe cuándo toca porque son las siete; una de salir de
casa no tiene hora. Lo que se prueba aquí es sobre todo lo que **no** pasa:
que un sensor caído no vacía la casa, que reiniciar no dispara nada y que la
casa vacía no es motivo para tocar una cerradura.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.bus import EventBus  # noqa: E402
from jarvis.config import Config  # noqa: E402
from jarvis.domotica import SinConexion  # noqa: E402
from jarvis.llm.tools import Toolbox  # noqa: E402
from jarvis.pipeline import Jarvis  # noqa: E402
from jarvis.presencia import EN_CASA, FUERA, Presencia  # noqa: E402
from jarvis.rutinas import SUCESOS  # noqa: E402


class FalsaCasa:
    """Home Assistant de mentira: le dices qué contesta a cada entidad."""

    def __init__(self, estados: dict | None = None, disponible: bool = True):
        self.estados_por_id = dict(estados or {})
        self.disponible = disponible
        self.caido = False

    def estado(self, entity_id: str) -> dict | None:
        if self.caido:
            raise SinConexion("no contesta")
        if entity_id not in self.estados_por_id:
            return None
        return {"entity_id": entity_id,
                "state": self.estados_por_id[entity_id]}


class TestEstado(unittest.TestCase):
    def presencia(self, estados: dict, **kwargs) -> Presencia:
        return Presencia(FalsaCasa(estados, **kwargs), list(estados))

    def test_uno_en_casa(self):
        self.assertEqual(self.presencia({"person.yelko": "home"}).estado(),
                         EN_CASA)

    def test_uno_fuera(self):
        self.assertEqual(self.presencia({"person.yelko": "not_home"}).estado(),
                         FUERA)

    def test_en_otra_zona_es_estar_fuera(self):
        """«trabajo» no es casa, y Home Assistant lo dice con el nombre de la zona."""
        self.assertEqual(self.presencia({"person.yelko": "trabajo"}).estado(),
                         FUERA)

    def test_basta_con_que_uno_este(self):
        presencia = self.presencia({"person.yelko": "not_home",
                                    "person.otra": "home"})
        self.assertEqual(presencia.estado(), EN_CASA)

    def test_vacia_solo_si_lo_estan_todos(self):
        presencia = self.presencia({"person.yelko": "not_home",
                                    "person.otra": "not_home"})
        self.assertEqual(presencia.estado(), FUERA)

    def test_un_sensor_caido_no_vacia_la_casa(self):
        """Apagarlo todo porque un móvil dejó de reportar sería peor que nada."""
        for valor in ("unknown", "unavailable", ""):
            with self.subTest(valor=valor):
                self.assertEqual(self.presencia({"person.yelko": valor}).estado(),
                                 "")

    def test_uno_caido_y_otro_fuera_sigue_siendo_fuera(self):
        presencia = self.presencia({"person.yelko": "unavailable",
                                    "person.otra": "not_home"})
        self.assertEqual(presencia.estado(), FUERA)

    def test_si_no_contesta_nadie_no_se_inventa(self):
        casa = FalsaCasa({"person.yelko": "home"})
        casa.caido = True
        self.assertEqual(Presencia(casa, ["person.yelko"]).estado(), "")

    def test_una_entidad_que_no_existe(self):
        self.assertEqual(Presencia(FalsaCasa({}), ["person.nadie"]).estado(), "")

    def test_sin_entidades_no_esta_disponible(self):
        presencia = Presencia(FalsaCasa({}), [])
        self.assertFalse(presencia.disponible)
        self.assertEqual(presencia.estado(), "")

    def test_sin_home_assistant_tampoco(self):
        presencia = Presencia(FalsaCasa({}, disponible=False), ["person.yelko"])
        self.assertFalse(presencia.disponible)


class TestCambio(unittest.TestCase):
    def setUp(self):
        self.casa = FalsaCasa({"person.yelko": "home"})
        self.presencia = Presencia(self.casa, ["person.yelko"])

    def mueve(self, donde: str) -> str:
        self.casa.estados_por_id["person.yelko"] = donde
        return self.presencia.cambio()

    def test_el_primer_vistazo_no_dispara_nada(self):
        """Reiniciar estando fuera no puede disparar la rutina de salir."""
        self.assertEqual(self.presencia.cambio(), "")

    def test_ni_aunque_arranque_con_la_casa_vacia(self):
        self.casa.estados_por_id["person.yelko"] = "not_home"
        self.assertEqual(self.presencia.cambio(), "")

    def test_salir(self):
        self.presencia.cambio()
        self.assertEqual(self.mueve("not_home"), "salir")

    def test_llegar(self):
        self.casa.estados_por_id["person.yelko"] = "not_home"
        self.presencia.cambio()
        self.assertEqual(self.mueve("home"), "llegar")

    def test_sin_moverse_no_pasa_nada(self):
        self.presencia.cambio()
        self.assertEqual(self.mueve("home"), "")
        self.assertEqual(self.mueve("home"), "")

    def test_no_se_repite(self):
        self.presencia.cambio()
        self.assertEqual(self.mueve("not_home"), "salir")
        self.assertEqual(self.mueve("not_home"), "")

    def test_ir_y_volver(self):
        self.presencia.cambio()
        self.assertEqual(self.mueve("not_home"), "salir")
        self.assertEqual(self.mueve("home"), "llegar")

    def test_un_hueco_sin_saber_no_cuenta_como_salida(self):
        """El móvil pierde cobertura un rato; eso no es haberse ido."""
        self.presencia.cambio()
        self.assertEqual(self.mueve("unavailable"), "")
        self.assertEqual(self.mueve("home"), "", "y al volver, tampoco pasa nada")

    def test_olvidar_vuelve_a_empezar(self):
        self.presencia.cambio()
        self.presencia.olvida()
        self.assertEqual(self.mueve("not_home"), "",
                         "el siguiente vistazo es el primero otra vez")


class CasoConCaja(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dicho: list[str] = []
        cfg = Config({
            "tools": {"allow_system": True,
                      "notes_file": str(Path(self.tmp.name) / "n.json"),
                      "memory_file": str(Path(self.tmp.name) / "m.json"),
                      "lists_file": str(Path(self.tmp.name) / "l.json"),
                      "routines_file": str(Path(self.tmp.name) / "rut.json"),
                      "presencia": {"entidades": ["person.yelko"]}},
            "llm": {"web_search": False},
        })
        with mock.patch.dict(os.environ, {"JARVIS_HASS_TOKEN": ""}):
            self.caja = Toolbox(cfg, EventBus(), on_announce=self.anuncia)
        self.casa = FalsaCasa({"person.yelko": "home"})
        self.caja.presencia = Presencia(self.casa, ["person.yelko"])

    async def anuncia(self, mensaje: str) -> None:
        self.dicho.append(mensaje)

    def tearDown(self):
        self.tmp.cleanup()

    async def crea(self, nombre: str, pasos: str, cuando: str) -> str:
        return await self.caja.run("crear_rutina", {"nombre": nombre,
                                                    "pasos": pasos,
                                                    "cuando": cuando})

    async def mueve(self, donde: str) -> int:
        self.casa.estados_por_id["person.yelko"] = donde
        return await self.caja.mira_la_presencia()


class TestHerramienta(CasoConCaja):
    async def test_crear_una_de_salir(self):
        salida = await self.crea("me voy", "apagar_luces", "salir")
        self.assertIn("cuando la casa se queda vacía", salida)

    async def test_crear_una_de_llegar(self):
        salida = await self.crea("ya estoy", "decir: bienvenido", "llegar")
        self.assertIn("cuando llegues a casa", salida)

    async def test_se_ven_al_preguntar(self):
        await self.crea("me voy", "apagar_luces", "salir")
        self.assertIn("cuando la casa se queda vacía",
                      await self.caja.run("ver_rutinas", {}))

    async def test_un_disparador_que_no_existe(self):
        """Se guarda a mano en vez de inventarse un suceso."""
        await self.crea("rara", "saludo", "cuando llueva")
        self.assertEqual(self.caja.rutinas.lista()[0]["disparador"], "mano")

    def test_los_sucesos_son_los_que_son(self):
        self.assertEqual(SUCESOS, ("alarma", "salir", "llegar"))


class TestDisparo(CasoConCaja):
    async def test_al_salir(self):
        await self.crea("me voy", "decir: hasta luego", "salir")
        await self.caja.mira_la_presencia()  # el primer vistazo solo apunta
        self.assertEqual(await self.mueve("not_home"), 1)
        self.assertEqual(self.dicho, ["hasta luego"])

    async def test_al_llegar(self):
        await self.crea("ya estoy", "decir: bienvenido", "llegar")
        await self.caja.mira_la_presencia()
        await self.mueve("not_home")
        self.dicho.clear()
        self.assertEqual(await self.mueve("home"), 1)
        self.assertEqual(self.dicho, ["bienvenido"])

    async def test_la_de_salir_no_salta_al_llegar(self):
        await self.crea("me voy", "decir: hasta luego", "salir")
        await self.caja.mira_la_presencia()
        await self.mueve("not_home")
        self.dicho.clear()
        self.assertEqual(await self.mueve("home"), 0)
        self.assertEqual(self.dicho, [])

    async def test_arrancar_no_dispara_nada(self):
        await self.crea("me voy", "decir: hasta luego", "salir")
        self.casa.estados_por_id["person.yelko"] = "not_home"
        self.assertEqual(await self.caja.mira_la_presencia(), 0)
        self.assertEqual(self.dicho, [])

    async def test_sin_presencia_configurada_no_hace_nada(self):
        self.caja.presencia = Presencia(FalsaCasa({}), [])
        await self.crea("me voy", "decir: hasta luego", "salir")
        self.assertEqual(await self.caja.mira_la_presencia(), 0)

    async def test_el_cambio_se_ve_en_la_interfaz(self):
        cola = self.caja.bus.subscribe()
        await self.caja.mira_la_presencia()
        await self.mueve("not_home")

        eventos = []
        while not cola.empty():
            eventos.append(cola.get_nowait())
        presencia = [e for e in eventos if e["type"] == "presencia"]
        self.assertEqual(len(presencia), 1)
        self.assertTrue(presencia[0].get("message"))

    async def test_las_de_hora_no_se_disparan_al_salir(self):
        await self.caja.run("crear_rutina", {"nombre": "buenos días",
                                             "pasos": "decir: hola",
                                             "hora": "07:00"})
        await self.caja.mira_la_presencia()
        self.assertEqual(await self.mueve("not_home"), 0)

    async def test_la_casa_vacia_no_toca_la_cerradura(self):
        """Irse de casa tampoco autoriza a una lista a echar la llave."""
        llamadas = []
        self.caja._dispositivo = lambda que: {
            "entity_id": "lock.puerta",
            "attributes": {"friendly_name": "la puerta"}}
        self.caja._tool_controlar_dispositivo = lambda args: llamadas.append(args)
        await self.crea("me voy", "apagar: la puerta", "salir")
        await self.caja.mira_la_presencia()
        await self.mueve("not_home")

        self.assertEqual(llamadas, [])
        self.assertIsNone(self.caja._pendiente)


class TestAlMovil(CasoConCaja):
    def setUp(self):
        super().setUp()
        self.mandado = []
        self.caja.avisos.envia = lambda texto, titulo="", urgente=False: (
            self.mandado.append((texto, titulo)))
        # `disponible` es una propiedad: se configura, no se parchea.
        self.caja.avisos.ntfy.topico = "jarvis-de-pruebas"

    async def ejecuta(self, pasos: str) -> str:
        await self.crea("me voy", pasos, "mano")
        return await self.caja.run("ejecutar_rutina", {"cual": "me voy"})

    async def test_manda_lo_dicho_hasta_ahi(self):
        await self.ejecuta("decir: te dejas la ventana abierta, al_movil")
        self.assertEqual(self.mandado,
                         [("te dejas la ventana abierta", "Jarvis")])

    async def test_no_manda_lo_que_viene_despues(self):
        """El paso corta por donde está: lo de después es para la próxima."""
        await self.ejecuta("decir: uno, al_movil, decir: dos")
        self.assertEqual(self.mandado[0][0], "uno")

    async def test_no_lo_repite_en_voz_alta(self):
        salida = await self.ejecuta("decir: uno, al_movil")
        self.assertEqual(salida, "uno", "el paso no añade nada a lo que se dice")

    async def test_sin_nada_que_decir_no_manda_nada(self):
        await self.ejecuta("al_movil")
        self.assertEqual(self.mandado, [])

    async def test_sin_avisos_configurados_no_estorba(self):
        self.caja.avisos.ntfy.topico = ""
        salida = await self.ejecuta("decir: uno, al_movil")
        self.assertEqual(salida, "uno")
        self.assertEqual(self.mandado, [])


class TestBucleDeLaTuberia(unittest.IsolatedAsyncioTestCase):
    async def test_llama_a_mirar_la_presencia(self):
        toolbox = FalsaCaja()
        tarea = asyncio.create_task(Jarvis._presence_worker(
            SimpleNamespace(toolbox=toolbox)))
        self.addCleanup(tarea.cancel)
        for _ in range(20):
            await asyncio.sleep(0)
            if toolbox.veces:
                break
        self.assertEqual(toolbox.veces, 1)


class FalsaCaja:
    def __init__(self):
        self.veces = 0

    async def mira_la_presencia(self) -> int:
        self.veces += 1
        return 0


if __name__ == "__main__":
    unittest.main()
