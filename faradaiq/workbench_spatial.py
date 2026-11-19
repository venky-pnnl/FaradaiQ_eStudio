"""Spatial and asynchronous comparisons of explicitly identified scalar exports."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import combinations
import re

import numpy as np
import pandas as pd
from scipy.interpolate import LinearNDInterpolator
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import min_weight_full_bipartite_matching
from scipy.spatial import QhullError, cKDTree
from scipy.stats import rankdata

from .browser_analysis import _UNITS

DISTANCE_UNITS = dict(_UNITS["x"], px=1.0)
TIME_UNITS = _UNITS["time"]
MODALITIES = ("Electrochemistry", "Raman", "Hyperspectral imaging (HSI)", "Thermal", "mMZI")
_PASS = {"pass", "passed", "ok", "success", "successful", "succeeded", "valid"}
_FAIL = {"fail", "failed", "error", "invalid", "unsuccessful"}
_TRUE = {"1", "1.0", "true", "yes"}
_FALSE = {"0", "0.0", "false", "no"}


def _unit(value):
    return str(value or "").strip().replace("μ", "u").replace("µ", "u").replace("Ω", "ohm")


def _axis_scale(source, target, mode):
    scales = DISTANCE_UNITS if mode == "coordinates" else TIME_UNITS
    source, target = _unit(source), _unit(target)
    if source not in scales or target not in scales:
        raise ValueError(f"Choose explicit {'coordinate' if mode == 'coordinates' else 'time'} units.")
    if (source == "px") != (target == "px"):
        raise ValueError("Pixel and physical coordinates require registration before comparison.")
    return float(Decimal(str(scales[source])) / Decimal(str(scales[target])))


def _value_scale(source, target):
    source, target = _unit(source), _unit(target)
    if source == target:
        return 1.0
    for role in ("potential", "current", "frequency", "zreal"):
        scales = _UNITS[role]
        if source in scales and target in scales:
            return float(Decimal(str(scales[source])) / Decimal(str(scales[target])))
    raise ValueError(f"Incompatible scalar units {source!r} and {target!r}.")


def header_unit(column, units):
    """Recognize a terminal explicit unit token for a widget default."""
    name = _unit(column)
    return next((unit for unit in units if re.search(rf"(?:[\s_\[(/]|^){re.escape(unit)}[\])]?$", name)),
                None)


def _columns(frame, columns):
    if not frame.columns.is_unique or len(set(columns)) != len(columns):
        raise ValueError("Select distinct columns from a table with unique column names.")
    if any(column not in frame for column in columns):
        raise ValueError("Select all required input columns.")


def _numeric(frame, column):
    values = pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=float, na_value=np.nan, copy=True)
    values[~np.isfinite(values)] = np.nan
    return values


def _axis_values(frame, column, unit, mode):
    allowed = DISTANCE_UNITS if mode == "coordinates" else TIME_UNITS
    declared = header_unit(column, allowed)
    return _numeric(frame, column) * _axis_scale(declared or unit, unit, mode)


def coordinate_rows(frame, *, axes, axis_unit):
    """Read positions for an overlay that may have no scalar value."""
    if len(axes) != 2:
        raise ValueError("Select an X column and a Y column.")
    _columns(frame, axes)
    return pd.DataFrame({"row": np.arange(1, len(frame) + 1),
                         **{name: _axis_values(frame, column, axis_unit, "coordinates")
                            for name, column in zip(("x", "y"), axes)}})


@dataclass(frozen=True)
class ScalarData:
    rows: pd.DataFrame
    mode: str
    axis_unit: str
    value_unit: str

    @property
    def axes(self):
        return ["x", "y"] if self.mode == "coordinates" else ["time"]

    @property
    def valid(self):
        return (~self.rows.failed & ~self.rows.status_missing
                & self.rows[self.axes + ["value"]].notna().all(axis=1)).to_numpy()


def status_flag_means(column):
    """Interpret binary values in a positive flag by its header."""
    words = set(re.findall(r"[a-z]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", str(column)).casefold()))
    if words & _PASS and not words & (_FAIL | {"not", "failure"}):
        return "Success"
    return "Failure"


def scalar_data(frame, *, axes, value, axis_unit, value_unit, status=None, true_means=None):
    """Prepare a spatial XY map or a time series, retaining unfiltered row identity."""
    if len(axes) not in (1, 2):
        raise ValueError("Select one time column or two coordinate columns.")
    mode = "coordinates" if len(axes) == 2 else "time"
    axis_unit = _unit(axis_unit)
    _axis_scale(axis_unit, axis_unit, mode)
    value_unit = _unit(value_unit)
    if not value_unit:
        raise ValueError("Enter the scalar value unit. Use a.u. or 1 for explicitly arbitrary or dimensionless values.")
    _columns(frame, [*axes, value] + ([status] if status is not None else []))
    names = ["x", "y"] if mode == "coordinates" else ["time"]
    arrays = {name: _axis_values(frame, column, axis_unit, mode) for name, column in zip(names, axes)}
    declared = header_unit(value, (*_UNITS["potential"], *_UNITS["current"], *_UNITS["frequency"],
                                   *_UNITS["zreal"], "K", "°C", "dB", "%", "a.u."))
    values = _numeric(frame, value) * _value_scale(declared or value_unit, value_unit)
    rows = pd.DataFrame({"row": np.arange(1, len(frame) + 1), **arrays, "value": values})
    if status is None:
        rows["failed"] = False
        rows["status_missing"] = False
    else:
        flags = frame[status].astype("string").str.strip().str.casefold()
        unknown = set(flags.dropna()) - _PASS - _FAIL - _TRUE - _FALSE - {""}
        if unknown:
            raise ValueError(f"Unrecognized acquisition status values: {', '.join(sorted(unknown)[:5])}. "
                             "Use pass/fail, true/false or 0/1.")
        true_means = true_means or status_flag_means(status)
        if true_means not in {"Success", "Failure"}:
            raise ValueError("Choose whether true or 1 means success or failure.")
        failed_values = _FALSE if true_means == "Success" else _TRUE
        rows["failed"] = flags.isin(_FAIL | failed_values).to_numpy()
        rows["status_missing"] = (flags.isna() | flags.eq("").fillna(False)).to_numpy(dtype=bool)
    return ScalarData(rows, mode, axis_unit, value_unit)


def _points(values, name, *, minimum=1):
    points = np.asarray(values, dtype=float)
    if (points.ndim != 2 or points.shape[1] != 2 or len(points) < minimum
            or not np.isfinite(points).all()):
        raise ValueError(f"{name} must contain at least {minimum} finite XY pairs.")
    return points


def apply_affine(points, matrix):
    points = np.asarray(points, dtype=float)
    matrix = np.asarray(matrix, dtype=float)
    if points.ndim != 2 or points.shape[1] != 2 or matrix.shape != (2, 3):
        raise ValueError("An affine transform requires XY pairs and a 2 × 3 matrix.")
    return points @ matrix[:, :2].T + matrix[:, 2]


def affine_registration(xy, pixels):
    """Fit and invert a stage-to-image affine transform. Residuals are in pixels."""
    xy, pixels = _points(xy, "Stage landmarks", minimum=3), _points(pixels, "Image landmarks", minimum=3)
    if len(xy) != len(pixels):
        raise ValueError("Enter the same number of stage and image landmarks.")
    origin = xy.mean(axis=0)
    centered = xy - origin
    scale = np.max(np.abs(centered), axis=0)
    if np.any(scale == 0) or np.linalg.matrix_rank(centered / scale) < 2:
        raise ValueError("Stage landmarks must be noncollinear.")
    pixel_origin = pixels.mean(axis=0)
    pixel_centered = pixels - pixel_origin
    pixel_scale = np.max(np.abs(pixel_centered), axis=0)
    if np.any(pixel_scale == 0) or np.linalg.matrix_rank(pixel_centered / pixel_scale) < 2:
        raise ValueError("Image landmarks must be noncollinear.")
    coefficients = np.linalg.lstsq(centered / scale, pixel_centered / pixel_scale, rcond=None)[0].T
    if np.linalg.matrix_rank(coefficients) < 2:
        raise ValueError("The fitted transform is singular and cannot be inverted.")
    linear = coefficients * pixel_scale[:, None] / scale
    forward = np.column_stack((linear, pixel_origin - linear @ origin))
    inverse_linear = np.linalg.inv(linear)
    inverse = np.column_stack((inverse_linear, -inverse_linear @ forward[:, 2]))
    fitted = apply_affine(xy, forward)
    residual = fitted - pixels
    distances = np.linalg.norm(residual, axis=1)
    landmarks = pd.DataFrame(np.column_stack((xy, pixels, fitted, residual, distances)), columns=[
        "x", "y", "u_px", "v_px", "fit_u_px", "fit_v_px", "residual_u_px", "residual_v_px", "residual_px",
    ])
    return forward, inverse, landmarks, float(np.sqrt(np.mean(distances**2)))


def _require_map(data):
    if data.mode != "coordinates":
        raise ValueError("Select a scalar table with two spatial coordinate columns.")


def _cross(a, b):
    return a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0]


def _on_segment(points, a, b, epsilon):
    edge = b - a
    delta = points - a
    return ((np.abs(_cross(edge, delta)) <= epsilon * np.linalg.norm(edge))
            & np.all(points >= np.minimum(a, b) - epsilon, axis=-1)
            & np.all(points <= np.maximum(a, b) + epsilon, axis=-1))


def polygon_mask(points, vertices):
    """Evaluate a simple polygon, including edges and vertices."""
    polygon = _points(vertices, "Polygon", minimum=3)
    if np.array_equal(polygon[0], polygon[-1]):
        polygon = polygon[:-1]
    if len(polygon) < 3:
        raise ValueError("A polygon requires at least three distinct vertices.")
    origin = polygon[0].copy()
    scale = float(np.max(np.ptp(polygon, axis=0)))
    if scale == 0 or len(np.unique(polygon, axis=0)) != len(polygon):
        raise ValueError("Polygon vertices must be distinct.")
    polygon = (polygon - origin) / scale
    points = (np.asarray(points, dtype=float) - origin) / scale
    ends = np.roll(polygon, -1, axis=0)
    epsilon = 32 * np.finfo(float).eps
    for left, (a, b) in enumerate(zip(polygon, ends)):
        for right in range(left + 2, len(polygon)):
            if left == 0 and right == len(polygon) - 1:
                continue
            c, d = polygon[right], ends[right]
            ab = [_cross(b - a, p - a) for p in (c, d)]
            cd = [_cross(d - c, p - c) for p in (a, b)]
            intersects = (ab[0] * ab[1] < 0 and cd[0] * cd[1] < 0
                          or _on_segment(c, a, b, epsilon) or _on_segment(d, a, b, epsilon)
                          or _on_segment(a, c, d, epsilon) or _on_segment(b, c, d, epsilon))
            if intersects:
                raise ValueError("Polygon edges must not cross or touch nonadjacent edges.")
    if abs(_cross(polygon, ends).sum()) <= epsilon:
        raise ValueError("Polygon area must be nonzero.")
    inside = np.zeros(len(points), dtype=bool)
    boundary = inside.copy()
    x, y = points.T
    for a, b in zip(polygon, ends):
        boundary |= _on_segment(points, a, b, epsilon)
        if a[1] != b[1]:
            crossing_x = a[0] + (b[0] - a[0]) / (b[1] - a[1]) * (y - a[1])
            inside ^= ((a[1] > y) != (b[1] > y)) & (x < crossing_x)
    return inside | boundary


def roi_statistics(data, vertices, *, shape):
    """Return the inclusive ROI outline, original points with membership, and finite unfailed statistics."""
    _require_map(data)
    points = data.rows[["x", "y"]].to_numpy()
    if shape == "Rectangle":
        corners = _points(vertices, "Rectangle corners", minimum=2)
        if len(corners) != 2:
            raise ValueError("Enter two opposite corners.")
        lo, hi = corners.min(axis=0), corners.max(axis=0)
        if np.any(lo == hi):
            raise ValueError("Both rectangle sides must have positive length.")
        outline = np.array([lo, [hi[0], lo[1]], hi, [lo[0], hi[1]]])
        inside = np.all((points >= lo) & (points <= hi), axis=1)
    elif shape == "Polygon":
        outline = _points(vertices, "Polygon", minimum=3)
        inside = polygon_mask(points, outline)
    else:
        raise ValueError("Choose a rectangle or polygon ROI.")
    result = data.rows.assign(in_roi=inside, included=inside & data.valid)
    sampled = result.loc[result.included, "value"]
    stats = {"roi_rows": int(inside.sum()), "included": len(sampled), "excluded": int(inside.sum()) - len(sampled),
             "mean": sampled.mean(), "median": sampled.median(), "sample_std": sampled.std(),
             "minimum": sampled.min(), "maximum": sampled.max()}
    return outline, result, stats


def _tolerance(value):
    value = float(value)
    if not np.isfinite(value) or value < 0:
        raise ValueError("Matching tolerance must be finite and nonnegative.")
    return value


def _distance_limit(tolerance, left, right):
    # Conversion and clock-offset arithmetic may shift a boundary by a few ULPs.
    magnitude = np.maximum(np.max(np.abs(left), axis=1), np.max(np.abs(right), axis=1))
    return tolerance + 4 * np.spacing(np.maximum(magnitude, tolerance))


def one_to_one_matches(left, right, tolerance):
    """Maximum-cardinality one-to-one matches, minimizing sum of eligible distances."""
    tolerance = _tolerance(tolerance)
    left, right = np.asarray(left, dtype=float), np.asarray(right, dtype=float)
    if left.ndim != 2 or right.ndim != 2 or left.shape[1] != right.shape[1]:
        raise ValueError("Matching axes must have the same dimension.")
    if not np.isfinite(left).all() or not np.isfinite(right).all():
        raise ValueError("Matching axes must be finite after unit conversion and offsets.")
    if not len(left) or not len(right):
        return np.empty(0, dtype=int), np.empty(0, dtype=int), np.empty(0)
    magnitude = max(float(np.max(np.abs(left))), float(np.max(np.abs(right))), tolerance)
    search = tolerance + 4 * np.spacing(magnitude)
    candidates = cKDTree(left).sparse_distance_matrix(cKDTree(right), search, output_type="coo_matrix")
    eligible = candidates.data <= _distance_limit(tolerance, left[candidates.row], right[candidates.col])
    if not eligible.any():
        return np.empty(0, dtype=int), np.empty(0, dtype=int), np.empty(0)
    # Positive weights retain exact matches in SciPy's sparse graph. Each left
    # row has a private dummy with penalty above the total possible real cost.
    scale = tolerance or 1.0
    rows = np.arange(len(left))
    distances = np.minimum(candidates.data[eligible] / scale, 1)
    costs = coo_matrix((np.r_[1 + distances, np.full(len(left), 2 * len(left) + 1.0)],
                       (np.r_[candidates.row[eligible], rows], np.r_[candidates.col[eligible], len(right) + rows])),
                      shape=(len(left), len(right) + len(left))).tocsr()
    matched_left, matched_right = min_weight_full_bipartite_matching(costs)
    real = matched_right < len(right)
    matched_left, matched_right = matched_left[real], matched_right[real]
    return matched_left, matched_right, np.linalg.norm(left[matched_left] - right[matched_right], axis=1)


def _sample_map(points, values, queries, tolerance, method):
    tolerance = _tolerance(tolerance)
    if not len(points):
        raise ValueError("The source map has no usable coordinates and scalar values.")
    indices = np.full(len(queries), -1, dtype=int)
    distances, nearest = cKDTree(points).query(queries)
    result = np.full(len(queries), np.nan)
    near = distances <= _distance_limit(tolerance, queries, points[nearest])
    if method == "Nearest":
        indices[near] = nearest[near]
        result[near] = values[nearest[near]]
    elif method == "Linear":
        if len(points) < 2 or len(np.unique(points, axis=0)) != len(points):
            raise ValueError("Linear interpolation needs at least two unique locations. Resolve repeated coordinates first.")
        centered = points - points.mean(axis=0)
        axes = np.linalg.svd(centered, full_matrices=False)[2]
        if np.linalg.matrix_rank(centered) == 1:
            coordinates = centered @ axes.T
            projected = (queries - points.mean(axis=0)) @ axes.T
            epsilon = np.max(np.abs(coordinates)) * 32 * np.finfo(float).eps
            eligible = near & (np.abs(projected[:, 1]) <= epsilon)
            order = np.argsort(coordinates[:, 0])
            result[eligible] = np.interp(projected[eligible, 0], coordinates[order, 0], values[order],
                                         left=np.nan, right=np.nan)
        else:
            try:
                interpolator = LinearNDInterpolator(points, values, fill_value=np.nan)
            except QhullError as error:
                raise ValueError("Could not triangulate the selected map coordinates.") from error
            result[near] = interpolator(queries[near])
    else:
        raise ValueError("Choose nearest sampling or linear interpolation.")
    return result, distances, indices


def line_profile(data, endpoints, *, count, tolerance, method="Nearest"):
    """Sample a segment in the map's units, leaving unsupported samples empty."""
    _require_map(data)
    endpoints = _points(endpoints, "Line endpoints", minimum=2)
    if len(endpoints) != 2 or not 2 <= count <= 10000 or int(count) != count:
        raise ValueError("Enter two endpoints and between 2 and 10,000 samples.")
    length = float(np.linalg.norm(endpoints[1] - endpoints[0]))
    if length == 0:
        raise ValueError("The line endpoints must be distinct.")
    queries = np.linspace(*endpoints, int(count))
    source = data.rows.loc[data.valid]
    values, distances, indices = _sample_map(source[["x", "y"]].to_numpy(), source.value.to_numpy(),
                                             queries, tolerance, method)
    source_row = pd.array([source.row.iloc[i] if i >= 0 else None for i in indices], dtype="Int64")
    return pd.DataFrame({"distance": np.linspace(0, length, int(count)), "x": queries[:, 0], "y": queries[:, 1],
                         "value": values, "nearest_distance": distances, "source_row": source_row,
                         "supported": np.isfinite(values)})


