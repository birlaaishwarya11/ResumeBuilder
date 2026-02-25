"""
Comprehensive test suite for the refactored service layer.

Tests cover (no external APIs or Daytona required):
  - ai_service:      parse_json_response, extract_ai_error
  - resume_service:  YAML validation, save/get/version lifecycle
  - parser_service:  state machine, ownership checks
  - jd_service:      suggestion schema helpers, yaml fence stripping
  - models:          all new CRUD (parsers, resume_versions, jd_sessions)
  - Flask routes:    auth, parser state, versions, jd_analyze/apply
  - mcp_server:      tool registration check
  - security:        cross-user access rejected

Run:
    cd SimpleLocalBuilder
    python -m pytest tests/ -v
"""

import json
import os
import sys
import tempfile
import shutil
import sqlite3
import unittest

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ---------------------------------------------------------------------------
# Fixtures: temporary SQLite DB + DATA_DIR
# ---------------------------------------------------------------------------

def _temp_env(test_case):
    """Set up a fresh temp DB and data directory for a test, tear down after."""
    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, 'test_resume.db')
    data_dir = os.path.join(tmp, 'data')
    os.makedirs(data_dir)

    # Patch environment so models.py uses the temp DB and data dir
    os.environ['DATABASE_URL'] = ''          # force SQLite
    os.environ.setdefault('SECRET_KEY', 'test-secret')

    import models as m
    # Monkey-patch paths for this test
    m.DB_PATH = db_path
    m.DATA_DIR = data_dir
    m.DB_BACKEND = 'sqlite'

    m.init_db()

    test_case.tmp = tmp
    test_case.db_path = db_path
    test_case.data_dir = data_dir
    test_case._models = m


def _cleanup_env(test_case):
    shutil.rmtree(test_case.tmp, ignore_errors=True)


def _create_test_user(m, name='Alice', email='alice@test.com', password='secret'):
    """Create a user and return user_id."""
    uid = m.create_user(name, email, password)
    assert uid is not None, 'create_user returned None'
    return uid


# ===========================================================================
# 1. ai_service
# ===========================================================================

class TestAiService(unittest.TestCase):

    def test_parse_json_response_dict(self):
        from ai_service import parse_json_response
        result = parse_json_response('{"key": "value"}')
        self.assertEqual(result, {'key': 'value'})

    def test_parse_json_response_list(self):
        from ai_service import parse_json_response
        result = parse_json_response('[1, 2, 3]')
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 3)

    def test_parse_json_response_with_markdown_fences(self):
        from ai_service import parse_json_response
        text = '```json\n{"score": 85}\n```'
        result = parse_json_response(text)
        self.assertEqual(result.get('score'), 85)

    def test_parse_json_response_embedded_json(self):
        from ai_service import parse_json_response
        text = 'Here is the result: {"match": true} done.'
        result = parse_json_response(text)
        self.assertEqual(result.get('match'), True)

    def test_parse_json_response_invalid_returns_empty(self):
        from ai_service import parse_json_response
        result = parse_json_response('not json at all')
        self.assertEqual(result, {})

    def test_extract_ai_error_status_code(self):
        from ai_service import extract_ai_error

        class FakeAPIError(Exception):
            status_code = 401

        err = extract_ai_error(FakeAPIError('Unauthorized'))
        self.assertEqual(err['status_code'], 401)
        self.assertIn('Unauthorized', err['message'])

    def test_extract_ai_error_no_status_code(self):
        from ai_service import extract_ai_error
        err = extract_ai_error(ValueError('Something went wrong'))
        self.assertIsNone(err['status_code'])
        self.assertIn('Something went wrong', err['message'])

    def test_extract_ai_error_numeric_prefix(self):
        from ai_service import extract_ai_error
        err = extract_ai_error(Exception('429 Rate limit exceeded'))
        self.assertEqual(err['status_code'], 429)

    def test_unknown_provider_raises(self):
        from ai_service import call_llm
        with self.assertRaises(ValueError) as ctx:
            call_llm('unknown_provider', 'key', '', 'hello')
        self.assertIn('unknown_provider', str(ctx.exception))


# ===========================================================================
# 2. resume_service (filesystem + DB)
# ===========================================================================

