"""Pure rewrite checks: only page resources change, app semantics survive."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from wechat_channels.page_injection import rewrite_html, rewrite_script

BASE = '/__clipgo/' + 'a' * 40
CDN = 'https://res.wx.qq.com/t/wx_fed/finder/web/js/connect.publish.js'


class PageInjectionTests(unittest.TestCase):
    def test_external_bootstrap_is_early_idempotent_and_nonce_aware(self):
        source = '<!doctype html><html><HEAD><script nonce="a&amp;b" src="https://res.wx.qq.com/a.js"></script></HEAD><body></body></html>'
        transformed = rewrite_html(source, {'basePath': BASE})
        self.assertIn('data-clipgo-base="' + BASE + '"', transformed)
        self.assertIn('nonce="a&amp;b"', transformed)
        self.assertLess(transformed.index('/embed.js'), transformed.index('https://res.wx.qq.com'))
        self.assertIn('_clipgo=', transformed)
        self.assertNotIn('window.__clipgoPageConfig=', transformed)
        self.assertIsNone(rewrite_html(transformed, {'base': BASE}))

    def test_invalid_config_or_missing_head_never_injects(self):
        self.assertIsNone(rewrite_html('<body>text</body>', {'base': BASE}))
        for base in ('https://evil.test', '/__clipgo/short', BASE + '" onload="x'):
            with self.assertRaises(ValueError):
                rewrite_html('<head></head>', {'base': base})

    def test_asset_cache_tags_do_not_touch_third_party_or_video(self):
        source = '<head><link rel="modulepreload" href="https://res.wx.qq.com/pre.js"><script src="https://other.test/file.js"></script><script src="https://finder.video.qq.com/a.js"></script><script src="/local.js"></script></head>'
        result = rewrite_html(source, {'base':BASE})
        self.assertIn('src="https://other.test/file.js"', result)
        self.assertIn('src="https://finder.video.qq.com/a.js"', result)
        self.assertIn('src="/local.js?_clipgo=', result)
        self.assertIn('href="https://res.wx.qq.com/pre.js?_clipgo=', result)

    def test_exact_aliases_hook_both_selected_sources(self):
        source = 'const x={flowTab:a,localFlowTab:b,goToNextFlowFeed:next,goToPrevFlowFeed:prev,loadLocalPlaylist:load};'
        result = rewrite_script(source, CDN)
        self.assertIn('track(a,"flowTab")', result)
        self.assertIn('track(b,"localFlowTab")', result)
        self.assertIn('wrap(next,()=>a,"goToNextFlowFeed")', result)
        self.assertIn('wrap(load,()=>b,"loadLocalPlaylist")', result)
        self.assertIsNone(rewrite_script(result, CDN))

    def test_unknown_host_or_unpaired_function_is_not_changed(self):
        source = 'const x={goToNextFlowFeed:n};'
        self.assertIsNone(rewrite_script(source, CDN))
        self.assertIsNone(rewrite_script('const x={flowTab:a}', 'https://other.test/a.js'))
        self.assertIsNone(rewrite_script('plain text', CDN))

    def test_literals_comments_and_regex_are_not_properties(self):
        source = '''const s="{flowTab:a,goToNextFlowFeed:n}";/*{flowTab:b}*/
        const t=`{localFlowTab:c}`;const r=/{flowTab:a,goToNextFlowFeed:n}/;
        // {flowTab:c}
        const d=6/3;'''
        self.assertIsNone(rewrite_script(source, CDN))

    def test_destructuring_bindings_are_never_rewritten(self):
        for source in ('const {flowTab:f,goToNextFlowFeed:n}=store;',
                       '({flowTab:f,goToNextFlowFeed:n}=store);',
                       'const fn=({flowTab:f,goToNextFlowFeed:n})=>f;',
                       'function fn({flowTab:f,goToNextFlowFeed:n}){return f}'):
            self.assertIsNone(rewrite_script(source, CDN))

    def test_each_function_binds_its_own_store_object(self):
        source = 'function A(){return {flowTab:a,goToNextFlowFeed:nextA}}function B(){return {flowTab:b,goToNextFlowFeed:nextB}}'
        result = rewrite_script(source, CDN)
        self.assertIn('wrap(nextA,()=>a,',result)
        self.assertIn('wrap(nextB,()=>b,',result)

    def test_store_return_comma_expression_after_nested_callback(self):
        source = 'function store(){return watch(x,v=>{if(v){call(v);}}),{flowTab:chosen,localFlowTab:local,goToNextFlowFeed:next}}'
        result = rewrite_script(source, CDN)
        self.assertIn('track(chosen,"flowTab")',result)
        self.assertIn('wrap(next,()=>chosen,',result)

    def test_detail_method_balances_nested_blocks_and_regex(self):
        source = '''class A{async finderGetCommentDetail(x){const s="}";const r=/}/;
          if(x){return {data:{object:x}}}return {data:{object:null}}}async other(){return 2}}'''
        result = rewrite_script(source, CDN)
        self.assertIn('detail(__clipgoResult)', result)
        self.assertIn('async other(){return 2}', result)
        self.assertIn('if(x){return {data:{object:x}}}', result)

    def test_import_cache_propagation_is_scoped(self):
        source = 'import{a}from"./a.js";import("./b.js");const files=["js/c.js"];const video="https://finder.video.qq.com/clip.mp4";'
        result = rewrite_script(source, CDN+'?_clipgo=123456abcdef')
        for name in ('a.js','b.js','c.js'):
            self.assertIn(name+'?_clipgo=123456abcdef', result)
        self.assertIn('https://finder.video.qq.com/clip.mp4', result)
        self.assertIsNone(rewrite_script(source, CDN))


if __name__ == '__main__':
    unittest.main()
