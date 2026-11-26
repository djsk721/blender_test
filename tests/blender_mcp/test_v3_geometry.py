import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from unittest.mock import Mock
from zipfile import ZipFile

import pytest
from PIL import Image

from blender_orchestration.mcp_adapter import MCPAdapterError, PolicyViolationError
from blender_orchestration.reference_manifest import build_manifest_with_primary
from blender_orchestration.v3.blender_checks import compare_character_export
from blender_orchestration.v3.geometry import resolve_geometry
from blender_orchestration.v3.checkpoints import CheckpointManager
from blender_orchestration.v3.executor import BlenderExecutor
from blender_orchestration.v3.pipeline import Production
from blender_orchestration.v3.provenance import ProvenanceLedger
from blender_orchestration.sequential_benchmark import BlenderSessionResetter
from blender_orchestration.v3.reviewer import build_review_prompt, parse_review
from blender_orchestration.v3.script_validator import validate_blender_script
from blender_orchestration.v3.status import StageStatus
from tests.blender_mcp.test_harness_v3 import roles

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def bundle(tmp_path):
    def sha(p):
        return hashlib.sha256(p.read_bytes()).hexdigest()
    blend = tmp_path / 'body.blend'
    blend.write_bytes(b'Blender library fixture')
    archive = tmp_path / 'body.zip'
    with ZipFile(archive, 'w') as z:
        z.write(blend, blend.name)
    data = json.loads((ROOT / 'assets/approved_base_meshes/blender_human_base_meshes/provenance.json').read_text())
    for key, path in [('archive_zip', archive), ('blend_library', blend)]:
        data['files'][key] = {'rel_path': path.name, 'sha256': sha(path), 'bytes': path.stat().st_size}
    manifest = tmp_path / 'provenance.json'
    manifest.write_text(json.dumps(data))
    return manifest, data


def test_strategy_cli_and_missing_inputs(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/run_harness_v3.py'), '--help'],
                            capture_output=True, text=True, check=True)
    for option in ('{FROM_SCRATCH,BASE_MESH}', '--base-mesh-manifest', '--base-mesh-name'):
        assert option in result.stdout
    assert resolve_geometry('FROM_SCRATCH') == {'geometry_strategy': 'FROM_SCRATCH'}
    with pytest.raises(ValueError, match='requires --base-mesh-manifest'):
        resolve_geometry('BASE_MESH')
    with pytest.raises(ValueError, match='requires --base-mesh-name'):
        resolve_geometry('BASE_MESH', tmp_path / 'absent.json')
    with pytest.raises(PolicyViolationError, match='manifest missing'):
        resolve_geometry('BASE_MESH', tmp_path / 'absent.json', 'Body')


@pytest.mark.parametrize('field,expected', [('archive_zip', 'archive SHA-256 mismatch'),
    ('blend_library', 'Base mesh SHA-256 mismatch'), ('license', 'license'), ('source_url', 'distribution provenance')])
def test_base_mesh_rejects_bad_provenance(bundle, field, expected):
    manifest, data = bundle
    if field in data['files']:
        data['files'][field]['sha256'] = '0' * 64
    else:
        data[field] = 'invalid'
    manifest.write_text(json.dumps(data))
    with pytest.raises(PolicyViolationError, match=expected):
        resolve_geometry('BASE_MESH', manifest, 'Body')


def test_rejects_library_not_in_verified_archive(bundle):
    manifest, data = bundle
    blend = manifest.parent / data['files']['blend_library']['rel_path']
    blend.write_bytes(b'different library approved by mistake')
    data['files']['blend_library']['sha256'] = hashlib.sha256(blend.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(data))
    with pytest.raises(PolicyViolationError, match='does not match archive member'):
        resolve_geometry('BASE_MESH', manifest, 'Body')


def test_source_bound_loader_is_the_only_supported_route():
    assert validate_blender_script('loaded = load_approved_body()').ok
    for code in ('bmo.verify_and_load_base_mesh(scene, path, name)', 'bpy.data.libraries.load(path)'):
        assert not validate_blender_script(code).ok


