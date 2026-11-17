#!/usr/bin/env python

import os
import argparse
from io import StringIO
import numpy as np
from scipy.interpolate import griddata
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.colors import Normalize, LogNorm


import re

def _sample_name_from_file(filename):
    base = os.path.splitext(os.path.basename(str(filename)))[0]
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("_.")
    return base or "sample"

def _fig_sample_root(filename):
    return os.path.join("Figures", _sample_name_from_file(filename))


def _finite_spatial_frame(xy, values, value_name):
    """Return finite, row-aligned X/Y/value data without inventing positions."""
    frame = xy[["X-pos[m]", "Y-pos[m]"]].copy()
    series = pd.Series(values).reset_index(drop=True)
    frame = frame.reset_index(drop=True)
    n = min(len(frame), len(series))
    frame = frame.iloc[:n].copy()
    frame[value_name] = series.iloc[:n].to_numpy()
    for column in ("X-pos[m]", "Y-pos[m]", value_name):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.replace([np.inf, -np.inf], np.nan).dropna(subset=["X-pos[m]", "Y-pos[m]", value_name])


def _spatial_geometry(frame):
    """Classify finite spatial samples as empty, point, line, or 2-D map."""
    if frame is None or len(frame) == 0:
        return "empty", {"n_points": 0, "rank": 0, "linearity_ratio": None}
    pts = frame[["X-pos[m]", "Y-pos[m]"]].to_numpy(dtype=float)
    pts = np.unique(pts, axis=0)
    n = len(pts)
    if n == 1:
        return "point", {"n_points": 1, "rank": 0, "linearity_ratio": 0.0}
    centered = pts - np.mean(pts, axis=0)
    rank = int(np.linalg.matrix_rank(centered))
    ratio = 0.0
    try:
        singular = np.linalg.svd(centered, compute_uv=False)
        ratio = float(singular[-1] / singular[0]) if len(singular) > 1 and singular[0] > 0 else 0.0
    except Exception:
        ratio = 0.0
    # Sparse sets and nearly collinear scans are one-dimensional measurements.
    if n < 4 or rank < 2 or ratio < 0.02:
        return "line", {"n_points": n, "rank": rank, "linearity_ratio": ratio}
    return "map2d", {"n_points": n, "rank": rank, "linearity_ratio": ratio}


def _distance_along_scan(frame):
    pts = frame[["X-pos[m]", "Y-pos[m]"]].to_numpy(dtype=float)
    if len(pts) == 0:
        return np.asarray([], dtype=float)
    if len(pts) == 1:
        return np.asarray([0.0], dtype=float)
    steps = np.sqrt(np.sum(np.diff(pts, axis=0) ** 2, axis=1))
    return np.concatenate(([0.0], np.cumsum(steps)))


def _safe_griddata(points, values, target_grid):
    """Try smooth 2-D interpolation, degrading only when geometry/data require it."""
    last_error = None
    for method in ("cubic", "linear", "nearest"):
        try:
            grid = griddata(points, values, target_grid, method=method)
            if method != "nearest" and not np.any(np.isfinite(grid)):
                continue
            return grid, method, None
        except Exception as exc:
            last_error = exc
    return None, "scatter", last_error


def _write_geometry_note(fig_dir, geometry, details, message):
    note = os.path.join(fig_dir, "README_spatial_geometry.txt")
    with open(note, "a", encoding="utf-8") as f:
        f.write(f"geometry={geometry}; details={details}; {message}\n")

# Keyword arguments for writing the parsed data
write_kwargs = dict(
    sep=",",
    index=False,
    float_format="%11.4E",
    header=True
)

class Data(object):
    """
    Class for parsing Ecorr/Icorr data

    Parameters
    ==========

    filename : str
        Name of the file to parse

    encoding : str
        Text encoding of the file to parse
    """

    def __init__(self, filename, encoding='windows-1252'):
        self.filename = filename
        self.encoding = encoding
        self.header = ""
        self.dataframe = pd.read_csv(
            self.filename, 
            encoding=self.encoding,
            header=0,
            skipinitialspace=True,
            skiprows=lambda x: x % 2 > 0 and x > 1,
            na_values=["NAN", "INF"]
        )

    def write(self, filename):
        """
        Write the parsed data to CSV

        Parameters
        ==========

        filename : str
            File to write the parsed data to
        """

        self.dataframe.to_csv(
            filename, 
            **write_kwargs
        )

class PositionData(Data):
    """
    Class to parse positional data

    Parameters
    ==========

    filename : str
        Name of the file to parse

    encoding : str
        Text encoding of the file to parse
    """

    def __init__(self, filename, encoding='windows-1252'):
        super().__init__(filename, encoding=encoding)

