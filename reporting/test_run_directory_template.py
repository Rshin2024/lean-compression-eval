from pathlib import Path
import unittest
from build_comparison import manifest_path


class RunDirectoryTests(unittest.TestCase):
    def test_existing_defaults(self):
        self.assertEqual(manifest_path('/runs','m',100),Path('/runs/om100-m-high/manifest.json'))
        self.assertEqual(manifest_path('/runs','m',10),Path('/runs/smoke-m-high/manifest.json'))
    def test_new_run_identity(self):
        self.assertEqual(manifest_path('/runs','gpt-6-sol',100,'om100-max-{model}-high-20261014'),
            Path('/runs/om100-max-gpt-6-sol-high-20261014/manifest.json'))
    def test_only_direct_children(self):
        for template in ('../{model}','/{model}','x\\{model}','constant','{model}/{model}','{model}-{unknown}'):
            with self.subTest(template=template),self.assertRaises(ValueError):
                manifest_path('/runs','m',100,template)


if __name__=='__main__':unittest.main()