def test_reference_analysis_retry_preserves_full_attachment_set(tmp_path):
    refs = tmp_path / 'refs'
    refs.mkdir()
    for name in ('face.png', 'turn.png'):
        Image.new('RGB', (12, 12)).save(refs / name)
    p = Production(run=tmp_path / 'run', root=ROOT, goal='goal', mode='static', roles=roles(),
                   manifest=build_manifest_with_primary(refs, 'face.png'), invoke=Mock(), budget=3, timeout=30, samples=4)
    plan = dict.fromkeys(('target_proportions', 'structural_geometry', 'face_landmarks', 'hair_masses', 'costume_layers',
                          'accessories', 'materials', 'rigging_requirements', 'animation_requirements', 'assumptions'), [])
    names = [f.name for f in p.files]
    replies = [{'per_reference': [{'file': names[0]}, {'file': names[0]}]},
               {'per_reference': [{'file': name} for name in names]}, plan]
    p.invoke.side_effect = [{'model_id': roles().producer, 'returncode': 0, 'session_id': f'qwen-{i}',
                             'text': json.dumps(reply)} for i, reply in enumerate(replies)]
    p.ledger.start('plan')
    assert p.plan() == plan
    assert all(call.kwargs['files'] == p.files for call in p.invoke.call_args_list)
    assert (p.run / 'handoffs/reference_analysis/response.json').exists()
    assert (p.run / 'handoffs/reference_analysis_retry/response.json').exists()


def test_technical_retries_preserve_qwen_source_and_failures(tmp_path):
    ref = tmp_path / 'ref.png'
    Image.new('RGB', (12, 12)).save(ref)
    invoke = Mock()
    scripts = ('return', 'bad_call()', 'next_bad_call()', 'pass')
    invoke.side_effect = [{'model_id': roles().producer, 'returncode': 0, 'session_id': f'qwen-{i}',
                           'text': f'```python\n{code}\n```'} for i, code in enumerate(scripts)]
    p = Production(run=tmp_path / 'run', root=ROOT, goal='goal', mode='static', roles=roles(),
                   manifest=build_manifest_with_primary(tmp_path, ref.name), invoke=invoke, budget=0, timeout=30, samples=4)
    ex = Mock()
    ex.execute_producer_script.side_effect = [
        {'ok': False, 'error': 'SyntaxError: return outside function', 'scene_unchanged': True},
        {'ok': False, 'error': 'Invalid keyword color'},
        {'ok': False, 'error': 'Bad vertex group membership'},
        {'ok': True, 'audit': {'mesh_count': 1}},
    ]
    p.ledger.start('plan')
    p.ledger.record_execution('plan', 'plan.json')
    p.ledger.finish('plan', StageStatus.PASS)
    p.ledger.start('silhouette')
    parent = p.run / 'candidate'
    assert p.edit(ex, 'silhouette', 'work', p.files, parent, None) == 'pass'
    assert [c.args[0] for c in ex.execute_producer_script.call_args_list] == list(scripts)
    assert ex.init_scene.call_count == 2
    assert 'SyntaxError: return outside function' in invoke.call_args.args[1]
    assert 'Invalid keyword color' in invoke.call_args.args[1]
    assert 'next_bad_call()' in invoke.call_args.args[1]
    assert all((parent / f'producer_{i}' / 'mcp.json').is_file() for i in range(1, 5))


def test_checkpoint_and_execution_identity(tmp_path, bundle):
    ref = tmp_path / 'ref.png'
    Image.new('RGB', (12, 12)).save(ref)
    options = dict(run=tmp_path / 'run', root=tmp_path, goal='goal', mode='animated', roles=roles(),
                   manifest=build_manifest_with_primary(tmp_path, ref.name), invoke=Mock(), budget=3, timeout=30, samples=4)
    base = dict(geometry_strategy='BASE_MESH', base_mesh_manifest=bundle[0], base_mesh_name='Exact Body')
    p = Production(**options, **base)
    assert p.identity['base_mesh']['object_name'] == 'Exact Body'
    p.plan_path.parent.mkdir()
    p.plan_path.write_text('{}')
    file = p.manager.new_path('silhouette', 'candidate')
    file.write_bytes(b'BLENDER' + b'x' * 2048)
    p.approved = p.manager.register(file, stage='silhouette', label='candidate', extra=p.geometry).checkpoint_id
    p.persist()
    rec = p.provenance.register_producer_output(model_id=roles().producer, session_id='qwen', work_order_id='silhouette',
                                               prompt='prompt', response_text='pass', code='pass')
    p.provenance.authorize_execution('pass', rec)
    for line in p.provenance.log_path.read_text().splitlines():
        assert json.loads(line)['geometry'] == p.geometry
    assert Production(**options, **base, resume=file).approved == p.approved
    with pytest.raises(ValueError, match='Resume identity mismatch'):
        Production(**options, resume=file)
    with pytest.raises(ValueError, match='Resume identity mismatch'):
        Production(**options, **(base | {'base_mesh_name': 'Another Body'}), resume=file)
    p.manager.entries[p.approved].extra = {}
    p.manager._save()
    with pytest.raises(ValueError, match='checkpoint geometry identity mismatch'):
        Production(**options, **base, resume=file)


