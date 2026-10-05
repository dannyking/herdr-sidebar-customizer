"""Exercise public setup/apply/restore transactions against real files."""
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import tomllib
import unittest
from unittest.mock import patch

import installation as install
import manage
import portable
import runtime
import setup_claude
import sidebar_layout
import sidebar_settings as prefs
from config_edit import owned_values

CONFIG = '''# personal config
[ui]
sidebar_max_width = 50
[ui.sidebar.spaces]
rows = [["workspace"]] # original rows
row_gap = 1
[ui.sidebar.agents]
rows = [["agent"]]
[theme.custom]
accent = "#112233"
'''
posix_hook = unittest.skipIf(portable.WINDOWS, 'the Claude status-line hook is POSIX-only and refused on Windows')


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='sidebar test ')))
        self.target = self.root / 'config with spaces.toml'
        self.directory = self.root / 'runtime' / 'endpoint'
        self.claude = self.root / 'claude' / 'settings.json'
        self.enterContext(patch.dict(os.environ, {
            'HERDR_CONFIG_PATH': str(self.target),
            'HERDR_PLUGIN_CONFIG_DIR': str(self.root / 'preferences'),
            'HERDR_PLUGIN_STATE_DIR': str(self.root / 'runtime'),
            'CLAUDE_CONFIG_DIR': str(self.claude.parent),
        }))
        self.enterContext(patch.object(prefs, 'check_config', side_effect=tomllib.loads))
        self.enterContext(patch.object(prefs, 'default_keys', return_value={
            'prefix': 'ctrl+b', 'goto': 'prefix+g', 'new_tab': 'prefix+c'}))
        self.api = self.enterContext(patch.object(install, 'rpc', side_effect=self.rpc))
        self.enterContext(patch.object(install, 'wait_for_workers'))

    @staticmethod
    def rpc(endpoint, method, params=None):
        if method == 'workspace.list': return {'workspaces': [{'workspace_id': 'w1'}]}
        if method == 'agent.list': return {'agents': []}
        return {'status': 'applied', 'diagnostics': []}

    def save(self, settings=None, setup=None, hook=False, **kwargs):
        return install.save('test', self.directory, settings or prefs.read_settings(),
                            kwargs.pop('expected', prefs.read_settings()),
                            setup_mode=setup, claude_hook=hook, **kwargs)

    def test_missing_empty_and_custom_config_round_trip_and_idempotence(self):
        for original in (None, '', CONFIG):
            with self.subTest(original=original):
                if original is not None: self.target.write_text(original)
                else: self.target.unlink(missing_ok=True)
                self.save(setup='managed')
                first = self.target.read_text()
                self.save(setup='managed')
                self.assertEqual(self.target.read_text(), first)
                self.save(prefs.read_settings() | {'animation': '20', 'spaces_gap': 0})
                self.assertEqual(install.restore('test'), [])
                self.assertEqual(install.read_text(self.target), original)
                self.assertEqual(prefs.read_settings()['animation'], '20')
                self.assertEqual(install.restore('test'), [])

    def test_later_unrelated_edits_survive_restore(self):
        self.target.write_text(CONFIG)
        self.save(setup='managed')
        self.target.write_text(self.target.read_text().replace('#112233', '#445566'))
        install.restore('test')
        self.assertEqual(tomllib.loads(self.target.read_text()), tomllib.loads(CONFIG.replace('#112233', '#445566')))
        self.assertIn('# original rows', self.target.read_text())

    def test_unrelated_edits_before_a_later_apply_survive_restore(self):
        self.target.write_text(CONFIG)
        self.save(setup='managed')
        edited = self.target.read_text().replace('#112233', '#445566') + '[ui.toast]\nenabled = false\n'
        self.target.write_text(edited)
        self.save(prefs.read_settings() | {'animation': '20'})
        self.assertEqual(install.restore('test'), [])
        expected = tomllib.loads(CONFIG.replace('#112233', '#445566') + '[ui.toast]\nenabled = false\n')
        self.assertEqual(tomllib.loads(self.target.read_text()), expected)
        self.assertIn('# original rows', self.target.read_text())

    def test_comments_after_plugin_shortcuts_survive_restore(self):
        self.target.write_text(CONFIG)
        self.save(prefs.DEFAULTS | {'shortcut_spacing': 'prefix+shift+x'}, setup='managed')
        self.assertIn('[[keys.command]]', self.target.read_text())
        notes = '# notes about the next table\n[extra]\nvalue = 1\n# commented-out block at the end\n'
        self.target.write_text(self.target.read_text() + '\n' + notes)
        self.assertEqual(install.restore('test'), [])
        restored = self.target.read_text()
        self.assertIn('# notes about the next table\n[extra]', restored)
        self.assertTrue(restored.endswith('# commented-out block at the end\n'))
        self.assertNotIn('[[keys.command]]', restored)

    def test_indented_row_gap_is_restored(self):
        indented = CONFIG.replace('row_gap = 1', '  row_gap = 1')
        self.target.write_text(indented)
        self.save(setup='managed')
        self.assertIn('  row_gap = 0', self.target.read_text())
        self.target.write_text(self.target.read_text().replace('#112233', '#445566'))
        self.assertEqual(install.restore('test'), [])
        restored = self.target.read_text()
        self.assertIn('\n  row_gap = 1\n', restored)
        self.assertEqual(tomllib.loads(restored), tomllib.loads(indented.replace('#112233', '#445566')))

    def test_owned_external_edits_are_refused_on_apply_and_preserved_on_restore(self):
        self.target.write_text(CONFIG)
        self.save(setup='managed')
        applied = self.target.read_text()
        self.target.write_text(applied.replace('row_gap = 0', 'row_gap = 2'))
        with self.assertRaisesRegex(RuntimeError, 'outside the plugin'):
            self.save()
        conflicts = install.restore('test')
        self.assertIn('spaces.row_gap', conflicts)
        self.assertEqual(tomllib.loads(self.target.read_text())['ui']['sidebar']['spaces']['row_gap'], 2)

    def test_manual_mode_never_writes_layout_and_rejects_spacing_shortcut(self):
        self.target.write_text(CONFIG)
        self.save(setup='manual')
        self.save(prefs.read_settings() | {'space_names': False})
        self.assertEqual(self.target.read_text(), CONFIG)
        with self.assertRaisesRegex(RuntimeError, 'managed layout'):
            sidebar_layout.toggle('test', self.directory)
        install.restore('test')
        self.assertEqual(self.target.read_text(), CONFIG)

    def test_native_conflict_fails_before_writes(self):
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            self.save(prefs.DEFAULTS | {'shortcut_spacing': 'prefix+g'}, setup='managed')
        self.assertFalse(self.target.exists())
        self.assertFalse(runtime.installation_path().exists())

    @posix_hook
    def test_failed_reload_rolls_back_hook_preferences_palette_and_record(self):
        self.target.write_text(CONFIG)
        self.claude.parent.mkdir()
        original_hook = '{"statusLine":{"type":"command","command":"printf original"}}'
        self.claude.write_text(original_hook)
        self.api.side_effect = lambda _, method, params=None: (
            self.rpc(_, method, params) if method != 'server.reload_config'
            else {'status': 'rejected', 'diagnostics': ['invalid']})
        with self.assertRaisesRegex(RuntimeError, 'rolled back'):
            self.save(setup='managed', hook=True,
                      colour_changes={'w1': 'blue_dark'}, expected_colours={'w1': None})
        self.assertEqual(self.target.read_text(), CONFIG)
        self.assertEqual(self.claude.read_text(), original_hook)
        for path in (prefs.preferences_path(), runtime.installation_path(),
                     self.directory / 'space-colors.json', runtime.state_root() / 'previous-statusline.json'):
            self.assertFalse(path.exists(), path.name)

    def test_invalid_hook_and_invalid_config_write_nothing(self):
        self.claude.parent.mkdir()
        self.claude.write_text('{broken')
        with self.assertRaises(ValueError): self.save(setup='managed', hook=True)
        self.assertFalse(self.target.exists())
        with patch.object(prefs, 'check_config', side_effect=ValueError('invalid config')):
            with self.assertRaises(ValueError): self.save(setup='managed')
        self.assertFalse(prefs.preferences_path().exists())

    @posix_hook
    def test_malformed_claude_settings_do_not_block_restore(self):
        self.target.write_text(CONFIG)
        self.claude.parent.mkdir()
        self.claude.write_text('{"statusLine":{"type":"command","command":"printf original"}}')
        self.save(setup='managed', hook=True)
        hooked = self.claude.read_text()
        self.claude.write_text(hooked.rstrip() + ',')
        self.assertEqual(install.restore('test'), [install.CLAUDE_CONFLICT])
        self.assertEqual(self.target.read_text(), CONFIG)
        self.assertEqual(self.claude.read_text(), hooked.rstrip() + ',')
        self.claude.write_text(hooked)
        self.assertEqual(install.restore('test'), [])
        self.assertEqual(json.loads(self.claude.read_text())['statusLine']['command'], 'printf original')

    def test_claude_settings_without_our_hook_are_not_parsed_on_restore(self):
        self.claude.parent.mkdir()
        for text in ('{broken', '[]'):
            with self.subTest(text=text):
                self.claude.write_text(text)
                self.assertEqual(setup_claude.prepare(self.claude, runtime.state_root(), remove=True), [])
        # Installing parses the settings, except on Windows, which refuses the hook first.
        with self.assertRaisesRegex(ValueError, 'Windows' if portable.WINDOWS else 'JSON object'):
            setup_claude.prepare(self.claude, runtime.state_root())

    @posix_hook
    def test_symlinked_claude_settings_and_preferences_stay_linked(self):
        dotfiles = self.root / 'dotfiles'
        dotfiles.mkdir()
        real_settings = dotfiles / 'settings.json'
        real_settings.write_text('{"statusLine":{"type":"command","command":"printf original"}}')
        self.claude.parent.mkdir()
        self.claude.symlink_to(real_settings)
        real_preferences = dotfiles / 'preferences.json'
        real_preferences.write_text(json.dumps(prefs.DEFAULTS))
        prefs.preferences_path().parent.mkdir(parents=True)
        prefs.preferences_path().symlink_to(real_preferences)
        self.save(prefs.read_settings() | {'animation': '20'}, setup='managed', hook=True)
        self.assertTrue(self.claude.is_symlink())
        self.assertTrue(prefs.preferences_path().is_symlink())
        self.assertIn(setup_claude.HOOK_MARKER, real_settings.read_text())
        self.assertEqual(json.loads(real_preferences.read_text())['animation'], '20')
        install.restore('test')
        self.assertTrue(self.claude.is_symlink())
        self.assertIn('printf original', real_settings.read_text())

    def test_settings_and_palette_concurrent_edits_preserved(self):
        self.save(setup='managed')
        old = prefs.read_settings()
        self.save(old | {'animation': '04'})
        with self.assertRaisesRegex(RuntimeError, 'changed elsewhere'):
            self.save(old, expected=old)
        self.directory.joinpath('space-colors.json').write_text('{"w1":"teal_dark"}')
        with self.assertRaisesRegex(RuntimeError, 'color changed elsewhere'):
            self.save(colour_changes={'w1': 'blue_dark'}, expected_colours={'w1': 'red_light'})
        self.assertEqual(json.loads(self.directory.joinpath('space-colors.json').read_text()), {'w1': 'teal_dark'})

    def test_spacing_uses_same_transaction_and_rolls_back(self):
        self.save(prefs.DEFAULTS | {'spaces_gap': 0, 'agents_gap': 0}, setup='managed')
        self.assertEqual(sidebar_layout.toggle('test', self.directory), 1)
        before = self.target.read_text()
        self.api.side_effect = None
        self.api.return_value = {'status': 'rejected'}
        with self.assertRaises(RuntimeError): sidebar_layout.toggle('test', self.directory)
        self.assertEqual(self.target.read_text(), before)
        self.assertEqual(prefs.read_settings()['spaces_gap'], 1)

    def test_concurrent_file_edit_survives_transaction_rollback(self):
        one, two = self.root / 'one', self.root / 'two'
        one.write_text('original'); two.write_text('another editor')
        with self.assertRaises(RuntimeError):
            install.transaction([(one, 'original', 'ours'), (two, None, 'ours')], 'test', False)
        self.assertEqual(one.read_text(), 'original')
        self.assertEqual(two.read_text(), 'another editor')

    def test_rollback_stops_at_an_edited_file_and_keeps_earlier_writes(self):
        record, one, two = self.root / 'record', self.root / 'one', self.root / 'two'
        one.write_text('a')
        two.write_text('c')

        def reload(_, method, params=None):
            one.write_text('edited meanwhile')
            return {'status': 'rejected'}
        self.api.side_effect = reload
        with self.assertRaisesRegex(RuntimeError, 'Could not undo'):
            install.transaction([(record, None, 'r'), (one, 'a', 'b'), (two, 'c', 'd')], 'test', True)
        self.assertEqual(record.read_text(), 'r')
        self.assertEqual(one.read_text(), 'edited meanwhile')
        self.assertEqual(two.read_text(), 'c')

    def test_interrupt_rolls_back(self):
        one = self.root / 'one'
        one.write_text('a')
        self.api.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            install.transaction([(one, 'a', 'b')], 'test', True)
        self.assertEqual(one.read_text(), 'a')

    def test_setup_writes_the_record_before_the_config(self):
        self.target.write_text(CONFIG)
        written = []
        real_replace = install.replace

        def tracking(path, before, after):
            written.append(Path(path).resolve())
            real_replace(path, before, after)
        with patch.object(install, 'replace', side_effect=tracking):
            self.save(setup='managed')
        # Resolve both sides: macOS temp paths under /var are /private/var.
        self.assertLess(written.index(runtime.installation_path().resolve()),
                        written.index(self.target.resolve()))

    def test_configure_refuses_claude_hook_on_windows_before_writing(self):
        with patch.object(manage.portable, 'WINDOWS', True), \
                patch.object(manage.runtime, 'check_prerequisites') as check:
            with self.assertRaisesRegex(ValueError, 'Windows'):
                manage.configure('test', 'managed', True, claude_hook=True)
            check.assert_not_called()
        self.assertFalse(runtime.installation_path().exists())

    def test_configure_without_collect_flag_keeps_saved_choice(self):
        self.save(prefs.DEFAULTS | {'collect_details': True}, setup='managed')
        with patch.object(manage.runtime, 'check_prerequisites'), \
                patch.object(manage.installation, 'configure') as configure:
            manage.configure('test', 'managed', None)
            self.assertTrue(configure.call_args.args[1]['collect_details'])
            manage.configure('test', 'managed', False)
            self.assertFalse(configure.call_args.args[1]['collect_details'])

    def test_setup_consent_names_every_detail_source(self):
        for name in ('Claude', 'Codex', 'OpenCode', 'pi', 'omp'):
            self.assertIn(name, manage.CONSENT)

    @posix_hook
    def test_relocated_hook_updates_without_chaining_itself(self):
        self.claude.parent.mkdir()
        self.claude.write_text('{"statusLine":{"type":"command","command":"printf original"}}')
        setup_claude.install(self.claude, runtime.state_root())
        original = (runtime.state_root() / 'previous-statusline.json').read_text()
        with patch.object(setup_claude, '__file__', str(self.root / 'new checkout/src/setup_claude.py')):
            setup_claude.install(self.claude, runtime.state_root())
        self.assertEqual((runtime.state_root() / 'previous-statusline.json').read_text(), original)
        self.assertIn('new checkout', self.claude.read_text())
        setup_claude.install(self.claude, runtime.state_root(), remove=True)
        self.assertEqual(json.loads(self.claude.read_text())['statusLine']['command'], 'printf original')

    def test_restore_retries_worker_shutdown_when_already_disabled(self):
        self.save(setup='managed')
        with patch.object(install, 'wait_for_workers', side_effect=RuntimeError('stopping')):
            with self.assertRaisesRegex(RuntimeError, 'stopping'): install.restore('test')
        self.assertFalse(install.active())
        with patch.object(install, 'wait_for_workers') as wait:
            install.restore('test')
            wait.assert_called_once()

    def test_two_processes_cannot_overwrite_the_same_preferences(self):
        self.save(setup='manual')
        script = """
import sys
from pathlib import Path
import installation
import sidebar_settings as prefs
try:
    installation.save('test', Path(sys.argv[1]), prefs.DEFAULTS | {'animation': sys.argv[2]}, prefs.DEFAULTS)
except RuntimeError as error:
    if 'changed elsewhere' not in str(error): raise
    raise SystemExit(3)
"""
        processes = [subprocess.Popen([sys.executable, '-c', script, str(self.directory), animation])
                     for animation in ('03', '04')]
        self.assertEqual(sorted(p.wait(timeout=10) for p in processes), [0, 3])
        self.assertIn(prefs.read_settings()['animation'], ('03', '04'))

    def test_different_config_targets_require_separate_preferences(self):
        self.save(setup='managed')
        other = self.root / 'other.toml'
        with patch.dict(os.environ, {'HERDR_CONFIG_PATH': str(other)}):
            with self.assertRaisesRegex(RuntimeError, 'Another config'):
                self.save(setup='managed')
        self.assertFalse(other.exists())

    def test_restore_preserves_a_later_deleted_config(self):
        self.save(setup='managed')
        self.target.unlink()
        self.assertIn('config deleted after setup', install.restore('test'))
        self.assertFalse(self.target.exists())

    def test_restore_clears_and_reloads_other_sessions_using_the_config(self):
        self.save(setup='managed')
        self.directory.joinpath('config-path').write_text(str(runtime.config_path()))
        self.directory.joinpath('endpoint').write_text('other-socket')
        with patch.object(install, 'clear_metadata') as clear:
            install.restore('test')
            self.assertEqual([c.args[0] for c in clear.call_args_list], ['test', 'other-socket'])
        self.assertIn(('other-socket', 'server.reload_config'), [c.args for c in self.api.call_args_list])

    def test_restore_removes_status_line_captures_only(self):
        self.save(setup='managed')
        self.directory.mkdir(parents=True, exist_ok=True)
        self.directory.joinpath('config-path').write_text(str(runtime.config_path()))
        capture = self.directory / '0123456789abcdef01234567.json'
        capture.write_text('{}')
        status = self.directory / 'status.json'
        status.write_text('{}')
        install.restore('test')
        self.assertFalse(capture.exists())
        self.assertTrue(status.exists())

    @posix_hook
    def test_hook_in_another_state_directory_is_not_removed_or_wrapped(self):
        self.claude.parent.mkdir()
        self.claude.write_text('{}')
        setup_claude.install(self.claude, runtime.state_root())
        before = self.claude.read_text()
        other = self.root / 'different-state'
        setup_claude.install(self.claude, other, remove=True)
        self.assertEqual(self.claude.read_text(), before)
        with self.assertRaisesRegex(RuntimeError, 'Another Sidebar Customizer'):
            setup_claude.install(self.claude, other)
        self.assertEqual(self.claude.read_text(), before)


