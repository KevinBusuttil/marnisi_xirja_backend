"""Result artifacts must contain complete outcomes without sensitive diagnostics."""
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import unittest
import xml.etree.ElementTree as ET

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'run_fiscal_integration.py'
spec = importlib.util.spec_from_file_location('marnisi_test_runner', SCRIPT)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def _events(*events):
    return io.StringIO('\n'.join(json.dumps(event) for event in events))


def _site(tmp_path, **changes):
    site = 'marnisi-fiscal-test.localhost'
    config = {
        'marnisi_disposable_fiscal_test': True,
        'db_host': '127.0.0.1',
        'db_name': 'marnisi_fiscal_disposable_test',
        'pause_scheduler': True,
        'allow_tests': True,
        'db_password': 'PASSWORD_NOT_FOR_EVIDENCE',
    }
    config.update(changes)
    folder = tmp_path / 'sites' / site
    folder.mkdir(parents=True)
    (folder / 'site_config.json').write_text(json.dumps(config))
    return site


@pytest.mark.parametrize('changes', [
    {'marnisi_disposable_fiscal_test': False},
    {'db_host': 'production.example'},
    {'db_name': 'production'},
    {'pause_scheduler': False},
    {'allow_tests': False},
])
def test_disposable_guard_rejects_each_unsafe_setting(tmp_path, changes):
    site = _site(tmp_path, **changes)
    with pytest.raises(ValueError):
        runner.validate_disposable(tmp_path, site)


@pytest.mark.parametrize('site', ['production.localhost', 'marnisi-fiscal-test../other', 'marnisi-fiscal-test.\\other'])
def test_disposable_guard_rejects_name_before_opening_files(tmp_path, site):
    with pytest.raises(ValueError):
        runner.validate_disposable(tmp_path, site)


def test_guard_failure_artifact_omits_config_and_exception_details(tmp_path, capsys):
    site = _site(tmp_path, db_host='production.example')
    directory = tmp_path / 'results'
    assert runner.run_integration(tmp_path, site, directory) == 1
    content = (directory / 'integration.json').read_text()
    assert json.loads(content)['reason_code'] == 'disposable_guard_failed'
    assert 'PASSWORD_NOT_FOR_EVIDENCE' not in content
    assert 'production.example' not in content
    assert 'PASSWORD_NOT_FOR_EVIDENCE' not in capsys.readouterr().err


def test_flutter_inventory_retains_names_and_omits_machine_diagnostics(tmp_path):
    output = tmp_path / 'flutter.json'
    stream = _events(
        {'type': 'suite', 'suite': {'id': 0, 'path': 'test/receipt_test.dart'}},
        {'type': 'testStart', 'test': {'id': 0, 'suiteID': 0, 'name': 'loading test'}},
        {'type': 'testDone', 'testID': 0, 'result': 'success', 'hidden': True},
        {'type': 'testStart', 'test': {'id': 1, 'suiteID': 0, 'name': 'frozen receipt € retains payment order'}},
        {'type': 'print', 'message': 'Cookie sid=DO_NOT_PUBLISH'},
        {'type': 'error', 'error': 'password=DO_NOT_PUBLISH', 'stackTrace': 'Bearer DO_NOT_PUBLISH'},
        {'type': 'testDone', 'testID': 1, 'result': 'failure', 'hidden': False},
        {'type': 'testStart', 'test': {'id': 2, 'suiteID': 0, 'name': 'physical printer acceptance'}},
        {'type': 'testDone', 'testID': 2, 'result': 'success', 'skipped': True, 'hidden': False},
        {'type': 'done', 'success': False},
    )
    assert runner.collect_flutter(stream, output) == 1
    report = json.loads(output.read_text())
    assert report['counts'] == {'total': 2, 'failed': 1, 'skipped': 1}
    assert report['cases'][0]['id'] == 'test/receipt_test.dart::frozen receipt € retains payment order'
    assert report['framework'] == []
    assert 'DO_NOT_PUBLISH' not in output.read_text()
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_flutter_success_and_truncated_stream_are_distinguished(tmp_path):
    events = [
        {'type': 'testStart', 'test': {'id': 1, 'suiteID': 0, 'name': 'saves receipt'}},
        {'type': 'testDone', 'testID': 1, 'result': 'success', 'hidden': False},
    ]
    assert runner.collect_flutter(_events(*events), tmp_path / 'partial.json') == 1
    assert json.loads((tmp_path / 'partial.json').read_text())['status'] == 'incomplete'
    assert runner.collect_flutter(_events(*events, {'type': 'done', 'success': True}), tmp_path / 'full.json') == 0
    assert json.loads((tmp_path / 'full.json').read_text())['counts'] == {'total': 1, 'passed': 1}


def test_flutter_failed_hidden_loader_remains_visible_as_framework_error(tmp_path):
    assert runner.collect_flutter(_events(
        {'type': 'testStart', 'test': {'id': 0, 'suiteID': 0, 'name': 'loading failing test'}},
        {'type': 'testDone', 'testID': 0, 'result': 'error', 'hidden': True},
        {'type': 'done', 'success': False},
    ), tmp_path / 'flutter.json') == 1
    report = json.loads((tmp_path / 'flutter.json').read_text())
    assert report['framework'][0]['status'] == 'error'
    assert report['counts']['total'] == 0