def test_basemesh_review_is_not_a_different_gate():
    opts = dict(phase='silhouette', goal_excerpt='target', plan_excerpt='', render_names=['sheet.png'],
                reference_lines=['reference.png'], prior_summary=None)
    assert 'sufficiently transformed' in build_review_prompt(**opts, base_mesh=True)
    assert 'sufficiently transformed' not in build_review_prompt(**opts)
    review = json.dumps({'stage_score_100': 74, 'passed': True, 'observations_by_view': {'front': ['tubular arms']}})
    assert not parse_review(review, phase='silhouette', render_views=['front'])['passed']


def test_reimport_requires_character_data_in_animated_mode():
    model = dict(mesh_count=4, height_m=1.7, material_count=3, texture_count=2, unmaterialed_meshes=[],
                 missing_images=[], rig_count=1, skins=4, actions=['ACT_Idle.001', 'ACT_Walk.001', 'ACT_Run.001'])
    assert compare_character_export(model, model, 'animated')['valid']
    for field, value, check in [('mesh_count', 2, 'mesh'), ('height_m', .017, 'scale'),
                                ('material_count', 0, 'materials'), ('texture_count', 0, 'textures'),
                                ('skins', 0, 'rig'), ('actions', [], 'animations')]:
        result = compare_character_export(model, model | {field: value}, 'animated')
        assert not result['valid'] and not result['checks'][check]


@pytest.mark.skipif(os.getenv('V3_LIVE_MCP') != '1', reason='opt-in isolated Blender MCP test')
def test_live_exact_mesh_selection(tmp_path, monkeypatch):
    manifest = ROOT / 'assets/approved_base_meshes/blender_human_base_meshes/provenance.json'
    geometry = resolve_geometry('BASE_MESH', manifest, 'GEO-body_female_stylized')
    source = geometry['base_mesh']
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    runtime = tmp_path / 'runtime'
    for key, value in {'BLENDER_RENDER_DEVICE': 'CPU', 'BLENDER_CPU_SOFTWARE_VIEWPORT': 'true',
                       'BLENDER_MCP_MANAGE_EXTERNAL': 'false'}.items():
        monkeypatch.setenv(key, value)
    ex = BlenderExecutor(project_root=ROOT, scene_name='SCENE_Selection', run_dir=tmp_path,
        mcp_env={'BLENDER_HOST': '127.0.0.1', 'BLENDER_PORT': str(port),
                 'BLENDER_MCP_RUNTIME_DIR': str(runtime), 'BLENDER_MCP_AUTO_RESTORE': 'false'},
        ledger=ProvenanceLedger(roles(), geometry=geometry), checkpoints=CheckpointManager(tmp_path), rpc_timeout_sec=120)
    try:
        result = ex.inspect_base_mesh(source)
        assert (result['name'], result['vertices'], result['polygons']) == (source['object_name'], 12502, 12500)
        with pytest.raises(MCPAdapterError, match='exact name required'):
            ex.inspect_base_mesh(source | {'object_name': 'GEO-body_female'})
        with pytest.raises(MCPAdapterError, match='not a nonempty polygon mesh'):
            ex.inspect_base_mesh(source | {'object_name': 'stylized_body_female'})
        assert ex.init_scene()['audit']['mesh_count'] == 0
    finally:
        ex.close()
        BlenderSessionResetter(project_root=ROOT, runtime_dir=runtime, blender_port=port).reset_for_next_model(
            start_fresh_blender=False, preserve_backups=True)