class TestResumeService(unittest.TestCase):

    def setUp(self):
        _temp_env(self)

    def tearDown(self):
        _cleanup_env(self)

    def test_validate_yaml_valid(self):
        from resume_service import _validate_yaml
        _validate_yaml('name: Alice\ncontact:\n  email: a@b.com\n')  # should not raise

    def test_validate_yaml_invalid_raises(self):
        from resume_service import _validate_yaml
        with self.assertRaises(ValueError):
            _validate_yaml('{not: valid: yaml: here')

    def test_parse_yaml_empty(self):
        from resume_service import parse_yaml
        self.assertEqual(parse_yaml(''), {})
        self.assertEqual(parse_yaml('   '), {})

    def test_parse_yaml_safe_load(self):
        from resume_service import parse_yaml
        result = parse_yaml('name: Bob\nage: 30\n')
        self.assertEqual(result['name'], 'Bob')
        self.assertEqual(result['age'], 30)

    def test_dump_yaml_roundtrip(self):
        from resume_service import parse_yaml, dump_yaml
        data = {'name': 'Carol', 'skills': ['Python', 'Flask']}
        yml = dump_yaml(data)
        restored = parse_yaml(yml)
        self.assertEqual(restored['name'], 'Carol')
        self.assertEqual(restored['skills'], ['Python', 'Flask'])

    def test_get_current_resume_none_before_upload(self):
        from resume_service import get_current_resume
        m = self._models
        uid = _create_test_user(m)
        result = get_current_resume(uid)
        self.assertIsNone(result)

    def test_save_and_get_current_resume(self):
        from resume_service import save_current_resume, get_current_resume
        m = self._models
        uid = _create_test_user(m)

        yaml_content = 'name: Dave\ncontact:\n  email: dave@test.com\n'
        version_id = save_current_resume(uid, yaml_content, source='upload')

        self.assertIsInstance(version_id, int)
        self.assertGreater(version_id, 0)

        retrieved = get_current_resume(uid)
        self.assertEqual(retrieved, yaml_content)

    def test_save_invalid_yaml_raises(self):
        from resume_service import save_current_resume
        m = self._models
        uid = _create_test_user(m)
        with self.assertRaises(ValueError):
            save_current_resume(uid, '{bad: yaml: here', source='upload')

    def test_list_versions(self):
        from resume_service import save_current_resume, list_versions
        m = self._models
        uid = _create_test_user(m)

        save_current_resume(uid, 'name: Eve\n', source='upload')
        save_current_resume(uid, 'name: Eve\nskills: [Python]\n', source='ai_edit')

        versions = list_versions(uid)
        self.assertEqual(len(versions), 2)
        # Newest first
        self.assertEqual(versions[0]['source'], 'ai_edit')
        self.assertEqual(versions[1]['source'], 'upload')

    def test_get_version_security(self):
        """User cannot retrieve another user's version."""
        from resume_service import save_current_resume, get_version
        m = self._models
        uid1 = _create_test_user(m, email='user1@test.com')
        uid2 = _create_test_user(m, name='Bob', email='user2@test.com')

        version_id = save_current_resume(uid1, 'name: Alice\n', source='upload')

        # uid2 tries to read uid1's version
        result = get_version(version_id, uid2)
        self.assertIsNone(result, 'Cross-user version access should return None')

    def test_restore_version(self):
        from resume_service import save_current_resume, restore_version, get_current_resume
        m = self._models
        uid = _create_test_user(m)

        v1_id = save_current_resume(uid, 'name: Frank\n', source='upload', label='v1')
        save_current_resume(uid, 'name: Frank Updated\n', source='ai_edit', label='v2')

        restored = restore_version(v1_id, uid)
        self.assertIn('Frank', restored)
        self.assertNotIn('Updated', restored)

        current = get_current_resume(uid)
        self.assertIn('Frank', current)

    def test_restore_wrong_user_raises(self):
        from resume_service import save_current_resume, restore_version
        m = self._models
        uid1 = _create_test_user(m, email='r1@test.com')
        uid2 = _create_test_user(m, name='G', email='r2@test.com')

        version_id = save_current_resume(uid1, 'name: Grace\n', source='upload')

        with self.assertRaises(ValueError):
            restore_version(version_id, uid2)


# ===========================================================================
# 3. models — parsers table
# ===========================================================================

