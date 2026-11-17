"""Sequence-aware electrochemistry role labeling for FaradaIQ Studio v297.

Keeps repeated measurements at the same point distinct when acquisition order
provides an unambiguous scientific relationship.  In particular, EIS acquired
before and after an IV/Tafel polarization step is not merged into one browser
method.  The labeling is deterministic and does not infer a pre/post role when
there is no Tafel/IV anchor or no usable acquisition order.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, MutableMapping, Sequence

VERSION = "v297"


def _ival(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except Exception:
        return default


def _order_key(rec: Mapping[str, Any]) -> tuple[int, int, int, int, int]:
    # HEKA hierarchy is the strongest chronology signal.  source_order is only
    # a final fallback because canonical export/copy order may differ.
    return (
        _ival(rec.get("group_index"), 0),
        _ival(rec.get("series_index_in_group"), _ival(rec.get("series_index"), 0)),
        _ival(rec.get("sweep"), 0),
        _ival(rec.get("cycle"), 0),
        _ival(rec.get("source_order"), 0),
    )


def _has_meaningful_order(rec: Mapping[str, Any]) -> bool:
    return any(_ival(rec.get(k), 0) > 0 for k in ("group_index", "series_index_in_group", "series_index", "sweep", "source_order"))


def _decorate(rec: MutableMapping[str, Any], role: str, label: str, anchor: str = "IV/TAFEL") -> None:
    rec["sequence_role"] = role
    rec["sequence_role_label"] = label
    rec["sequence_role_anchor"] = anchor
    rec["sequence_role_basis"] = "relative HEKA acquisition order within the same point"
    base_title = str(rec.get("method_title") or rec.get("series_title") or rec.get("technique_label") or rec.get("technique_key") or "measurement").strip()
    # Avoid double decoration if a cached catalog is rebuilt.
    for suffix in (" · Pre-Tafel", " · Post-Tafel", " · Between Tafel steps"):
        if base_title.endswith(suffix):
            base_title = base_title[:-len(suffix)]
    rec["method_title"] = f"{base_title} · {label}"
    sig = str(rec.get("method_signature") or f"{rec.get('technique_key','')}:{base_title}")
    sig = sig.split("|sequence_role=")[0]
    rec["method_signature"] = f"{sig}|sequence_role={role}"
    ev = list(rec.get("evidence") or [])
    msg = f"{label} determined from acquisition order relative to IV/Tafel at the same point"
    if msg not in ev:
        ev.append(msg)
    rec["evidence"] = ev


def assign_sequence_roles(records: Sequence[MutableMapping[str, Any]]) -> dict[str, Any]:
    """Label EIS/OCP around a Tafel/IV anchor without guessing.

    When at least one IV/TAFEL record exists at a point and acquisition order is
    available, EIS records strictly before the first Tafel record are labeled
    Pre-Tafel EIS and records strictly after the last Tafel record are labeled
    Post-Tafel EIS.  The same rule is applied to repeated OCP records because a
    before/after OCP distinction is also scientifically meaningful.  Measurements
    inside a multi-Tafel bracket are labeled Between Tafel steps.
    """
    by_point: dict[int, list[MutableMapping[str, Any]]] = defaultdict(list)
    for rec in records:
        pt = _ival(rec.get("point"), 0)
        if pt > 0:
            by_point[pt].append(rec)

    counts = defaultdict(int)
    points_with_anchor = 0
    for pt, rows in by_point.items():
        tafels = [r for r in rows if str(r.get("technique_key") or "").upper() in {"IV/TAFEL", "TAFEL", "IV"} and _has_meaningful_order(r)]
        if not tafels:
            continue
        tafel_keys = sorted(_order_key(r) for r in tafels)
        first_tafel, last_tafel = tafel_keys[0], tafel_keys[-1]
        points_with_anchor += 1
        for rec in rows:
            tech = str(rec.get("technique_key") or "").upper()
            if tech not in {"EIS", "OCP", "OCV"} or not _has_meaningful_order(rec):
                continue
            k = _order_key(rec)
            if k < first_tafel:
                role = "pre_tafel"
                label = "Pre-Tafel EIS" if tech == "EIS" else "Pre-Tafel OCP"
            elif k > last_tafel:
                role = "post_tafel"
                label = "Post-Tafel EIS" if tech == "EIS" else "Post-Tafel OCP"
            else:
                # Only useful when there are multiple Tafel/IV series and the
                # repeated diagnostic measurement occurs between them.
                if len(tafel_keys) < 2:
                    continue
                role = "between_tafel"
                label = "Between Tafel steps EIS" if tech == "EIS" else "Between Tafel steps OCP"
            _decorate(rec, role, label)
            counts[f"{tech}:{role}"] += 1

    return {
        "version": VERSION,
        "points_with_tafel_anchor": points_with_anchor,
        "role_counts": dict(counts),
    }
