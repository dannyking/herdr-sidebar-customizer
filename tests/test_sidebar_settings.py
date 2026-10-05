import json
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

import agent_info
import sidebar_settings as prefs
import sidebar_state
import space_colors
import settings_ui


CONFIG = '''# Keep this comment.
[ui]
sidebar_max_width = 72
[ui.sidebar.spaces]
row_gap = 0 # keep gap comment
rows = [["workspace"], ["branch"]]
[ui.sidebar.agents]
row_gap = 0
rows = [["agent"]]
[theme.custom]
accent = "#687b9f"
[keys]
open_notification_target = "prefix+alt+o"
[[keys.command]]
key = "prefix+g"
type = "plugin_action"
command = "herdr-sidebar-customizer.toggle-spacing"
[[keys.command]]
key = "prefix+shift+c"
type = "plugin_action"
command = "herdr-sidebar-customizer.space-color"
[[keys.command]]
key = "prefix+f"
type = "plugin_action"
command = "file-explorer.open"
description = "file explorer"
'''


class SidebarSettingsTests(unittest.TestCase):
    def test_selected_animations_speed_and_static_states(self):
        for ident,(_,frames,duration) in prefs.ANIMATIONS.items():
            for speed in (.25,1,4):
                settings=prefs.DEFAULTS | {'animation':ident,'speed':speed}
                step=duration/1000/speed
                actual=''.join(sidebar_state.symbol('working',(i+.01)*step,settings) for i in range(len(frames)))
                self.assertEqual(actual,frames)
                self.assertEqual(sidebar_state.symbol('working',(len(frames)+.01)*step,settings),frames[0])
            settings |= {'animate':False,'working_symbol':'*','blocked_symbol':'!'}
            self.assertEqual(sidebar_state.symbol('working',12,settings),'*')
            self.assertEqual(sidebar_state.symbol('blocked',12,settings),'!')

    def test_indicators_and_names_can_be_hidden_independently(self):
        for names,icons,visible in [(False,False,None),(True,False,'Example'),(False,True,'○')]:
            values=sidebar_state.label_tokens('idle',0,'Example','red_light',
                prefs.DEFAULTS | {'space_names':names,'state_icons':icons})
            label=values['space_red']
            if label: label=label.replace('\u2062','').replace('\u2063','')
            self.assertEqual(label,visible)

    def test_agent_text_fields_labels_context_and_templates(self):
        observation={'model':'Opus 5','model_id':'claude-opus-5','effort':'high',
                     'context':agent_info.context(250000,1000000)}
        def render(changes,obs=observation):
            return agent_info.tokens(obs,'claude',prefs.DEFAULTS|changes)['agent_info']
        self.assertEqual(render({'claude_label':'Assistant','context_mode':'remaining'}),
                         'Assistant Opus 5 · high · 75%/1M left')
        self.assertEqual(render({'show_agent':False,'show_effort':False,'show_capacity':False}), 'Opus 5 · 25%')
        self.assertIsNone(render({'agent_text':False}))
        self.assertIsNone(render({'show_agent':False,'show_model':False,'show_effort':False,'show_context':False}))
        self.assertEqual(render({'text_mode':'custom','text_template':'{model} · effort {effort} · {context}'},
                                {'model':'Opus 5','model_id':'claude-opus-5'}),'Opus 5')
        self.assertEqual(render({'text_mode':'custom','text_template':'{agent} · {used} of {capacity}'}),'Claude · 250k of 1M')
        self.assertEqual(len(render({'text_mode':'custom','text_template':'a'*160})),80)

    def test_custom_text_is_independent_and_old_templates_migrate(self):
        observation={'model':'Opus 5','effort':'high','context':agent_info.context(250000,1000000)}
        custom=prefs.validate({'text_template':'{agent} {model} · {effort} · {remaining_pct} left',
                              'show_agent':False,'show_model':False,'show_effort':False,
                              'show_context':False,'show_capacity':False,'claude_label':'Assistant'})
        self.assertEqual(custom['text_mode'],'custom')
        self.assertEqual(agent_info.tokens(observation,'claude',custom)['agent_info'],
                         'Claude Opus 5 · high · 75% left')
        automatic=custom|{'text_mode':'automatic','show_model':True}
        self.assertEqual(agent_info.tokens(observation,'claude',automatic)['agent_info'],'Opus 5')
        self.assertEqual(prefs.validate({})['text_mode'],'automatic')
        with self.assertRaises(ValueError):prefs.validate({'text_mode':'combined'})

    def test_invalid_settings_do_not_reach_renderer(self):
        for bad in [{'speed':0},{'speed':float('nan')},{'speed':True},{'animate':'false'},
                    {'animation':'02'},{'agent_colour':'red'},{'unknown':True},
                    {'done_symbol':'🔥'},{'done_symbol':'\u0301'},{'done_symbol':''},
                    {'claude_label':'bad\nlabel'},{'spaces_gap':True},
                    {'text_template':'{model.__class__}'},{'text_template':'{model:999999}'},
                    {'text_template':'{model!r}'},{'text_template':'{unknown}'},
                    {'shortcut_settings':'prefix+shift+s\ncommand=bad'}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError): prefs.validate(bad)

    def test_reader_reloads_and_keeps_last_valid_preferences(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'settings.json';reader=prefs.SettingsReader(path)
            self.assertEqual(reader.load()['animation'],'01')
            path.write_text(json.dumps({'animation':'03','speed':.5}))
            self.assertEqual(reader.load()['animation'],'03')
            path.write_text('{broken')
            self.assertEqual(reader.load()['animation'],'03')
            self.assertIsNotNone(reader.error)
            path.write_text(json.dumps({'animation':'04'}))
            self.assertEqual(reader.load()['animation'],'04')
            self.assertIsNone(reader.error)

    def test_render_preserves_unrelated_config_and_removes_optional_rows(self):
        changed=prefs.render_config(CONFIG,prefs.DEFAULTS|{'show_tab':False,'show_git':False,
                                    'show_branch':False,'empty_git_row':'hide','agent_text':False,'name_bold':False})
        config=tomllib.loads(changed)
        self.assertIn('# Keep this comment.',changed)
        self.assertIn('# keep gap comment',changed)
        self.assertEqual(config['theme']['custom']['accent'],'#687b9f')
        self.assertEqual(config['ui']['sidebar_max_width'],72)
        for section in ('spaces','agents'):
            self.assertEqual(len(config['ui']['sidebar'][section]['rows'][0]),12 + (section=='agents'))
            self.assertFalse(config['ui']['sidebar'][section]['rows'][0][0]['bold'])
        self.assertEqual(len(config['ui']['sidebar']['spaces']['rows']),1)
        # The machine label and native fallback text stay with optional rows hidden.
        agents=config['ui']['sidebar']['agents']['rows']
        self.assertEqual(agents[0][-1]['token'],'machine')
        self.assertEqual([t['token'] for t in agents[1]],['agent','state_text'])
        commands=config['keys']['command']
        self.assertTrue(any(c['command']=='file-explorer.open' and c['key']=='prefix+f' for c in commands))
        self.assertEqual(prefs.render_config(changed,prefs.DEFAULTS|{'show_tab':False,'show_git':False,
                         'show_branch':False,'empty_git_row':'hide','agent_text':False,'name_bold':False}),changed)

    def test_section_header_on_last_line_without_newline(self):
        changed=prefs.render_config('[ui.sidebar.spaces]',prefs.DEFAULTS)
        sidebar=tomllib.loads(changed)['ui']['sidebar']
        self.assertEqual(changed.count('[ui.sidebar.spaces]'),1)
        self.assertEqual(sidebar['spaces']['row_gap'],0)
        self.assertEqual(len(sidebar['spaces']['rows']),2)
        self.assertIn('[ui.sidebar.spaces]\nrow_gap = 0\nrows = [',changed)

    def test_indented_rows_are_replaced_not_duplicated(self):
        changed=prefs.render_config('[ui.sidebar.agents]\n  rows = [["agent"]]\n',prefs.DEFAULTS)
        self.assertEqual(changed.count('rows ='),2)
        tomllib.loads(changed)

    def test_indented_row_gap_is_updated_in_place(self):
        text = '[ui.sidebar.agents]\n  row_gap = 0 # gap note\n  rows = [["agent"]]\n'
        changed = prefs.render_config(text, prefs.DEFAULTS | {'agents_gap': 1})
        self.assertIn('  row_gap = 1 # gap note\n', changed)
        self.assertEqual(changed.count('row_gap'), 2)
        self.assertEqual(tomllib.loads(changed)['ui']['sidebar']['agents']['row_gap'], 1)

    def test_comment_after_plugin_shortcut_survives_render(self):
        text = CONFIG.replace('[[keys.command]]\nkey = "prefix+f"',
                              '# Note about the next binding.\n[[keys.command]]\nkey = "prefix+f"')
        changed = prefs.render_config(text, prefs.DEFAULTS)
        self.assertIn('# Note about the next binding.\n[[keys.command]]\nkey = "prefix+f"', changed)
        commands = tomllib.loads(changed)['keys']['command']
        managed = [c['command'] for c in commands if c['command'] in prefs.MANAGED_COMMANDS]
        self.assertEqual(len(managed), len(set(managed)))

    def test_default_keys_rejects_unexpected_output(self):
        prefs.default_keys.cache_clear()
        self.addCleanup(prefs.default_keys.cache_clear)
        with patch.object(prefs.runtime, 'herdr_binary', return_value='herdr'), \
             patch.object(prefs.subprocess, 'check_output', return_value='[ui]\n'):
            with self.assertRaisesRegex(ValueError, 'Unexpected herdr --default-config output'):
                prefs.default_keys()

    def test_default_keys_reads_commented_bindings(self):
        output = '[ui]\n[keys]\n# prefix = "ctrl+b"\n# new_tab = "prefix+c"\n[theme]\n# goto = "x"\n'
        prefs.default_keys.cache_clear()
        self.addCleanup(prefs.default_keys.cache_clear)
        with patch.object(prefs.runtime, 'herdr_binary', return_value='herdr'), \
             patch.object(prefs.subprocess, 'check_output', return_value=output):
            self.assertEqual(prefs.default_keys(), {'prefix': 'ctrl+b', 'new_tab': 'prefix+c'})

    def test_neutral_colours_and_new_space_defaults_keep_existing_assignments(self):
        settings=prefs.DEFAULTS|{'space_colours':False,'neutral_colour':'#abcdef',
                               'default_hue':'teal','default_shade':'dark'}
        data=tomllib.loads('[ui.sidebar.spaces]\nrows = [[\n'+space_colors.style_block(settings)+'\n]]')
        for token in data['ui']['sidebar']['spaces']['rows'][0]:
            self.assertEqual(token['fg'],'#abcdef')
            self.assertEqual({r['fg'] for r in token['rules']},{'#abcdef'})
        with tempfile.TemporaryDirectory() as root:
            directory=Path(root);(directory/'space-colors.json').write_text('{"old":"red_light"}')
            self.assertEqual(space_colors.assignments(directory,[{'workspace_id':'old'},{'workspace_id':'new'}],
                             settings=settings),{'old':'red_light','new':'teal_dark'})

    def test_shortcuts_reject_native_external_and_internal_conflicts(self):
        defaults={'prefix':'ctrl+b','new_tab':'prefix+c','goto':'prefix+g','switch_tab':'prefix+1..9'}
        with patch.object(prefs,'default_keys',return_value=defaults):
            prefs.check_shortcuts(CONFIG,prefs.DEFAULTS)
            for chord in ('prefix+c','prefix+f','prefix+g','prefix+5','ctrl+b'):
                with self.subTest(chord=chord), self.assertRaises(ValueError):
                    prefs.check_shortcuts(CONFIG,prefs.DEFAULTS|{'shortcut_settings':chord})
            prefs.check_shortcuts(CONFIG,prefs.DEFAULTS|{'shortcut_colours':''})

    def test_editor_cycles_draft_without_saving_and_defaults_keep_space_colours(self):
        with tempfile.TemporaryDirectory() as root,patch.object(settings_ui.settings,'read_settings',return_value=dict(prefs.DEFAULTS)), \
             patch.object(settings_ui,'rpc',return_value={'workspaces':[{'workspace_id':'w1','label':'Example'}]}):
            path=Path(root)/'space-colors.json';path.write_text('{"w1":"red_light"}')
            session=settings_ui.Session('test',Path(root))
            session.cycle(next(f for _,fields in settings_ui.SECTIONS for f in fields if f[0]=='animation'),1)
            self.assertEqual(session.draft['animation'],'03')
            self.assertEqual(session.base['animation'],'01')
            session.set('@colour','teal_dark')
            self.assertTrue(session.dirty)
            self.assertEqual(json.loads(path.read_text())['w1'],'red_light')
            session.defaults()
            self.assertEqual(session.colours,{'w1':'red_light'})
            self.assertFalse(session.dirty)


if __name__=='__main__':unittest.main()