class TestModels_Parsers(unittest.TestCase):

    def setUp(self):
        _temp_env(self)
        self.uid = _create_test_user(self._models)

    def tearDown(self):
        _cleanup_env(self)

    def test_create_parser_returns_id(self):
        m = self._models
        pid = m.create_parser(self.uid, 'def parse(lines): return {}', state='DRAFT')
        self.assertIsInstance(pid, int)
        self.assertGreater(pid, 0)

    def test_get_parser_by_id(self):
        m = self._models
        pid = m.create_parser(self.uid, 'def parse(l): return {}', state='DRAFT')
        row = m.get_parser_by_id(pid)
        self.assertIsNotNone(row)
        self.assertEqual(row['user_id'], self.uid)
        self.assertEqual(row['state'], 'DRAFT')

    def test_get_active_parser_prefers_locked(self):
        m = self._models
        active_id = m.create_parser(self.uid, 'def parse(l): return {"a": 1}', state='ACTIVE')
        locked_id = m.create_parser(self.uid, 'def parse(l): return {"b": 2}', state='LOCKED')

        best = m.get_active_parser(self.uid)
        self.assertIsNotNone(best)
        self.assertEqual(best['id'], locked_id)
        self.assertEqual(best['state'], 'LOCKED')

    def test_get_active_parser_falls_back_to_active(self):
        m = self._models
        active_id = m.create_parser(self.uid, 'def parse(l): return {}', state='ACTIVE')

        best = m.get_active_parser(self.uid)
        self.assertIsNotNone(best)
        self.assertEqual(best['state'], 'ACTIVE')

    def test_get_active_parser_none_when_only_draft(self):
        m = self._models
        m.create_parser(self.uid, 'def parse(l): return {}', state='DRAFT')

        best = m.get_active_parser(self.uid)
        self.assertIsNone(best)

    def test_update_parser_state(self):
        m = self._models
        pid = m.create_parser(self.uid, 'def parse(l): return {}', state='DRAFT')
        m.update_parser_state(pid, 'ACTIVE')

        row = m.get_parser_by_id(pid)
        self.assertEqual(row['state'], 'ACTIVE')

    def test_update_parser_state_invalid_raises(self):
        m = self._models
        pid = m.create_parser(self.uid, 'def parse(l): return {}', state='DRAFT')
        with self.assertRaises(ValueError):
            m.update_parser_state(pid, 'INVALID_STATE')

    def test_lock_parser_demotes_previous_locked(self):
        m = self._models
        first_id = m.create_parser(self.uid, 'def parse(l): return {}', state='LOCKED')
        second_id = m.create_parser(self.uid, 'def parse(l): return {}', state='ACTIVE')

        m.lock_parser(self.uid, second_id)

        first = m.get_parser_by_id(first_id)
        second = m.get_parser_by_id(second_id)

        self.assertEqual(first['state'], 'ACTIVE',
                         'Previously locked parser should be demoted to ACTIVE')
        self.assertEqual(second['state'], 'LOCKED')

    def test_delete_parser_ownership_enforced(self):
        m = self._models
        uid2 = _create_test_user(m, name='Eve', email='eve@test.com')
        pid = m.create_parser(self.uid, 'def parse(l): return {}', state='DRAFT')

        # Attacker tries to delete uid1's parser as uid2
        m.delete_parser(pid, uid2)

        row = m.get_parser_by_id(pid)
        self.assertIsNotNone(row, 'Parser should NOT be deleted when wrong user_id is supplied')

    def test_list_parsers_excludes_code(self):
        m = self._models
        m.create_parser(self.uid, 'def parse(l): return {}', state='DRAFT')
        rows = m.list_parsers(self.uid)
        self.assertEqual(len(rows), 1)
        self.assertNotIn('code', rows[0], 'list_parsers should not return code field')

    def test_coverage_score_stored(self):
        m = self._models
        pid = m.create_parser(self.uid, 'def parse(l): return {}',
                              state='DRAFT', coverage_score=87.5)
        row = m.get_parser_by_id(pid)
        self.assertAlmostEqual(row['coverage_score'], 87.5)


# ===========================================================================
# 4. models — resume_versions table
# ===========================================================================

