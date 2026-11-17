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

class CorrData(Data):
    """
    Class to parse Ecorr and Icorr data

    Parameters
    ==========

    filename : str
        Name of the file to parse

    encoding : str
        Text encoding of the file to parse
    """

    def __init__(self, filename, encoding='windows-1252'):
        super().__init__(filename, encoding=encoding)


class CVData(object):
    """
    Class to parse CV data

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
        "cv_file",
        type=str,
        help="Filename of the file containing the data"
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Make plots"
    )
    args = parser.parse_args()

    # Read the data
    cv_data = CVData(args.cv_file)

    # Write the CV data in separate files
    for group_name, group in enumerate(cv_data.groups):
        path_base, path_ext = os.path.splitext(args.cv_file)
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
        cv_path_base, cv_path_ext = os.path.splitext(args.cv_file)
        sample_name = _sample_name_from_file(args.cv_file)
        fig_root = _fig_sample_root(args.cv_file)

        # Additional plots
        x_label, x_unit = "Emon-2[V]".strip(']').split('[')
        os.makedirs(fig_root, exist_ok=True)

        for group_name, group in enumerate(cv_data.groups):
            
            # Read (parsed) EIS data
            data = pd.read_csv(f"{path_base}_Groups/{path_base}_{group_name:03d}{path_ext}")

            # Plots
            for column in ["Imon-2[A]"]:
                column_label, column_unit = column.strip(']').split('[')

                # Generate figure
                fig = plt.figure(figsize=(3.5, 3.5))
                axs = fig.add_subplot(1, 1, 1)
                axs.plot(data["Emon-2[V]"], data[column], c="red")
                axs.set_xlabel(f"{x_label} ({x_unit})")
                axs.set_ylabel(f"{column_label} ({column_unit})")

                # Save figure
                out_filename = os.path.join(fig_root, f"{sample_name}_{column.rsplit('[')[0]}_{group_name:03d}")
                fig.savefig(f"{out_filename}.png", bbox_inches="tight")
                plt.close()

                # Save data for the plot
                df = pd.DataFrame(
                    data=np.column_stack((
                        data["Emon-2[V]"],
                        data[column]
                    )),
                    columns=("Emon-2[V]", column)
                )
                df.to_csv(
                    f"{out_filename}.txt",
                    **write_kwargs
                )