def _aligned_axes(data, unit, offset=0):
    factor = _axis_scale(data.axis_unit, unit, data.mode)
    return data.rows[data.axes].to_numpy() * factor + offset


def difference_map(before, after, *, tolerance, method="Nearest"):
    """Subtract paired samples or locally interpolated after values on the before grid."""
    _require_map(before)
    _require_map(after)
    scale = _value_scale(after.value_unit, before.value_unit)
    left, right = before.rows.loc[before.valid], after.rows.loc[after.valid]
    left_xy = left[["x", "y"]].to_numpy()
    right_xy = _aligned_axes(after, before.axis_unit)[after.valid]
    output = before.rows.rename(columns={"row": "before_row", "value": "before_value"}).copy()
    output["after_value"] = np.nan
    output["after_row"] = pd.array([None] * len(output), dtype="Int64")
    output["nearest_distance"] = np.nan
    output["status"] = np.where(before.valid, "unmatched", "invalid before row")
    matched_after = None
    if method == "Nearest":
        i, j, distance = one_to_one_matches(left_xy, right_xy, tolerance)
        selected = left.index[i]
        output.loc[selected, "after_value"] = right.value.to_numpy()[j] * scale
        output.loc[selected, "after_row"] = right.row.to_numpy()[j]
        output.loc[selected, "nearest_distance"] = distance
        output.loc[selected, "status"] = "matched"
        matched_after = len(j)
    elif method == "Linear":
        values, distances, _ = _sample_map(right_xy, right.value.to_numpy() * scale, left_xy, tolerance, method)
        output.loc[left.index, "after_value"] = values
        output.loc[left.index, "nearest_distance"] = distances
        output.loc[left.index[np.isfinite(values)], "status"] = "interpolated"
    else:
        raise ValueError("Choose nearest matching or linear interpolation.")
    output["difference"] = output.after_value - output.before_value
    supported = int(output.difference.notna().sum())
    counts = {"before_rows": len(before.rows), "after_rows": len(after.rows),
              "valid_before": len(left), "valid_after": len(right), "supported_before": supported,
              "unmatched_valid_before": len(left) - supported, "matched_after": matched_after,
              "unmatched_valid_after": None if matched_after is None else len(right) - matched_after}
    return output, counts


