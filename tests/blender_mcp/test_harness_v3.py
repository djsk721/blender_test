import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image

from blender_orchestration.reference_manifest import build_manifest_with_primary, ReferenceManifestError
from blender_orchestration.v3.checkpoints import CheckpointManager, validate_state_against_registry
from blender_orchestration.v3.correction import CorrectionController
from blender_orchestration.v3.executor import BlenderExecutor
from blender_orchestration.v3.pipeline import Production
from blender_orchestration.v3.provenance import (DEFAULT_REVIEWER_MODEL_ID, REQUIRED_PRODUCER_MODEL_ID,
    ProvenanceLedger, RoleViolationError, validate_model_roles)
from blender_orchestration.v3.reviewer import IndependentReviewer, parse_review
from blender_orchestration.v3.status import StageLedger, StageLedgerError, StageStatus


def roles():
    return validate_model_roles(REQUIRED_PRODUCER_MODEL_ID, DEFAULT_REVIEWER_MODEL_ID)


def test_only_qwen_source_dispatched(tmp_path):
    with pytest.raises(RoleViolationError):
        validate_model_roles(DEFAULT_REVIEWER_MODEL_ID, DEFAULT_REVIEWER_MODEL_ID)
    assert roles().reviewer == 'openai/gpt-6.2-sol'
    for name in (REQUIRED_PRODUCER_MODEL_ID, 'another/qwen-test', 'strata/alias'):
        with pytest.raises(RoleViolationError):
            validate_model_roles(REQUIRED_PRODUCER_MODEL_ID, name)
    ledger = ProvenanceLedger(roles())
    adapter = Mock()
    ex = BlenderExecutor(project_root=tmp_path, scene_name='Test', run_dir=tmp_path, mcp_env={}, ledger=ledger,
                         checkpoints=CheckpointManager(tmp_path), adapter=adapter)
    def register(code, model=roles().producer):
        return ledger.register_producer_output(model_id=model, session_id='qwen-1', work_order_id='unit',
                                               prompt='work', response_text=code, code=code)
    with pytest.raises(RoleViolationError):
        register('pass', roles().reviewer)
    rec = register('pass')
    with pytest.raises(RoleViolationError):
        ex.execute_producer_script('pass', replace(rec, session_id='wrong'), phase='test', rollback_checkpoint_id=None)
    with pytest.raises(RoleViolationError):
        ex.execute_producer_script('print(1)', rec, phase='test', rollback_checkpoint_id=None)
    for source in ('x = (', 'return', 'bpy.ops.wm.open_mainfile()', 'mesh.use_auto_smooth = True'):
        assert not ex.execute_producer_script(source, register(source), phase='test', rollback_checkpoint_id=None)['ok']
    adapter.execute_blender_json.assert_not_called()
    adapter.execute_blender_json.return_value = {'parsed': {'ok': True}}
    assert ex.execute_producer_script('pass', rec, phase='test', rollback_checkpoint_id=None)['ok']
    assert ex.calls[-1]['record_id'] == rec.record_id


def test_status_and_dependencies():
    ledger = StageLedger(['a', 'b', 'c'], {'b': ['a'], 'c': ['b']})
    with pytest.raises(StageLedgerError):
        ledger.finish('a', StageStatus.PASS)
    ledger.start('a')
    ledger.finish('a', StageStatus.NOT_VERIFIED)
    with pytest.raises(StageLedgerError):
        ledger.start('b')
    ledger.start('a')
    ledger.record_execution('a', 'verified')
    ledger.finish('a', StageStatus.PASS)
    ledger.finish('b', StageStatus.NOT_APPLICABLE, reason='static')
    ledger.start('c')
    assert not ledger.validate_consistency()


def test_budget_above_two():
    controller = CorrectionController('silhouette', max_attempts=4)
    for i in range(5):
        score = 40 + i * 3
        defects = [{'region': 'proportions', 'category': str(i), 'severity': 'MAJOR'}]
        controller.observe_review(score=score, passed=False, defects=defects, accepted=True)
        assert controller.decide(passed=False, defects=defects).action == ('CORRECT' if i < 4 else 'EXHAUSTED')


