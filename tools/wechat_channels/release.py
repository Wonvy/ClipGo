"""Shareable HTML-only executable entry point."""
from pathlib import Path
import sys

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
    from wechat_channels.web_app import main
    if len(sys.argv) == 1:
        main()
    elif len(sys.argv) == 2 and sys.argv[1] == '--page-button':
        main(enable_page_button=True)
    elif len(sys.argv) == 3 and sys.argv[1] == '--diagnose-web':
        main(sys.argv[2])
    else:
        raise SystemExit(2)