def quality_map(data, *, threshold=3.5):
    """Flag missing, failed and robust median-deviation outlier rows separately."""
    _require_map(data)
    threshold = float(threshold)
    if not np.isfinite(threshold) or threshold <= 0:
        raise ValueError("The outlier threshold must be finite and positive.")
    rows = data.rows.copy()
    rows["missing"] = rows[data.axes + ["value"]].isna().any(axis=1) | rows.status_missing
    usable = data.valid
    sampled = rows.loc[usable, "value"].to_numpy()
    rows["robust_z"] = np.nan
    if len(sampled) >= 3:
        deviation = np.abs(sampled - np.median(sampled))
        spread = 1.4826 * np.median(deviation)
        if spread > 0:
            rows.loc[usable, "robust_z"] = deviation / spread
        else:
            rows.loc[usable, "robust_z"] = np.where(deviation > 0, np.inf, 0.0)
    rows["outlier"] = rows.robust_z.gt(threshold)
    rows["quality"] = np.select([rows.failed, rows.missing, rows.outlier],
                                ["failed", "missing", "outlier"], default="usable")
    return rows


def correlate_scalars(series, *, mode, tolerance, unit, offsets=None):
    """Compare each pair using tolerance-limited matches in an explicit common axis unit."""
    if mode not in {"coordinates", "time"} or len(series) < 2:
        raise ValueError("Choose a matching axis and at least two scalar series.")
    _axis_scale(unit, unit, mode)
    offsets = offsets or {}
    summary, tables = [], {}
    prepared = {}
    for name, data in series.items():
        if data.mode != mode:
            raise ValueError(f"{name} has incompatible matching axes.")
        offset = float(offsets.get(name, 0))
        if not np.isfinite(offset) or (mode == "coordinates" and offset):
            raise ValueError("Offsets must be finite and can only be applied to time matching.")
        prepared[name] = (_aligned_axes(data, unit, offset)[data.valid], data.rows.loc[data.valid], data)
    for left_name, right_name in combinations(series, 2):
        left_axes, left, left_data = prepared[left_name]
        right_axes, right, right_data = prepared[right_name]
        i, j, distance = one_to_one_matches(left_axes, right_axes, tolerance)
        left_values, right_values = left.value.to_numpy()[i], right.value.to_numpy()[j]
        pairs = pd.DataFrame({"left_row": left.row.to_numpy()[i], "right_row": right.row.to_numpy()[j],
                              "left_value": left_values, "right_value": right_values,
                              "match_distance": distance})
        for index, axis in enumerate(left_data.axes):
            pairs[f"left_{axis}"] = left_axes[i, index]
            pairs[f"right_{axis}"] = right_axes[j, index]
        tables[left_name, right_name] = pairs
        pearson = spearman = np.nan
        status = "ok"
        if len(i) < 3:
            status = "fewer than three matched pairs"
        elif np.ptp(left_values) == 0 or np.ptp(right_values) == 0:
            status = "constant matched values"
        else:
            pearson = float(np.corrcoef(left_values, right_values)[0, 1])
            spearman = float(np.corrcoef(rankdata(left_values), rankdata(right_values))[0, 1])
        summary.append({"left": left_name, "right": right_name, "left_unit": left_data.value_unit,
                        "right_unit": right_data.value_unit, "matched": len(i),
                        "left_unmatched": len(left_data.rows) - len(i), "right_unmatched": len(right_data.rows) - len(j),
                        "left_invalid": len(left_data.rows) - len(left), "right_invalid": len(right_data.rows) - len(right),
                        "pearson_r": pearson, "spearman_rho": spearman, "status": status})
    return pd.DataFrame(summary), tables