class TestModels_ResumeVersions(unittest.TestCase):

    def setUp(self):
        _temp_env(self)
        self.uid = _create_test_user(self._models)

    def tearDown(self):
        _cleanup_env(self)

    def test_save_and_retrieve_version(self):
        m = self._models
        vid = m.save_resume_version(self.uid, 'name: Alice\n', source='upload')
        row = m.get_resume_version(vid, self.uid)
        self.assertIsNotNone(row)
        self.assertIn('Alice', row['yaml_content'])
        self.assertEqual(row['source'], 'upload')

    def test_invalid_source_normalized(self):
        m = self._models
        vid = m.save_resume_version(self.uid, 'name: X\n', source='hacked_source')
        row = m.get_resume_version(vid, self.uid)
        self.assertEqual(row['source'], 'manual_edit')

    def test_get_version_wrong_user_returns_none(self):
        m = self._models
        uid2 = _create_test_user(m, name='Bob', email='b@test.com')
        vid = m.save_resume_version(self.uid, 'name: Alice\n', source='upload')
        row = m.get_resume_version(vid, uid2)
        self.assertIsNone(row)

    def test_list_versions_newest_first(self):
        m = self._models
        m.save_resume_version(self.uid, 'v1\n', source='upload')
        m.save_resume_version(self.uid, 'v2\n', source='ai_edit')
        versions = m.list_resume_versions(self.uid)
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0]['source'], 'ai_edit')

    def test_get_latest_version(self):
        m = self._models
        m.save_resume_version(self.uid, 'v1\n', source='upload')
        m.save_resume_version(self.uid, 'v2\n', source='ai_edit')
        latest = m.get_latest_resume_version(self.uid)
        self.assertIn('v2', latest['yaml_content'])


# ===========================================================================
# 5. models — jd_sessions table
# ===========================================================================

class TestModels_JdSessions(unittest.TestCase):

    def setUp(self):
        _temp_env(self)
        self.uid = _create_test_user(self._models)

    def tearDown(self):
        _cleanup_env(self)

    def test_create_and_get_session(self):
        m = self._models
        sid = m.create_jd_session(self.uid, 'We need a Python engineer...')
        row = m.get_jd_session(sid, self.uid)
        self.assertIsNotNone(row)
        self.assertIn('Python', row['jd_text'])

    def test_update_session_with_analysis(self):
        m = self._models
        sid = m.create_jd_session(self.uid, 'JD text here')
        suggestions = [{'id': 'add_keyword_0', 'type': 'add_keyword', 'section': 'skills',
                        'value': 'Kubernetes', 'reason': 'mentioned 5x', 'priority': 1}]
        m.update_jd_session(sid, 72, suggestions)

        row = m.get_jd_session(sid, self.uid)
        self.assertEqual(row['match_score'], 72)
        self.assertIsInstance(row.get('suggestions'), list)
        self.assertEqual(row['suggestions'][0]['value'], 'Kubernetes')

    def test_get_session_wrong_user_returns_none(self):
        m = self._models
        uid2 = _create_test_user(m, name='Eve', email='e@test.com')
        sid = m.create_jd_session(self.uid, 'secret jd')
        row = m.get_jd_session(sid, uid2)
        self.assertIsNone(row)

    def test_mark_jd_applied(self):
        m = self._models
        sid = m.create_jd_session(self.uid, 'JD')
        vid = m.save_resume_version(self.uid, 'name: A\n', source='jd_applied')
        m.mark_jd_applied(sid, vid)

        row = m.get_jd_session(sid, self.uid)
        self.assertEqual(row['applied_version_id'], vid)

    def test_list_sessions_hides_full_jd(self):
        m = self._models
        long_jd = 'A' * 500
        m.create_jd_session(self.uid, long_jd)
        rows = m.list_jd_sessions(self.uid)
        self.assertEqual(len(rows), 1)
        self.assertLessEqual(len(rows[0]['jd_preview']), 200)


# ===========================================================================
# 6. parser_service — state machine
# ===========================================================================

