"""Verify the final runtime's complete compiled evidence and source exclusion."""
from hashlib import sha256
import json
from pathlib import Path
import sys

sys.path.insert(0, '/opt/eval/source')
from lean_compression.io import digest, evaluator_hash, file_hash

package = Path('/opt/eval/package')
env = json.loads(Path('/opt/eval/build-environment.json').read_text())
assert env['environment_hash'] == digest({k:v for k,v in env.items() if k != 'environment_hash'})
assert env['lean_toolchain'] == 'leanprover/lean4:v4.34.1'
assert env['mathlib_commit'] == 'd13f23b723b8a846827a245b89c10fc7d3f11612'
roots = {item['name']:Path(item['library_path']) for item in env['packages']}
roots['toolchain'] = Path(env['toolchain_root'])/'lib/lean'
assert '__root__' in roots
assert str(roots['__root__']) in env['lean_search_paths']
assert file_hash(env['lean_binary']) == env['lean_binary_hash']
for evidence in env['compiled_files']:
    prefix, relative = evidence['path'].split('/',1)
    path = roots[prefix]/relative
    assert path.is_file(), str(path)
    assert file_hash(path) == evidence['sha256'], str(path)
oai_compiled = [x for x in env['compiled_files'] if x['path'].startswith('__root__/OAI/') and x['path'].endswith('.olean')]
oai_sources = [x for x in env['source_files'] if x['path'].startswith('__root__/OAI/')]
assert len(oai_compiled) == 457
assert len(oai_sources) == 457
assert not list(package.rglob('*.lean'))
assert not [x for x in package.rglob('.git') if x.is_dir()]
expected_python = Path('/opt/eval/evaluator-python-hash').read_text().strip()
assert evaluator_hash() == expected_python
print(json.dumps({'status':'verified_source_stripped_runtime','compiled_files_verified':len(env['compiled_files']),
                  'oai_compiled_modules_verified':len(oai_compiled),'oai_source_hashes_recorded':len(oai_sources),
                  'evaluator_python_hash':expected_python,'compiled_environment_hash':env['compiled_environment_hash'],
                  'source_environment_hash':env['source_environment_hash'],
                  'source_exclusion_audit':{'lean_sources':0,'git_directories':0}},indent=2))
