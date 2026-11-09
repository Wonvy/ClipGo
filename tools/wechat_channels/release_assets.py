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

RUNTIME_PACKAGES = ('Pillow', 'pywebview', 'pythonnet', 'clr_loader', 'bottle',
                    'proxy_tools', 'typing_extensions', 'cffi', 'pycparser')
ROOT = Path(__file__).resolve().parents[2]


def prepare(version, target):
    if not re.fullmatch(r'\d+\.\d+\.\d+', version) or any(int(n)>65535 for n in version.split('.')):
        raise ValueError('Expected a three-part Windows version')
    target.mkdir(parents=True, exist_ok=True)
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
            if not (path.name.upper().startswith(('LICENSE','COPYING','NOTICE')) or 'licenses' in path.parts):
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
