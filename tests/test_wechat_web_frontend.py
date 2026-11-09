"""Browser integration checks for our own local HTML assets and mocked bridge.

Run explicitly with the development Playwright install; no WeChat/process reads.
"""
import base64
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from PIL import Image

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

ROOT = Path(__file__).resolve().parents[1]
EDGE = Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
ASSETS = ROOT / 'tools/wechat_channels/web_assets/index.html'


@unittest.skipUnless(sync_playwright and EDGE.exists(), 'Optional browser test runtime is unavailable')
class WebFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = sync_playwright().start()
        cls.browser = cls.runtime.chromium.launch(executable_path=str(EDGE), headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()

    def setUp(self):
        self.page = self.browser.new_page(viewport={'width':1120,'height':800})
        self.page.clock.install()
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        covers = []
        fallback_image = io.BytesIO()
        Image.new('RGB',(32,48),(190,211,198)).save(fallback_image,format='PNG')
        fallback_cover = 'data:image/png;base64,' + base64.b64encode(fallback_image.getvalue()).decode()
        for name in ('wechat-detached-preview.jpg','wechat-downloaded-preview.jpg','wechat-thumbnail-preview.jpg'):
            path = ROOT / 'build' / name
            covers.append('data:image/jpeg;base64,' + base64.b64encode(path.read_bytes()).decode() if path.exists() else fallback_cover)
        items = [
            {'id':'interview','title':'走进影视飓风，聊聊创作、生活与理想','author':'陈溥江','duration':1407,'thumbnail':covers[0],
             'engagement':{'recommend':34000,'share':70000,'like':23000,'comment':3712}},
            {'id':'basketball','title':'把热爱留在球场上，每一次跳投都是新的开始','author':'篮球日记','duration':22,'thumbnail':covers[1]},
            {'id':'family','title':'每次喊爸爸都是越喊越大声 🥰 记录生活中的小瞬间','author':'i是清樾呀','duration':10,'thumbnail':covers[2]},
            {'id':'long','title':'一个很长的视频标题：城市、光影与日常观察。' * 15,'author':'生活记录','duration':3705,'thumbnail':covers[0]},
            {'id':'missing','title':'没有封面的视频仍可显示标题','author':'影像随记','duration':35,'thumbnail':None},
            {'id':'unsafe','title':'<img src=x onerror="window.titleInjection=true">文字应原样显示','author':'安全测试','duration':8,'thumbnail':None},
            {'id':'pending','title':'暂未读取到标题','author':'','duration':None,'thumbnail':None},
        ]
        for item in items:
            item.update(recognized=item['id']!='pending',status='ready' if item['id']!='pending' else 'pending',thumbnail_state='ready' if item['thumbnail'] else 'unavailable')
            item.setdefault('engagement', {name:None for name in ('recommend','share','like','comment')})
        initial = {'revision':1,'source':'all','folder':r'C:\Users\Wonvy\Downloads\ClipGo视频号','scan':{'state':'done','message':'已读取'},'items':items,'download':{'state':'idle'},'preview':{'state':'idle'}}
        self.page.add_init_script('''window.mockState = ''' + json.dumps(initial, ensure_ascii=False) + ''';
          window.bridgeCalls=[];
          window.mockCatalog=structuredClone(mockState.items);
          window.mockRemoved=new Set();
          window.pywebview={api:{
            get_state:async()=>structuredClone(window.mockState),
            scan:async(source='all')=>{bridgeCalls.push(['scan',source]);mockState.source=source;
              const retained=new Map(mockState.items.map(item=>[item.id,item]));
              for(const item of mockCatalog)if(!mockRemoved.has(item.id)&&!retained.has(item.id))retained.set(item.id,structuredClone(item));
              mockState.items=[...retained.values()].filter(item=>!mockRemoved.has(item.id));mockState.revision++;return {ok:true};},
            remove_video:async(id)=>{bridgeCalls.push(['remove_video',id]);const removed=mockState.items.some(item=>item.id===id)?1:0;mockRemoved.add(id);
              mockState.items=mockState.items.filter(item=>item.id!==id);
              if(mockState.preview.id===id)mockState.preview={state:'idle'};
              mockState.revision++;return {ok:true,removed};},
            clear_videos:async()=>{bridgeCalls.push(['clear_videos']);const removed=mockState.items.length;
              for(const item of mockState.items)mockRemoved.add(item.id);
              mockState.items=[];mockState.preview={state:'idle'};mockState.revision++;return {ok:true,removed};},
            download:async(id)=>{bridgeCalls.push(['download',id]);const item=mockState.items.find(i=>i.id===id);item.status='downloading';mockState.download={state:'downloading',id,title:item.title,received:1048576,total:2097152,percent:50,message:'正在下载'};mockState.revision++;return {ok:true};},
            cancel_download:async()=>{bridgeCalls.push(['cancel']);mockState.items.find(i=>i.id===mockState.download.id).status='cancelled';mockState.download.state='cancelled';mockState.download.percent=0;mockState.revision++;return {ok:true};},
            choose_folder:async()=>{bridgeCalls.push(['choose_folder']);mockState.folder='D:\\Videos';mockState.revision++;return {ok:true};},
            open_folder:async()=>{bridgeCalls.push(['open_folder']);return {ok:true};},
            open_video:async(id)=>{bridgeCalls.push(['open_video',id]);return {ok:true};},
            prepare_preview:async(id)=>{bridgeCalls.push(['prepare_preview',id]);mockState.preview={state:'preparing',id,percent:25,message:'正在准备播放'};mockState.revision++;return {ok:true};},
            close_preview:async()=>{bridgeCalls.push(['close_preview']);mockState.preview={state:'idle'};mockState.revision++;return {ok:true};}
          }};''')
        self.page.goto(ASSETS.as_uri())
        self.page.wait_for_function('window.clipgoDiagnostics && window.clipgoDiagnostics().cards === 6')

    def tearDown(self):
        try:
            self.assertEqual(self.errors, [])
        finally:
            self.page.close()

    def assert_engagement(self, stats, values):
        numbers = stats.locator('[data-stat]')
        self.assertEqual(numbers.evaluate_all('nodes=>nodes.map(node=>node.dataset.stat)'),
                         ['recommend','share','like','comment'])
        self.assertEqual([value.strip() for value in numbers.all_text_contents()], values)
        self.assertEqual(stats.locator('svg').count(), 4)
        self.assertEqual(stats.locator('svg use').evaluate_all('nodes=>nodes.map(node=>node.getAttribute("href"))'),
                         ['#i-thumb','#i-share','#i-heart','#i-comment'])
        # Meaning is conveyed by the four icons and accessibility/hover metadata,
        # without adding visible Chinese labels between the screenshot's counts.
        self.assertNotRegex(stats.inner_text(), r'[^0-9.万+—\s]')

    def test_certificate_controls_require_explicit_choice_and_allow_removal(self):
        self.page.evaluate('''()=>{
          mockState.page_certificate={keep:false,installed:true};
          pywebview.api.set_page_certificate_keep=async(keep)=>{
            bridgeCalls.push(['set_page_certificate_keep',keep]);
            mockState.page_certificate.keep=keep;mockState.revision++;return {ok:true};};
          pywebview.api.remove_page_certificate=async()=>{
            bridgeCalls.push(['remove_page_certificate']);
            mockState.page_certificate={keep:false,installed:false};
            mockState.revision++;return {ok:true};};
          mockState.revision++;
        }''')
        self.page.clock.run_for(600)
        self.page.get_by_role('button',name='设置',exact=True).click()
        choice=self.page.get_by_label('保留本机证书',exact=True)
        self.assertFalse(choice.is_checked())
        self.assertNotIn('set_page_certificate_keep',str(self.page.evaluate('bridgeCalls')))
        choice.check()
        self.page.wait_for_function('mockState.page_certificate.keep===true')
        self.assertIn(['set_page_certificate_keep',True],self.page.evaluate('bridgeCalls'))
        self.page.get_by_role('button',name='移除证书',exact=True).click()
        self.page.wait_for_function("document.querySelector('#page-certificate-status').textContent==='未安装'")
        self.assertFalse(choice.is_checked())
        self.assertTrue(self.page.get_by_role('button',name='移除证书',exact=True).is_disabled())
        self.assert_readable_layout('certificate settings')

    def update_engagement(self, values):
        self.page.evaluate("""values=>{
          mockState.items.find(item=>item.id==='interview').engagement=values;
          mockState.revision++;
        }""", values)
        self.page.clock.run_for(600)

    def assert_readable_layout(self, label):
        snapshot = self.page.evaluate("""()=>{
          const shown=element=>{
            if(!element || !element.getClientRects().length)return false;
            for(let current=element;current;current=current.parentElement){
              const style=getComputedStyle(current);
              if(style.display==='none'||['hidden','collapse'].includes(style.visibility)||Number(style.opacity)===0)return false;
            }
            return true;
          };
          const smallText=[];
          const record=(element,text,style=getComputedStyle(element))=>{
            const size=parseFloat(style.fontSize);
            if(!Number.isFinite(size)||size<12)smallText.push({text:text.trim().slice(0,70),size,
              element:element.tagName.toLowerCase(),id:element.id,className:String(element.className)});
          };
          const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
          for(let node;node=walker.nextNode();){
            if(!node.textContent.trim()||!shown(node.parentElement))continue;
            const range=document.createRange();range.selectNodeContents(node);
            if([...range.getClientRects()].some(rect=>rect.width>0&&rect.height>0))record(node.parentElement,node.textContent);
          }
          for(const input of document.querySelectorAll('input,textarea')){
            if(!shown(input)||['hidden','checkbox','radio'].includes(input.type))continue;
            if(input.value)record(input,input.value);
            else if(input.placeholder)record(input,input.placeholder,getComputedStyle(input,'::placeholder'));
          }
          const rects=[...document.querySelectorAll('[data-video-id]')].filter(shown).map(node=>({id:node.dataset.videoId,rect:node.getBoundingClientRect()}));
          const cardOverlaps=[];
          for(let i=0;i<rects.length;i++)for(let j=i+1;j<rects.length;j++){
            const a=rects[i].rect,b=rects[j].rect;
            if(Math.min(a.right,b.right)-Math.max(a.left,b.left)>1 && Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>1)cardOverlaps.push([rects[i].id,rects[j].id]);
          }
          const authorOverlaps=[...document.querySelectorAll('.card-bottom')].filter(shown).filter(row=>{
            const author=row.querySelector('.card-author').getBoundingClientRect(),button=row.querySelector('.card-download').getBoundingClientRect();
            return author.right>button.left+1;
          }).length;
          const panelOverflow=[...document.querySelectorAll('dialog[open],#settings-menu:not([hidden])')].filter(shown).filter(panel=>{
            const rect=panel.getBoundingClientRect();
            return rect.left < -.5 || rect.right>innerWidth+.5 || panel.scrollWidth>panel.clientWidth+1;
          }).map(panel=>panel.id);
          const statLayoutErrors=[];
          for(const stats of document.querySelectorAll('.card-stats,#detail-stats')){
            if(!shown(stats))continue;
            const numbers=[...stats.querySelectorAll('[data-stat]')];
            for(const number of numbers){
              const icon=number.parentElement.querySelector('svg');
              if(!icon||!shown(icon)){statLayoutErrors.push(number.dataset.stat+': missing icon');continue;}
              const i=icon.getBoundingClientRect(),n=number.getBoundingClientRect(),bounds=stats.getBoundingClientRect();
              if(i.bottom>n.top+1||Math.abs((i.left+i.right-n.left-n.right)/2)>2)
                statLayoutErrors.push(number.dataset.stat+': icon is not above its number');
              if(n.left<bounds.left-1||n.right>bounds.right+1)statLayoutErrors.push(number.dataset.stat+': count overflows');
            }
            for(let index=1;index<numbers.length;index++){
              if(numbers[index-1].getBoundingClientRect().right>numbers[index].getBoundingClientRect().left+1)
                statLayoutErrors.push('adjacent counts overlap');
            }
          }
          return {smallText,cardOverlaps,authorOverlaps,panelOverflow,statLayoutErrors,
            horizontalOverflow:document.documentElement.scrollWidth>innerWidth};
        }""")
        problem = (snapshot['smallText'] or snapshot['cardOverlaps'] or snapshot['authorOverlaps']
                   or snapshot['panelOverflow'] or snapshot['statLayoutErrors'] or snapshot['horizontalOverflow'])
        if problem:
            self.page.screenshot(path=str(ROOT / 'build' / f'wechat-layout-failure-{label}.png'))
        self.assertEqual(snapshot['smallText'], [], f'{label}: visible text smaller than 12px')
        self.assertEqual(snapshot['cardOverlaps'], [], f'{label}: cards overlap')
        self.assertEqual(snapshot['authorOverlaps'], 0, f'{label}: author overlaps card action')
        self.assertEqual(snapshot['panelOverflow'], [], f'{label}: popup overflows')
        self.assertEqual(snapshot['statLayoutErrors'], [], f'{label}: engagement icon/count layout')
        self.assertFalse(snapshot['horizontalOverflow'], f'{label}: page overflows horizontally')

    def test_engagement_icons_and_numbers_match_screenshot_order_and_player(self):
        card = self.page.locator('[data-video-id="interview"] .card-stats')
        self.assert_engagement(card, ['3.4万','7万','2.3万','3712'])
        for name, meaning, count in (('recommend','喜欢',34000), ('share','分享',70000),
                                     ('like','点赞',23000), ('comment','评论',3712)):
            metadata = card.locator(f'[data-stat="{name}"]').evaluate("""node=>
              [node,node.parentElement].flatMap(element=>[element.getAttribute('title')||'',element.getAttribute('aria-label')||'']).join(' ')
            """)
            self.assertIn(meaning, metadata)
            self.assertIn(str(count), metadata.replace(',', '').replace('，', ''))
        self.page.locator('[data-video-id="interview"] .card-title').click()
        self.assert_engagement(self.page.locator('#detail-stats'), ['3.4万','7万','2.3万','3712'])
        self.update_engagement({'recommend':0,'share':8,'like':None,'comment':12})
        self.assert_engagement(card, ['0','8','—','12'])
        self.assert_engagement(self.page.locator('#detail-stats'), ['0','8','—','12'])
        self.update_engagement({})
        self.assert_engagement(card, ['—','—','—','—'])
        self.assert_engagement(self.page.locator('#detail-stats'), ['—','—','—','—'])
        self.page.locator('#close-details').click()

    def test_engagement_format_boundaries_and_invalid_values(self):
        card = self.page.locator('[data-video-id="interview"] .card-stats')
        cases = [
            ({'recommend':9999,'share':10000,'like':99999,'comment':100000}, ['9999','1万','9.9万','10万+']),
            ({'recommend':0,'share':10599,'like':9007199254740991,'comment':9007199254740992}, ['0','1万','10万+','—']),
            ({'recommend':-1,'share':1.5,'like':True,'comment':'123'}, ['—','—','—','—']),
        ]
        for values, expected in cases:
            with self.subTest(values=values):
                self.update_engagement(values)
                self.assert_engagement(card, expected)
        self.assert_engagement(self.page.locator('[data-video-id="missing"] .card-stats'), ['—','—','—','—'])

    def test_search_filters_and_safe_text_detail_keeps_full_title(self):
        self.page.locator('#search').fill('篮球日记')
        self.assertEqual(self.page.locator('[data-video-id]').count(), 1)
        self.page.locator('#clear-search').click()
        self.page.locator('[data-video-id="unsafe"] .card-title').click()
        self.assertIn('<img src=x', self.page.locator('#details-title').inner_text())
        self.assertFalse(self.page.evaluate('Boolean(window.titleInjection)'))
        self.assertEqual(self.page.locator('#details-title img').count(), 0)
        self.page.locator('#close-details').click()
        self.page.locator('[data-video-id="long"] .card-title').click()
        self.assertGreater(len(self.page.locator('#details-title').inner_text()), 200)

    def test_card_download_cancel_and_open_use_only_the_matching_id(self):
        self.page.locator('[data-video-id="basketball"] .card-download').click()
        self.page.wait_for_function("mockState.download.state==='downloading'")
        self.assertIn(['download','basketball'],self.page.evaluate('bridgeCalls'))
        self.assertTrue(self.page.locator('[data-video-id="family"] .card-download').is_disabled())
        self.assertEqual(self.page.locator('#download-title').inner_text(), self.page.evaluate('mockState.items[1].title'))
        self.page.locator('#cancel-download').click()
        self.page.wait_for_function("document.querySelector('#download-state').textContent==='已取消'")
        self.assertFalse(self.page.locator('[data-video-id="family"] .card-download').is_disabled())
        self.page.evaluate("mockState.items[1].status='done';mockState.download.state='done';mockState.download.percent=100;mockState.revision++")
        self.page.wait_for_function("document.querySelector('[data-video-id=basketball] .card-download span').textContent==='打开'")
        self.page.locator('[data-video-id="basketball"] .card-download').click()
        self.assertIn(['open_video','basketball'],self.page.evaluate('bridgeCalls'))

    def test_filters_folder_and_empty_search(self):
        self.page.locator('#settings-trigger').click()
        self.page.locator('#filter-all').click()
        self.assertEqual(self.page.locator('[data-video-id]').count(),7)
        self.assertEqual(self.page.evaluate('clipgoDiagnostics().titled'),6)
        self.page.locator('#search').fill('不存在的标题关键词')
        self.assertTrue(self.page.locator('#empty-state').is_visible())
        self.assertFalse(self.page.locator('#empty-refresh').is_visible())
        self.page.locator('#clear-search').click()
        self.assertEqual(self.page.locator('#search').input_value(),'')
        self.page.locator('#settings-trigger').click()
        self.page.locator('#choose-folder').click()
        self.page.wait_for_function("document.querySelector('#folder-path').textContent==='D:\\Videos'")
        self.page.locator('#open-folder').click()
        self.assertIn(['open_folder'],self.page.evaluate('bridgeCalls'))

    def test_card_play_space_pause_and_close_cleanup(self):
        card = self.page.locator('[data-video-id="basketball"]')
        card.hover()
        self.page.keyboard.press('Space')
        self.page.wait_for_function("bridgeCalls.some(c=>c[0]==='prepare_preview'&&c[1]==='basketball')")
        self.assertTrue(self.page.locator('#details-dialog').is_visible())
        self.assertIn('正在准备', self.page.locator('#preview-message').inner_text())
        # A real local media response is tested separately; stub only playback
        # controls here to check the keyboard and focus behavior deterministically.
        self.page.evaluate("""()=>{
          const v=document.querySelector('#preview-video');
          window.previewPaused=false;window.playCalls=0;window.pauseCalls=0;
          Object.defineProperty(v,'paused',{get:()=>previewPaused});
          v.play=async()=>{previewPaused=false;playCalls++};
          v.pause=()=>{previewPaused=true;pauseCalls++};
          mockState.preview={state:'ready',id:'basketball',url:'http://127.0.0.1:12345/media/test'};
          mockState.revision++;
        }""")
        self.page.wait_for_function('window.playCalls===1')
        self.page.keyboard.press('Space')
        self.assertEqual(self.page.evaluate('pauseCalls'),1)
        self.page.keyboard.press('Space')
        self.assertEqual(self.page.evaluate('playCalls'),2)
        self.page.locator('#close-details').click()
        self.page.wait_for_function("bridgeCalls.some(c=>c[0]==='close_preview')")
        self.assertIsNone(self.page.locator('#preview-video').get_attribute('src'))
        self.page.locator('#search').fill(' ')
        self.page.locator('#search').press('Space')
        self.assertFalse(self.page.locator('#details-dialog').is_visible())

    def test_auto_refresh_preserves_cards_and_can_be_paused(self):
        self.page.evaluate("window.originalCard=document.querySelector('[data-video-id=interview]')")
        self.page.clock.run_for(5500)
        self.assertGreaterEqual(len([call for call in self.page.evaluate('bridgeCalls') if call[0]=='scan']),1)
        self.assertTrue(self.page.evaluate("originalCard===document.querySelector('[data-video-id=interview]')"))
        self.page.evaluate("""mockState.items.push({id:'newly-loaded',title:'刚刚加载的新视频',author:'新作者',duration:8,recognized:true,status:'ready',thumbnail:null,thumbnail_state:'loading'});mockState.revision++""")
        self.page.clock.run_for(600)
        self.assertEqual(self.page.locator('[data-video-id]').first.get_attribute('data-video-id'),'newly-loaded')
        self.assertTrue(self.page.locator('[data-video-id="newly-loaded"] .new-badge').is_visible())
        self.page.locator('#auto-refresh').click()
        count = len([call for call in self.page.evaluate('bridgeCalls') if call[0]=='scan'])
        self.page.clock.run_for(12000)
        self.assertEqual(len([call for call in self.page.evaluate('bridgeCalls') if call[0]=='scan']),count)

    def test_refresh_retains_playing_row_until_explicit_card_removal(self):
        self.page.locator('[data-video-id="basketball"] .cover-action').click()
        # The underlying renderer can stop reporting an older video. A refresh
        # must retain the library row and its open preview until explicit removal.
        self.page.evaluate("mockCatalog=mockCatalog.filter(i=>i.id!=='basketball');window.pywebview.api.scan()")
        self.page.clock.run_for(600)
        self.assertEqual(self.page.locator('[data-video-id="basketball"]').count(),1)
        self.assertTrue(self.page.locator('#details-dialog').is_visible())
        # Dispatch the real remove button's action while a preview owns the modal
        # layer, covering cleanup when that same entry is explicitly removed.
        self.page.locator('[data-video-id="basketball"] .card-remove').evaluate('button=>button.click()')
        self.page.wait_for_function("!document.querySelector('[data-video-id=basketball]')")
        self.assertFalse(self.page.locator('#details-dialog').is_visible())
        self.assertIsNone(self.page.locator('#preview-video').get_attribute('src'))
        self.assertIn(['remove_video','basketball'],self.page.evaluate('bridgeCalls'))
        self.assertIn(['close_preview'],self.page.evaluate('bridgeCalls'))
        self.assertEqual(len(self.page.evaluate('mockState.items')),6)

    def test_no_source_selector_and_refresh_reads_both_sources(self):
        self.assertEqual(self.page.locator('#source,#source-trigger,#source-menu').count(),0)
        self.assertTrue(self.page.get_by_text('视频列表',exact=True).is_visible())
        self.page.locator('#refresh').click()
        self.page.wait_for_function("bridgeCalls.some(call=>call[0]==='scan'&&call[1]==='all')")
        self.assertEqual(self.page.evaluate('mockState.source'),'all')

    def test_remove_filtered_video_preserves_others_and_auto_refresh_tombstone(self):
        self.page.locator('#search').fill('篮球日记')
        self.assertEqual(self.page.locator('[data-video-id]').count(),1)
        self.page.locator('[data-video-id="basketball"] .card-remove').click()
        self.page.wait_for_function("!mockState.items.some(item=>item.id==='basketball')")
        self.assertEqual(self.page.locator('#search').input_value(),'篮球日记')
        self.assertTrue(self.page.locator('#empty-state').is_visible())
        self.assertIn(['remove_video','basketball'],self.page.evaluate('bridgeCalls'))
        self.assertFalse(any(call[0]=='prepare_preview' for call in self.page.evaluate('bridgeCalls')))
        self.assertEqual(set(self.page.evaluate('mockState.items.map(item=>item.id)')),
                         {'interview','family','long','missing','unsafe','pending'})
        self.page.locator('#clear-search').click()
        self.assertEqual(self.page.locator('[data-video-id]').count(),5)
        self.page.clock.run_for(5500)
        self.assertTrue(self.page.evaluate("mockCatalog.some(item=>item.id==='basketball')"))
        self.assertFalse(self.page.evaluate("mockState.items.some(item=>item.id==='basketball')"))
        self.assertEqual(self.page.locator('[data-video-id="basketball"]').count(),0)

    def test_clear_requires_confirmation_and_includes_filtered_unrecognized_rows(self):
        self.page.locator('#search').fill('篮球日记')
        self.page.locator('#clear-library').click()
        self.assertTrue(self.page.locator('#clear-dialog').is_visible())
        self.assertFalse(any(call[0]=='clear_videos' for call in self.page.evaluate('bridgeCalls')))
        self.page.locator('#cancel-clear').click()
        self.assertFalse(self.page.locator('#clear-dialog').is_visible())
        self.assertEqual(len(self.page.evaluate('mockState.items')),7)
        self.page.locator('#clear-library').click()
        self.page.locator('#confirm-clear').click()
        self.page.wait_for_function('mockState.items.length===0')
        self.assertFalse(self.page.locator('#clear-dialog').is_visible())
        self.assertEqual(self.page.evaluate('mockRemoved.size'),7)
        self.assertTrue(self.page.evaluate("mockRemoved.has('pending')"))
        self.assertTrue(self.page.locator('#empty-state').is_visible())
        self.assertEqual(self.page.locator('[data-video-id]').count(),0)
        self.page.clock.run_for(5500)
        self.assertEqual(self.page.locator('[data-video-id]').count(),0)
        self.assertEqual(self.page.evaluate('mockState.items.length'),0)
        # Empty-library controls remain usable, and only genuinely new media
        # can appear after the explicit clear operation.
        self.page.locator('#search').fill('')
        self.page.evaluate("mockCatalog.push({id:'after-clear',title:'清空后新加载的视频',author:'新作者',duration:9,recognized:true,status:'ready',thumbnail:null,thumbnail_state:'unavailable'})")
        self.page.locator('#refresh').click()
        self.page.wait_for_function("document.querySelector('[data-video-id=after-clear]')")
        self.assertEqual(self.page.locator('[data-video-id]').count(),1)

    def test_real_mp4_decodes_plays_and_seeks_in_the_page(self):
        from tools.wechat_channels.playback import PlaybackServer
        ffmpeg = ROOT / 'build/windows/payload/runtime/bin/ffmpeg.exe'
        if not ffmpeg.exists():
            self.skipTest('Optional local FFmpeg test runtime is unavailable')
        server = PlaybackServer()
        try:
            with tempfile.TemporaryDirectory(prefix='clipgo-player-test-') as temporary:
                path = Path(temporary) / 'sample.mp4'
                subprocess.run([str(ffmpeg),'-hide_banner','-loglevel','error','-f','lavfi','-i',
                    'color=c=0x4e956f:s=160x240:r=10:d=8','-c:v','libx264','-pix_fmt','yuv420p',
                    '-movflags','+faststart','-y',str(path)],check=True,timeout=15)
                url = server.register(path)
                self.page.locator('[data-video-id="basketball"] .cover-action').click()
                self.page.evaluate("""url=>{
                  mockState.preview={state:'ready',id:'basketball',url,percent:100};
                  mockState.revision++;
                }""",url)
                self.page.wait_for_function("document.querySelector('#preview-video').videoWidth===160")
                self.page.wait_for_function("document.querySelector('#preview-video').currentTime>0")
                self.assertFalse(self.page.locator('#preview-loading').is_visible())
                self.page.keyboard.press('Space')
                self.assertTrue(self.page.locator('#preview-video').evaluate('v=>v.paused'))
                self.page.locator('#preview-video').evaluate('v=>{v.currentTime=5}')
                self.page.wait_for_function("Math.abs(document.querySelector('#preview-video').currentTime-5)<.2")
                self.page.keyboard.press('Space')
                self.assertFalse(self.page.locator('#preview-video').evaluate('v=>v.paused'))
                self.page.locator('#close-details').click()
                server.revoke_all()
        finally:
            server.close()

    def test_responsive_layout_and_actual_thumbnail_rendering(self):
        self.page.wait_for_function("[...document.querySelectorAll('img[data-cover]')].filter(i=>!i.hidden).every(i=>i.complete&&i.naturalWidth>0)")
        self.assertEqual(self.page.evaluate('clipgoDiagnostics().thumbnails'),4)
        for width in (1120,840,600):
            self.page.set_viewport_size({'width':width,'height':800})
            self.page.wait_for_timeout(100)
            self.assertFalse(self.page.evaluate('clipgoDiagnostics().horizontalOverflow'))
            overlapping = self.page.evaluate("""[...document.querySelectorAll('.card-bottom')].some(row=>{
              const a=row.querySelector('.card-author').getBoundingClientRect(),b=row.querySelector('.card-download').getBoundingClientRect();
              return a.right>b.left+1;
            })""")
            self.assertFalse(overlapping)
        self.page.set_viewport_size({'width':1120,'height':800})
        self.page.screenshot(path=str(ROOT/'build/wechat-html-layout.png'))

    def test_every_visible_text_is_at_least_twelve_pixels_in_each_ui_state(self):
        for width in (1120,840,600):
            with self.subTest(width=width):
                self.page.set_viewport_size({'width':width,'height':800})
                self.page.clock.run_for(150)
                self.assert_readable_layout(f'{width}-library')
                self.page.locator('#settings-trigger').click()
                self.assertTrue(self.page.locator('#settings-menu').is_visible())
                self.page.clock.run_for(150)
                self.assert_readable_layout(f'{width}-settings')
                self.page.locator('#settings-trigger').click()
                self.page.locator('[data-video-id="long"] .card-title').click()
                self.assertTrue(self.page.locator('#details-dialog').is_visible())
                self.page.clock.run_for(150)
                self.assert_readable_layout(f'{width}-player')
                self.page.locator('#close-details').click()
                self.page.locator('#clear-library').click()
                self.assertTrue(self.page.locator('#clear-dialog').is_visible())
                self.page.clock.run_for(150)
                self.assert_readable_layout(f'{width}-clear')
                self.page.locator('#cancel-clear').click()
                self.page.evaluate("""mockState.download={state:'downloading',id:'basketball',title:'下载进度中文字大小检查',received:1048576,total:2097152,percent:50,message:'正在下载视频，请稍候'};mockState.revision++""")
                self.page.clock.run_for(600)
                self.assertTrue(self.page.locator('#download-panel').is_visible())
                self.assert_readable_layout(f'{width}-download')
                self.page.evaluate("mockState.download={state:'idle'};mockState.revision++")
                self.page.clock.run_for(600)
                self.page.locator('#search').fill('没有匹配的字号检查')
                self.assertTrue(self.page.locator('#empty-state').is_visible())
                self.assert_readable_layout(f'{width}-empty')
                self.page.locator('#clear-search').click()


if __name__ == '__main__':
    unittest.main()
