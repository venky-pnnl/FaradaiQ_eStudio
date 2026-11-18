"""Fetch and verify the published macOS-compatible release package."""
import sys,json,urllib.request,hashlib,tempfile,zipfile,subprocess,shutil,re,os
from pathlib import Path
REPO='venky-pnnl/FaradaiQ_eStudio'
def get(url):return urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'FaradaIQ-eStudio-updater','Accept':'application/vnd.github+json'}),timeout=90)
def main():
    if sys.platform!='darwin':raise RuntimeError('Run this updater on macOS.')
    with get(f'https://api.github.com/repos/{REPO}/releases/latest') as stream:release=json.load(stream)
    assets={x['name']:x for x in release['assets']}
    candidates=[n for n in assets if re.fullmatch(r'FaradaIQ_eStudio_v\d+_package.zip',n)]
    if len(candidates)!=1:raise RuntimeError('Expected exactly one published eStudio package ZIP.')
    name=candidates[0]
    with get(assets[name+'.sha256']['browser_download_url']) as stream:expected=stream.read().decode().split()[0].lower()
    if not re.fullmatch('[a-f0-9]{64}',expected):raise RuntimeError('Invalid published checksum.')
    with tempfile.TemporaryDirectory(prefix='FaradaIQ-eStudio-update-') as temporary:
        root=Path(temporary);archive=root/name
        with get(assets[name]['browser_download_url']) as source,archive.open('wb') as target:shutil.copyfileobj(source,target)
        digest=hashlib.sha256()
        with archive.open('rb') as source:
            for block in iter(lambda:source.read(8*1024*1024),b''):digest.update(block)
        if digest.hexdigest()!=expected:raise RuntimeError('Downloaded ZIP failed SHA-256 validation.')
        payload=root/'package';payload.mkdir()
        with zipfile.ZipFile(archive) as package:
            if sum(item.file_size for item in package.infolist())>4*1024**3:raise RuntimeError('Unexpected package expansion size.')
            for item in package.infolist():
                if '\\' in item.filename or not (payload/item.filename).resolve().is_relative_to(payload.resolve()) or ((item.external_attr>>16)&0o170000)==0o120000:raise RuntimeError('Unsafe package path.')
            package.extractall(payload)
        meta=json.loads((payload/'release.json').read_text())
        if meta['package_asset']!=name or meta['version']!=release['tag_name']:raise RuntimeError('Release identity mismatch.')
        environment=os.environ.copy();environment['FARADAIQ_SOURCE_PACKAGE_ZIP']=str(archive)
        installer=payload/f"Install_FaradaIQ_eStudio_{meta['version']}.sh"
        subprocess.run(['/bin/bash',str(installer),*sys.argv[1:]],env=environment,check=True)
if __name__=='__main__':
    try:main()
    except Exception as exc:sys.exit('Update failed: '+str(exc))
