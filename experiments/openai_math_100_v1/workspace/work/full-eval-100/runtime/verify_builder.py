"""Read-only integrity check for the private builder image."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess

root = Path('/opt/eval/package')
manifest = json.loads(Path('/opt/eval/closure-manifest.json').read_text())
assert manifest['oai_module_count'] == 457
missing, changed = [], []
for module, evidence in manifest['files'].items():
    relative = Path(evidence['source_path']).relative_to('lean')
    source = root / relative
    if sha256(source.read_bytes()).hexdigest() != evidence['sha256']:
        changed.append(module)
    compiled = root / '.lake/build/lib/lean' / relative.with_suffix('.olean')
    if not compiled.is_file(): missing.append(module)
assert not missing, missing
assert not changed, changed
assert len(list((root/'OAI').rglob('*.lean'))) == 457
assert len(list((root/'.lake/build/lib/lean/OAI').rglob('*.olean'))) == 457
actual_mathlib = subprocess.check_output(['git','-C',str(root/'.lake/packages/mathlib'),'rev-parse','HEAD'],text=True).strip()
assert actual_mathlib == manifest['mathlib_commit']
binary = Path('/opt/eval/lean-binary').read_text().strip()
version = subprocess.check_output([binary,'--version'],text=True).strip()
assert 'version 4.34.1' in version
library = Path(binary).parent.parent/'lib/lean/libleanshared.so'
assert library.is_file() and library.open('rb').read(4) == b'\x7fELF'
print(json.dumps({'status':'verified_private_builder','lean_version':version,'mathlib_commit':actual_mathlib,
                  'oai_sources_verified':457,'oai_compiled_modules':457,'native_elf_lean_library':str(library),
                  'evaluation_runtime_ready':False,'reason':'sourceful builder; final Python and source stripping pending'},indent=2))
