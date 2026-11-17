"""Readable, portable names for NEW artifacts. Existing source paths are untouched."""
from __future__ import annotations
import re,unicodedata,json,os
from pathlib import Path
from datetime import datetime
_RESERVED=re.compile(r'^(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)',re.I)

def safe_component(value, *, max_chars=120, default='experiment'):
    text=unicodedata.normalize('NFC',str(value or default))
    text=re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]','_',text)
    text=re.sub(r'\s+',' ',text).strip(' .')
    text=re.sub('_+','_',text) or default
    if _RESERVED.match(text):text='_'+text
    # Keep both the experimental prefix and meaningful suffix if space is tight.
    if len(text)>max_chars:
        ext=Path(text).suffix
        if len(ext)>12:ext=''
        stem=text[:-len(ext)] if ext else text
        room=max_chars-len(ext)-2
        left=max(1,(room*2)//3);right=max(1,room-left)
        text=stem[:left].rstrip(' ._')+'__'+stem[-right:].lstrip(' ._')+ext
    return text.rstrip(' .') or default

def path_component(parent,value, *, reserve=48, max_chars=120):
    # UTF-16 units matter on Windows; reserve room for descendants and filenames.
    parent=Path(parent)
    used=len(str(parent.resolve()).encode('utf-16-le'))//2
    budget=min(max_chars,235-used-reserve-1)
    if budget<24:
        raise ValueError('The selected output folder is too deep. Choose a shorter output root so project and sample names remain readable.')
    out=safe_component(value,max_chars=budget)
    while len(out.encode('utf-16-le'))//2>budget:out=safe_component(out,max_chars=len(out)-1)
    return out

def new_folder(parent, label, *, now=None, reserve=48):
    parent=Path(parent)
    stamp=(now or datetime.now()).strftime('%Y-%m-%d_%H%M%S')
    base=path_component(parent,f'{label}_{stamp}',reserve=reserve,max_chars=140)
    parent.mkdir(parents=True,exist_ok=True)
    for i in range(1,10000):
        name=base if i==1 else path_component(parent,f'{base}_{i:02d}',reserve=reserve,max_chars=150)
        folder=parent/name
        try:folder.mkdir()
        except FileExistsError:continue
        (folder/'name_mapping.json').write_text(json.dumps({'original_label':str(label),'folder_name':name,'created':stamp,'policy':'readable name, local timestamp, numbered collisions; sources unchanged'},ensure_ascii=False,indent=2),encoding='utf-8')
        return folder
    raise FileExistsError('Too many folders with the same label and timestamp.')
