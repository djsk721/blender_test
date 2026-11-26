"""Reference-guided character production. Model calls and scene dispatch are separate."""
from __future__ import annotations

import json
from pathlib import Path
from shutil import copyfile

from ..autonomous_harness import _format_compact_scene_inventory
from ..reference_manifest import (detect_reference_overlaps, load_reference_manifest,
                                  make_review_copies, snapshot_references, verify_image_integrity)
from ..rubric_evaluator import BlindRubricEvaluator, FROZEN_MIN_TOTAL_SCORE
from .checkpoints import CheckpointManager, sha256_file, validate_state_against_registry, write_json_atomic
from .correction import CorrectionController
from .provenance import ProvenanceLedger, sha256_text
from .reviewer import IndependentReviewer, _extract_json
from .script_validator import extract_python_block
from .status import StageLedger, StageStatus
from .work_orders import (build_correction_work_order, build_phase_work_order,
                          build_retry_work_order, plan_work_order, reference_analysis_prompt)

STAGES = ['plan', 'silhouette', 'face', 'hair', 'costume', 'modeling_gate', 'materials', 'rigging', 'animation', 'final']
PREREQUISITES = {stage: [previous] for previous, stage in zip(STAGES, STAGES[1:])}
SKILLS = {
    'plan': ['blender-director'], 'silhouette': ['character-artist', 'stylized-style'],
    'face': ['character-artist', 'sculpting'], 'hair': ['hair-groom'],
    'costume': ['character-artist', 'cloth-sim'], 'materials': ['materials', 'uv-workflow'],
    'rigging': ['rigging'], 'animation': ['animation'], 'modeling_gate': ['qa-review'],
    'final': ['rendering', 'export-pipeline', 'qa-review'],
}


class Blocked(RuntimeError):
    pass


