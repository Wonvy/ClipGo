"""Shareable HTML-only executable entry point."""
from pathlib import Path
import sys

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == '__main__':
    from wechat_channels.web_app import main
    if len(sys.argv) == 1:
        main()
    elif len(sys.argv) == 3 and sys.argv[1] == '--diagnose-web':
        main(sys.argv[2])
    else:
        raise SystemExit(2)
