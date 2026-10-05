import curses
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import settings_ui as ui
import sidebar_settings as prefs


class Screen:
    def __init__(self,height=63,width=91,keys=()):
        self.height,self.width=height,width
        self.keys=iter(keys);self.frames=[];self.erase()
    def getmaxyx(self):return self.height,self.width
    def erase(self):self.rows=[[' ']*self.width for _ in range(self.height)]
    def addnstr(self,y,x,text,n,style=0):
        for i,char in enumerate(text[:n]):
            if 0<=y<self.height and 0<=x+i<self.width:self.rows[y][x+i]=char
    def refresh(self):self.frames.append('\n'.join(''.join(row).rstrip() for row in self.rows))
    def get_wch(self):return next(self.keys)
    def keypad(self,*args):pass
    def timeout(self,*args):pass


class SettingsUITests(unittest.TestCase):
    def setUp(self):
        root=self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(ui.settings,'read_settings',return_value=dict(prefs.DEFAULTS)))
        self.enterContext(patch.object(ui,'rpc',return_value={'workspaces':[]}))
        self.session=ui.Session('test',Path(root))
        self.enterContext(patch.object(ui,'color_attr',return_value=0))

    def run_ui(self,keys,height=63,width=91):
        for name in ('curs_set','set_escdelay','mouseinterval','mousemask','start_color','use_default_colors'):
            self.enterContext(patch.object(curses,name))
        screen=Screen(height,width,keys)
        ui.screen_main(screen,self.session)
        return screen.frames

    def test_empty_session_gets_two_examples_without_creating_or_saving_spaces(self):
        for kind in ('spaces','agents'):
            rendered='\n'.join(''.join(s[0] for s in row) for row in ui.preview_rows(self.session,kind,0))
            self.assertIn('Website',rendered);self.assertIn('Sandbox',rendered)
        self.session.set('@colour','red_light')
        self.assertEqual(self.session.value('@colour'),'red_light')
        self.assertEqual(self.session.colours,{})
        self.assertFalse(self.session.dirty)
        ui.rpc.assert_called_once_with('test','workspace.list')

    def test_both_previews_respond_to_visibility_and_independent_spacing(self):
        before={kind:ui.preview_rows(self.session,kind,0) for kind in ('spaces','agents')}
        self.session.draft|={'spaces_gap':1}
        self.assertEqual(len(ui.preview_rows(self.session,'spaces',0)),len(before['spaces'])+1)
        self.assertEqual(ui.preview_rows(self.session,'agents',0),before['agents'])
        self.session.draft|={'agents_gap':1}
        self.assertEqual(len(ui.preview_rows(self.session,'agents',0)),len(before['agents'])+1)
        for field,missing in [('show_git','↑1 ↓2'),('show_branch','feature/ui')]:
            self.session.draft[field]=False
            text=str(ui.preview_rows(self.session,'spaces',0))
            self.assertNotIn(missing,text)
        self.session.draft|={'agent_text':False,'show_tab':False}
        # After the name, the remote sample's label needs no details row.
        self.assertEqual(len(ui.preview_rows(self.session,'agents',0)),3)
        self.assertIn('Build box',str(ui.preview_rows(self.session,'agents',0)[2]))
        # Leading the details row, it keeps that row even without details.
        self.session.draft['machine_position']='details'
        self.assertEqual(len(ui.preview_rows(self.session,'agents',0)),4)
        self.session.draft['show_machine']=False
        self.assertEqual(len(ui.preview_rows(self.session,'agents',0)),3)

    def test_empty_git_row_toggle_changes_space_height_but_keeps_gap_independent(self):
        before=ui.preview_rows(self.session,'spaces',0)
        self.assertEqual(len(before),4)
        self.assertEqual(before[1],[])
        self.assertTrue(before[3])
        self.session.set('empty_git_row','hide')
        self.assertEqual(ui.preview_rows(self.session,'spaces',0),[before[0],before[2],before[3]])
        self.session.set('spaces_gap',1)
        self.assertEqual(ui.preview_rows(self.session,'spaces',0),before)
        self.session.set('empty_git_row','blank')
        self.assertEqual(ui.preview_rows(self.session,'spaces',0),before[:2]+[[]]+before[2:])
        self.session.draft|={'show_branch':False,'show_git':False,'spaces_gap':0}
        self.assertEqual(ui.preview_rows(self.session,'spaces',0),[before[0],[],before[2],[]])

    def test_placeholder_and_git_icon_preview_match_the_selected_modes(self):
        from space_rows import GIT_ICON
        self.session.draft|={'empty_git_row':'placeholder','show_git_icon':True}
        rows=ui.preview_rows(self.session,'spaces',0)
        self.assertEqual(''.join(segment[0] for segment in rows[1]),GIT_ICON+' no git repo')
        self.assertTrue(''.join(segment[0] for segment in rows[3]).startswith(GIT_ICON+' feature/ui ↑1'))
        self.session.set('empty_git_row','blank')
        self.assertEqual(ui.preview_rows(self.session,'spaces',0)[1],[])
        self.session.set('empty_git_row','hide')
        self.assertEqual(len(ui.preview_rows(self.session,'spaces',0)),3)
        self.session.draft|={'empty_git_row':'placeholder','show_branch':False,'show_git':False}
        self.assertIn('Git details hidden',str(ui.preview_rows(self.session,'spaces',0)))

    def test_git_icon_selector_cycles_all_choices_without_saving(self):
        field=next(f for f in ui.SECTIONS[1][1] if f[0]=='git_icon')
        self.session.set('show_git_icon',True)
        self.session.set('empty_git_row','placeholder')
        seen=set()
        for _ in prefs.GIT_ICONS:
            ident=self.session.value('git_icon');label,glyph=prefs.GIT_ICONS[ident]
            seen.add(ident)
            self.assertEqual(ui.display_value(self.session,'git_icon'),glyph+' '+label)
            self.assertEqual(ui.preview_rows(self.session,'spaces',0)[1][0][0],glyph+' no git repo')
            self.session.cycle(field,1)
        self.assertEqual(seen,set(prefs.GIT_ICONS))
        self.assertEqual(self.session.base['git_icon'],'41')
        self.assertEqual(self.session.value('git_icon'),'41')

    def test_animation_strip_exposes_all_choices_and_arrows_select_without_ids(self):
        frames=self.run_ui(['4',curses.KEY_DOWN,curses.KEY_RIGHT,'q','q'])
        self.assertEqual(self.session.draft['animation'],'03')
        self.assertEqual(ui.display_value(self.session,'animation'),'Half circle')
        screen=Screen()
        hits,_=ui.draw_form(screen,self.session,3,1,0,13,56,'@colour',False)
        animation_hits=[a for _,_,_,a in hits if a[0]=='animation']
        self.assertEqual({a[1] for a in animation_hits},set(prefs.ANIMATIONS))
        screen.refresh()
        for name in ('Braille','Half circle','Open arc','Concentric','Starburst','Rising bar'):
            self.assertIn(name,screen.frames[-1])
        self.assertNotIn('03  Half circle',frames[-1])

    def test_text_modes_show_exclusive_controls_and_keep_drafts(self):
        self.session.set('claude_label','Assistant')
        self.session.set('show_model',False)
        self.session.set('text_mode','custom')
        self.assertEqual([f[0] for f in ui.active_fields(self.session,2)],['agent_text','collect_details','detail_hints','text_mode','text_template'])
        self.assertEqual(self.session.draft['text_template'],prefs.DEFAULT_TEMPLATE)
        self.session.set('text_template','{model} · {remaining_pct} left')
        self.session.set('text_mode','automatic')
        self.assertNotIn('text_template',[f[0] for f in ui.active_fields(self.session,2)])
        self.assertEqual(self.session.draft['claude_label'],'Assistant')
        self.assertFalse(self.session.draft['show_model'])
        self.session.set('text_mode','custom')
        self.assertEqual(self.session.draft['text_template'],'{model} · {remaining_pct} left')

    def test_template_reference_is_inline_and_scrollable_in_small_pane(self):
        frames=self.run_ui(['3',curses.KEY_DOWN,curses.KEY_DOWN,curses.KEY_DOWN,'\n','q','q'])
        self.assertIn('AVAILABLE FIELDS',frames[-1])
        for field in prefs.FIELDS:self.assertIn('{'+field+'}',frames[-1])
        small=self.run_ui(['3',curses.KEY_NPAGE,curses.KEY_NPAGE,curses.KEY_NPAGE,curses.KEY_NPAGE,'q','q'],28,48)
        for field in prefs.FIELDS:self.assertIn('{'+field+'}','\n'.join(small))
        for line in ui.template_help(48):self.assertLessEqual(len(line),44)

    def test_text_palettes_include_neutrals_preserve_original_and_remain_stable(self):
        for key in ('neutral_colour','agent_colour','tab_colour','branch_colour'):
            choices=ui.color_options(self.session,key)
            self.assertIn(('theme','Follow theme','theme'),choices)
            self.assertTrue(any(label=='White' for _,label,_ in choices))
            field=next(f for f in ui.SECTIONS[4][1] if f[0]==key)
            self.session.cycle(field,1)
            self.assertEqual(ui.color_options(self.session,key),choices)
            self.session.cycle(field,-1)
            self.assertEqual(self.session.value(key),self.session.base[key])
            self.assertNotIn('#',ui.display_value(self.session,key))
        self.assertEqual(len(ui.color_options(self.session,'@colour')),36)

    def test_color_palette_is_embedded_and_keyboard_changes_only_draft(self):
        frames=self.run_ui(['5',*[curses.KEY_DOWN]*6,'\n',curses.KEY_RIGHT,'\x1b','q','q'])
        self.assertIn('Agent details color — choose a swatch','\n'.join(frames))
        self.assertNotEqual(self.session.draft['agent_colour'],self.session.base['agent_colour'])
        self.assertEqual(self.session.base['agent_colour'],prefs.DEFAULTS['agent_colour'])
        self.assertTrue(any('████' in frame for frame in frames))
        self.assertIn('7 Shortcuts',frames[-1])

    def test_navigation_and_actions_are_readable_at_minimum_pane_size(self):
        frame=self.run_ui(['q'],24,48)[-1]
        footer=' '.join(line.strip() for line in frame.splitlines()[-3:])
        for hint in ('Tab: page','Up/Down: select','Left/Right: change',
                     'Enter: edit','PgUp/PgDn: scroll'):
            self.assertIn(hint,footer)
        for label in ('[A Apply]','[U Reload]','[D Defaults]','[Q Close]'):
            self.assertIn(label,footer)

    def test_reload_requires_confirmation_and_navigation_cancels_it(self):
        frames=self.run_ui([curses.KEY_RIGHT,'u',curses.KEY_DOWN,'u','q','q'],24,48)
        self.assertTrue(self.session.dirty)
        self.assertIn('Discard unsaved edits?',frames[-2])
        self.assertIn('any other key cancels.',' '.join(frames[-2].split()))
        self.run_ui(['u','u','q'])
        self.assertFalse(self.session.dirty)
        self.assertEqual(self.session.draft,self.session.base)

    def test_mouse_reload_and_page_click_follow_confirmation_rules(self):
        self.session.set('space_names',False)
        # Reload, click Git tab (cancels), Reload: edits must still be present.
        clicks=[(0,14,62,0,curses.BUTTON1_PRESSED),
                (0,15,11,0,curses.BUTTON1_PRESSED),
                (0,14,62,0,curses.BUTTON1_PRESSED)]
        with patch.object(curses,'getmouse',side_effect=clicks):
            self.run_ui([curses.KEY_MOUSE]*3+['q','q'])
        self.assertTrue(self.session.dirty)
        release=(0,14,62,0,curses.BUTTON1_RELEASED)
        with patch.object(curses,'getmouse',side_effect=[clicks[0],release,clicks[0]]):
            self.run_ui([curses.KEY_MOUSE]*3+['q'])
        self.assertFalse(self.session.dirty)

    def test_failed_reload_preserves_unsaved_draft(self):
        self.session.set('space_names',False)
        with patch.object(ui,'rpc',side_effect=RuntimeError('Server unavailable')):
            frames=self.run_ui(['u','u','q','q'])
        self.assertTrue(self.session.dirty)
        self.assertFalse(self.session.draft['space_names'])
        self.assertIn('Server unavailable',frames[-2])

    def test_apply_status_clears_after_next_edit_and_failure_keeps_draft(self):
        def save(*args):
            ui.settings.read_settings.return_value=dict(self.session.draft)
        with patch.object(prefs,'apply_settings',side_effect=save):
            frames=self.run_ui([curses.KEY_RIGHT,'a',curses.KEY_RIGHT,'q','q'])
        self.assertIn('Applied.',frames[2])
        self.assertIn('[saved]',frames[2])
        self.assertIn('[unsaved]',frames[3])
        self.assertNotIn('Applied.',frames[3])
        with patch.object(prefs,'apply_settings',side_effect=RuntimeError('Apply failed')):
            frames=self.run_ui(['a','q','q'])
        self.assertIn('Apply failed',frames[1])
        self.assertTrue(self.session.dirty)

    def test_inactive_controls_keep_values_and_explain_dependencies(self):
        for parent,value,child in [('animate',False,'speed'),
                                   ('animate',False,'animation'),
                                   ('show_git_icon',False,'git_icon'),
                                   ('show_context',False,'show_capacity'),
                                   ('show_context',False,'context_mode'),
                                   ('agent_text',False,'text_mode')]:
            with self.subTest(child=child):
                self.session.reload()
                self.session.set(parent,value)
                original=self.session.value(child)
                field=next(f for _,fields in ui.SECTIONS for f in fields if f[0]==child)
                self.session.cycle(field,1)
                self.assertEqual(self.session.value(child),original)
                self.assertIn('Inactive:',self.session.message)
        # Collection remains an independent opt-in when the row is hidden.
        self.session.set('collect_details',True)
        self.assertTrue(self.session.draft['collect_details'])

    def test_agent_colour_stays_editable_without_details_row(self):
        # Machine labels follow agent_colour by default, even with the row hidden.
        self.session.set('agent_text',False)
        self.assertEqual(ui.inactive_reason(self.session,'agent_colour'),'')
        field=next(f for _,fields in ui.SECTIONS for f in fields if f[0]=='agent_colour')
        self.session.cycle(field,1)
        self.assertNotEqual(self.session.draft['agent_colour'],self.session.base['agent_colour'])
        self.assertEqual(prefs.machine_colour(self.session.draft),self.session.draft['agent_colour'])

    def test_manual_layout_marks_shortcuts_inactive_and_says_rows_need_updating(self):
        self.session.layout_mode='manual'
        self.assertIn('Inactive:',ui.inactive_reason(self.session,'shortcut_settings'))
        self.assertEqual(ui.inactive_reason(self.session,'spaces_gap'),'')
        self.session.set('spaces_gap',1)
        with patch.object(prefs,'apply_settings'):
            self.session.apply()
        self.assertIn('Update your rows',self.session.message)
        self.session.layout_mode='managed'
        self.assertEqual(ui.inactive_reason(self.session,'shortcut_settings'),'')

    def test_config_check_timeout_keeps_the_draft(self):
        self.session.set('spaces_gap', 1)
        timeout = subprocess.TimeoutExpired(['herdr', 'config', 'check'], 10)
        with patch.object(prefs, 'apply_settings', side_effect=timeout):
            frames = self.run_ui(['a', 'q', 'q'])
        self.assertEqual(self.session.draft['spaces_gap'], 1)
        self.assertTrue(self.session.dirty)
        self.assertIn('timed out', frames[1])

    def test_line_editor_keys(self):
        edit=ui.apply_edit_key
        self.assertEqual(edit('abc',3,'d'),('abcd',4))
        self.assertEqual(edit('abc',1,'\x7f'),('bc',0))
        self.assertEqual(edit('abc',0,curses.KEY_BACKSPACE),('abc',0))
        self.assertEqual(edit('abc',1,curses.KEY_DC),('ac',1))
        self.assertEqual(edit('abc',1,'\x15'),('',0))
        self.assertEqual(edit('abc',1,curses.KEY_END),('abc',3))
        self.assertEqual(edit('abc',2,'\x01'),('abc',0))
        self.assertEqual(edit('abc',3,curses.KEY_RIGHT),('abc',3))
        self.assertEqual(edit('x'*160,160,'y'),('x'*160,160))

    def test_disabled_animation_rejects_mouse_selection_and_keyboard_changes(self):
        self.session.set('animate',False)
        with patch.object(curses,'getmouse',return_value=(0,15,16,0,curses.BUTTON1_PRESSED)):
            frames=self.run_ui(['4',curses.KEY_MOUSE,curses.KEY_RIGHT,'q','q'])
        self.assertEqual(self.session.draft['animation'],self.session.base['animation'])
        self.assertIn('Inactive: turn on Animate working agents.',frames[-2])

    def test_all_pages_remain_accessible_at_minimum_size(self):
        for page,name in enumerate(('Display','Git','Agent text','Animation','Colors','States','Shortcuts'),1):
            frames=self.run_ui([str(page),'q'],24,48)
            self.assertIn(str(page)+' '+name,frames[-1])
            self.assertIn(ui.SECTIONS[page-1][1][0][1],frames[-1])


if __name__=='__main__':unittest.main()