class Production:
    def __init__(self, *, run, root, goal, manifest, roles, mode, invoke, budget,
                 timeout, samples, resume=None, asset='Character', port=None):
        self.run, self.root, self.goal, self.roles = run, root, goal, roles
        if not goal.strip():
            raise ValueError('Empty production goal')
        self.mode, self.invoke, self.budget = mode, invoke, budget
        self.timeout, self.samples, self.asset = timeout, samples, asset
        self.identity = {'goal_sha256': sha256_text(goal), 'models': roles.to_dict(),
                         'references': manifest.to_dict(), 'animation_mode': mode, 'asset': asset,
                         'geometry_strategy': 'FROM_SCRATCH', 'reference_mode': 'REFERENCE_GUIDED',
                         'correction_budget': budget, 'blender_port': port}
        if resume is None and run.exists() and any(run.iterdir()):
            raise ValueError('Fresh production requires an empty run directory')
        self.manager = CheckpointManager(run)
        self.provenance = ProvenanceLedger(roles, run / 'provenance.jsonl')
        self.state_path = run / 'production_state.json'
        self.refs_path = run / 'references' / 'reference_manifest.json'
        self.plan_path = run / 'handoffs' / 'plan.json'
        self.approved = None
        self.pending = None
        self.phase = None
        self.sessions = set()
        self.attempts = {}
        self.ledger = StageLedger(STAGES, PREREQUISITES)
        if resume is not None:
            state = json.loads(self.state_path.read_text())
            if state['identity'] != self.identity or (run / 'goal.txt').read_text() != goal:
                raise ValueError('Resume identity mismatch')
            if validate_state_against_registry(state, self.manager):
                raise ValueError('Resume checkpoint registry/hash mismatch')
            self.approved = state.get('approved_checkpoint_id', state['resume_checkpoint_id'])
            self.pending = state.get('pending_review')
            if Path(self.manager.verify(state['resume_checkpoint_id']).path) != resume.resolve():
                raise ValueError('Not the recorded resume checkpoint')
            self.ledger = StageLedger.from_dict(state['ledger'])
            if self.ledger.validate_consistency() or self.ledger.order != STAGES or self.ledger.prerequisites != PREREQUISITES:
                raise ValueError('Invalid stage ledger')
            if state['plan_sha256'] != sha256_file(self.plan_path):
                raise ValueError('Resume plan hash mismatch')
            snapshot = load_reference_manifest(self.refs_path)
            if [e.to_dict() | {'path': ''} for e in snapshot.entries] != [e.to_dict() | {'path': ''} for e in manifest.entries]:
                raise ValueError('Reference snapshot differs from source manifest')
            self.attempts = state['attempts']
            self.sessions = set(state.get('producer_sessions', []))
        else:
            (run / 'goal.txt').write_text(goal, encoding='utf-8')
            if len({e.path.name for e in manifest.entries}) != len(manifest.entries):
                raise ValueError('Reference filenames must be unique')
            _, snapshot = snapshot_references(manifest, self.refs_path.parent)
        for entry in snapshot.entries:
            verify_image_integrity(entry.path)
        copies = make_review_copies(snapshot, run / 'references' / 'bounded')
        self.copies = copies
        self.files = [Path(c['review_copy']) for c in copies]
        self.lines = [f"{Path(c['review_copy']).name}: {c['role']} — {c['purpose']}" for c in copies]
        write_json_atomic(run / 'references' / 'delivery.json', copies)
        self.overlaps = detect_reference_overlaps(snapshot.entries)
        self.reviewer = IndependentReviewer(roles.reviewer, invoke, timeout_sec=timeout)
        self.persist()

    def persist(self):
        pointers = self.manager.best_reviewed
        ids = set(pointers.values()) | ({self.approved} if self.approved else set())
        resume_id = self.pending['checkpoint'] if self.pending else self.approved
        if resume_id:
            ids.add(resume_id)
        write_json_atomic(self.state_path, {
            'schema': 'production_state.v3', 'identity': self.identity, 'ledger': self.ledger.to_dict(),
            'resume_checkpoint_id': resume_id, 'approved_checkpoint_id': self.approved,
            'pending_review': self.pending, 'best_reviewed': pointers,
            'checkpoint_hashes': {cid: self.manager.verify(cid).sha256 for cid in ids},
            'plan_sha256': sha256_file(self.plan_path) if self.plan_path.is_file() else None,
            'attempts': self.attempts, 'producer_sessions': sorted(self.sessions),
        })

    def skills(self, stage):
        chunks = []
        for skill in SKILLS.get(stage, []):
            path = self.root / '.opencode' / 'skills' / skill / 'SKILL.md'
            chunks.append(f'\n{skill} (local guidance excerpt):\n' + path.read_text()[:1700])
        return '\n'.join(chunks)

    def call(self, role, prompt, files, directory):
        directory.mkdir(parents=True, exist_ok=False)
        model = getattr(self.roles, role)
        (directory / 'prompt.txt').write_text(prompt, encoding='utf-8')
        print(f'{role}: {directory.relative_to(self.run)}', flush=True)
        r = self.invoke(model, prompt, files=files, timeout_sec=self.timeout, title='V3 ' + directory.name)
        write_json_atomic(directory / 'response.json', r)
        if r.get('returncode') != 0 or not r.get('session_id') or r.get('errors') or r.get('model_id', model) != model:
            raise Blocked(f"{role} call failed: {r.get('errors') or r.get('stderr') or 'missing session or wrong model'}")
        if role == 'producer':
            self.sessions.add(r['session_id'])
        return r

    def plan(self):
        d = self.run / 'handoffs'
        raw = self.call('producer', reference_analysis_prompt(self.goal, self.lines, self.overlaps), self.files, d / 'reference_analysis')
        analysis = _extract_json(raw['text'])
        if not analysis or len(analysis.get('per_reference', [])) < len(self.files):
            raise Blocked('Reference analysis did not account for the complete image set')
        write_json_atomic(d / 'reference_analysis.json', analysis)
        prompt = plan_work_order(self.goal, self.lines, json.dumps(analysis)) + self.skills('plan')
        raw = self.call('producer', prompt, self.files, d / 'producer_plan')
        plan = _extract_json(raw['text'])
        keys = ('target_proportions', 'structural_geometry', 'face_landmarks', 'hair_masses', 'costume_layers',
                'accessories', 'materials', 'rigging_requirements', 'animation_requirements', 'assumptions')
        if not plan or not all(key in plan for key in keys):
            raise Blocked('Qwen plan omitted required fields')
        write_json_atomic(self.plan_path, plan)
        self.ledger.record_execution('plan', str(d / 'producer_plan' / 'response.json'))
        return plan

    def edit(self, ex, stage, prompt, files, parent, rollback):
        original = prompt + self.skills(stage)
        prompt = original
        code = None
        for retry in range(1, 3):
            directory = parent / f'producer_{retry}'
            r = self.call('producer', prompt, files, directory)
            code = extract_python_block(r['text'])
            if code:
                record = self.provenance.register_producer_output(model_id=self.roles.producer, session_id=r['session_id'],
                    work_order_id=str(directory.relative_to(self.run)), prompt=prompt, response_text=r['text'], code=code)
                (directory / 'asset.py').write_text(code, encoding='utf-8')
                result = ex.execute_producer_script(code, record, phase=stage, rollback_checkpoint_id=rollback)
                write_json_atomic(directory / 'mcp.json', result | {'provenance': record.to_dict()})
                if result.get('ok') and (result.get('audit') or {}).get('mesh_count'):
                    write_json_atomic(directory / 'skill_evidence.json', {
                        'selected_skills': SKILLS.get(stage, []), 'operation': 'execute_blender_code',
                        'producer_record': record.record_id, 'mcp_evidence': 'mcp.json',
                        'applied_skill_status': 'NOT_VERIFIED',
                    })
                    self.ledger.record_execution(stage, str(directory / 'mcp.json'))
                    return code
                if result.get('needs_recovery'):
                    raise Blocked('MCP transport failed; restart isolated runtime from recorded checkpoint: ' + result['error'])
                if rollback and not result.get('scene_unchanged'):
                    ex.load_checkpoint(rollback)
                if not rollback and not result.get('scene_unchanged'):
                    ex.init_scene()
            else:
                result = {'error': 'No fenced Python block in Qwen reply'}
            prompt = build_retry_work_order(original, str(result.get('traceback') or result.get('error') or 'No meshes'), code)
        raise Blocked(f'{stage}: bounded technical retries exhausted: {result.get("error")}')

    def candidate(self, ex, stage, plan, edit=True):
        gate = CorrectionController(stage, max_attempts=self.budget)
        best, last_code, decision = None, None, None
        checkpoint_id = self.approved
        used = self.attempts.get(stage, 0)
        if self.pending:
            pending = self.pending
            if pending['stage'] != stage:
                raise Blocked('Pending review stage does not match the next gate')
            for render in pending['renders']:
                if sha256_file(Path(render['path'])) != render['sha256']:
                    raise Blocked('Pending render no longer matches its hash')
            checkpoint_id = pending['checkpoint']
            ex.load_checkpoint(checkpoint_id)
            candidate = self.manager.verify(checkpoint_id)
            d = Path(candidate.path).parent.parent / 'candidates' / stage / f"{pending['index']:02d}"
            review_dir = d / ('resume_review_' + str(len(list(d.glob('resume_review_*'))) + 1))
            review = self.review_candidate(stage, plan, pending['renders'], review_dir, None, candidate)
            promotion = self.manager.consider_promotion(stage, checkpoint_id)
            if review['passed'] and promotion['promoted']:
                self.pending = None
                return checkpoint_id, review['score']
            old = self.manager.best_reviewed_entry(stage)
            checkpoint_id = old.checkpoint_id
            best = json.loads(Path(old.review_path).read_text())
            ex.load_checkpoint(checkpoint_id)
            self.pending = None
            gate.attempts = max(0, used - 1)
            gate.observe_review(score=best['score'], passed=False, defects=best['defects'], accepted=True)
            decision = gate.decide(passed=False, defects=best['defects'])
            if decision.action != 'CORRECT':
                raise Blocked(decision.reason)
        for index in range(used, self.budget + 1):
            self.attempts[stage] = index + 1
            self.persist()
            d = self.run / 'candidates' / stage / f'{index:02d}'
            d.mkdir(parents=True, exist_ok=False)
            measurement = ex.measure() if checkpoint_id else {}
            inventory = _format_compact_scene_inventory(measurement.get('audit', {}), measurement.get('anomalies'))
            findings = measurement.get('face', {}).get('findings', [])
            if decision:
                prompt = build_correction_work_order(phase=stage, goal=self.goal, plan=plan, inventory=inventory,
                    strategy=decision.strategy.value, regions=decision.regions, defects=decision.defects,
                    reference_lines=self.lines, previous_code=last_code, automated_findings=findings,
                    attempt=index, budget=self.budget)
            else:
                prompt = build_phase_work_order(phase=stage, goal=self.goal, plan=plan, inventory=inventory,
                    reference_lines=self.lines, automated_findings=findings)
            if decision or (edit and stage != 'modeling_gate'):
                images = [Path(best['sheet'])] if best and best.get('sheet') else []
                try:
                    new_code = self.edit(ex, stage, prompt, self.files + images, d, checkpoint_id)
                except Blocked as exc:
                    gate.observe_technical_error(str(exc))
                    write_json_atomic(d / 'correction.json', gate.to_dict())
                    raise
            else:
                new_code = None
            candidate = ex.save_checkpoint(stage=stage, label=f'candidate_{index:02d}')
            measurement = ex.measure()
            write_json_atomic(d / 'measurement.json', measurement)
            if stage in {'rigging', 'animation'}:
                renders = ex.render_motion(d / 'renders', animation=stage == 'animation', samples=self.samples)
            else:
                renders = ex.render(d / 'renders', stage, mode='lookdev' if stage == 'materials' else 'clay',
                    res=(480, 600), samples=self.samples, face=stage != 'silhouette')['render']['renders']
            self.pending = {'stage': stage, 'index': index, 'checkpoint': candidate.checkpoint_id,
                            'renders': [r | {'sha256': sha256_file(Path(r['path']))} for r in renders],
                            'measurement': measurement}
            self.persist()
            review = self.review_candidate(stage, plan, renders, d, best, candidate)
            self.pending = None
            promotion = self.manager.consider_promotion(stage, candidate.checkpoint_id)
            gate.observe_review(score=review['score'], passed=review['passed'], defects=review['defects'], accepted=promotion['promoted'])
            if promotion['promoted']:
                best, last_code, checkpoint_id = review, new_code, candidate.checkpoint_id
            decision = gate.decide(passed=bool(promotion['promoted'] and review['passed']), defects=(best or review)['defects'])
            write_json_atomic(d / 'correction.json', gate.to_dict() | {'promotion': promotion})
            print(f"{stage} {index}: {review['score']}/100 {review['verdict']}; {decision.action}", flush=True)
            self.persist()
            ex.load_checkpoint(checkpoint_id)
            if decision.action == 'PASS':
                return checkpoint_id, review['score']
            if decision.action != 'CORRECT':
                break
        self.ledger.finish(stage, StageStatus.FAIL, reason='Review gate did not pass within the correction budget',
                           score=best['score'] if best else None)
        raise Blocked(f'{stage}: mandatory review gate did not pass')

    def review_candidate(self, stage, plan, renders, directory, best, candidate):
        review = self.reviewer.review(phase=stage, renders=renders, reference_copies=self.copies, goal_excerpt=self.goal,
            plan_excerpt=json.dumps(plan), out_dir=directory, producer_session_ids=self.sessions, prior=best)
        audit = self.pending.get('measurement', {}).get('audit', {}) if self.pending else {}
        face = self.pending.get('measurement', {}).get('face', {}) if self.pending else {}
        defects = []
        if stage in {'face', 'modeling_gate'}:
            for finding in face.get('findings', []):
                if finding['code'] in {'REVERSED_FACE', 'EMBEDDED_EYE'}:
                    defects.append({'defect_id': finding['code'], 'category': finding['code'].lower(), 'region': 'eyes',
                        'object': finding['object'], 'observed': finding['detail'], 'severity': 'CRITICAL', 'structural': True,
                        'expected_correction': 'Rebuild the eye and socket relationship in the forward-facing head',
                        'verification_criterion': 'Eyes visible in front and profile; automated placement flag cleared'})
        if stage == 'materials' and audit and (audit.get('uv_missing_objects') or any(not m['materials'] for m in audit['meshes'])):
            defects.append({'defect_id': 'UV_MATERIAL', 'region': 'materials', 'severity': 'CRITICAL',
                'observed': 'Export meshes lack UVs or assigned materials', 'expected_correction': 'Unwrap and assign materials',
                'verification_criterion': 'Every export mesh has UVs and a material'})
        if defects and review['verdict'] != 'NOT_VERIFIED':
            review.update(verdict='FAIL', passed=False, critical_defects=review['critical_defects'] + len(defects))
            review['defects'].extend(defects)
        path = directory / 'review.json'
        write_json_atomic(path, review)
        if review['verdict'] == 'NOT_VERIFIED':
            raise Blocked(review['reason'])
        self.ledger.record_execution(stage, str(path))
        self.manager.attach_review(candidate.checkpoint_id, score=review['score'],
            critical_defects=review['critical_defects'], review_path=str(path))
        return review

    def release(self, ex, plan):
        if any(self.ledger.status(s) not in {StageStatus.PASS, StageStatus.NOT_APPLICABLE} for s in STAGES[:-1]):
            raise Blocked('Release prerequisites not satisfied')
        directory = self.run / 'diagnostics'
        directory.mkdir(exist_ok=True)
        renders = ex.render(directory / 'renders', 'final', mode='lookdev', res=(768, 960), samples=self.samples * 2)['render']['renders']
        pending = directory / f'{self.asset}.glb'
        technical = ex.export_glb(pending)
        evaluator = BlindRubricEvaluator(model_id=self.roles.reviewer, timeout_sec=self.timeout, invoke_fn=self.invoke)
        review = evaluator.evaluate(renders=renders, producer_session_ids=self.sessions, reference_mode='REFERENCE_GUIDED',
            approved_reference_path=self.files[0], additional_references=self.files[1:],
            frozen_design_spec=json.dumps(plan), technical_gates_passed=technical.get('valid') is True)
        write_json_atomic(directory / 'final_review.json', review)
        if not (review.get('passed') is True and review.get('total_score', 0) >= FROZEN_MIN_TOTAL_SCORE
                and technical.get('valid') is True and pending.is_file()):
            raise Blocked('Final technical or independent rubric gate failed')
        final = self.run / 'final'
        final.mkdir(exist_ok=False)
        copyfile(self.manager.verify(self.approved).path, final / f'{self.asset}.blend')
        pending.replace(final / pending.name)
        self.ledger.record_execution('final', str(directory / 'final_review.json'))
        return review['total_score']

    def execute(self, ex):
        try:
            plan = None
            if self.ledger.status('plan') == StageStatus.PASS:
                if self.approved:
                    ex.load_checkpoint(self.approved)
                elif not self.pending:
                    ex.init_scene()
                plan = json.loads(self.plan_path.read_text())
            for stage in STAGES:
                if self.ledger.status(stage) in {StageStatus.PASS, StageStatus.NOT_APPLICABLE}:
                    continue
                self.phase = stage
                if stage == 'rigging' and self.mode == 'static' or stage == 'animation' and self.mode != 'animated':
                    self.ledger.finish(stage, StageStatus.NOT_APPLICABLE, reason=f'animation mode is {self.mode}')
                    continue
                self.ledger.start(stage)
                self.persist()
                score = None
                if stage == 'plan':
                    plan = self.plan()
                    ex.init_scene()
                elif stage == 'final':
                    score = self.release(ex, plan)
                else:
                    self.approved, score = self.candidate(ex, stage, plan)
                self.ledger.finish(stage, StageStatus.PASS, score=score)
                self.persist()
            reason = None
        except (RuntimeError, OSError, ValueError, KeyError) as exc:
            reason = str(exc)
            if self.phase and self.ledger.status(self.phase) == StageStatus.RUNNING:
                self.ledger.finish(self.phase, StageStatus.NOT_VERIFIED, reason=reason)
            print(f'BLOCKED: {reason}', flush=True)
        finally:
            self.persist()
            write_json_atomic(self.run / 'mcp_calls.json', ex.adapter.call_trace)
            ex.close()
        write_json_atomic(self.run / 'iteration_report.json', {'status': 'BLOCKED' if reason else 'PASS',
            'reason': reason, 'ledger': self.ledger.to_dict(), 'checkpoint_registry': self.manager.summary(),
            'export_approved': not reason and self.ledger.status('final') == StageStatus.PASS})
        return 1 if reason else 0
