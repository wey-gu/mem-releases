import importlib.util
from pathlib import Path
import unittest
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('wrapper', ROOT/'scripts/server-codegen-wrapper.py')
wrapper = importlib.util.module_from_spec(spec); spec.loader.exec_module(wrapper)
class ExperimentTest(unittest.TestCase):
    def test_only_server_overridden(self):
        for units in (1,4,16):
            self.assertEqual(wrapper.command('rustc', ['--crate-name','nmem_server'], 'nmem-server', units)[-2:],
                             ['-C', f'codegen-units={units}'])
        self.assertEqual(wrapper.command('rustc', ['--crate-name','serde'], 'serde', 16),
                         ['sccache','rustc','--crate-name','serde'])
    def test_rejects_other_units(self):
        with self.assertRaises(ValueError): wrapper.command('rustc', [], 'nmem-server', 8)
    def test_rss_is_descendant_tree_only(self):
        self.assertEqual(wrapper.descendants([(10,1,100),(11,10,200),(12,11,300),(99,1,900)],10),600)
    def test_experiment_does_not_publish(self):
        text = (ROOT/'.github/workflows/release-desktop.yml').read_text()
        self.assertIn('if: ${{ inputs.server_codegen_experiment != true }}', text)
        experiment = text.split('  server-codegen-experiment:',1)[1]
        for forbidden in ('upload-package','release upload','GPG_PRIVATE','APPLE_CERTIFICATE'):
            self.assertNotIn(forbidden, experiment)
        self.assertIn('save-if: false', experiment)
if __name__ == '__main__': unittest.main()
