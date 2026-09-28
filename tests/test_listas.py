"""Listas de la compra: lo que pasa cuando se dictan en voz alta."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.bus import EventBus  # noqa: E402
from jarvis.config import Config  # noqa: E402
from jarvis.listas import Listas, normaliza  # noqa: E402
from jarvis.llm.tools import Toolbox  # noqa: E402


class TestNormalizacion(unittest.TestCase):
    def test_tildes_y_mayusculas_fuera(self):
        self.assertEqual(normaliza("  Plátanos  "), "platanos")
        self.assertEqual(normaliza("JAMÓN"), "jamon")

    def test_los_espacios_de_sobra_se_colapsan(self):
        self.assertEqual(normaliza("papel   de  cocina"), "papel de cocina")

    def test_vacio(self):
        self.assertEqual(normaliza(""), "")
        self.assertEqual(normaliza(None), "")


class CasoConFichero(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fichero = Path(self.tmp.name) / "listas.json"
        self.listas = Listas(self.fichero)

    def tearDown(self):
        self.tmp.cleanup()

    def textos(self, lista="la compra") -> list[str]:
        return [a["texto"] for a in self.listas.ver(lista).get("articulos", [])]


class TestAnadir(CasoConFichero):
    def test_varios_de_una_vez(self):
        r = self.listas.anade(["leche", "pan", "huevos"])
        self.assertEqual(r["nuevos"], ["leche", "pan", "huevos"])
        self.assertEqual(self.textos(), ["leche", "pan", "huevos"])

    def test_repetir_no_duplica(self):
        self.listas.anade(["leche"])
        r = self.listas.anade(["Leche"])
        self.assertEqual(r["nuevos"], [])
        self.assertEqual(r["repetidos"], ["leche"])
        self.assertEqual(self.textos(), ["leche"])

    def test_las_tildes_no_duplican(self):
        self.listas.anade(["plátanos"])
        self.listas.anade(["platanos"])
        self.assertEqual(len(self.textos()), 1)

    def test_pedir_algo_ya_tachado_lo_destacha(self):
        """Si lo vuelve a decir es que lo quiere otra vez, no que sobre."""
        self.listas.anade(["leche"])
        self.listas.tacha(["leche"])
        r = self.listas.anade(["leche"])
        self.assertEqual(r["destachados"], ["leche"])
        self.assertEqual(len(self.listas.pendientes(self.listas.ver())), 1)

    def test_el_texto_se_guarda_tal_cual_se_dijo(self):
        self.listas.anade(["dos litros de leche entera"])
        self.assertEqual(self.textos(), ["dos litros de leche entera"])

    def test_los_vacios_se_ignoran(self):
        r = self.listas.anade(["", "   ", "pan"])
        self.assertEqual(r["nuevos"], ["pan"])

    def test_listas_distintas_no_se_mezclan(self):
        self.listas.anade(["leche"])
        self.listas.anade(["tornillos"], lista="la ferretería")
        self.assertEqual(self.textos(), ["leche"])
        self.assertEqual(self.textos("la ferretería"), ["tornillos"])

    def test_el_nombre_de_la_lista_tambien_ignora_tildes(self):
        self.listas.anade(["tornillos"], lista="la ferretería")
        self.assertEqual(self.textos("la ferreteria"), ["tornillos"])

    def test_hay_un_tope(self):
        from jarvis.listas import MAX_ARTICULOS

        self.listas.anade([f"cosa {n}" for n in range(MAX_ARTICULOS + 20)])
        self.assertEqual(len(self.textos()), MAX_ARTICULOS)


class TestTachar(CasoConFichero):
    def setUp(self):
        super().setUp()
        self.listas.anade(["leche entera", "pan", "huevos"])

    def test_tachar_no_borra(self):
        r = self.listas.tacha(["pan"])
        self.assertEqual(r["hechos"], ["pan"])
        self.assertEqual(len(self.textos()), 3)
        self.assertEqual([a["texto"] for a in self.listas.tachados(self.listas.ver())],
                         ["pan"])

    def test_quitar_si_borra(self):
        self.listas.tacha(["pan"], quitar=True)
        self.assertEqual(self.textos(), ["leche entera", "huevos"])

    def test_basta_con_decir_parte(self):
        """«quita la leche» tiene que encontrar «leche entera»."""
        r = self.listas.tacha(["leche"])
        self.assertEqual(r["hechos"], ["leche entera"])

    def test_lo_exacto_gana_a_lo_parcial(self):
        self.listas.anade(["leche"])
        r = self.listas.tacha(["leche"])
        self.assertEqual(r["hechos"], ["leche"], "no debe coger «leche entera»")

    def test_entre_parciales_gana_el_mas_corto(self):
        self.listas.anade(["leche entera sin lactosa"])
        r = self.listas.tacha(["leche"])
        self.assertEqual(r["hechos"], ["leche entera"])

    def test_lo_que_no_esta_se_dice(self):
        r = self.listas.tacha(["merluza"])
        self.assertEqual(r["no_estan"], ["merluza"])
        self.assertEqual(r["hechos"], [])

    def test_una_lista_que_no_existe_no_se_inventa(self):
        r = self.listas.tacha(["algo"], lista="la del gimnasio")
        self.assertTrue(r["no_existe"])
        self.assertNotIn("la del gimnasio", self.listas.nombres())


class TestVaciar(CasoConFichero):
    def setUp(self):
        super().setUp()
        self.listas.anade(["leche", "pan", "huevos"])
        self.listas.tacha(["pan"])

    def test_vaciar_del_todo(self):
        r = self.listas.vacia()
        self.assertEqual(r["quitados"], 3)
        self.assertEqual(self.textos(), [])

    def test_solo_lo_tachado(self):
        r = self.listas.vacia(solo_tachados=True)
        self.assertEqual(r["quitados"], 1)
        self.assertEqual(self.textos(), ["leche", "huevos"])

    def test_una_lista_que_no_existe(self):
        r = self.listas.vacia(lista="inventada")
        self.assertTrue(r["no_existe"])


class TestFichero(CasoConFichero):
    def test_sobrevive_al_reinicio(self):
        self.listas.anade(["leche"])
        self.assertEqual(Listas(self.fichero).ver()["articulos"][0]["texto"], "leche")

    def test_un_json_roto_no_revienta(self):
        self.fichero.write_text("{esto no es json", encoding="utf-8")
        self.assertEqual(self.listas.ver()["articulos"], [])
        self.listas.anade(["pan"])
        self.assertEqual(self.textos(), ["pan"])

    def test_se_relee_en_cada_operacion(self):
        """Se puede editar el fichero a mano sin que la próxima frase lo pise."""
        self.listas.anade(["leche"])
        datos = json.loads(self.fichero.read_text(encoding="utf-8"))
        datos["la compra"]["articulos"].append(
            {"texto": "queso", "tachado": False, "fecha": "2026-01-01T00:00:00+00:00"})
        self.fichero.write_text(json.dumps(datos), encoding="utf-8")

        self.listas.anade(["pan"])
        self.assertEqual(self.textos(), ["leche", "queso", "pan"])

    def test_el_fichero_es_legible_a_ojo(self):
        self.listas.anade(["leche"])
        crudo = self.fichero.read_text(encoding="utf-8")
        self.assertIn("\n", crudo, "va con sangrado")
        self.assertIn("leche", crudo, "sin escapes unicode")


class TestHerramientas(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfg = Config({
            "tools": {"allow_system": True,
                      "notes_file": str(Path(self.tmp.name) / "n.json"),
                      "memory_file": str(Path(self.tmp.name) / "m.json"),
                      "lists_file": str(Path(self.tmp.name) / "l.json")},
            "llm": {"web_search": False},
        })
        self.toolbox = Toolbox(cfg, EventBus())

    def tearDown(self):
        self.tmp.cleanup()

    async def test_el_ciclo_de_una_compra(self):
        await self.toolbox.run("anadir_a_lista",
                               {"articulos": ["leche", "pan", "huevos"]})
        self.assertIn("3 por comprar", await self.toolbox.run("ver_lista", {}))

        await self.toolbox.run("tachar_de_lista", {"articulos": ["pan"]})
        vista = await self.toolbox.run("ver_lista", {})
        self.assertIn("2 por comprar", vista)
        self.assertIn("Tachados ya 1", vista)

    async def test_se_enumera_como_se_habla(self):
        salida = await self.toolbox.run("anadir_a_lista",
                                        {"articulos": ["leche", "pan", "huevos"]})
        self.assertIn("leche, pan y huevos", salida)

    async def test_una_cadena_suelta_tambien_vale(self):
        """El modelo a veces manda «leche, pan» en vez de dos elementos."""
        await self.toolbox.run("anadir_a_lista", {"articulos": "leche, pan"})
        self.assertIn("2 por comprar", await self.toolbox.run("ver_lista", {}))

    async def test_sin_articulos_pregunta(self):
        self.assertIn("¿Qué añado?",
                      await self.toolbox.run("anadir_a_lista", {"articulos": []}))
        self.assertIn("¿Qué tacho?",
                      await self.toolbox.run("tachar_de_lista", {"articulos": []}))

    async def test_una_lista_vacia_lo_dice(self):
        self.assertIn("está vacía", await self.toolbox.run("ver_lista", {}))

    async def test_y_sugiere_las_que_si_existen(self):
        await self.toolbox.run("anadir_a_lista",
                               {"articulos": ["tornillos"], "lista": "la ferretería"})
        salida = await self.toolbox.run("ver_lista", {})
        self.assertIn("la ferretería", salida)

    async def test_vaciar_pide_confirmacion(self):
        await self.toolbox.run("anadir_a_lista", {"articulos": ["leche", "pan"]})
        salida = await self.toolbox.run("vaciar_lista", {})
        self.assertIn("Sin vaciar todavía", salida)
        self.assertIn("leche, pan", salida)
        self.assertIn("2 por comprar", await self.toolbox.run("ver_lista", {}))

    async def test_confirmar_de_entrada_no_vacia(self):
        await self.toolbox.run("anadir_a_lista", {"articulos": ["leche"]})
        salida = await self.toolbox.run("vaciar_lista", {"confirmar": True})
        self.assertIn("Sin vaciar todavía", salida)
        self.assertIn("1 por comprar", await self.toolbox.run("ver_lista", {}))

    async def test_el_segundo_paso_si_vacia(self):
        await self.toolbox.run("anadir_a_lista", {"articulos": ["leche", "pan"]})
        await self.toolbox.run("vaciar_lista", {})
        salida = await self.toolbox.run("vaciar_lista", {"confirmar": True})
        self.assertIn("Vaciados 2", salida)
        self.assertIn("está vacía", await self.toolbox.run("ver_lista", {}))

    async def test_cambiar_el_alcance_obliga_a_confirmar_otra_vez(self):
        """Confirmar «solo lo tachado» no puede valer para vaciarlo todo."""
        await self.toolbox.run("anadir_a_lista", {"articulos": ["leche", "pan"]})
        await self.toolbox.run("tachar_de_lista", {"articulos": ["pan"]})
        await self.toolbox.run("vaciar_lista", {"solo_tachados": True})
        salida = await self.toolbox.run("vaciar_lista", {"confirmar": True})
        self.assertIn("Sin vaciar todavía", salida)
        vista = await self.toolbox.run("ver_lista", {})
        self.assertIn("1 por comprar", vista)
        self.assertIn("Tachados ya 1", vista, "no se ha borrado nada")

    async def test_vaciar_solo_lo_tachado(self):
        await self.toolbox.run("anadir_a_lista", {"articulos": ["leche", "pan"]})
        await self.toolbox.run("tachar_de_lista", {"articulos": ["pan"]})
        await self.toolbox.run("vaciar_lista", {"solo_tachados": True})
        salida = await self.toolbox.run("vaciar_lista", {"solo_tachados": True,
                                                         "confirmar": True})
        self.assertIn("Vaciados 1", salida)
        self.assertIn("1 por comprar", await self.toolbox.run("ver_lista", {}))

    async def test_vaciar_lo_que_ya_esta_vacio(self):
        self.assertIn("no hay nada", await self.toolbox.run("vaciar_lista", {}))

    async def test_la_lista_no_es_contenido_externo(self):
        """La ha dictado la persona: no hay que vallarla ni bloquear `abrir`."""
        await self.toolbox.run("anadir_a_lista", {"articulos": ["leche"]})
        salida = await self.toolbox.run("ver_lista", {})
        self.assertNotIn("DATOS EXTERNOS", salida)
        self.assertFalse(self.toolbox.external_content_seen)

    async def test_las_herramientas_se_ofrecen_sin_indice(self):
        """No dependen de las fuentes: son del equipo, como las notas."""
        nombres = [spec["name"] for spec in self.toolbox.definitions()]
        for herramienta in ("ver_lista", "anadir_a_lista", "tachar_de_lista",
                            "vaciar_lista"):
            self.assertIn(herramienta, nombres)


if __name__ == "__main__":
    unittest.main()