class TestParserService(unittest.TestCase):

    def setUp(self):
        _temp_env(self)
        self.uid = _create_test_user(self._models)

    def tearDown(self):
        _cleanup_env(self)

    def _make_parser(self, state='DRAFT'):
        m = self._models
        return m.create_parser(self.uid, 'def parse(l): return {}', state=state)

    def test_activate_draft(self):
        import parser_service
        pid = self._make_parser('DRAFT')
        parser_service.activate_parser(pid, self.uid)

        row = self._models.get_parser_by_id(pid)
        self.assertEqual(row['state'], 'ACTIVE')

    def test_activate_wrong_user_raises(self):
        import parser_service
        uid2 = _create_test_user(self._models, name='Bob', email='bob@test.com')
        pid = self._make_parser('DRAFT')
        with self.assertRaises(ValueError):
            parser_service.activate_parser(pid, uid2)

    def test_confirm_and_lock(self):
        import parser_service
        pid = self._make_parser('ACTIVE')
        parser_service.confirm_and_lock(pid, self.uid)

        row = self._models.get_parser_by_id(pid)
        self.assertEqual(row['state'], 'LOCKED')

    def test_lock_demotes_previous(self):
        import parser_service
        m = self._models
        first_id = m.create_parser(self.uid, 'def parse(l): return {}', state='LOCKED')
        second_id = m.create_parser(self.uid, 'def parse(l): return {}', state='ACTIVE')

        parser_service.confirm_and_lock(second_id, self.uid)

        first = m.get_parser_by_id(first_id)
        self.assertEqual(first['state'], 'ACTIVE')

    def test_unlock_locked(self):
        import parser_service
        pid = self._make_parser('LOCKED')
        parser_service.unlock_parser(pid, self.uid)

        row = self._models.get_parser_by_id(pid)
        self.assertEqual(row['state'], 'ACTIVE')

    def test_unlock_non_locked_raises(self):
        import parser_service
        pid = self._make_parser('DRAFT')
        with self.assertRaises(ValueError):
            parser_service.unlock_parser(pid, self.uid)

    def test_discard_parser(self):
        import parser_service
        pid = self._make_parser('DRAFT')
        parser_service.discard_parser(pid, self.uid)

        row = self._models.get_parser_by_id(pid)
        self.assertIsNone(row)

    def test_discard_wrong_user_raises(self):
        import parser_service
        uid2 = _create_test_user(self._models, name='Carol', email='c@test.com')
        pid = self._make_parser('DRAFT')
        with self.assertRaises(ValueError):
            parser_service.discard_parser(pid, uid2)

    def test_get_best_parser_prefers_locked(self):
        import parser_service
        m = self._models
        m.create_parser(self.uid, 'def parse(l): return {}', state='ACTIVE')
        m.create_parser(self.uid, 'def parse(l): return {}', state='LOCKED')

        best = parser_service.get_best_parser(self.uid)
        self.assertEqual(best['state'], 'LOCKED')

    def test_get_best_parser_none_if_only_draft(self):
        import parser_service
        m = self._models
        m.create_parser(self.uid, 'def parse(l): return {}', state='DRAFT')

        best = parser_service.get_best_parser(self.uid)
        self.assertIsNone(best)

    def test_list_user_parsers_no_code(self):
        import parser_service
        m = self._models
        m.create_parser(self.uid, 'def parse(l): return {}', state='DRAFT')
        parsers = parser_service.list_user_parsers(self.uid)
        self.assertEqual(len(parsers), 1)
        self.assertNotIn('code', parsers[0])

    def test_get_parser_wrong_user_returns_none(self):
        import parser_service
        uid2 = _create_test_user(self._models, name='D', email='d@test.com')
        pid = self._make_parser('ACTIVE')
        row = parser_service.get_parser(pid, uid2)
        self.assertIsNone(row)


# ===========================================================================
# 7. jd_service helpers (no LLM calls)
# ===========================================================================

class TestJdServiceHelpers(unittest.TestCase):

    def test_strip_yaml_fences_plain(self):
        from jd_service import _strip_yaml_fences
        text = 'name: Alice\n'
        self.assertEqual(_strip_yaml_fences(text), 'name: Alice')

    def test_strip_yaml_fences_with_backtick_yaml(self):
        from jd_service import _strip_yaml_fences
        text = '```yaml\nname: Alice\nskills:\n  - Python\n```'
        stripped = _strip_yaml_fences(text)
        self.assertNotIn('```', stripped)
        self.assertIn('Alice', stripped)

    def test_strip_yaml_fences_generic_backticks(self):
        from jd_service import _strip_yaml_fences
        text = '```\nname: Bob\n```'
        stripped = _strip_yaml_fences(text)
        self.assertNotIn('```', stripped)
        self.assertIn('Bob', stripped)

    def test_suggestion_ids_assigned_when_missing(self):
        """Simulate the id-assignment logic from jd_service.analyze."""
        suggestions = [
            {'type': 'add_keyword', 'section': 'skills', 'value': 'K8s',
             'reason': 'x', 'priority': 1},
            {'id': 'existing_id', 'type': 'rephrase', 'section': 'experience',
             'value': 'Do more', 'reason': 'y', 'priority': 2},
        ]
        for i, s in enumerate(suggestions):
            if not s.get('id'):
                s['id'] = f"{s.get('type', 'suggestion')}_{i}"

        self.assertEqual(suggestions[0]['id'], 'add_keyword_0')
        self.assertEqual(suggestions[1]['id'], 'existing_id')


