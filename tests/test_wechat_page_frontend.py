"""Browser checks for our own injected script with simulated Channels markup."""
import json
from pathlib import Path
import sys
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from wechat_channels.page_injection import rewrite_html, rewrite_script

EDGE = Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
SCRIPT = (ROOT / 'tools/wechat_channels/page_assets/embed.js').read_text(encoding='utf-8')
BASE = '/__clipgo/' + 'b' * 40
CDN = 'https://res.wx.qq.com/assets/connect.publish.js'


def feed(ident, title):
    return {'id':ident, 'objectDesc':{'description':title, 'mediaType':4,
        'media':[{'url':f'https://finder.video.qq.com/stodownload?encfilekey={ident}',
                  'urlToken':'&token=test', 'decodeKey':'18446744073709551615', 'videoPlayLen':20}]},
        'contact':{'nickname':'测试作者'}, 'favCount':10}


@unittest.skipUnless(sync_playwright and EDGE.exists(), 'Optional browser runtime unavailable')
class PageFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime=sync_playwright().start()
        cls.browser=cls.runtime.chromium.launch(executable_path=str(EDGE),headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()

    def setUp(self):
        self.page=self.browser.new_page(viewport={'width':900,'height':800})
        self.page.set_default_timeout(6000)
        self.page.clock.install()
        self.errors=[]
        self.events=[]
        self.downloads=[]
        self.job={'state':'idle'}
        self.page.on('pageerror',lambda error:self.errors.append(str(error)))
        self.a,self.b=feed('video-a','第一条 <img src=x onerror=alert(1)>'),feed('video-b','第二条')
        self.script='''const f=window.f={value:{feeds:FEEDS,currentFeedIndex:0}};
        const local=window.local={value:{feeds:[],currentFeedIndex:0}};
        function next(arg){window.receiver=this;f.value.currentFeedIndex=1;return arg;}
        function load(arg){local.value.feeds=[FEED_B];local.value.currentFeedIndex=0;return arg;}
        window.nav={flowTab:f,localFlowTab:local,goToNextFlowFeed:next,loadLocalPlaylist:load};
        '''.replace('FEEDS',json.dumps([self.a,self.b])).replace('FEED_B',json.dumps(self.b))
        self.body='<main class="slides-scroll"><section class="slides-item"><div class="actions"><button class="click-box op-item">喜欢</button></div></section></main>'
        self.page.route('https://res.wx.qq.com/**',lambda route:route.fulfill(status=200,content_type='application/javascript; charset=utf-8',body=rewrite_script(self.script,route.request.url) or self.script))
        self.page.route('https://channels.weixin.qq.com/**',self.route)

    def route(self,route):
        path=route.request.url.split('https://channels.weixin.qq.com',1)[1]
        if path.startswith(BASE+'/embed.js'):
            return route.fulfill(status=200,content_type='application/javascript; charset=utf-8',body=SCRIPT)
        if path.startswith(BASE+'/event'):
            self.events.append(route.request.post_data_json)
            return route.fulfill(json={'ok':True})
        if path.startswith(BASE+'/download'):
            self.downloads.append(route.request.post_data_json)
            self.job={'state':'downloading','id':'job-one','percent':40}
            return route.fulfill(json={'ok':True,'id':'job-one'})
        if path.startswith(BASE+'/status'):
            return route.fulfill(json={'ok':True,'download':self.job})
        content=f'<html><head><meta charset="utf-8"><script src="{CDN}"></script></head><body>{self.body}</body></html>'
        return route.fulfill(status=200,content_type='text/html; charset=utf-8',body=rewrite_html(content,{'base':BASE}))

    def open(self):
        self.page.goto('https://channels.weixin.qq.com/web/pages/home')
        self.page.wait_for_function('!!window.__clipgoPage')
        self.page.clock.run_for(400)
        self.page.wait_for_function('window.__clipgoPage.diagnostics().connected')

    def tearDown(self):
        try:self.assertEqual(self.errors,[])
        finally:self.page.close()

    def test_purple_real_toolbar_button_downloads_exact_selected_feed(self):
        self.open()
        button=self.page.get_by_role('button',name='ClipGo 下载')
        self.assertEqual(button.count(),1)
        self.assertEqual(button.evaluate('(node)=>getComputedStyle(node).color'),'rgb(139, 92, 246)')
        self.assertGreaterEqual(float(button.evaluate('(node)=>parseFloat(getComputedStyle(node).fontSize)')),12)
        self.assertEqual(button.evaluate('(node)=>node.parentElement.className'),'actions')
        button.click()
        self.page.wait_for_function('document.querySelector(".clipgo-page-download span").textContent!=="下载"')
        self.assertEqual(self.downloads[0]['feed']['id'],'video-a')
        self.assertEqual(self.downloads[0]['feed']['objectDesc']['media'][0]['decodeKey'],'18446744073709551615')
        self.job={'state':'done','id':'job-one','message':'已保存'}
        self.page.clock.run_for(1600)
        self.page.wait_for_function('document.querySelector("#clipgo-page-toast").textContent==="已保存"')

    def test_navigation_preserves_receiver_and_result_and_changes_download_snapshot(self):
        self.open()
        outcome=self.page.evaluate('''()=>{const marker={value:3};const result=nav.goToNextFlowFeed(marker);return {same:result===marker,receiver:window.receiver===nav};}''')
        self.assertEqual(outcome,{'same':True,'receiver':True})
        self.page.get_by_role('button',name='ClipGo 下载').click()
        self.page.wait_for_function('document.querySelector(".clipgo-page-download").disabled')
        self.assertEqual(self.downloads[0]['feed']['id'],'video-b')

    def test_async_wrapper_preserves_promise_and_rejections(self):
        self.open()
        outcome=self.page.evaluate('''async()=>{
          const promise=Promise.resolve(7), fn=()=>promise;
          const wrapped=__clipgoPage.wrap(fn,()=>f,"test");
          const same=wrapped()===promise;
          const failed=Promise.reject(new Error("expected"));
          const rejecting=__clipgoPage.wrap(()=>failed,()=>f,"test");
          const rejected=rejecting();
          let message;try{await rejected;}catch(error){message=error.message;}
          return {same,rejectionSame:rejected===failed,message};
        }''')
        self.assertEqual(outcome,{'same':True,'rejectionSame':True,'message':'expected'})

    def test_reused_dom_reinserts_once_and_does_not_duplicate(self):
        self.open()
        self.page.evaluate('document.querySelector(".actions").innerHTML="<button class=\\"click-box op-item\\">分享</button>"')
        self.page.clock.run_for(100)
        self.assertEqual(self.page.locator('.clipgo-page-download').count(),1)
        self.page.evaluate('document.querySelector(".actions").append(document.createElement("span"))')
        self.page.clock.run_for(100)
        self.assertEqual(self.page.locator('.clipgo-page-download').count(),1)

    def test_sidebar_is_in_page_and_moves_into_late_toolbar(self):
        self.body='<main class="slides-scroll"></main>'
        self.open()
        self.page.clock.run_for(3100)
        self.assertEqual(self.page.locator('body > .clipgo-page-sidebar').count(),1)
        self.page.evaluate('document.querySelector("main").innerHTML="<section class=slides-item><div class=actions><button class=\\"click-box op-item\\">喜欢</button></div></section>"')
        self.page.clock.run_for(100)
        self.assertEqual(self.page.locator('.clipgo-page-sidebar').count(),0)
        self.assertEqual(self.page.locator('.actions > .clipgo-page-download').count(),1)

    def test_ambiguous_initial_flows_do_not_guess_current(self):
        self.script=self.script.replace('feeds:[],currentFeedIndex:0','feeds:'+json.dumps([self.b])+',currentFeedIndex:0')
        self.open()
        self.assertFalse(self.page.evaluate('__clipgoPage.diagnostics().active'))
        self.assertTrue(self.page.get_by_role('button',name='ClipGo 下载').is_disabled())
        self.page.evaluate('nav.loadLocalPlaylist(1)')
        self.assertTrue(self.page.evaluate('__clipgoPage.diagnostics().active'))

    def test_unsafe_number_decode_key_is_not_stringified(self):
        self.a['objectDesc']['media'][0]['decodeKey']=18446744073709551615
        self.script=self.script.replace(json.dumps([feed('video-a','第一条 <img src=x onerror=alert(1)>'),self.b]),json.dumps([self.a,self.b]))
        self.open()
        self.page.get_by_role('button',name='ClipGo 下载').click()
        self.page.wait_for_function('document.querySelector(".clipgo-page-download").disabled')
        self.assertNotIn('decodeKey',self.downloads[0]['feed']['objectDesc']['media'][0])

    def test_legacy_media_origin_uses_the_official_https_mapping(self):
        self.script=self.script.replace('https://finder.video.qq.com/stodownload?encfilekey=video-a',
                                        "http://wxapp.tc.qq.com/stodownload?encfilekey=video-a&raw=%2f+'==")
        self.open()
        self.page.get_by_role('button',name='ClipGo 下载').click()
        self.page.wait_for_function('document.querySelector(".clipgo-page-download").disabled')
        media=self.downloads[0]['feed']['objectDesc']['media'][0]
        self.assertEqual(media['url'],"https://finder.video.qq.com/stodownload?encfilekey=video-a&raw=%2f+'==")
        self.assertEqual(media['urlToken'],'&token=test')
        self.assertEqual(media['decodeKey'],'18446744073709551615')


if __name__=='__main__':unittest.main()
