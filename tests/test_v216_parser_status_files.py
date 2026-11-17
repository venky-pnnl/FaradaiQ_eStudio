from __future__ import annotations
from pathlib import Path
from types import SimpleNamespace


def test_parser_monitor_reports_command_and_cumulative_file_counts(tmp_path, monkeypatch):
    import faradaiq_studio_core as core
    from faradaiq import eta_v216

    # Three parser commands representing 2, 3, and 4 input files.
    dirs=[]
    for n in (2,3,4):
        d=tmp_path/f'in{n}'; d.mkdir()
        for i in range(n): (d/f'f{i}.csv').write_text('x,y\n1,2\n',encoding='utf-8')
        dirs.append(d)
    progress=tmp_path/'progress.txt'
    commands=[(['python','parse_CA.py',str(d)],tmp_path) for d in dirs]
    eta_v216.write_profiles(progress,commands)

    stages=[
        '1 of 3 RUNNING parse_CA files 0/2',
        '1 of 3 DONE rc=0',
        '2 of 3 RUNNING parse_CA files 0/3',
        '2 of 3 DONE rc=0',
        '3 of 3 RUNNING parse_CA files 0/4',
        '3 of 3 DONE rc=0',
        '3 of 3 FINISHED complete',
        '3 of 3 FINISHED complete',
        '3 of 3 FINISHED complete',
    ]
    progress.write_text(stages[0],encoding='utf-8')

    class Var:
        def __init__(self): self.value=''; self.history=[]
        def set(self,v): self.value=str(v); self.history.append(self.value)
        def get(self): return self.value
    class Prog:
        def configure(self,**kwargs): self.kwargs=kwargs
    class Gui:
        def __init__(self):
            self._faradaiq_pyseccm_status_var=Var(); self._faradaiq_pyseccm_progress=Prog(); self.status=[]; self.i=0
        def set_status(self,v): self.status.append(str(v))
        def after(self,_ms,callback):
            if self.i < len(stages): progress.write_text(stages[self.i],encoding='utf-8')
            self.i += 1
            callback()
    gui=Gui()
    monkeypatch.setattr(core,'_v67_add_pyseccm_status_bar',lambda _g: None)
    core._v216_parser_monitor(gui,progress,3)
    hist='\n'.join(gui._faradaiq_pyseccm_status_var.history)
    assert 'files parsed 0/9 (0%)' in hist
    assert 'files parsed 2/9 (22%)' in hist
    assert 'files parsed 5/9 (56%)' in hist
    assert 'files parsed 9/9 (100%)' in hist
    assert 'finished 3/3 command(s); files parsed 9/9' in gui._faradaiq_pyseccm_status_var.value
