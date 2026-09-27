"""
材料库持久化测试。

回归点：材料库原本是内存列表，自定义材料**重启即丢**。现在内置材料仍来自代码，
自定义材料写进 SQLite；这里用**临时数据库**验证，不污染开发数据。
"""

import asyncio
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

import materials as materials_module
from material_store import MaterialStore


def _payload(material_id="custom_test_steel", **overrides):
    data = {
        "id": material_id,
        "name": "测试合金",
        "density": 7800.0,
        "youngsModulus": 2.05e11,
        "poissonsRatio": 0.29,
        "color": "#123456",
        "type": "metal",
        "description": "用于测试的自定义材料",
    }
    data.update(overrides)
    return data


class MaterialStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test-materials.db"
        self.store = MaterialStore(self.db_path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_new_store_is_empty(self):
        self.assertEqual(self.store.list_custom(), [])
        self.assertEqual(self.store.count(), 0)

    def test_add_then_read_back(self):
        saved = self.store.add(_payload())
        self.assertEqual(saved["id"], "custom_test_steel")

        fetched = self.store.get_custom("custom_test_steel")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["name"], "测试合金")
        self.assertAlmostEqual(fetched["youngsModulus"], 2.05e11)
        self.assertEqual(fetched["type"], "metal")

    def test_persists_across_store_instances(self):
        """核心断言：换一个 MaterialStore 实例（模拟进程重启）数据仍在。"""
        self.store.add(_payload())

        reopened = MaterialStore(self.db_path)
        self.assertEqual(reopened.count(), 1)
        self.assertEqual(reopened.get_custom("custom_test_steel")["name"], "测试合金")

    def test_duplicate_id_is_rejected(self):
        from constraints import BoundaryCondition  # noqa: F401  (仅确保模块可导入)

        self.store.add(_payload())
        with self.assertRaises(ValueError):
            self.store.add(_payload(name="另一个名字"))

    def test_accepts_dict_and_model(self):
        from materials import Material

        self.store.add(Material(**_payload("custom_model_1")))
        self.store.add(_payload("custom_dict_1"))
        self.assertEqual(self.store.count(), 2)

    def test_missing_required_field_is_rejected(self):
        broken = _payload()
        broken.pop("youngsModulus")
        with self.assertRaises(ValueError):
            self.store.add(broken)

    def test_delete_removes_only_target(self):
        self.store.add(_payload("custom_a"))
        self.store.add(_payload("custom_b"))
        self.assertTrue(self.store.delete("custom_a"))
        self.assertIsNone(self.store.get_custom("custom_a"))
        self.assertIsNotNone(self.store.get_custom("custom_b"))
        self.assertFalse(self.store.delete("custom_a"))

    def test_survives_unicode_and_null_description(self):
        self.store.add(_payload("custom_中文", name="铝合金 6061", description=None))
        fetched = self.store.get_custom("custom_中文")
        self.assertEqual(fetched["name"], "铝合金 6061")
        self.assertIsNone(fetched["description"])


class MaterialsApiTest(unittest.TestCase):
    """
    端点层：注入一个临时库，验证"内置 + 自定义"的合并语义。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = MaterialStore(Path(self._tmp.name) / "api-materials.db")
        self._original = materials_module.get_store
        materials_module.get_store = lambda: self.store

    def tearDown(self):
        materials_module.get_store = self._original
        self._tmp.cleanup()

    def _create(self, **overrides):
        from materials import Material, create_material

        return asyncio.run(create_material(Material(**_payload(**overrides))))

    def _list(self):
        from materials import get_materials

        return asyncio.run(get_materials())

    def test_list_includes_builtin_and_custom(self):
        from materials import MATERIALS_DB

        self._create()
        listing = self._list()
        self.assertEqual(len(listing), len(MATERIALS_DB) + 1)
        self.assertIn("custom_test_steel", [m.id for m in listing])

    def test_custom_material_is_retrievable_by_id(self):
        from materials import get_material

        self._create()
        fetched = asyncio.run(get_material("custom_test_steel"))
        self.assertEqual(fetched.name, "测试合金")

    def test_builtin_material_still_available(self):
        from materials import get_material

        material = asyncio.run(get_material("structural_steel"))
        self.assertEqual(material.id, "structural_steel")

    def test_unknown_id_returns_404(self):
        from materials import get_material

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(get_material("definitely-not-here"))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_cannot_shadow_builtin_id(self):
        with self.assertRaises(HTTPException) as ctx:
            self._create(id="structural_steel", name="假的钢")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_duplicate_custom_id_returns_400(self):
        self._create()
        with self.assertRaises(HTTPException) as ctx:
            self._create(name="重名")
        self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