class PortablePathsTests(unittest.TestCase):
    def test_xdg_and_explicit_paths_and_stale_binary_override(self):
        with tempfile.TemporaryDirectory(prefix='paths with spaces ') as directory:
            base = Path(directory).resolve()
            env = {'XDG_CONFIG_HOME': str(base / 'config'), 'XDG_STATE_HOME': str(base / 'state'),
                   'PATH': os.environ['PATH'], 'HERDR_BIN_PATH': '/missing/herdr (deleted)'}
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(runtime.config_path(), base / 'config/herdr/config.toml')
                self.assertEqual(runtime.preferences_path(), base / 'config/herdr/plugins/config' / runtime.PLUGIN / 'settings.json')
                self.assertEqual(runtime.state_root(), base / 'state/herdr/plugins' / runtime.PLUGIN)
                binary = base / 'brew bin/herdr'; binary.parent.mkdir(); binary.write_text('#!/bin/sh\n'); binary.chmod(0o700)
                with patch.object(runtime.shutil, 'which', return_value=str(binary)):
                    self.assertEqual(runtime.herdr_binary(), str(binary))
                with patch.dict(os.environ, {'HERDR_CONFIG_PATH': str(base / 'custom.toml')}):
                    self.assertEqual(runtime.config_path(), base / 'custom.toml')
