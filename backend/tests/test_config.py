"""
配置与输入校验的单元测试。

重点覆盖 ``resolve_upload_path`` 的安全边界（路径穿越）——这是协作项目里
必须锁死的行为，任何改动都不应该让它失效。
"""

import unittest
from pathlib import Path

import config


class ResolveUploadPathTests(unittest.TestCase):
    def test_accepts_plain_filename(self):
        path = config.resolve_upload_path("part.step")
        self.assertEqual(path.name, "part.step")
        self.assertEqual(path.parent, config.UPLOAD_DIR.resolve())

    def test_accepts_all_whitelisted_extensions(self):
        for name in ("a.stl", "b.step", "c.stp", "d.iges", "e.igs"):
            with self.subTest(name=name):
                self.assertEqual(config.resolve_upload_path(name).name, name)

    def test_extension_check_is_case_insensitive(self):
        # 实际项目里有 `零件1.STEP` 这种大写扩展名
        self.assertEqual(config.resolve_upload_path("零件1.STEP").name, "零件1.STEP")

    def test_accepts_non_ascii_filename(self):
        # 注意：resolve() 会把「已存在文件」的名字规范化为磁盘上的真实大小写
        # （Windows 文件系统大小写不敏感，仓库里有 `零件1.STEP`），
        # 所以这里断言目录与扩展名，而不是精确的文件名字符串。
        path = config.resolve_upload_path("零件1.step")
        self.assertEqual(path.parent, config.UPLOAD_DIR.resolve())
        self.assertEqual(path.suffix.lower(), ".step")

    def test_case_is_canonicalised_for_existing_files(self):
        """已存在文件会被规范化成磁盘上的真实大小写（行为记录）。"""
        existing = config.UPLOAD_DIR / "零件1.STEP"
        if not existing.exists():
            self.skipTest("测试夹具 零件1.STEP 不存在")
        self.assertEqual(config.resolve_upload_path("零件1.step").name, "零件1.STEP")

    def test_rejects_path_traversal(self):
        bad_names = [
            "../secret.step",
            "..\\secret.step",
            "sub/dir.step",
            "sub\\dir.step",
            "/etc/passwd.step",
            "C:\\windows\\evil.step",
            "..",
            ".",
            "a/../b.step",
        ]
        for name in bad_names:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    config.resolve_upload_path(name)

    def test_rejects_empty_name(self):
        for name in ("", "   ", None):
            with self.subTest(name=repr(name)):
                with self.assertRaises(ValueError):
                    config.resolve_upload_path(name)

    def test_rejects_unsupported_extension(self):
        for name in ("evil.exe", "archive.zip", "noextension", "mesh.msh"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    config.resolve_upload_path(name)

    def test_never_escapes_upload_dir(self):
        """性质测试：任何被接受的文件名都必须落在上传目录内。"""
        for name in ("ok.stl", "零件1.STEP", "a.b.step"):
            with self.subTest(name=name):
                resolved = config.resolve_upload_path(name).resolve()
                self.assertTrue(
                    str(resolved).startswith(str(config.UPLOAD_DIR.resolve())),
                    f"{name} 解析到了上传目录之外：{resolved}",
                )


class ValidateMeshSizeTests(unittest.TestCase):
    def test_passes_through_valid_value(self):
        self.assertEqual(config.validate_mesh_size(1.5), 1.5)
        self.assertEqual(config.validate_mesh_size("2"), 2.0)

    def test_rejects_out_of_range(self):
        for value in (0, -1, config.MESH_SIZE_MAX * 10):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    config.validate_mesh_size(value)

    def test_rejects_non_numeric(self):
        for value in ("abc", None, [1]):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    config.validate_mesh_size(value)


class ConfigSanityTests(unittest.TestCase):
    def test_upload_dir_is_absolute(self):
        """回归：UPLOAD_DIR 曾是相对路径，导致必须 cd backend 才能启动。"""
        self.assertTrue(config.UPLOAD_DIR.is_absolute())

    def test_upload_dir_is_under_backend(self):
        self.assertEqual(config.UPLOAD_DIR.parent, config.BASE_DIR)

    def test_limits_are_sane(self):
        self.assertGreater(config.MAX_UPLOAD_BYTES, 0)
        self.assertLess(config.MESH_SIZE_MIN, config.MESH_SIZE_MAX)
        self.assertTrue(config.CORS_ALLOW_ORIGINS)


if __name__ == "__main__":
    unittest.main()