def event_table(frame, *, name, time, unit, offset_s=0.0):
    """Read named stimulus events on a shared time axis in seconds."""
    _columns(frame, [name, time])
    scale = _axis_scale(header_unit(time, TIME_UNITS) or unit, "s", "time")
    _axis_scale(unit, unit, "time")
    offset = float(offset_s)
    names = frame[name].astype("string").str.strip()
    times = _numeric(frame, time) * scale + offset
    if not len(frame) or names.isna().any() or names.eq("").any() or not np.isfinite(times).all():
        raise ValueError("Every event needs a name and a finite numeric time, and at least one event is required.")
    return pd.DataFrame({"event_id": np.arange(1, len(frame) + 1), "event": names.to_numpy(), "event_time_s": times})


def align_events(series, events, *, before_s, after_s, baseline_s, offsets_s=None):
    """Produce an absolute timeline and event windows without resampling traces."""
    before_s, after_s, baseline_s = (_tolerance(number) for number in (before_s, after_s, baseline_s))
    if before_s + after_s == 0 or baseline_s == 0:
        raise ValueError("Use a positive pre- or post-event window and a positive baseline window.")
    if not len(series) or not len(events):
        raise ValueError("Select at least one signal and one event.")
    offsets_s = offsets_s or {}
    timeline, windows, baseline_rows = [], [], []
    for label, signal in series.items():
        if signal.mode != "time":
            raise ValueError(f"{label} requires a time column for event alignment.")
        offset = float(offsets_s.get(label, 0))
        if not np.isfinite(offset):
            raise ValueError("Time offsets must be finite.")
        trace = signal.rows[["row", "value"]].copy()
        trace["time_s"] = _aligned_axes(signal, "s", offset)[:, 0]
        trace["valid"] = signal.valid
        trace["signal"], trace["unit"] = label, signal.value_unit
        trace = trace.loc[np.isfinite(trace.time_s)].sort_values("time_s", kind="stable")
        timeline.append(trace)
        for event in events.itertuples(index=False):
            relative = trace.time_s - event.event_time_s
            baseline = trace.loc[trace.valid & relative.ge(-baseline_s) & relative.lt(0), "value"]
            mean = float(baseline.mean()) if len(baseline) else np.nan
            window = trace.loc[relative.between(-before_s, after_s)].copy()
            window["event_id"], window["event"] = event.event_id, event.event
            window["event_time_s"] = event.event_time_s
            window["relative_time_s"] = relative.loc[window.index]
            window["baseline"] = mean
            window["delta"] = window.value.where(window.valid) - mean
            window["relative_change_pct"] = window.delta / mean * 100 if np.isfinite(mean) and mean != 0 else np.nan
            windows.append(window)
            baseline_rows.append({"signal": label, "unit": signal.value_unit, "event_id": event.event_id,
                                  "event": event.event, "baseline_samples": len(baseline), "baseline": mean,
                                  "window_samples": len(window), "valid_window_samples": int(window.valid.sum()),
                                  "missing_time_rows": len(signal.rows) - len(trace)})
    return (pd.concat(timeline, ignore_index=True), pd.concat(windows, ignore_index=True),
            pd.DataFrame(baseline_rows))
