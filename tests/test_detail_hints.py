"""Missing-detail hints: the worker's counts, sidebar text and diagnostics."""
import json
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import patch

import agent_info as info
import detail_hints as hints
import fallback
import portable
import sidebar_settings as prefs
import space_colors
from setup_claude import HOOK_MARKER

SID = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'


def statusline(root, marker=True):
    # Quoted as hook_command quotes it, so Windows backslashes survive shlex.split.
    command = ('env ' + (HOOK_MARKER + ' ' if marker else '') + shlex.quote('HERDR_PLUGIN_STATE_DIR=' + str(root))
               + ' /bin/sh python.sh agent_info.py claude-statusline')
    return json.dumps({'statusLine': {'type': 'command', 'command': command}})


class HookTests(unittest.TestCase):
    def test_detects_only_this_installations_hook(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertTrue(hints.hook_installed(statusline(root), root))
            self.assertFalse(hints.hook_installed(statusline(root, marker=False), root))
            self.assertFalse(hints.hook_installed(statusline(root + '/other'), root))
            for text in ('{}', 'not json', '{"statusLine": "cat"}', '{"statusLine": {"command": "\'"}}'):
                self.assertFalse(hints.hook_installed(text, root))

    def test_check_rereads_after_the_settings_file_changes(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'settings.json'
            check = hints.HookCheck(path, root)
            self.assertFalse(check.installed())
            path.write_text(statusline(root))
            self.assertTrue(check.installed())
            path.write_text('{}')
            self.assertFalse(check.installed())


    def test_unreadable_settings_count_as_no_hook_and_are_retried(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'settings.json'
            path.mkdir()
            check = hints.HookCheck(path, root)
            self.assertFalse(check.installed())
            path.rmdir()
            path.write_bytes(b'\xff\xfe not utf-8')
            self.assertFalse(check.installed())
            path.write_text(statusline(root))
            self.assertTrue(check.installed())


class IssueTests(unittest.TestCase):
    def test_each_gap_names_its_fix(self):
        found = hints.issues({'missing_session': {'claude': 2, 'codex': 0}, 'missing_context': 1,
                              'integrations': {'claude': False}})
        self.assertEqual(len(found), 2)
        self.assertIn("2 Claude agents report no session ID: Herdr's claude integration is not installed", found[0])
        self.assertIn('herdr integration install claude', found[0])
        self.assertIn('1 Claude agent shows no context', found[1])
        self.assertIn('status-line hook', found[1])

    def test_installed_integration_means_restart_not_install(self):
        found = hints.issues({'missing_session': {'codex': 1}, 'integrations': {'codex': True}})
        self.assertEqual(found, ['1 Codex agent reports no session ID, so details are blank. '
                                 "Restart or resume it to pick up Herdr's codex integration."])
        self.assertIn('may be missing', hints.issues({'missing_session': {'codex': 1}})[0])
        self.assertEqual(hints.session_hint('codex', {'codex': True}), hints.RESTART_HINT)
        self.assertEqual(hints.session_hint('codex', {}), hints.SESSION_HINT)

    def test_unreadable_sessions_and_reader_errors_are_reported(self):
        found = hints.issues({'unreadable_session': {'pi': 2}, 'observe_errors': {'codex': 'KeyError'}})
        self.assertEqual(len(found), 2)
        self.assertIn('2 pi agents report a session this plugin will not read', found[0])
        self.assertIn('Reading Codex details failed with KeyError', found[1])
        self.assertIn('doctor', found[1])

    def test_unsupported_hook_is_not_suggested(self):
        found = hints.issues({'missing_context': 1, 'claude_hook_supported': False})
        self.assertEqual(len(found), 1)
        self.assertNotIn('Run setup', found[0])
        self.assertIn('not available on Windows', found[0])

    def test_installed_hook_or_complete_details_raise_nothing(self):
        self.assertEqual(hints.issues({'missing_context': 3, 'claude_hook': True}), [])
        self.assertEqual(hints.issues({}), [])

    def test_hint_text_stays_within_the_api_limit(self):
        self.assertEqual(hints.with_hint('Claude', 'x'), 'Claude · x')
        self.assertEqual(hints.with_hint(None, 'x'), 'x')
        self.assertEqual(len(hints.with_hint('y' * 90, 'x')), 80)


class IntegrationStatusTests(unittest.TestCase):
    @unittest.skipIf(portable.WINDOWS, 'the stand-in herdr binary is a /bin/sh script')
    def test_parses_status_and_degrades_to_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'herdr'
            binary.write_text('#!/bin/sh\nprintf "claude: not installed (/x)\\ncodex: current (v8) (/y)\\n"\n')
            binary.chmod(0o755)
            self.assertEqual(hints.integration_status(str(binary)), {'claude': False, 'codex': True})
            binary.write_text('#!/bin/sh\nexit 3\n')
            self.assertEqual(hints.integration_status(str(binary)), {})
            self.assertEqual(hints.integration_status(str(Path(directory) / 'missing')), {})

    def test_check_is_cached_between_syncs(self):
        check = hints.IntegrationCheck('herdr')
        with patch.object(hints, 'integration_status', side_effect=[{'codex': False}, {'codex': True}]) as status:
            self.assertEqual(check.installed(now=0), {'codex': False})
            self.assertEqual(check.installed(now=30), {'codex': False})
            self.assertEqual(check.installed(now=hints.INTEGRATION_SECONDS), {'codex': True})
        self.assertEqual(status.call_count, 2)


class CollectorTests(unittest.TestCase):
    def collect(self, agents, settings=None, hook=False, integrations=None):
        with tempfile.TemporaryDirectory() as directory:
            collector = info.Collector('/test.sock', Path(directory))
            values = prefs.DEFAULTS | {'collect_details': True} | (settings or {})
            collector.settings.load = lambda: values
            collector.hook.installed = lambda: hook
            collector.integrations.installed = lambda: integrations or {}
            calls = []
            def rpc(endpoint, method, params=None):
                calls.append((method, params))
                return {'agents': agents} if method == 'agent.list' else {}
            with patch.object(info, 'rpc', rpc), patch.object(fallback, 'rpc', rpc), \
                    patch.object(collector, 'discover'), patch.object(space_colors, 'sync', autospec=True), \
                    patch.object(collector.space_rows, 'sync'):
                status = collector.sync()
            self.assertFalse({'space_colors_error', 'space_rows_error'} & status.keys())
            reports = {p['pane_id']: p['tokens'] for m, p in calls
                       if m == 'pane.report_metadata' and p.get('source') == info.SOURCE}
            return status, reports

    def test_agents_without_a_session_get_a_hint_and_are_counted(self):
        status, reports = self.collect([{'pane_id': 'w1:p1', 'agent': 'claude'},
                                        {'pane_id': 'w1:p2', 'agent': 'hermes'}])
        self.assertEqual(status['missing_session'], {'claude': 1})
        self.assertEqual(reports['w1:p1']['agent_info'], 'Claude · ' + hints.SESSION_HINT)
        self.assertNotIn('w1:p2', reports)
        status, reports = self.collect([{'pane_id': 'w1:p1', 'agent': 'claude'}], integrations={'claude': True})
        self.assertEqual(reports['w1:p1']['agent_info'], 'Claude · ' + hints.RESTART_HINT)
        self.assertEqual(status['integrations'], {'claude': True})

    def test_claude_without_context_mentions_the_hook_only_when_missing(self):
        agent = {'pane_id': 'w1:p1', 'agent': 'claude', 'agent_session': {'kind': 'id', 'value': SID}}
        status, reports = self.collect([agent])
        self.assertEqual(status['missing_context'], 1)
        self.assertFalse(status['claude_hook'])
        # Windows cannot run the hook, so its rows get no hint to install it.
        self.assertEqual(reports['w1:p1']['agent_info'].endswith(hints.CONTEXT_HINT), not portable.WINDOWS)
        status, reports = self.collect([agent], hook=True)
        self.assertNotIn(hints.CONTEXT_HINT, reports['w1:p1']['agent_info'] or '')

    def test_hints_can_be_turned_off_but_counts_remain(self):
        status, reports = self.collect([{'pane_id': 'w1:p1', 'agent': 'codex'}], {'detail_hints': False})
        self.assertEqual(status['missing_session'], {'codex': 1})
        self.assertNotIn('w1:p1', reports)

    def test_collection_off_reports_no_gaps(self):
        status, reports = self.collect([{'pane_id': 'w1:p1', 'agent': 'claude'}], {'collect_details': False})
        self.assertEqual(status['missing_session'], {})
        self.assertEqual(reports, {})


if __name__ == '__main__':
    unittest.main()
