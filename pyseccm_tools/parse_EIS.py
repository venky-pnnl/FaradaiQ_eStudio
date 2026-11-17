#!/usr/bin/env python

import os
import argparse
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


def _safe_griddata(points, values, target_grid):
    """Return (grid, method, error). Falls back for line scans/collinear points."""
    last_error = None
    for method in ("cubic", "linear", "nearest"):
        try:
            grid = griddata(points, values, target_grid, method=method)
            try:
                if method != "nearest" and np.all(~np.isfinite(grid)):
                    continue
            except Exception:
                pass
            return grid, method, None
        except Exception as exc:
            last_error = exc
    return None, "scatter", last_error

# Keyword arguments for writing the parsed data
write_kwargs = dict(
    sep=",",
    index=False,
    float_format="%11.4E",
    header=True
)

class Data(object):
    """
    Base class for parsing data

    Parameters
    ==========

    filename : str
        Name of the file to parse

    encoding : str
        Text encoding of the file to parse
    """

    def __init__(self, filename, encoding=None):#'windows-1252'):
        self.filename = filename
        self.encoding = encoding
        self.header = ""
        self.dataframe = pd.read_csv(
            self.filename, 
            encoding=self.encoding,
            header=0,
            skipinitialspace=True,
            skiprows=lambda x: x % 2 > 0 and x > 1
        )

        new_columns = []
        for column in self.dataframe.columns:
            try:
                column_label, column_unit = column.strip(']').split('[')

                # Get rid of the degree character because it mucks everything up
                if column_label == "Phase":
                    column_unit = "deg"

                new_columns.append(f"{column_label}[{column_unit}]")

            except ValueError:
                new_columns.append(column)

        self.dataframe.columns = new_columns

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

