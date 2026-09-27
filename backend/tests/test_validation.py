"""
业务规则、材料库与密钥卫生的测试。

其中 ``SecretHygieneTest`` 是安全回归：项目历史上曾把真实 DeepSeek API Key
硬编码在 ``ai_assistant.py``，这里加一道闸门防止它再回来。
"""

import asyncio
import re
import unittest
from pathlib import Path

from fastapi import HTTPException

from constraints import BoundaryCondition, SimulationSetup, validate_setup
from materials import MATERIALS_DB, get_material, get_materials

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent


def _bc(bc_id, bc_type, index=1, **kwargs):
    return BoundaryCondition(
        id=bc_id,
        name=bc_id,
        type=bc_type,
        applicationType="face",
        entityIndex=index,
        **kwargs,
    )


class SetupValidationTest(unittest.TestCase):
    def test_accepts_fixed_support_plus_load(self):
        setup = SimulationSetup(
            material_id="structural_steel",
            mesh_size=1.0,
            boundary_conditions=[
                _bc("fixed", "fixed"),
                _bc("load", "force", 2, force={"x": 0, "y": 0, "z": -1000}),
            ],
        )
        result = asyncio.run(validate_setup(setup))
        self.assertTrue(result["valid"], result)

    def test_rejects_missing_fixed_support(self):
        setup = SimulationSetup(
            material_id="structural_steel",
            mesh_size=1.0,
            boundary_conditions=[_bc("load", "force", 2, force={"x": 0, "y": 0, "z": -1})],
        )
        result = asyncio.run(validate_setup(setup))
        self.assertFalse(result["valid"])
        self.assertTrue(any("Fixed" in e for e in result["errors"]))

    def test_rejects_missing_load(self):
        setup = SimulationSetup(
            material_id="structural_steel",
            mesh_size=1.0,
            boundary_conditions=[_bc("fixed", "fixed")],
        )
        result = asyncio.run(validate_setup(setup))
        self.assertFalse(result["valid"])
        self.assertTrue(any("Load" in e for e in result["errors"]))


class MaterialLibraryTest(unittest.TestCase):
    def test_library_is_not_empty(self):
        self.assertGreaterEqual(len(MATERIALS_DB), 5)

    def test_every_material_has_a_valid_type(self):
        """回归：后端曾缺 type 字段，导致前端分类筛选永久失效。"""
        allowed = {"metal", "plastic", "concrete", "wood", "custom"}
        for material in MATERIALS_DB:
            with self.subTest(material=material.id):
                self.assertIn(material.type, allowed)

    def test_physical_properties_are_physical(self):
        for material in MATERIALS_DB:
            with self.subTest(material=material.id):
                self.assertGreater(material.density, 0)
                self.assertGreater(material.youngsModulus, 0)
                self.assertTrue(0.0 <= material.poissonsRatio < 0.5)

    def test_list_endpoint_returns_library(self):
        self.assertEqual(len(asyncio.run(get_materials())), len(MATERIALS_DB))

    def test_unknown_material_returns_404(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(get_material("does-not-exist"))
        self.assertEqual(ctx.exception.status_code, 404)


class SecretHygieneTest(unittest.TestCase):
    def test_no_hardcoded_api_key_in_backend_sources(self):
        """安全回归：源码里不允许再出现硬编码的 sk- 密钥。"""
        key_pattern = re.compile(r"sk-[A-Za-z0-9]{16,}")
        offenders = []
        for path in BACKEND_DIR.rglob("*.py"):
            parts = set(path.parts)
            if {"venv", ".venv", ".venv2", "__pycache__"} & parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if key_pattern.search(text):
                offenders.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(offenders, [], f"源码中疑似硬编码 API Key：{offenders}")

    def test_env_file_is_gitignored(self):
        entries = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").split()
        self.assertIn(".env", entries, "backend/.env 必须被 gitignore 忽略")

    def test_env_example_exists_for_documentation(self):
        self.assertTrue((BACKEND_DIR / ".env.example").exists())


if __name__ == "__main__":
    unittest.main()
