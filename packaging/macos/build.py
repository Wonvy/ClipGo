"""Build a macOS source installer ZIP on any platform (LF text, Unix modes)."""
import hashlib
import json
import pathlib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
VERSION = json.loads((ROOT / 'extension/manifest.json').read_text(encoding='utf-8-sig'))['version']
OUTPUT = ROOT / 'dist' / f'ClipGo-{VERSION}-macOS.zip'
TEXT = {'.sh', '.command', '.py', '.js', '.json', '.html', '.css', '.txt', '.md'}


def add(archive, path, relative):
    data = path.read_bytes()
    if path.suffix in TEXT:
        data = data.decode('utf-8-sig').replace('\r\n', '\n').encode('utf-8')
    info = zipfile.ZipInfo('ClipGo/' + relative)
    info.create_system = 3
    info.external_attr = (0o100755 if path.suffix in {'.sh', '.command'} else 0o100644) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, data)


OUTPUT.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(OUTPUT, 'w') as archive:
    for folder in ('extension', 'host/plugins'):
        for path in sorted((ROOT / folder).rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
                add(archive, path, path.relative_to(ROOT).as_posix())
    for name in ('clipgo_host.py', 'browser-stores.json', 'install-mac.sh', 'uninstall-mac.sh'):
        add(archive, ROOT / 'host' / name, 'host/' + name)
    add(archive, ROOT / 'packaging/macos/Install ClipGo.command', 'Install ClipGo.command')
    add(archive, ROOT / 'packaging/macos/README.txt', 'README-Mac.txt')

with zipfile.ZipFile(OUTPUT) as archive:
    assert archive.testzip() is None
    assert b'\r' not in archive.read('ClipGo/host/install-mac.sh')
    assert archive.getinfo('ClipGo/Install ClipGo.command').external_attr >> 16 & 0o111
    assert 'ClipGo/extension/manifest.json' in archive.namelist()
    assert 'ClipGo/host/plugins/yt_dlp_plugins/extractor/clipgo_tencent.py' in archive.namelist()
digest = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
OUTPUT.with_suffix('.zip.sha256').write_text(f'{digest}  {OUTPUT.name}\n', encoding='ascii')
print(OUTPUT)
print(digest)
