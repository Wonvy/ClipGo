"""Source and frozen application entry point."""
import os
import sys
from pathlib import Path

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == '__main__':
    if len(sys.argv) >= 2 and sys.argv[1] == '--elevated-user':
        from wechat_channels.elevation import elevated_main
        raise SystemExit(elevated_main(sys.argv[2:]))
    if len(sys.argv) == 3 and sys.argv[1] == '--page-proxy':
        from wechat_channels.page_proxy import main
        main(sys.argv[2])
        raise SystemExit(0)
    if len(sys.argv) == 2 and sys.argv[1] == '--page-button':
        from wechat_channels.web_app import main
        main(enable_page_button=True)
        raise SystemExit(0)
    if len(sys.argv) >= 2 and sys.argv[1] == '--diagnose-web':
        if len(sys.argv) != 3:
            raise SystemExit(2)
        from wechat_channels.web_app import main
        main(sys.argv[2])
        raise SystemExit(0)
    if '--diagnose-ui' in sys.argv:
        from wechat_channels.app import diagnose_ui
        diagnose_ui(sys.argv[sys.argv.index('--diagnose-ui') + 1])
        raise SystemExit(0)
    if len(sys.argv) >= 2 and sys.argv[1] == '--diagnose-scan':
        import json
        import threading
        from wechat_channels.memory import ScanError, scan_loaded_videos
        if len(sys.argv) != 3:
            raise SystemExit(2)
        diagnostics = {}
        result = {'status': 'error', 'candidate_count': 0, 'diagnostics': diagnostics}
        try:
            items = scan_loaded_videos(threading.Event(), diagnostics=diagnostics)
            result.update(status='ok', candidate_count=len(items))
        except ScanError as exc:
            result['error'] = str(exc)
        except Exception:
            result['error'] = '读取诊断未完成，发生内部错误。'
        # This diagnostic deliberately excludes URLs, keys and process details.
        Path(sys.argv[2]).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
        raise SystemExit(0 if result['status'] == 'ok' else 2)
    elif '--capture' in sys.argv:
        sys.argv.remove('--capture')
        # Windowless builds have no console streams.
        if sys.stdout is None:
            sys.stdout = open(os.devnull, 'w')
        if sys.stderr is None:
            sys.stderr = open(os.devnull, 'w')
        from mitmproxy.tools.main import mitmdump
        mitmdump()
    elif '--legacy-ui' in sys.argv:
        from wechat_channels.app import main
        main()
    else:
        from wechat_channels.web_app import main
        main()
