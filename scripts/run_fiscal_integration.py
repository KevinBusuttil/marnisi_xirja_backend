#!/usr/bin/env python3
"""Guard disposable integration tests and optionally publish sanitized test inventories.

The combined frontend runner is the single test trigger. Result artifacts retain
case names and statuses, never test output, exception messages, subtest values,
request/response bodies, properties, or tracebacks.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET


SUITES = ('flutter', 'backend', 'integration')


def validate_disposable(bench, site):
    if not site.startswith('marnisi-fiscal-test.') or '/' in site or '\\' in site:
        raise ValueError('Use an explicitly provisioned marnisi-fiscal-test.* disposable site')
    config = json.loads((bench / 'sites' / site / 'site_config.json').read_text())
    if config.get('marnisi_disposable_fiscal_test') is not True:
        raise ValueError('Site must carry marnisi_disposable_fiscal_test=true')
    if config.get('db_host') not in {'localhost', '127.0.0.1', '::1'}:
        raise ValueError('Only loopback databases are allowed')
    if not str(config.get('db_name', '')).startswith('marnisi_fiscal_disposable'):
        raise ValueError('Disposable database name must start marnisi_fiscal_disposable')
    if not config.get('pause_scheduler') or not config.get('allow_tests'):
        raise ValueError('Disposable site requires paused scheduler and allow_tests')
    return config


def _write_private(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.marnisi-result-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            handle.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _report(suite, cases, status, **extra):
    counts = Counter(case['status'] for case in cases if case.get('kind') != 'fixture')
    return {
        'schema_version': 1,
        'suite': suite,
        'status': status,
        'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
        'counts': {'total': sum(counts.values()), **dict(sorted(counts.items()))},
        'cases': cases,
        **extra,
    }


def write_report(path, report):
    _write_private(path, json.dumps(report, ensure_ascii=False, indent=2) + '\n')


def initialize_results(directory):
    directory = Path(directory)
    # Replace only this runner's named outputs, so a rerun cannot present stale
    # successes after a command fails before producing its report.
    for suite in SUITES:
        write_report(directory / f'{suite}.json', _report(suite, [], 'not_run'))
    _write_private(directory / 'backend.xml', '<testsuites />\n')
    write_report(directory / 'summary.json', {'schema_version': 1, 'status': 'running'})


def collect_flutter(stream, output):
    suites, tests, completed = {}, {}, False
    successful = False
    for line in stream:
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            # Flutter startup/status output is not part of the machine protocol.
            continue
        if not isinstance(event, dict):
            continue
        event_type = event.get('type')
        if event_type == 'suite':
            suite = event.get('suite', {})
            suites[suite.get('id')] = suite.get('path', '')
        elif event_type == 'testStart':
            test = event.get('test', {})
            tests[test.get('id')] = {
                'name': test.get('name', ''),
                'suite_id': test.get('suiteID'),
                'status': 'incomplete',
                'hidden': False,
            }
        elif event_type == 'testDone' and event.get('testID') in tests:
            test = tests[event['testID']]
            test['hidden'] = bool(event.get('hidden'))
            result = event.get('result')
            test['status'] = ('skipped' if event.get('skipped') else
                              'passed' if result == 'success' else
                              'failed' if result == 'failure' else 'error')
        elif event_type == 'done':
            completed = True
            successful = event.get('success') is True
        # In particular, never retain print/error/log payloads or stack traces.
    cases, framework = [], []
    for test in tests.values():
        file = suites.get(test['suite_id'], '')
        case = {'id': f"{file}::{test['name']}", 'name': test['name'],
                'file': file, 'status': test['status']}
        if test['hidden']:
            if test['status'] not in {'passed', 'skipped'}:
                framework.append(case)
        else:
            cases.append(case)
    complete = completed and bool(cases) and all(case['status'] != 'incomplete' for case in cases)
    status = ('incomplete' if not complete else
              'passed' if successful and not framework and
              all(case['status'] in {'passed', 'skipped'} for case in cases) else 'failed')
    write_report(output, _report('flutter', cases, status, framework=framework))
    print(f"Flutter structured results: {len(cases)} cases, {status}")
    return 0 if status == 'passed' else 1


def sanitize_pytest_junit(source, xml_output, json_output):
    root = ET.parse(source).getroot()
    safe_root = ET.Element('testsuites')
    safe_suite = ET.SubElement(safe_root, 'testsuite', name='Marnisi backend unit tests')
    cases = []
    for node in root.iter('testcase'):
        attributes = {key: node.attrib[key] for key in ('classname', 'name', 'file', 'line', 'time')
                      if key in node.attrib}
        case_node = ET.SubElement(safe_suite, 'testcase', attributes)
        # Prioritize errors if an unusual producer supplies multiple outcomes.
        outcome = next((key for key in ('error', 'failure', 'skipped') if node.find(key) is not None), None)
        status = {'error': 'error', 'failure': 'failed', 'skipped': 'skipped', None: 'passed'}[outcome]
        if outcome:
            ET.SubElement(case_node, outcome, message='Diagnostic details omitted from evidence')
        cases.append({'id': f"{attributes.get('classname', '')}::{attributes.get('name', '')}",
                      'name': attributes.get('name', ''),
                      'class': attributes.get('classname', ''), 'status': status})
    counts = Counter(case['status'] for case in cases)
    safe_suite.set('tests', str(len(cases)))
    safe_suite.set('failures', str(counts['failed']))
    safe_suite.set('errors', str(counts['error']))
    safe_suite.set('skipped', str(counts['skipped']))
    _write_private(xml_output, ET.tostring(safe_root, encoding='unicode') + '\n')
    status = ('incomplete' if not cases else
              'failed' if counts['failed'] or counts['error'] else 'passed')
    report = _report('backend', cases, status)
    write_report(json_output, report)
    return report


def run_unit_tests(tests, directory):
    directory = Path(directory)
    # Raw JUnit may contain captured credentials. It exists only in a private
    # temporary directory outside the artifact folder, and is always removed.
    with tempfile.TemporaryDirectory(prefix='marnisi-private-junit-') as temporary:
        raw = Path(temporary) / 'results.xml'
        previous_mask = os.umask(0o077)
        try:
            code = subprocess.run([sys.executable, '-m', 'pytest', '-q', str(tests), f'--junitxml={raw}'],
                                  check=False).returncode
        finally:
            os.umask(previous_mask)
        try:
            report = sanitize_pytest_junit(raw, directory / 'backend.xml', directory / 'backend.json')
        except (OSError, ET.ParseError):
            report = _report('backend', [], 'incomplete', reason_code='junit_unavailable')
            write_report(directory / 'backend.json', report)
        if code == 0 and report['status'] != 'passed':
            return 1
        return code if code >= 0 else 1


class InventoryResult(unittest.TestResult):
    """Keep stable parent IDs; identify subtests by ordinal, never their values."""

    def __init__(self, stream=None, descriptions=None, verbosity=None, *, known_tests=()):
        super().__init__()
        self.cases = []
        self._cases_by_id = {}
        for test in known_tests:
            self._case(test)['status'] = 'not_run'

    def _exc_info_to_string(self, err, test):
        return 'Diagnostic details omitted from evidence'

    def printErrors(self):
        pass

    def _case(self, test, *, fixture=False):
        identity = test.id()
        if identity not in self._cases_by_id:
            case = {'id': identity, 'name': identity, 'status': 'incomplete', 'subtests': []}
            if fixture:
                case['kind'] = 'fixture'
            self._cases_by_id[identity] = case
            self.cases.append(case)
        return self._cases_by_id[identity]

    def startTest(self, test):
        super().startTest(test)
        self._case(test)['status'] = 'incomplete'

    def stopTest(self, test):
        super().stopTest(test)

    def addSuccess(self, test):
        super().addSuccess(test)
        self._case(test)['status'] = 'passed'

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._case(test)['status'] = 'failed'

    def addError(self, test, err):
        super().addError(test, err)
        self._case(test, fixture=test.id() not in self._cases_by_id)['status'] = 'error'

    def addSkip(self, test, reason):
        # unittest passes _SubTest to addSkip for a skipped subtest; str/id of
        # that object can contain passwords, cookies or whole request bodies.
        parent = getattr(test, 'test_case', None)
        super().addSkip(parent or test, 'Skip reason omitted from evidence')
        if parent is not None:
            self._subtest(parent, 'skipped')
        else:
            self._case(test, fixture=test.id() not in self._cases_by_id)['status'] = 'skipped'

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._case(test)['status'] = 'expected_failure'

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._case(test)['status'] = 'unexpected_success'

    def _subtest(self, test, status):
        case = self._case(test)
        case['subtests'].append({'id': f"{case['id']}::subtest[{len(case['subtests']) + 1}]", 'status': status})
        if status == 'error' or (status == 'failed' and case['status'] != 'error'):
            case['status'] = status

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        status = ('passed' if err is None else
                  'failed' if issubclass(err[0], test.failureException) else 'error')
        self._subtest(test, status)

    def report(self, *, interrupted=False):
        if not interrupted:
            for case in self.cases:
                # A skipped subtest suppresses unittest's parent addSuccess
                # callback even when the other subtests succeed.
                if case['status'] == 'incomplete' and case['subtests']:
                    case['status'] = 'passed'
        subtests = [subtest for case in self.cases for subtest in case['subtests']]
        counts = Counter(subtest['status'] for subtest in subtests)
        return _report('integration', self.cases,
                       'incomplete' if interrupted or any(case['status'] in {'not_run', 'incomplete'} for case in self.cases)
                       else 'passed' if self.wasSuccessful() else 'failed',
                       tests_run=self.testsRun,
                       subtest_counts={'total': len(subtests), **dict(sorted(counts.items()))})


def summarize_results(directory, exits):
    directory = Path(directory)
    summaries = {}
    total_counts = Counter()
    for suite, exit_code in zip(SUITES, exits):
        try:
            report = json.loads((directory / f'{suite}.json').read_text())
        except (ValueError, OSError):
            report = _report(suite, [], 'incomplete', reason_code='report_unavailable')
        if exit_code == 'not_run':
            report = _report(suite, [], 'not_run')
            write_report(directory / f'{suite}.json', report)
            status = 'not_run'
        else:
            status = ('passed' if exit_code == '0' and report.get('status') == 'passed'
                      else 'failed')
        counts = report.get('counts', {})
        total_counts.update(counts)
        summaries[suite] = {'status': status, 'exit_code': None if exit_code == 'not_run' else int(exit_code),
                            'counts': counts, 'artifact': f'{suite}.json'}
        if suite == 'integration':
            summaries[suite]['subtest_counts'] = report.get('subtest_counts', {'total': 0})
    status = ('failed' if any(suite['status'] == 'failed' for suite in summaries.values())
              else 'incomplete' if any(suite['status'] == 'not_run' for suite in summaries.values()) else 'passed')
    write_report(directory / 'summary.json', {
        'schema_version': 1, 'status': status,
        'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
        'counts': dict(sorted(total_counts.items())), 'suites': summaries,
        'diagnostics_policy': 'Names and outcomes only; diagnostic messages, output and subtest values omitted.',
    })
    return 0


def iter_test_cases(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from iter_test_cases(test)
        else:
            yield test


def run_integration(bench, site, results_directory=None):
    bench = bench.resolve()
    output = Path(results_directory) / 'integration.json' if results_directory else None
    try:
        validate_disposable(bench, site)
    except Exception:
        if output is None:
            raise
        write_report(output, _report('integration', [], 'blocked', reason_code='disposable_guard_failed'))
        print('Integration blocked: disposable-site guard failed.', file=sys.stderr)
        return 1
    os.environ['MARNISI_TEST_SITE'] = site
    os.environ['MARNISI_TEST_BENCH'] = str(bench)
    os.chdir(bench / 'sites')
    package = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(package / 'integration'))
    suite = unittest.defaultTestLoader.discover(str(package / 'integration'), pattern='test_*.py')
    if not suite.countTestCases():
        if output is not None:
            write_report(output, _report('integration', [], 'incomplete', reason_code='no_tests_collected'))
            return 1
        raise RuntimeError('No integration tests collected')
    if output is None:
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    else:
        result = InventoryResult(known_tests=iter_test_cases(suite))
        try:
            suite.run(result)
        except BaseException:
            write_report(output, result.report(interrupted=True))
            raise
        report = result.report()
        write_report(output, report)
        print(f'Integration structured results: {result.testsRun} cases, {report["status"]}')
        return 0 if report['status'] == 'passed' else 1
    return 0 if result.wasSuccessful() else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bench', type=Path)
    parser.add_argument('--site')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--collect-flutter-results', type=Path)
    modes.add_argument('--run-unit-tests', type=Path)
    modes.add_argument('--initialize-results', type=Path)
    modes.add_argument('--summarize-results', type=Path)
    parser.add_argument('--results-dir', type=Path, default=os.environ.get('MARNISI_TEST_RESULTS_DIR') or None)
    parser.add_argument('--suite-exits', nargs=3)
    args = parser.parse_args()
    if args.collect_flutter_results is not None:
        return collect_flutter(sys.stdin, args.collect_flutter_results)
    if args.initialize_results is not None:
        initialize_results(args.initialize_results)
        return 0
    if args.run_unit_tests is not None:
        if args.results_dir is None:
            parser.error('--run-unit-tests requires --results-dir')
        return run_unit_tests(args.run_unit_tests, args.results_dir)
    if args.summarize_results is not None:
        if args.suite_exits is None or any(value != 'not_run' and not value.isdigit() for value in args.suite_exits):
            parser.error('--summarize-results requires three numeric/not_run --suite-exits')
        return summarize_results(args.summarize_results, args.suite_exits)
    if args.bench is None or args.site is None:
        parser.error('integration requires --bench and --site')
    # Resolve before the disposable runner changes its current directory.
    directory = args.results_dir.resolve() if args.results_dir is not None else None
    return run_integration(args.bench, args.site, directory)


if __name__ == '__main__':
    sys.exit(main())
