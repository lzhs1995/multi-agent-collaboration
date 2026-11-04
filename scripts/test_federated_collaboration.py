import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

import federated_collaboration as f


def make_fixture(base, count=4):
    """Synthetic original declarations only; no live identity or registration."""
    def uid(label):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, 'synthetic-federation/' + label))

    originals = {'bindings': {}, 'modes': {
        name: {'name': name, 'revision': 'example-v1'} for name in
        ('peer', 'host_dispatch', 'hierarchical', 'closed_loop')}, 'budgets': {},
        'registrations': {}}
    manifest = {'schema': 'federated-collaboration-v1', 'project_id': 'SYNTHETIC-EXAMPLE',
                'senior': 'senior', 'actors': [], 'chapters': [], 'tasks': []}

    def ref(pointer):
        return {'path': 'synthetic-originals.json', 'sha256': '0' * 64, 'pointer': pointer}

    def actor(alias, role, workspace, endpoint, pane):
        originals['bindings'][alias] = {'agent': 'claude', 'native_session_id': uid(alias),
            'terminal_id': 'synthetic-term-' + alias, 'pane_id': pane, 'endpoint': endpoint,
            'outer_cmux': {'workspace_uuid': uid(workspace), 'pane_uuid': uid(workspace + '-pane'),
                           'surface_uuid': uid(workspace + '-surface')}}
        manifest['actors'].append({'actor_id': alias, 'role': role,
                                  'binding_ref': ref('/bindings/' + alias)})

    actor('senior', 'supervisor', 'manuscript',
          {'transport': 'cmux', 'host_identity': 'synthetic-host'}, 'unused-outer')
    for n in range(1, count + 1):
        chapter = 'chapter-' + str(n)
        service = {'host_identity': 'synthetic-host', 'session_selector': chapter,
                   'socket': '/synthetic/services/' + chapter + '/herdr.sock'}
        members = [chapter + '-supervisor', chapter + '-executor']
        for alias, role, pane in zip(members, ('supervisor', 'executor'), ('w1:p2', 'w1:p3')):
            actor(alias, role, chapter, {'transport': 'herdr', **service}, pane)
        manifest['chapters'].append({'chapter_id': chapter, 'workspace_uuid': uid(chapter),
            'herdr_service': service, 'supervisor': members[0], 'participants': members})

    def task(task_id, parent, chapter, owner, participants, recipient, mode):
        originals['budgets'][task_id] = {'issue_lineage_id': 'issue-' + task_id,
            'acceptance_scope_id': 'scope-' + task_id, 'max_rounds': 3, 'rounds_used': 1,
            'time_limit_seconds': 600, 'token_limit': 12000}
        manifest['tasks'].append({'task_id': task_id, 'parent_task_id': parent,
            'chapter_id': chapter, 'owner': owner, 'participants': participants,
            'controller': {'source': '/synthetic/immutable/source', 'revision': 'example-v1'},
            'mode_ref': ref('/modes/' + mode), 'budget_ref': ref('/budgets/' + task_id),
            'routes': []})
        for peer in participants:
            if peer == owner:
                continue
            for sender, receiver in ((owner, peer), (peer, owner)):
                manifest['tasks'][-1]['routes'].append({
                    'sender': sender, 'recipient': receiver,
                    'coordination_scope': 'federation' if chapter is None else 'chapter',
                    'delivery_transport': originals['bindings'][receiver]['endpoint']['transport']})

    task('manuscript', None, None, 'senior',
         ['senior'] + [c['supervisor'] for c in manifest['chapters']],
         manifest['chapters'][0]['supervisor'], 'hierarchical')
    for c in manifest['chapters']:
        task(c['chapter_id'], 'manuscript', c['chapter_id'], c['supervisor'],
             c['participants'], c['participants'][1], 'host_dispatch')
    write_fixture(base, manifest, originals)
    return manifest, originals


def write_fixture(base, manifest, originals):
    data = (json.dumps(originals, indent=2, ensure_ascii=False) + '\n').encode()
    (base / 'synthetic-originals.json').write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()

    def update(value):
        if isinstance(value, dict):
            if value.get('path') == 'synthetic-originals.json' and 'sha256' in value:
                value['sha256'] = digest
            for child in value.values():
                update(child)
        elif isinstance(value, list):
            for child in value:
                update(child)
    update(manifest)
    (base / 'four-chapters.json').write_text(json.dumps(manifest, indent=2) + '\n')