class EISData(Data):
    """
    Class to parse EIS data

    Parameters
    ==========

    filename : str
        Name of the file to parse

    encoding : str
        Text encoding of the file to parse
    """
    
    def __init__(self, filename, encoding="windows-1252"):
        super().__init__(filename, encoding=encoding)
        self.freq_groups = self.dataframe.groupby("f2[Hz]")
        self.n_group_entries = len(self.freq_groups)
        self.groups = self.dataframe.groupby(self.dataframe.index // self.n_group_entries)
        self.n_groups = len(self.groups)

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

if __name__ == "__main__":

    # Parse arguments
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "pos_file",
        type=str,
        help="Filename of the file containing the positional data"
    )
    parser.add_argument(
        "eis_file",
        type=str,
        help="Filename of the file containing the EIS data"
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
    eis_data = EISData(args.eis_file)
    pos_data = PositionData(args.pos_file)

    # Write the parsed position data
    path_base, path_ext = os.path.splitext(args.pos_file)
    pos_data.write(f"{path_base}_str{path_ext}")

    # Write the EIS data in separate files
    for group_name, group in eis_data.groups:
        path_base, path_ext = os.path.splitext(args.eis_file)
        os.makedirs(f"{path_base}_Groups", exist_ok=True)
        filename = f"{path_base}_Groups/{path_base}_{group_name:02d}{path_ext}"
        group.to_csv(
            filename,
            **write_kwargs
        )

    # Plotting
    if args.plot:
        plt.style.use("./base.mplstyle")

        # Read all the (parsed) positional data back in
        eis_path_base, eis_path_ext = os.path.splitext(args.eis_file)
        sample_name = _sample_name_from_file(args.eis_file)
        fig_root = _fig_sample_root(args.eis_file)
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
        # Line scans and nearly-collinear point sets should not use 2-D cubic
        # triangulation.  They are rendered as fast scatter maps so one bad
        # Position map can never prevent Nyquist/Bode parser outputs.
        try:
            xy_arr = np.asarray(xy[["X-pos[m]", "Y-pos[m]"]], dtype=float)
            centered = xy_arr - np.nanmean(xy_arr, axis=0)
            x_span = float(np.nanmax(xy_arr[:,0]) - np.nanmin(xy_arr[:,0]))
            y_span = float(np.nanmax(xy_arr[:,1]) - np.nanmin(xy_arr[:,1]))
            span_max = max(abs(x_span), abs(y_span), 1e-300)
            span_min = min(abs(x_span), abs(y_span))
            is_line_scan = (len(xy_arr) < 4) or (np.linalg.matrix_rank(centered) < 2) or (span_min / span_max < 0.02)
        except Exception:
            is_line_scan = False

        # Positional heatmaps. For line scans, skip the expensive 2-D frequency
        # heatmap loop by default; Nyquist/Bode point plots are generated below.
        fig_dir = os.path.join(fig_root, "Position")
        os.makedirs(fig_dir, exist_ok=True)
        skip_position_maps = bool(is_line_scan) and str(os.environ.get("FARADAIQ_EIS_POSITION_MAPS", "")).strip().lower() not in {"1", "true", "yes", "on"}
        if skip_position_maps:
            with open(os.path.join(fig_dir, "README_line_scan_position_maps_skipped.txt"), "w", encoding="utf-8") as _f:
                _f.write("Position heatmaps were skipped because this appears to be a line scan or collinear point set. ")
                _f.write("Nyquist, RealZ, ImagZ, ImpZ, and Phase point plots were still generated. ")
                _f.write("Set FARADAIQ_EIS_POSITION_MAPS=1 before running the parser to force full position maps.\n")
        else:
            for group_name, group in eis_data.freq_groups:
                freq = group["f2[Hz]"].iloc[0]

                for column in ("RealZ[Ohm]", "ImagZ[Ohm]", "ImpZ[Ohm]", "Phase[deg]"):#"Phase[°]"):
                    column_label, column_unit = column.strip(']').split('[')

                    ## Get rid of the degree character because it mucks everything up
                    #if column_label == "Phase":
                    #    column_unit = "deg"

                    if is_line_scan:
                        grid_data, grid_method, grid_error = None, "scatter", None
                    else:
                        grid_data, grid_method, grid_error = _safe_griddata(xy, group[column], (x_grid, y_grid))

                    # Generate figure. For collinear line scans, 2-D cubic triangulation can fail;
                    # use a scatter fallback so Nyquist/Bode generation continues.
                    fig = plt.figure(figsize=(3.5, 3.5))
                    axs = fig.add_subplot(1, 1, 1)
                    out_filename = os.path.join(fig_dir, f"freq_{group_name}_{column.rsplit('[')[0]}")
                    if grid_data is not None:
                        im = axs.imshow(
                            grid_data,
                            origin="lower",
                            extent=(x_min, x_max, y_min, y_max)
                        )
                        fig.colorbar(im, label=f"{column_label} ({column_unit})")
                        axs.set_title(f"{freq} (Hz) [{grid_method}]")
                        df = pd.DataFrame(
                            data=np.column_stack((
                                x_grid.flatten(),
                                y_grid.flatten(),
                                grid_data.flatten()
                            )),
                            columns=("X-pos[m]", "Y-pos[m]", column)
                        )
                    else:
                        sx = np.asarray(xy["X-pos[m]"], dtype=float)
                        sy = np.asarray(xy["Y-pos[m]"], dtype=float)
                        sv = np.asarray(group[column], dtype=float)
                        sn = min(len(sx), len(sy), len(sv))
                        sx, sy, sv = sx[:sn], sy[:sn], sv[:sn]
                        im = axs.scatter(sx, sy, c=sv, cmap="viridis")
                        fig.colorbar(im, label=f"{column_label} ({column_unit})")
                        axs.set_title(f"{freq} (Hz) [line-scan scatter fallback]")
                        df = pd.DataFrame(
                            data=np.column_stack((sx, sy, sv)),
                            columns=("X-pos[m]", "Y-pos[m]", column)
                        )
                    axs.set_xlabel("X (m)")
                    axs.set_ylabel("Y (m)")

                    # Save figure and the data used for the plot
                    fig.savefig(f"{out_filename}.png", bbox_inches="tight")
                    plt.close()
                    df.to_csv(
                        f"{out_filename}.csv",
                        **write_kwargs
                    )

        # Additional plots
        freq_label, freq_unit = "f2[Hz]".strip(']').split('[')
        nyquist_dir = os.path.join(fig_root, "Nyquist")
        os.makedirs(nyquist_dir, exist_ok=True)
        for column in ("RealZ[Ohm]", "ImagZ[Ohm]", "ImpZ[Ohm]", "Phase[deg]"):#"Phase[°]"):
            os.makedirs(os.path.join(fig_root, column.rsplit('[')[0]), exist_ok=True)

        for (xy_name, xy_point), (group_name, group) in zip(xy.iterrows(), eis_data.groups):
            
            # Nyquist plot
            # Read (parsed) EIS data
            data = pd.read_csv(f"{path_base}_Groups/{eis_path_base}_{xy_name:02d}{eis_path_ext}")

            # Build a line with differently colored line segments
            # corresponding to the frequency
            points = np.column_stack((data["RealZ[Ohm]"], data["ImagZ[Ohm]"])).reshape(-1, 1, 2)
            line_segments = np.concatenate((points[0:-1], points[1:]), axis=1)
            norm = LogNorm(vmin=np.amin(data["f2[Hz]"]), vmax=np.amax(data["f2[Hz]"]))
            line_collection = LineCollection(line_segments, cmap="inferno", norm=norm)
            line_collection.set_array(data["f2[Hz]"])

            # Generate figure
            fig = plt.figure(figsize=(3.5, 3.5))
            axs = fig.add_subplot(1, 1, 1)
            line = axs.add_collection(line_collection)
            fig.colorbar(line, label=f"{freq_label} ({freq_unit})")
            axs.scatter(data["RealZ[Ohm]"], data["ImagZ[Ohm]"], c=data["f2[Hz]"], cmap="inferno", norm=norm, zorder=2)

            real_label, real_unit = "RealZ[Ohm]".strip(']').split('[')
            imag_label, imag_unit = "ImagZ[Ohm]".strip(']').split('[')
            axs.set_xlabel(f"{real_label} ({real_unit})")
            axs.set_ylabel(f"{imag_label} ({imag_unit})")
            axs.set_title(f"X: {xy_point['X-pos[m]']},\nY: {xy_point['Y-pos[m]']}")

            # Save figure
            out_filename = os.path.join(nyquist_dir, f"{sample_name}_Nyquist_{xy_name:02d}")
            fig.savefig(f"{out_filename}.png", bbox_inches="tight")
            plt.close()

            # Save data for the plot
            df = pd.DataFrame(
                data=np.column_stack((
                    data["f2[Hz]"],
                    data["RealZ[Ohm]"],
                    data["ImagZ[Ohm]"]
                )),
                columns=("f2[Hz]", "RealZ[Ohm]", "ImagZ[Ohm]")
            )
            df.to_csv(
                f"{out_filename}.csv",
                **write_kwargs
            )

            # Plots vs. frequency
            for column in ("RealZ[Ohm]", "ImagZ[Ohm]", "ImpZ[Ohm]", "Phase[deg]"):#"Phase[°]"):
                column_label, column_unit = column.strip(']').split('[')

                # Get rid of the degree character because it mucks everything up
                #if column_label == "Phase":
                #    column_unit = "deg"

                # Build a line with differently colored line segments
                # corresponding to the frequency
                points = np.column_stack((data["f2[Hz]"], data[column])).reshape(-1, 1, 2)
                line_segments = np.concatenate((points[0:-1], points[1:]), axis=1)
                norm = LogNorm(vmin=np.amin(data["f2[Hz]"]), vmax=np.amax(data["f2[Hz]"]))
                line_collection = LineCollection(line_segments, cmap="inferno", norm=norm)
                line_collection.set_array(data["f2[Hz]"])

                # Generate figure
                fig = plt.figure(figsize=(3.5, 3.5))
                axs = fig.add_subplot(1, 1, 1)
                line = axs.add_collection(line_collection)
                fig.colorbar(line, label=f"{freq_label} ({freq_unit})")
                axs.scatter(data["f2[Hz]"], data[column], c=data["f2[Hz]"], cmap="inferno", norm=norm, zorder=2)
                axs.set_xscale("log")
                x_lim = axs.get_xlim()
                axs.set_xlim(x_lim[-1], x_lim[0])
                axs.set_xlabel(f"{freq_label} ({freq_unit})")
                axs.set_ylabel(f"{column_label} ({column_unit})")
                axs.set_title(f"X: {xy_point['X-pos[m]']},\nY: {xy_point['Y-pos[m]']}")

                # Save figure
                out_filename = os.path.join(fig_root, column.rsplit('[')[0], f"{sample_name}_{column.rsplit('[')[0]}_{xy_name:02d}")
                fig.savefig(f"{out_filename}.png", bbox_inches="tight")
                plt.close()

                # Save data for the plot
                df = pd.DataFrame(
                    data=np.column_stack((
                        data["f2[Hz]"],
                        data[column]
                    )),
                    columns=("f2[Hz]", column)
                )
                df.to_csv(
                    f"{out_filename}.csv",
                    **write_kwargs
                )