def test_junit_sanitization_preserves_every_outcome_and_removes_sensitive_fields(tmp_path):
    source = tmp_path / 'raw.xml'
    source.write_text('''<testsuites><testsuite name="DO_NOT_PUBLISH">
      <properties><property name="password" value="DO_NOT_PUBLISH"/></properties>
      <testcase classname="test_auth" name="own_password" time="0.1"><system-out>DO_NOT_PUBLISH</system-out></testcase>
      <testcase classname="test_auth" name="rejects_guest"><failure message="DO_NOT_PUBLISH" type="DO_NOT_PUBLISH">Cookie DO_NOT_PUBLISH</failure></testcase>
      <testcase classname="test_auth" name="rejects_expired"><error message="DO_NOT_PUBLISH">request DO_NOT_PUBLISH</error></testcase>
      <testcase classname="test_auth" name="physical_device"><skipped message="DO_NOT_PUBLISH"/></testcase>
    </testsuite></testsuites>''')
    report = runner.sanitize_pytest_junit(source, tmp_path / 'safe.xml', tmp_path / 'safe.json')
    assert report['counts'] == {'total': 4, 'error': 1, 'failed': 1, 'passed': 1, 'skipped': 1}
    assert report['cases'][0]['id'] == 'test_auth::own_password'
    for name in ['safe.xml', 'safe.json']:
        assert 'DO_NOT_PUBLISH' not in (tmp_path / name).read_text()
        assert stat.S_IMODE((tmp_path / name).stat().st_mode) == 0o600
    root = ET.parse(tmp_path / 'safe.xml').getroot()
    assert len(list(root.iter('testcase'))) == 4
    assert root.find('.//system-out') is None
    assert root.find('.//properties') is None


def test_private_raw_junit_is_removed_and_pytest_failure_exit_is_preserved(tmp_path, monkeypatch):
    seen = []

    def fake_run(command, check):
        assert command[1:4] == ['-m', 'pytest', '-q']
        raw = Path(next(arg.split('=', 1)[1] for arg in command if arg.startswith('--junitxml=')))
        seen.append(raw)
        assert not raw.is_relative_to(tmp_path / 'results')
        assert stat.S_IMODE(raw.parent.stat().st_mode) == 0o700
        raw.write_text('<testsuites><testsuite><testcase classname="test_one" name="passes"/></testsuite></testsuites>')
        return SimpleNamespace(returncode=2)

    monkeypatch.setattr(runner.subprocess, 'run', fake_run)
    assert runner.run_unit_tests(tmp_path / 'tests', tmp_path / 'results') == 2
    assert not seen[0].exists()
    assert not seen[0].parent.exists()
    assert (tmp_path / 'results' / 'backend.xml').exists()


def test_inventory_records_parent_and_subtest_statuses_without_values():
    class Outcomes(unittest.TestCase):
        def runTest(self):
            with self.subTest(password='DO_NOT_PUBLISH'):
                self.assertTrue(True)
            with self.subTest(cookie='DO_NOT_PUBLISH'):
                self.fail('DO_NOT_PUBLISH')
            with self.subTest(body={'token': 'DO_NOT_PUBLISH'}):
                raise RuntimeError('DO_NOT_PUBLISH')
            with self.subTest(login='DO_NOT_PUBLISH'):
                self.skipTest('DO_NOT_PUBLISH')

    test = Outcomes()
    result = runner.InventoryResult(known_tests=[test])
    unittest.TestSuite([test]).run(result)
    report = result.report()
    assert report['tests_run'] == 1
    assert report['counts'] == {'total': 1, 'error': 1}
    assert report['subtest_counts'] == {'total': 4, 'error': 1, 'failed': 1, 'passed': 1, 'skipped': 1}
    assert report['cases'][0]['id'] == test.id()
    assert report['cases'][0]['subtests'][1]['id'] == f'{test.id()}::subtest[2]'
    assert 'DO_NOT_PUBLISH' not in json.dumps(report)


def test_inventory_keeps_unstarted_and_interrupted_cases_out_of_passed_count():
    class Interrupted(unittest.TestCase):
        def runTest(self):
            with self.subTest(label='first'):
                self.assertTrue(True)
            raise KeyboardInterrupt()

    class Unstarted(unittest.TestCase):
        def runTest(self):
            pass

    tests = [Interrupted(), Unstarted()]
    result = runner.InventoryResult(known_tests=tests)
    with pytest.raises(KeyboardInterrupt):
        unittest.TestSuite(tests).run(result)
    report = result.report(interrupted=True)
    assert report['status'] == 'incomplete'
    assert report['counts'] == {'total': 2, 'incomplete': 1, 'not_run': 1}