# ===========================================================================
# 8. Flask routes (using Flask test client)
# ===========================================================================

class TestFlaskRoutes(unittest.TestCase):

    def setUp(self):
        _temp_env(self)

        # Import after patching DB paths
        import local_app as app_module
        app_module.app.config['TESTING'] = True
        app_module.app.config['WTF_CSRF_ENABLED'] = False
        self.app = app_module.app.test_client()
        self.app_module = app_module

        # Create a test user and log in
        m = self._models
        self.uid = _create_test_user(m)
        with self.app.session_transaction() as sess:
            sess['user_id'] = self.uid

    def tearDown(self):
        _cleanup_env(self)

    def _json(self, response):
        return json.loads(response.data)

    # --- Parser state routes ---

    def test_list_parsers_empty(self):
        resp = self.app.get('/api/parser/list')
        data = self._json(resp)
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['parsers'], [])

    def test_list_parsers_returns_parser(self):
        m = self._models
        m.create_parser(self.uid, 'def parse(l): return {}', state='DRAFT')
        resp = self.app.get('/api/parser/list')
        data = self._json(resp)
        self.assertEqual(len(data['parsers']), 1)
        self.assertEqual(data['parsers'][0]['state'], 'DRAFT')

    def test_activate_parser_route(self):
        m = self._models
        pid = m.create_parser(self.uid, 'def parse(l): return {}', state='DRAFT')
        resp = self.app.post('/api/parser/activate',
                             json={'parser_id': pid},
                             content_type='application/json')
        data = self._json(resp)
        self.assertEqual(data['status'], 'success')
        self.assertEqual(m.get_parser_by_id(pid)['state'], 'ACTIVE')

    def test_lock_parser_route(self):
        m = self._models
        pid = m.create_parser(self.uid, 'def parse(l): return {}', state='ACTIVE')
        resp = self.app.post('/api/parser/lock',
                             json={'parser_id': pid},
                             content_type='application/json')
        data = self._json(resp)
        self.assertEqual(data['status'], 'success')
        self.assertEqual(m.get_parser_by_id(pid)['state'], 'LOCKED')

    def test_unlock_parser_route(self):
        m = self._models
        pid = m.create_parser(self.uid, 'def parse(l): return {}', state='LOCKED')
        resp = self.app.post('/api/parser/unlock',
                             json={'parser_id': pid},
                             content_type='application/json')
        data = self._json(resp)
        self.assertEqual(data['status'], 'success')
        self.assertEqual(m.get_parser_by_id(pid)['state'], 'ACTIVE')

    def test_discard_parser_route(self):
        m = self._models
        pid = m.create_parser(self.uid, 'def parse(l): return {}', state='DRAFT')
        resp = self.app.post('/api/parser/discard',
                             json={'parser_id': pid},
                             content_type='application/json')
        data = self._json(resp)
        self.assertEqual(data['status'], 'success')
        self.assertIsNone(m.get_parser_by_id(pid))

    def test_activate_missing_parser_id_returns_400(self):
        resp = self.app.post('/api/parser/activate',
                             json={},
                             content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    # --- Version routes ---

    def test_list_versions_empty(self):
        resp = self.app.get('/api/versions')
        data = self._json(resp)
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['versions'], [])

    def test_list_versions_after_upload(self):
        from resume_service import save_current_resume
        save_current_resume(self.uid, 'name: Alice\n', source='upload')
        resp = self.app.get('/api/versions')
        data = self._json(resp)
        self.assertEqual(len(data['versions']), 1)

    def test_restore_version_route(self):
        from resume_service import save_current_resume, get_current_resume
        v1_id = save_current_resume(self.uid, 'name: Original\n', source='upload')
        save_current_resume(self.uid, 'name: Changed\n', source='ai_edit')

        resp = self.app.post('/api/versions/restore',
                             json={'version_id': v1_id},
                             content_type='application/json')
        data = self._json(resp)
        self.assertEqual(data['status'], 'success')
        self.assertIn('Original', data['yaml'])

    def test_restore_missing_version_id_returns_400(self):
        resp = self.app.post('/api/versions/restore',
                             json={},
                             content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    # --- JD routes ---

    def test_jd_analyze_missing_jd_returns_400(self):
        resp = self.app.post('/api/jd_analyze',
                             json={'api_key': 'key'},
                             content_type='application/json')
        self.assertEqual(resp.status_code, 400)
        data = self._json(resp)
        self.assertIn('jd_text', data['message'])

    def test_jd_analyze_missing_api_key_returns_400(self):
        resp = self.app.post('/api/jd_analyze',
                             json={'jd_text': 'We need Python engineers'},
                             content_type='application/json')
        self.assertEqual(resp.status_code, 400)
        data = self._json(resp)
        self.assertIn('api_key', data['message'])

    def test_jd_apply_missing_session_returns_400(self):
        resp = self.app.post('/api/jd_apply',
                             json={'suggestion_ids': ['a'], 'api_key': 'k'},
                             content_type='application/json')
        self.assertEqual(resp.status_code, 400)
        data = self._json(resp)
        self.assertIn('session_id', data['message'])

    def test_jd_apply_missing_suggestions_returns_400(self):
        resp = self.app.post('/api/jd_apply',
                             json={'session_id': 1, 'api_key': 'k'},
                             content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    # --- Auth check ---

    def test_unauthenticated_redirects(self):
        """Routes redirect to login when no session cookie."""
        anon = self.app_module.app.test_client()
        resp = anon.get('/api/parser/list')
        self.assertIn(resp.status_code, (302, 401))

    def test_parser_lock_wrong_user_returns_400(self):
        """Parser owned by uid1 cannot be locked by uid2."""
        m = self._models
        uid2 = _create_test_user(m, name='Bob', email='bbb@test.com')
        pid = m.create_parser(self.uid, 'def parse(l): return {}', state='ACTIVE')

        # Switch session to uid2
        with self.app.session_transaction() as sess:
            sess['user_id'] = uid2

        resp = self.app.post('/api/parser/lock',
                             json={'parser_id': pid},
                             content_type='application/json')
        data = self._json(resp)
        self.assertEqual(data['status'], 'error',
                         'uid2 should not be able to lock uid1\'s parser')


# ===========================================================================
# 9. MCP server — tool registration sanity check
# ===========================================================================

class TestMcpServer(unittest.TestCase):

    def test_tools_registered(self):
        import mcp_server
        expected = {
            'get_resume', 'update_resume', 'ai_edit_resume',
            'analyze_jd', 'apply_jd_suggestions', 'apply_full_jd',
            'list_versions', 'restore_version', 'create_version',
        }
        # FastMCP stores tools by name
        registered = set(mcp_server.mcp._tool_manager._tools.keys())
        missing = expected - registered
        self.assertEqual(missing, set(),
                         f'Missing MCP tools: {missing}')

    def test_no_http_imports_in_mcp(self):
        """MCP server must not import requests or tools (the deleted blueprint)."""
        import ast, pathlib
        src = pathlib.Path(__file__).parent.parent / 'mcp_server.py'
        tree = ast.parse(src.read_text())
        bad = {'requests', 'tools', 'urllib'}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [n.name for n in node.names] if isinstance(node, ast.Import) else [node.module or '']
                for name in names:
                    self.assertNotIn(name.split('.')[0], bad,
                                     f'mcp_server imports {name!r} which should not be there')


# ===========================================================================
# 10. DB delete_user cascade
# ===========================================================================

class TestDeleteUserCascade(unittest.TestCase):

    def setUp(self):
        _temp_env(self)

    def tearDown(self):
        _cleanup_env(self)

    def test_delete_user_removes_related_rows(self):
        m = self._models
        uid = _create_test_user(m)

        # Create related data
        m.create_parser(uid, 'def parse(l): return {}', state='DRAFT')
        m.save_resume_version(uid, 'name: Alice\n', source='upload')
        m.create_jd_session(uid, 'Some JD text')

        # Delete user
        m.delete_user(uid)

        # Verify cascade: all related rows gone
        conn = sqlite3.connect(m.DB_PATH)
        for table in ('parsers', 'resume_versions', 'jd_sessions', 'user_settings'):
            count = conn.execute(
                f'SELECT COUNT(*) FROM {table} WHERE user_id = ?', (uid,)
            ).fetchone()[0]
            self.assertEqual(count, 0, f'{table} still has rows after delete_user')
        conn.close()


# ===========================================================================
# Entry point
# ===========================================================================

if __name__ == '__main__':
    unittest.main(verbosity=2)