class AvgOCPData(Data):
    """
    Class to parse averaged OCP data

    Parameters
    ==========

    filename : str
        Name of the file to parse

    encoding : str
        Text encoding of the file to parse
    """

    def __init__(self, filename, encoding='windows-1252'):
        super().__init__(filename, encoding=encoding)


class OCPData(object):
    """
    Class to parse OCP data

    Parameters
    ==========

    filename : str
        Name of the file to parse

    encoding : str
        Text encoding of the file to parse
    """
    
    def __init__(self, filename, encoding="windows-1252"):
        self.groups = []
        with open(filename, "r") as f:
            group_lines = []
            for line in f:
                if line.startswith("Series") or line.startswith ("Sweep"):
                    continue
                elif line.startswith('\n'):
                    group_string = StringIO(''.join(group_lines).replace(' ', ''))
                    group_dataframe = pd.read_csv(group_string)
                    self.groups.append(group_dataframe)
                    group_lines = []
                else:
                    group_lines.append(line.replace('"', '').replace(' ', ''))

if __name__ == "__main__":

    # Parse arguments
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "pos_file",
        type=str,
        help="Filename of the file containing the positional data"
    )
    parser.add_argument(
        "avg_ocp_file",
        type=str,
        help="Filename of the file containing the averaged OCP data"
    )
    parser.add_argument(
        "ocp_file",
        type=str,
        help="Filename of the file containing the full OCP data"
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Make plots"
    )
    parser.add_argument(
        "--n_grid",
        type=int,
        default=100,
        help="Number of grid points in X and Y for the position maps"
    )
    args = parser.parse_args()

    # Read the data
    ocp_data = OCPData(args.ocp_file)
    avg_ocp_data = AvgOCPData(args.avg_ocp_file)
    pos_data = PositionData(args.pos_file)

    # Write the parsed position data
    avg_ocp_path_base, avg_ocp_path_ext = os.path.splitext(args.avg_ocp_file)
    avg_ocp_data.write(f"{avg_ocp_path_base}_str{avg_ocp_path_ext}")
    pos_path_base, pos_path_ext = os.path.splitext(args.pos_file)
    pos_data.write(f"{pos_path_base}_str{pos_path_ext}")

    # Write the IV data in separate files
    for group_name, group in enumerate(ocp_data.groups):
        path_base, path_ext = os.path.splitext(args.ocp_file)
        os.makedirs(f"{path_base}_Groups", exist_ok=True)
        filename = f"{path_base}_Groups/{path_base}_{group_name:03d}{path_ext}"
        group.to_csv(
            filename,
            **write_kwargs
        )

    # Plotting
    if args.plot:
        plt.style.use("./base.mplstyle")

        # Read all the (parsed) positional data back in
        avg_ocp_path_base, avg_ocp_path_ext = os.path.splitext(args.avg_ocp_file)
        sample_name = _sample_name_from_file(args.ocp_file)
        fig_root = _fig_sample_root(args.ocp_file)
        avg_ocp = pd.read_csv(f"{avg_ocp_path_base}_str{avg_ocp_path_ext}")
        pos_path_base, pos_path_ext = os.path.splitext(args.pos_file)
        pos = pd.read_csv(f"{pos_path_base}_str{pos_path_ext}")

        # Create XY grid for plotting positional heatmaps
        xy = pos[["X-pos[m]", "Y-pos[m]"]]
        x_min, x_max = np.amin(xy["X-pos[m]"]), np.amax(xy["X-pos[m]"])
        y_min, y_max = np.amin(xy["Y-pos[m]"]), np.amax(xy["Y-pos[m]"])
        x_grid, y_grid = np.meshgrid(
            np.linspace(x_min, x_max, args.n_grid),
            np.linspace(y_min, y_max, args.n_grid)
        )

        # Positional heatmaps
        fig_dir = os.path.join(fig_root, "Position")
        os.makedirs(fig_dir, exist_ok=True)
        for column in ["OCP-value[V]"]:
            column_label, column_unit = column.strip(']').split('[')
            colorbar_label = f"{column_label} ({column_unit})"

            grid_df = _finite_spatial_frame(xy, avg_ocp[column], column)
            geometry, geometry_details = _spatial_geometry(grid_df)
            out_filename = os.path.join(fig_dir, f"{column.rsplit('[')[0]}")

            fig = plt.figure(figsize=(3.5, 3.5))
            axs = fig.add_subplot(1, 1, 1)

            if geometry == "map2d":
                grid_data, grid_method, grid_error = _safe_griddata(
                    grid_df[["X-pos[m]", "Y-pos[m]"]],
                    grid_df[column],
                    (x_grid, y_grid),
                )
                if grid_data is not None:
                    im = axs.imshow(
                        grid_data,
                        origin="lower",
                        extent=(x_min, x_max, y_min, y_max),
                        aspect="auto",
                    )
                    fig.colorbar(im, label=colorbar_label)
                    axs.set_xlabel("X (m)")
                    axs.set_ylabel("Y (m)")
                    axs.set_title(f"OCP spatial map [{grid_method}]")
                    df = pd.DataFrame(
                        data=np.column_stack((x_grid.flatten(), y_grid.flatten(), grid_data.flatten())),
                        columns=("X-pos[m]", "Y-pos[m]", column),
                    )
                else:
                    geometry = "scatter"
                    _write_geometry_note(fig_dir, geometry, geometry_details, f"2-D interpolation failed; scatter fallback used: {grid_error}")

            if geometry in {"line", "point", "scatter"}:
                if geometry == "line":
                    distance = _distance_along_scan(grid_df)
                    order = np.argsort(distance)
                    axs.plot(distance[order], grid_df[column].to_numpy(dtype=float)[order], marker="o")
                    axs.set_xlabel("Distance along scan (m)")
                    axs.set_ylabel(colorbar_label)
                    axs.set_title("OCP line-scan profile")
                    df = grid_df.copy()
                    df.insert(2, "Distance-along-scan[m]", distance)
                    _write_geometry_note(fig_dir, geometry, geometry_details, "1-D spatial profile used; 2-D triangulation intentionally skipped.")
                else:
                    sx = grid_df["X-pos[m]"].to_numpy(dtype=float)
                    sy = grid_df["Y-pos[m]"].to_numpy(dtype=float)
                    sv = grid_df[column].to_numpy(dtype=float)
                    if len(grid_df):
                        im = axs.scatter(sx, sy, c=sv)
                        fig.colorbar(im, label=colorbar_label)
                    else:
                        axs.text(0.5, 0.5, "No finite OCP spatial values", ha="center", va="center", transform=axs.transAxes)
                    axs.set_xlabel("X (m)")
                    axs.set_ylabel("Y (m)")
                    axs.set_title("OCP spatial points")
                    df = grid_df.copy()
                    _write_geometry_note(fig_dir, geometry, geometry_details, "Sparse/point spatial data retained without 2-D interpolation.")

            elif geometry == "empty":
                axs.text(0.5, 0.5, "No finite OCP spatial values", ha="center", va="center", transform=axs.transAxes)
                axs.set_axis_off()
                df = grid_df.copy()
                _write_geometry_note(fig_dir, geometry, geometry_details, "No finite spatial values were available; parser continued without interpolation.")

            fig.savefig(f"{out_filename}.png", bbox_inches="tight")
            plt.close()
            df.to_csv(f"{out_filename}.csv", **write_kwargs)

        # Additional plots
        x_label, x_unit = "Time[s]".strip(']').split('[')
        for column in ["Emon-2[V]"]:
            os.makedirs(os.path.join(fig_root, column.rsplit('[')[0]), exist_ok=True)

        for (xy_name, xy_point), (group_name, group) in zip(xy.iterrows(), enumerate(ocp_data.groups)):
            
            # Read (parsed) EIS data
            data = pd.read_csv(f"{path_base}_Groups/{path_base}_{group_name:03d}{path_ext}")

            # Plots
            for column in ["Emon-2[V]"]:
                column_label, column_unit = column.strip(']').split('[')

                # Generate figure
                fig = plt.figure(figsize=(3.5, 3.5))
                axs = fig.add_subplot(1, 1, 1)
                axs.plot(data["Time[s]"], data[column], c="red")
                axs.set_xlabel(f"{x_label} ({x_unit})")
                axs.set_ylabel(f"{column_label} ({column_unit})")
                axs.set_title(f"X: {xy_point['X-pos[m]']},\nY: {xy_point['Y-pos[m]']}")

                # Save figure
                out_filename = os.path.join(fig_root, column.rsplit('[')[0], f"{sample_name}_{column.rsplit('[')[0]}_{xy_name:03d}")
                fig.savefig(f"{out_filename}.png", bbox_inches="tight")
                plt.close()

                # Save data for the plot
                df = pd.DataFrame(
                    data=np.column_stack((
                        data["Time[s]"],
                        data[column]
                    )),
                    columns=("Time[s]", column)
                )
                df.to_csv(
                    f"{out_filename}.txt",
                    **write_kwargs
                )