def test_checkpoint_preservation_and_interrupted_state(tmp_path):
    manager = CheckpointManager(tmp_path)
    def candidate(label, score):
        p = manager.new_path('face', label)
        p.write_bytes(b'BLENDER' + label.encode() * 2000)
        e = manager.register(p, stage='face', label=label)
        manager.attach_review(e.checkpoint_id, score=score, critical_defects=1, review_path=None)
        return e, manager.consider_promotion('face', e.checkpoint_id)
    best, _ = candidate('old', 70)
    newer, promotion = candidate('new', 60)
    assert not promotion['promoted'] and manager.best_technical_id == newer.checkpoint_id
    state = {'resume_checkpoint_id': best.checkpoint_id, 'checkpoint_hashes': {best.checkpoint_id: best.sha256}}
    restored = CheckpointManager(tmp_path)
    assert not validate_state_against_registry(state, restored)
    assert restored.best_reviewed_entry('face') == best
    Path(best.path).write_bytes(b'changed')
    assert validate_state_against_registry(state, restored)


def test_full_reference_delivery_and_reviewer_isolation(tmp_path):
    refs = tmp_path / 'sources'
    refs.mkdir()
    for name in ['face.png', 'turn.png', 'official.png']:
        Image.new('RGB', (12, 12)).save(refs / name)
    m = build_manifest_with_primary(refs, 'official.png')
    assert len(m.entries) == 3 and m.primary.path.name == 'official.png'
    payload = json.dumps({'stage_score_100': 91, 'passed': True, 'observations_by_view': {'front': ['grounded']}})
    invoke = Mock(return_value={'returncode': 0, 'session_id': 'q', 'text': payload, 'model_id': roles().producer})
    p = Production(run=tmp_path / 'run', root=tmp_path, goal='exact\n', manifest=m, roles=roles(), mode='static',
                   invoke=invoke, budget=3, timeout=10, samples=1)
    p.call('producer', 'plan', p.files, p.run / 'test_call')
    assert invoke.call_args.kwargs['files'] == p.files
    invoke.return_value['model_id'] = roles().reviewer
    result = IndependentReviewer(roles().reviewer, invoke).review(phase='silhouette',
        renders=[{'view': 'front', 'path': m.primary.path}], reference_copies=p.copies, goal_excerpt='goal',
        plan_excerpt='', out_dir=tmp_path / 'review', producer_session_ids={'q'})
    assert result['verdict'] == 'NOT_VERIFIED'
    assert invoke.call_args.kwargs['files'][1:] == p.files
    invoke.return_value.update(session_id='gpt', errors=['HTTP 402 insufficient_quota'])
    result = IndependentReviewer(roles().reviewer, invoke).review(phase='silhouette',
        renders=[{'view': 'front', 'path': m.primary.path}], reference_copies=p.copies, goal_excerpt='goal',
        plan_excerpt='', out_dir=tmp_path / 'review', producer_session_ids={'q'})
    assert result['verdict'] == 'NOT_VERIFIED' and '402' in result['reason']
    assert parse_review(payload + '\n```python\npass\n```', phase='silhouette', render_views=['front'])['verdict'] == 'NOT_VERIFIED'
    (refs / 'missing.jpg').write_text('not an image')
    with pytest.raises(ReferenceManifestError):
        build_manifest_with_primary(refs, 'official.png')
    assert parse_review('{"passed":true}', phase='silhouette', render_views=['front'])['verdict'] == 'NOT_VERIFIED'
    with pytest.raises(RuntimeError, match='prerequisites'):
        p.release(None, {})


def test_resume_pending_review_never_marks_stage_passed(tmp_path):
    ref = tmp_path / 'ref.png'
    Image.new('RGB', (12, 12)).save(ref)
    manifest = build_manifest_with_primary(tmp_path, ref.name)
    options = dict(run=tmp_path / 'run', root=tmp_path, goal='goal\n', manifest=manifest, roles=roles(), mode='animated',
                   invoke=Mock(), budget=3, timeout=10, samples=1)
    p = Production(**options)
    p.plan_path.parent.mkdir()
    p.plan_path.write_text('{}')
    p.ledger.start('plan')
    p.ledger.record_execution('plan', str(p.plan_path))
    p.ledger.finish('plan', StageStatus.PASS)
    p.ledger.start('silhouette')
    file = p.manager.new_path('silhouette', 'candidate')
    file.write_bytes(b'BLENDER' + b'x' * 2000)
    entry = p.manager.register(file, stage='silhouette', label='candidate')
    p.pending = {'stage': 'silhouette', 'checkpoint': entry.checkpoint_id, 'renders': []}
    p.persist()
    resumed = Production(**options, resume=file)
    assert resumed.pending == p.pending
    assert resumed.approved is None
    assert resumed.ledger.status('rigging') == StageStatus.NOT_STARTED
    file.unlink()
    with pytest.raises(ValueError, match='checkpoint'):
        Production(**options, resume=file)