def test_skipped_subtest_parent_success_is_counted_without_reason():
    class SkippedSubtest(unittest.TestCase):
        def runTest(self):
            with self.subTest(password='DO_NOT_PUBLISH'):
                self.skipTest('DO_NOT_PUBLISH')
            with self.subTest(number=2):
                self.assertTrue(True)

    result = runner.InventoryResult()
    unittest.TestSuite([SkippedSubtest()]).run(result)
    report = result.report()
    assert report['status'] == 'passed'
    assert report['counts'] == {'total': 1, 'passed': 1}
    assert report['subtest_counts'] == {'total': 2, 'passed': 1, 'skipped': 1}
    assert 'DO_NOT_PUBLISH' not in json.dumps(report)


def test_aggregation_respects_process_exit_and_unrequested_integration(tmp_path):
    runner.initialize_results(tmp_path)
    for suite in runner.SUITES:
        runner.write_report(tmp_path / f'{suite}.json', runner._report(suite, [{'id': 'case', 'status': 'passed'}], 'passed'))
    assert runner.summarize_results(tmp_path, ['1', '0', 'not_run']) == 0
    report = json.loads((tmp_path / 'summary.json').read_text())
    assert report['status'] == 'failed'
    assert report['suites']['flutter']['status'] == 'failed'
    assert report['suites']['backend']['status'] == 'passed'
    assert report['suites']['integration']['status'] == 'not_run'
    assert report['counts'] == {'passed': 2, 'total': 2}
    assert json.loads((tmp_path / 'integration.json').read_text())['cases'] == []


def test_zero_exit_without_completed_report_cannot_be_reported_as_pass(tmp_path):
    runner.initialize_results(tmp_path)
    runner.summarize_results(tmp_path, ['0', '0', '0'])
    report = json.loads((tmp_path / 'summary.json').read_text())
    assert report['status'] == 'failed'
    assert all(suite['status'] == 'failed' for suite in report['suites'].values())


def test_initializing_new_run_replaces_prior_successes_but_preserves_other_files(tmp_path):
    (tmp_path / 'notes.txt').write_text('retain me')
    (tmp_path / 'flutter.json').write_text('{"status":"passed"}')
    runner.initialize_results(tmp_path)
    assert json.loads((tmp_path / 'flutter.json').read_text())['status'] == 'not_run'
    assert json.loads((tmp_path / 'summary.json').read_text())['status'] == 'running'
    assert (tmp_path / 'notes.txt').read_text() == 'retain me'


@pytest.mark.parametrize('structured', [False, True])
def test_combined_trigger_runs_every_suite_and_preserves_pipeline_failures(tmp_path, structured):
    import subprocess
    import sys

    frontend = Path(__file__).resolve().parents[4] / 'XIRJA-frontend-marnisi'
    shell_runner = frontend / 'run_all_tests.sh'
    assert shell_runner.exists()
    bench = tmp_path / 'bench with spaces'
    backend = bench / 'apps' / 'backend'
    backend.mkdir(parents=True)
    binary = bench / 'env' / 'bin'
    binary.mkdir(parents=True)
    record = tmp_path / 'commands.jsonl'
    fake_python = binary / 'python'
    fake_python.write_text(f'''#!{sys.executable}
import json, os, sys
args = sys.argv[1:]
with open(os.environ['TEST_COMMAND_RECORD'], 'a') as handle:
    handle.write(json.dumps(args) + '\\n')
if '--collect-flutter-results' in args:
    sys.stdin.read()
if '--run-unit-tests' in args or args[:2] == ['-m', 'pytest']:
    sys.exit(2)
if '--site' in args:
    sys.exit(3)
sys.exit(0)
''')
    fake_python.chmod(0o700)
    flutter = binary / 'flutter'
    flutter.write_text('#!/bin/sh\nexit 1\n')
    flutter.chmod(0o700)
    env = dict(os.environ, PATH=f'{binary}{os.pathsep}{os.environ["PATH"]}',
               MARNISI_BACKEND_PATH=str(backend), MARNISI_TEST_SITE='marnisi-fiscal-test.localhost',
               TEST_COMMAND_RECORD=str(record))
    env.pop('MARNISI_TEST_RESULTS_DIR', None)
    if structured:
        env['MARNISI_TEST_RESULTS_DIR'] = str(tmp_path / 'results with spaces')
    process = subprocess.run(['bash', str(shell_runner)], env=env, capture_output=True, text=True)
    commands = [json.loads(line) for line in record.read_text().splitlines()]
    assert process.returncode == 1
    assert 'FAIL: Flutter complete regression suite (exit 1)' in process.stderr
    assert 'FAIL: Marnisi backend complete unit suite (exit 2)' in process.stderr
    assert 'FAIL: Disposable Frappe database and HTTP acceptance (exit 3)' in process.stderr
    assert any('--site' in command for command in commands)
    assert any('--run-unit-tests' in command or command[:2] == ['-m', 'pytest'] for command in commands)
    if structured:
        summary = next(command for command in commands if '--summarize-results' in command)
        assert summary[-3:] == ['1', '2', '3']
    else:
        assert not any('--summarize-results' in command for command in commands)
