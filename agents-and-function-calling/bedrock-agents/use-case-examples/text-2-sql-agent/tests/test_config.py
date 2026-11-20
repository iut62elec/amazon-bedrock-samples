import importlib.util
import pathlib
import subprocess
import sys
import types
import unittest
from unittest import mock

DEPENDENCIES_ROOT = pathlib.Path(__file__).resolve().parents[1] / "dependencies"
sys.path.insert(0, str(DEPENDENCIES_ROOT))

from security_config import require_aws_region


class RegionConfigurationTests(unittest.TestCase):
    def test_notebook_and_script_imports_from_clean_interpreters(self):
        # Do not inherit the test runner's sys.path: the notebook only adds the
        # sample root, whereas running dependencies/*.py adds that directory.
        program = """
import importlib, pathlib, sys, types
from unittest.mock import Mock
session = Mock(region_name='us-east-1')
session.client.return_value.get_caller_identity.return_value = {'Account': '123456789012'}
sys.modules['boto3'] = types.SimpleNamespace(session=types.SimpleNamespace(Session=lambda: session))
sys.path.insert(0, str(pathlib.Path.cwd()))
config = importlib.import_module(sys.argv[1])
assert config.region == 'us-east-1'
assert config.account_id == '123456789012'
"""
        for module, cwd in (("dependencies.config", DEPENDENCIES_ROOT.parent), ("config", DEPENDENCIES_ROOT)):
            with self.subTest(module=module):
                run = subprocess.run([sys.executable, "-I", "-B", "-c", program, module],
                                     cwd=cwd, capture_output=True, text=True)
                self.assertEqual(run.returncode, 0, run.stderr)

    def test_normalizes_configured_region(self):
        self.assertEqual(require_aws_region(" eu-west-1 "), "eu-west-1")

    def test_rejects_missing_region_before_resource_construction(self):
        for region in (None, "", "   "):
            with self.subTest(region=region), self.assertRaisesRegex(
                RuntimeError,
                "Configure an AWS Region",
            ):
                require_aws_region(region)

    def test_config_import_rejects_missing_region_before_client_creation(self):
        class RegionlessSession:
            region_name = None

            def client(self, service_name, region_name=None):
                raise AssertionError(
                    f"created {service_name} client before validating Region"
                )

        fake_boto3 = types.SimpleNamespace(
            session=types.SimpleNamespace(Session=RegionlessSession)
        )
        module_path = DEPENDENCIES_ROOT / "config.py"
        specification = importlib.util.spec_from_file_location(
            "regionless_sample_config",
            module_path,
        )
        module = importlib.util.module_from_spec(specification)

        with mock.patch.dict(sys.modules, {"boto3": fake_boto3}), self.assertRaisesRegex(
            RuntimeError,
            "Configure an AWS Region",
        ):
            specification.loader.exec_module(module)


if __name__ == "__main__":
    unittest.main()
