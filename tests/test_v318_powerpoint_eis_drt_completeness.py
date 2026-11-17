from pathlib import Path
import json, sys, zipfile, shutil


def test_v318_powerpoint_receipt_forces_standard_eis_and_drt_slides(tmp_path):
    from faradaiq.v290_powerpoint_summary import write_powerpoint_summary
    src = Path('/mnt/data/result_v6/20260902_16_V6-suc__552a6cdd')
    if not src.exists():
        return
    work = tmp_path / 'result'
    shutil.copytree(src, work)
    manifest = json.loads((work / 'faradaiq_portable_browser_manifest.json').read_text())
    receipt = write_powerpoint_summary(work, manifest=manifest, use_direct_llm=False)
    titles = [str(x.get('title') or '') for x in receipt.get('slide_index') or []]
    assert receipt.get('v318_forced_standard_eis_drt_slides') is True
    assert any('Usual EIS figures: Nyquist and Bode magnitude' == t for t in titles)
    assert any('Usual EIS figures: Bode phase' == t for t in titles)
    assert any('DRT figures: relaxation behavior versus potential' == t for t in titles)
    assert (work / 'FaradaIQ_Data_Summary.pptx').exists()
    with zipfile.ZipFile(work / 'FaradaIQ_Data_Summary.pptx') as z:
        text = '\n'.join(z.read(n).decode('utf-8', errors='ignore') for n in z.namelist() if n.startswith('ppt/slides/slide') and n.endswith('.xml'))
    assert 'Usual EIS figures: Nyquist and Bode magnitude' in text
    assert 'Usual EIS figures: Bode phase' in text
    assert 'DRT figures: relaxation behavior versus potential' in text


def test_v320_release_identity_and_app_preference():
    import faradaiq.release_identity as rid
    assert rid.APP_VERSION == 'v320'
    assert rid.SEMVER == '0.320.0'
    app = Path(__file__).parents[1] / 'faradaiq' / 'app.py'
    text = app.read_text()
    assert '_v320_main_final' in text
    assert text.index('_v320_main_final') < text.index('_v319_main_final')