class Federation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.manifest, self.originals = make_fixture(self.base)

    def check(self):
        return f.validate(self.manifest, self.base)

    def refresh(self):
        write_fixture(self.base, self.manifest, self.originals)

    def test_four_chapters_identical_role_labels_different_native_sessions(self):
        before = copy.deepcopy(self.manifest)
        result = self.check()
        self.assertEqual((result['chapter_count'], result['actor_count']), (4, 9))
        self.assertTrue(result['declaration_consistent'])
        for key in ('live_identity_verified', 'registered', 'input_sent', 'accepted',
                    'resource_exclusion_verified', 'migrate'):
            self.assertFalse(result[key])
        self.assertEqual(self.manifest, before)

    def test_n_chapters_is_not_fixed_at_four(self):
        for count in (1, 2, 7):
            self.manifest, self.originals = make_fixture(self.base, count)
            self.assertEqual(self.check()['chapter_count'], count)

    def test_senior_can_reuse_dedicated_terminal_without_fifth_workspace(self):
        self.originals['bindings']['senior']['outer_cmux']['workspace_uuid'] = (
            self.manifest['chapters'][0]['workspace_uuid'])
        self.refresh()
        self.assertTrue(self.check()['declaration_consistent'])

    def test_cross_team_scope_is_not_erased_by_herdr_last_hop(self):
        result = self.check()['routes'][0]
        self.assertEqual((result['coordination_scope'], result['profile']), ('federation', 'herdr'))
        self.manifest['tasks'][0]['routes'][0]['coordination_scope'] = 'chapter'
        with self.assertRaisesRegex(f.FederationError, 'COORDINATION_SCOPE_MISMATCH'):
            self.check()

    def test_reverse_route_to_plain_cmux_senior(self):
        routes = self.check()['routes']
        forward = next(r for r in routes if r['task_id'] == 'manuscript' and r['sender'] == 'senior')
        reverse = next(r for r in routes if r['task_id'] == 'manuscript' and r['recipient'] == 'senior')
        self.assertEqual((forward['profile'], reverse['profile']), ('herdr', 'cmux'))
        self.assertEqual(len([r for r in routes if r['task_id'] == 'manuscript']), 8)

    def test_chapter_bidirectional_edges_share_one_task_and_budget(self):
        result = self.check()
        routes = [r for r in result['routes'] if r['task_id'] == 'chapter-1']
        self.assertEqual(len(routes), 2)
        self.assertEqual(routes[0]['sender'], routes[1]['recipient'])
        self.assertEqual(routes[0]['recipient'], routes[1]['sender'])
        self.assertEqual(result['task_count'], 5)

    def test_duplicate_edge_is_rejected_without_requiring_all_to_all(self):
        task = self.manifest['tasks'][0]
        task['routes'].append(copy.deepcopy(task['routes'][0]))
        with self.assertRaisesRegex(f.FederationError, 'DUPLICATE_ROUTE_EDGE'):
            self.check()
        task['routes'] = task['routes'][:1]
        self.assertTrue(self.check()['declaration_consistent'])

    def test_reverse_edge_cannot_use_herdr_for_cmux_senior(self):
        self.manifest['tasks'][0]['routes'][1]['delivery_transport'] = 'herdr'
        with self.assertRaisesRegex(f.FederationError, 'DELIVERY_ENDPOINT_MISMATCH'):
            self.check()

    def test_duplicate_workspace_is_rejected_even_with_different_case(self):
        self.manifest['chapters'][1]['workspace_uuid'] = self.manifest['chapters'][0]['workspace_uuid'].upper()
        with self.assertRaisesRegex(f.FederationError, 'DUPLICATE_WORKSPACE'):
            self.check()

    def test_duplicate_service_socket_or_selector_is_rejected(self):
        for field, reason in (('socket', 'DUPLICATE_SERVICE_SOCKET'),
                              ('session_selector', 'DUPLICATE_SERVICE_SELECTOR')):
            with self.subTest(field=field):
                original = self.manifest['chapters'][1]['herdr_service'][field]
                self.manifest['chapters'][1]['herdr_service'][field] = self.manifest['chapters'][0]['herdr_service'][field]
                with self.assertRaisesRegex(f.FederationError, reason):
                    self.check()
                self.manifest['chapters'][1]['herdr_service'][field] = original

    def test_same_native_actor_cannot_be_registered_under_two_aliases(self):
        self.originals['bindings']['chapter-2-supervisor']['native_session_id'] = (
            self.originals['bindings']['chapter-1-supervisor']['native_session_id'])
        self.refresh()
        with self.assertRaisesRegex(f.FederationError, 'DUPLICATE_NATIVE_ACTOR'):
            self.check()

    def test_terminal_must_be_unique_even_if_session_label_changes(self):
        self.originals['bindings']['chapter-1-executor']['terminal_id'] = (
            self.originals['bindings']['chapter-1-supervisor']['terminal_id'])
        self.refresh()
        with self.assertRaisesRegex(f.FederationError, 'DUPLICATE_NATIVE_TERMINAL'):
            self.check()

    def test_same_service_pane_cannot_claim_two_different_occupants(self):
        self.originals['bindings']['chapter-1-executor']['pane_id'] = 'w1:p2'
        self.refresh()
        with self.assertRaisesRegex(f.FederationError, 'DUPLICATE_INNER_PANE'):
            self.check()

    def test_target_service_and_workspace_must_match_its_chapter(self):
        for field, reason in (('endpoint', 'ACTOR_SERVICE_MISMATCH'),
                             ('outer_cmux', 'ACTOR_WORKSPACE_MISMATCH')):
            saved = copy.deepcopy(self.originals)
            target = self.originals['bindings']['chapter-1-executor']
            if field == 'endpoint':
                target[field]['socket'] = '/synthetic/services/unknown/herdr.sock'
            else:
                target[field] = copy.deepcopy(self.originals['bindings']['chapter-2-executor'][field])
            self.refresh()
            with self.assertRaisesRegex(f.FederationError, reason):
                self.check()
            self.originals = saved
            self.refresh()

    def test_wrong_senior_and_chapter_owner_are_rejected(self):
        self.manifest['tasks'][0]['owner'] = 'chapter-1-supervisor'
        with self.assertRaisesRegex(f.FederationError, 'FEDERATION_OWNER_OR_SCOPE'):
            self.check()
        self.manifest['tasks'][0]['owner'] = 'senior'
        self.manifest['tasks'][1]['owner'] = 'chapter-1-executor'
        with self.assertRaisesRegex(f.FederationError, 'CHAPTER_ROOT_OWNER_MISMATCH'):
            self.check()

    def test_wrong_final_transport_or_cross_chapter_route_is_rejected(self):
        route = self.manifest['tasks'][1]['routes'][0]
        route['delivery_transport'] = 'cmux'
        with self.assertRaisesRegex(f.FederationError, 'DELIVERY_ENDPOINT_MISMATCH'):
            self.check()
        route.update(delivery_transport='herdr', recipient='chapter-2-executor')
        with self.assertRaisesRegex(f.FederationError, 'ROUTE_OUTSIDE_PARTICIPANTS'):
            self.check()

    def test_registration_preserves_old_controller_and_bytes(self):
        task = self.manifest['tasks'][1]
        reg = {'task_id': task['task_id'], 'controller': {'source': '/old/release'},
               'schema': 'legacy-task-v1', 'old_attempt': 'unchanged'}
        self.originals['registrations']['old'] = reg
        task['registration_ref'] = {'path': 'synthetic-originals.json', 'sha256': '0'*64,
                                    'pointer': '/registrations/old'}
        task['controller'] = copy.deepcopy(reg['controller'])
        for route in task['routes']:
            route['delivery_transport'] = 'original'
        self.refresh()
        before = (self.base / 'synthetic-originals.json').read_bytes()
        result = next(r for r in self.check()['routes'] if r['task_id'] == task['task_id'])
        self.assertEqual(result['profile'], 'original')
        self.assertEqual(result['controller'], reg['controller'])
        self.assertEqual((self.base / 'synthetic-originals.json').read_bytes(), before)
        task['routes'][0]['delivery_transport'] = 'herdr'
        with self.assertRaisesRegex(f.FederationError, 'LEGACY_ROUTE_REQUIRES_ORIGINAL'):
            self.check()
        task['routes'][0]['delivery_transport'] = 'original'
        task['controller'] = {'source': '/new/release'}
        with self.assertRaisesRegex(f.FederationError, 'ORIGINAL_CONTROLLER_CHANGED'):
            self.check()

    def test_existing_registered_profile_conflict_is_not_migrated(self):
        task = self.manifest['tasks'][1]
        self.originals['registrations']['old'] = {'schema': 'collaboration-profile-v1',
            'task_id': task['task_id'], 'controller': task['controller'], 'profile': 'cmux'}
        task['registration_ref'] = {'path': 'synthetic-originals.json', 'sha256': '0'*64,
                                    'pointer': '/registrations/old'}
        self.refresh()
        with self.assertRaisesRegex(f.FederationError, 'REGISTERED_ROUTE_CONFLICT'):
            self.check()

    def test_one_scope_or_lineage_cannot_fork_budget_across_tasks(self):
        for field, reason in (('issue_lineage_id', 'ISSUE_LINEAGE_BUDGET_FORK'),
                             ('acceptance_scope_id', 'ACCEPTANCE_SCOPE_BUDGET_FORK')):
            saved = copy.deepcopy(self.originals)
            self.originals['budgets']['chapter-2'][field] = self.originals['budgets']['chapter-1'][field]
            self.refresh()
            with self.assertRaisesRegex(f.FederationError, reason):
                self.check()
            self.originals = saved
            self.refresh()

    def test_same_budget_ref_can_be_shared_without_reset(self):
        self.manifest['tasks'][2]['budget_ref'] = copy.deepcopy(self.manifest['tasks'][1]['budget_ref'])
        self.originals['budgets']['chapter-1']['rounds_used'] = 3
        self.refresh()
        self.assertFalse(self.check()['accepted'])

    def test_rounds_beyond_cap_and_boolean_budgets_fail(self):
        for field, value in (('max_rounds', 4), ('rounds_used', 4), ('rounds_used', True),
                             ('token_limit', 0)):
            saved = copy.deepcopy(self.originals)
            self.originals['budgets']['chapter-1'][field] = value
            self.refresh()
            with self.assertRaises(f.FederationError):
                self.check()
            self.originals = saved
            self.refresh()

    def test_hash_and_pointer_fail_closed(self):
        ref = self.manifest['actors'][0]['binding_ref']
        ref['sha256'] = '0' * 64
        with self.assertRaisesRegex(f.FederationError, 'REFERENCE_HASH_MISMATCH'):
            self.check()
        self.refresh()
        ref['pointer'] = '/bindings/missing'
        with self.assertRaisesRegex(f.FederationError, 'REFERENCE_POINTER_MISSING'):
            self.check()

    def test_missing_original_native_binding_fails(self):
        del self.originals['bindings']['chapter-1-supervisor']['native_session_id']
        self.refresh()
        with self.assertRaisesRegex(f.FederationError, 'INCOMPLETE_NATIVE_BINDING'):
            self.check()

    def test_mode_revision_and_task_tree_must_be_valid(self):
        self.originals['modes']['host_dispatch']['revision'] = ''
        self.refresh()
        with self.assertRaisesRegex(f.FederationError, 'INVALID_MODE'):
            self.check()
        self.originals['modes']['host_dispatch']['revision'] = 'v2'
        self.refresh()
        self.manifest['tasks'][1]['parent_task_id'] = 'chapter-1'
        with self.assertRaisesRegex(f.FederationError, 'TASK_TREE_CYCLE'):
            self.check()

    def test_malformed_json_and_duplicate_keys_produce_clean_failure(self):
        for payload in ('null', '{"schema":1,"schema":2}', '{'):
            path = self.base / 'bad.json'
            path.write_text(payload)
            stream = io.StringIO()
            with patch('sys.stdout', stream):
                self.assertEqual(f.main([str(path)]), 1)
            self.assertFalse(json.loads(stream.getvalue())['input_sent'])

    def test_cli_has_no_process_or_file_mutation_side_effect(self):
        before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in self.base.iterdir()}
        with patch('subprocess.run', side_effect=AssertionError('no external commands')):
            with patch('sys.stdout', io.StringIO()):
                self.assertEqual(f.main([str(self.base/'four-chapters.json')]), 0)
        after = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in self.base.iterdir()}
        self.assertEqual(before, after)

    def test_real_cli_and_import_B_do_not_create_bytecode(self):
        script = Path(f.__file__).resolve()
        scratch = self.base / 'source'
        scratch.mkdir()
        for name in ('federated_collaboration.py', 'collaboration_profile.py'):
            (scratch / name).write_bytes((script.parent / name).read_bytes())
        # The normal CLI must also protect its subsequent profile import.
        result = subprocess.run([sys.executable, str(scratch/script.name), str(self.base/'four-chapters.json')],
                                capture_output=True, text=True, check=True)
        self.assertTrue(json.loads(result.stdout)['declaration_consistent'])
        subprocess.run([sys.executable, '-B', '-c', 'import federated_collaboration'],
                       cwd=scratch, capture_output=True, check=True)
        self.assertFalse(list(self.base.rglob('*.pyc')))


if __name__ == '__main__':
    unittest.main()
