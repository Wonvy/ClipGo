"""Prepare local release artwork, package notices and reproducibility metadata."""
from __future__ import annotations

import argparse
from importlib import metadata
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

RUNTIME_PACKAGES = (
    'Pillow', 'pywebview', 'pythonnet', 'clr_loader', 'bottle', 'proxy_tools',
    'typing_extensions', 'cffi', 'pycparser', 'mitmproxy', 'mitmproxy_rs',
    'aioquic', 'argon2-cffi', 'argon2-cffi-bindings', 'asgiref', 'attrs',
    'backports.zstd', 'bcrypt', 'blinker', 'brotli', 'certifi', 'click',
    'cryptography', 'Flask', 'h11', 'h2', 'hpack', 'hyperframe', 'itsdangerous',
    'Jinja2', 'kaitaistruct', 'ldap3', 'MarkupSafe', 'pyasn1', 'pylsqpack',
    'pyOpenSSL', 'pyparsing', 'pyperclip', 'ruamel.yaml', 'sortedcontainers',
    'tornado', 'Werkzeug', 'wsproto', 'publicsuffix2', 'pydivert',
)
# Ship these unmodified libraries as readable/replaceable Python files. Their
# complete Python source is also retained with the license notices below.
SOURCE_PACKAGES = {'ldap3': 'ldap3', 'pydivert': 'pydivert',
                   'certifi': 'certifi', 'publicsuffix2': 'publicsuffix2'}
ROOT = Path(__file__).resolve().parents[2]


def prepare(version, target):
    if not re.fullmatch(r'\d+\.\d+\.\d+', version) or any(int(n)>65535 for n in version.split('.')):
        raise ValueError('Expected a three-part Windows version')
    target.mkdir(parents=True, exist_ok=True)
    hooks = target/'hooks'
    hooks.mkdir(exist_ok=True)
    for module in SOURCE_PACKAGES.values():
        (hooks/f'hook-{module}.py').write_text(
            "from PyInstaller.utils.hooks import collect_data_files\n"
            "module_collection_mode = 'py'\n"
            + ("datas = []  # No WinDivert DLL, SYS or driver files.\n" if module == 'pydivert'
               else f"datas = collect_data_files({module!r})\n"), encoding='utf-8')
    (hooks/'hook-mitmproxy_rs.py').write_text(
        "from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs\n"
        "datas = collect_data_files('mitmproxy_rs')\n"
        "binaries = collect_dynamic_libs('mitmproxy_rs')\n"
        "hiddenimports = []  # Regular proxy does not load the local redirector.\n",
        encoding='utf-8')
    from PIL import Image
    with Image.open(ROOT/'extension/icons/icon128.png') as source:
        source.convert('RGBA').save(target/'ClipGoChannels.ico',sizes=[(16,16),(32,32),(48,48),(128,128)])
    resource = """VSVersionInfo(
 ffi=FixedFileInfo(filevers=VERSION_TUPLE,prodvers=VERSION_TUPLE,mask=0x3f,flags=0x0,OS=0x40004,fileType=0x1,subtype=0x0,date=(0,0)),
 kids=[StringFileInfo([StringTable('080404B0',[
  StringStruct('CompanyName','ClipGo'),
  StringStruct('FileDescription','ClipGo 视频号'),
  StringStruct('FileVersion','VERSION_TEXT'),
  StringStruct('InternalName','ClipGoChannels'),
  StringStruct('LegalCopyright','ClipGo contributors'),
  StringStruct('OriginalFilename','ClipGoChannels.exe'),
  StringStruct('ProductName','ClipGo 视频号'),
  StringStruct('ProductVersion','VERSION_TEXT')])]),
  VarFileInfo([VarStruct('Translation',[2052,1200])])])
"""
    resource = resource.replace('VERSION_TUPLE', repr(tuple(map(int, version.split('.')))+(0,)))
    (target/'version.txt').write_text(resource.replace('VERSION_TEXT',version),encoding='utf-8')


def collect_notices(version, payload):
    destination = payload/'licenses'
    destination.mkdir(parents=True,exist_ok=True)
    for package in (*RUNTIME_PACKAGES, 'PyInstaller'):
        dist = metadata.distribution(package)
        for item in dist.files or []:
            path = Path(str(item))
            name = path.name.upper()
            source_module = SOURCE_PACKAGES.get(package)
            source_file = (source_module and path.parts and path.parts[0] == source_module
                           and path.suffix in ('.py', '.dat', '.ABOUT'))
            if not (name.startswith(('LICENSE', 'COPYING', 'NOTICE', 'AUTHORS'))
                    or name.endswith(('.LICENSE', '.LICENCE'))
                    or 'licenses' in path.parts or 'sboms' in path.parts or source_file):
                continue
            source = Path(dist.locate_file(item))
            if not source.is_file():
                continue
            # Keep full package-relative paths; nested third-party notices must
            # not overwrite another license with the same generic filename.
            if path.is_absolute() or '..' in path.parts:
                continue
            target = destination/package/path
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,target)
    # These notices cover native code whose wheel metadata does not include
    # all upstream notices. Missing required originals must block distribution.
    for relative in ('mitmproxy_rs-0.12.15/LICENSE',
                     'native-proxy/THIRD-PARTY-LICENSES.txt',
                     'native-proxy/PROVENANCE.json',
                     'OpenSSL-4.0.3/LICENSE.txt',
                     'library-source/ldap3-2.9.1.tar.gz',
                     'library-source/pydivert-3.1.3-source.tar.gz',
                     'library-source/PROVENANCE.json'):
        if not (ROOT/'packaging/wechat_channels/licenses'/relative).is_file():
            raise FileNotFoundError(f'Required proxy release notice missing: {relative}')
    python_license = Path(sys.base_prefix)/'LICENSE.txt'
    if not python_license.is_file():
        raise FileNotFoundError('Python redistribution license is missing')
    (destination/'Python').mkdir(exist_ok=True)
    shutil.copy2(python_license,destination/'Python/LICENSE.txt')
    license_source = ROOT/'packaging/wechat_channels/licenses'
    if not license_source.is_dir():
        raise FileNotFoundError('Release third-party licenses are missing')
    shutil.copytree(license_source,destination/'release',dirs_exist_ok=True)
    shutil.copy2(ROOT/'packaging/wechat_channels/RELEASE-NOTICES.txt',payload/'THIRD-PARTY-NOTICES.txt')
    shutil.copy2(ROOT/'tools/wechat_channels/SHARE-README.txt',payload/'使用说明.txt')
    commit = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    dirty = bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip())
    manifest = {'product':'ClipGoChannels','version':version,'architecture':'x64',
                'python':sys.version.split()[0],'source_commit':commit,'source_dirty':dirty,
                'dependencies':{name:metadata.version(name) for name in RUNTIME_PACKAGES}}
    (payload/'release.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['prepare','notices'])
    parser.add_argument('--version',required=True)
    parser.add_argument('--target',required=True,type=Path)
    args=parser.parse_args()
    (prepare if args.action=='prepare' else collect_notices)(args.version,args.target)
