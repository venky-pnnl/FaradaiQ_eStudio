from __future__ import annotations

from faradaiq.simple_browser_v302 import _simple_selected_context


class _Tree:
    def __init__(self, text: str):
        self._text=text
    def selection(self): return ('n1',)
    def item(self, iid): return {'text':self._text,'values':('technique','')}
    def parent(self, iid): return ''


class _Browser:
    def __init__(self,text: str): self.tree=_Tree(text)
    def selected_paths(self, plottable_only=False): return ['one.csv']


def test_ocp_context_does_not_match_cv_or_cp_substrings():
    kind,count=_simple_selected_context(_Browser('Open-circuit potential (OCP / OCV)'))
    assert kind=='OCP'
    assert count==1


def test_tafel_context_is_specific():
    kind,count=_simple_selected_context(_Browser('I-V / Tafel / corrosion'))
    assert kind=='IV/Tafel'
    assert count==1


def test_eis_context_is_specific():
    kind,count=_simple_selected_context(_Browser('Electrochemical impedance spectroscopy (EIS)'))
    assert kind=='EIS'
    assert count==1
