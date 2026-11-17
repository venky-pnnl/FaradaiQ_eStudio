from pathlib import Path


def test_master_index_lazy_loads_hidden_visualizations(tmp_path: Path):
    from faradaiq.multidim_v216 import _write_master_index

    entries=[]
    for i in range(3):
        child=tmp_path/f'cycle_{i+1}.html'
        child.write_text(f'<html>cycle {i+1}</html>',encoding='utf-8')
        entries.append({'label':f'Cycle {i+1:02d}','html':str(child)})
    master=tmp_path/'master.html'
    _write_master_index(master,'CV cycles',entries)
    text=master.read_text(encoding='utf-8')

    # Only the initially visible cycle receives a real src. Hidden cycles retain
    # their URLs in data-src until the user clicks their tab.
    assert text.count('data-loaded="1"') == 1
    assert text.count('data-loaded="0"') == 2
    assert text.count('src="about:blank"') == 2
    assert 'frame.src=frame.dataset.src' in text
    assert 'Other tabs load only when selected.' in text
