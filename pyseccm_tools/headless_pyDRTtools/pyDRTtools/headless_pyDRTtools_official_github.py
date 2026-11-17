#!/usr/bin/env python
"""FaradaIQ v273 official ciuccislab pyDRTtools headless bridge.

This runner intentionally tries to import the installed/official pyDRTtools package
from the Python environment, not the bundled local v110-style pyDRTtools.py module.
It writes the same FaradaIQ batch CSV/PNG outputs as the legacy headless wrapper.
"""

import os
import sys
import csv
import glob
import argparse
import json
import uuid
import hashlib
from datetime import datetime
import numpy as np
import pandas as pd
import matplotlib as mpl
import matplotlib.pyplot as plt
# Import the official/installed ciuccislab pyDRTtools computational module.
_THIS_DIR = os.path.abspath(os.path.dirname(__file__))
_CWD = os.path.abspath(os.getcwd())
_clean_path = []
for _p in list(sys.path):
    if _p in ('', os.curdir):
        continue
    try:
        if os.path.abspath(_p) in {_THIS_DIR, _CWD}:
            continue
    except Exception:
        pass
    _clean_path.append(_p)
sys.path[:] = _clean_path
try:
    from pyDRTtools import runs as _official_runs
except Exception as _official_exc:
    print("ERROR: official/installed ciuccislab pyDRTtools was not importable.", flush=True)
    print("Install it with install_official_pydrttools_optional.bat or use --engine legacy.", flush=True)
    print(f"Import error: {_official_exc}", flush=True)
    raise SystemExit(82)
EIS_object = _official_runs.EIS_object
Simple_run = getattr(_official_runs, 'simple_run', None) or getattr(_official_runs, 'Simple_run', None)
Bayesian_run = getattr(_official_runs, 'Bayesian_run', None) or getattr(_official_runs, 'bayesian_run', None)
BHT_run = getattr(_official_runs, 'BHT_run', None) or getattr(_official_runs, 'bht_run', None)
if Simple_run is None:
    print("ERROR: official pyDRTtools runs module does not expose simple_run/Simple_run.", flush=True)
    raise SystemExit(83)

class Headless_pyDRTtools_GUI(object):
    def __init__(
        self, 
        path, 
        rbf_type,
        data_used,
        induct_used,
        der_used,
        lambda_value,
        shape_control,
        coeff,
        sample_number
    ):

        self.path = path 

        self.rbf_type = rbf_type
        self.data_used = data_used
        self.induct_used = induct_used
        self.der_used = der_used
        self.lambda_value = lambda_value
        self.shape_control = shape_control
        self.coeff = coeff
        self.sample_number = sample_number
       
        # return if there are no path or the file format is not correct
        if not (self.path.endswith('.csv') or self.path.endswith('.txt')):
            print('return')
            return
            
        self.data = EIS_object.from_file(path)
        # discard inductance data if necessary
        self.inductance_callback()
    
    def inductance_callback(self):        
        if self.data is None:
            return
        
        elif self.induct_used == "discard": 
            # discard inductive data
            self.data.freq = self.data.freq[-self.data.Z_double_prime>0]
            self.data.Z_prime = self.data.Z_prime[-self.data.Z_double_prime>0]
            self.data.Z_double_prime = self.data.Z_double_prime[-self.data.Z_double_prime>0]
            self.data.Z_exp = self.data.Z_prime + self.data.Z_double_prime*1j
            
        else: 
            # use original data otherwise
            self.data.freq = self.data.freq_0
            self.data.Z_prime = self.data.Z_prime_0
            self.data.Z_double_prime = self.data.Z_double_prime_0
            self.data.Z_exp = self.data.Z_exp_0
        
        self.data.tau = 1/self.data.freq # we assume that the collocation points equal to 1/freq as default
        self.data.tau_fine = np.logspace(
            np.log10(self.data.tau.min()) - 0.5,
            np.log10(self.data.tau.max()) + 0.5,
            10 * self.data.freq.shape[0]
        )
        self.data.method = 'none'
        
    def simple_run_callback(self):
        # callback for simple ridge regularization
        if self.data is None:
            return

        # perform computation
        self.data = Simple_run(
            self.data, 
            self.rbf_type, 
            self.data_used, 
            self.induct_used, 
            self.der_used, 
            self.lambda_value, 
            self.shape_control, 
            self.coeff
        )

    def bayesian_run_callback(self):
        # callback for bayesian regularization
        if self.data is None:
            return

        # perform computation
        self.data = Bayesian_run(
            self.data, 
            self.rbf_type, 
            self.data_used, 
            self.induct_used, 
            self.der_used, 
            self.lambda_value, 
            self.sample_number,
            self.shape_control, 
            self.coeff
        )
        
    def BHT_run_callback(self):
        # callback for Hilbert Transform run
        if self.data is None:
            return

        # perform computation
        self.data = BHT_run(
            self.data, 
            self.rbf_type, 
            self.der_used, 
            self.shape_control, 
            self.coeff
        )
        
    def plotting_callback(self, plot_to_show, output_prefix=None):
        # not plotting anything if no data is imported
        if self.data == None:
            return
        # initalize the figure object
        fig = Figure_Canvas()
        # plot the kind of figure we want
        if plot_to_show == 'EIS_data':
            fig.EIS_data(self.data)
        elif plot_to_show == 'Magnitude':
            fig.Magnitude(self.data)
        elif plot_to_show == 'Phase':
            fig.Phase(self.data)
        elif plot_to_show == 'Re_data':
            fig.Re_data(self.data)
        elif plot_to_show == 'Im_data':
            fig.Im_data(self.data)
        elif plot_to_show == 'Re_residual':
            fig.Re_residual(self.data)
        elif plot_to_show == 'Im_residual':
            fig.Im_residual(self.data)    
        elif plot_to_show == 'DRT_data':
            fig.DRT_data(self.data)
        elif plot_to_show == 'Score':
            fig.Score(self.data)
        else:
            return

        # Save figure
        if output_prefix is not None:
            plt.savefig(f"{output_prefix}_{plot_to_show.lower()}.png", bbox_inches="tight")
            fig.plot_data.to_csv(f"{output_prefix}_{plot_to_show.lower()}.csv", index=False)

        plt.close()
    
    def export_DRT(self, path):
        # callback for exporting the DRT results
        # return if users have not conduct any computation
        if self.data == None:
            return
        
        if self.data.method == 'simple':
            with open(path, 'w', newline='') as save_file:
                writer = csv.writer(save_file)
                # first save L and R
                writer.writerow(['L', self.data.L])
                writer.writerow(['R', self.data.R])
                writer.writerow(['tau','gamma'])
                # after that save tau and gamma
                for n in range(self.data.out_tau_vec.shape[0]):
                    writer.writerow([self.data.out_tau_vec[n], self.data.gamma[n]])
            
        elif self.data.method == 'credit':
            with open(path, 'w', newline='') as save_file:
                writer = csv.writer(save_file)
                # first save L and R
                writer.writerow(['L', self.data.L])
                writer.writerow(['R', self.data.R])
                writer.writerow(['tau','MAP','Mean','Upperbound','Lowerbound'])
                # after that save tau, gamma, mean, upper bound and lower bound
                for n in range(self.data.out_tau_vec.shape[0]):
                    writer.writerow(
                        [
                            self.data.out_tau_vec[n], 
                            self.data.gamma[n],
                            self.data.mean[n], 
                            self.data.upper_bound[n], 
                            self.data.lower_bound[n]
                        ]
                    )
                        
        elif self.data.method == 'BHT':
            with open(path, 'w', newline='') as save_file:
                writer = csv.writer(save_file)
                # first save L and R
                writer.writerow(['L', self.data.mu_L_0])
                writer.writerow(['R', self.data.mu_R_inf])
                writer.writerow(['tau','gamma_Re','gamma_Im'])
                # after that save tau, gamma_re and gamma_im 
                for n in range(self.data.out_tau_vec.shape[0]):
                    writer.writerow(
                        [
                            self.data.out_tau_vec[n], 
                            self.data.mu_gamma_fine_re[n],
                            self.data.mu_gamma_fine_im[n]
                        ]
                    )
    
    def export_EIS(self, path):
        # callback for exporting the EIS fitting results
        # return if users have not conduct any computation
        if self.data == None:
            return
                        
        if self.data.method == 'BHT': # save result for BHT
            with open(path, 'w', newline='') as save_file:
                writer = csv.writer(save_file)
                writer.writerow(
                    [
                        's_res_re', 
                        self.data.out_scores['s_res_re'][0],
                        self.data.out_scores['s_res_re'][1],
                        self.data.out_scores['s_res_re'][2]
                    ]
                )
                writer.writerow(
                    [
                        's_res_im', 
                        self.data.out_scores['s_res_im'][0],
                        self.data.out_scores['s_res_im'][1],
                        self.data.out_scores['s_res_im'][2]
                    ]
                )
                writer.writerow(['s_mu_re', self.data.out_scores['s_mu_re']])
                writer.writerow(['s_mu_im', self.data.out_scores['s_mu_im']])
                writer.writerow(['s_HD_re', self.data.out_scores['s_HD_re']])
                writer.writerow(['s_HD_im', self.data.out_scores['s_HD_im']])
                writer.writerow(['s_JSD_re', self.data.out_scores['s_JSD_re']])
                writer.writerow(['s_JSD_im', self.data.out_scores['s_JSD_im']])
                writer.writerow(
                    [
                        'freq', 
                        'mu_Z_re',
                        'mu_Z_im',
                        'mu_H_re',
                        'mu_H_im',
                        'Z_H_re_band',
                        'Z_H_im_band',
                        'Z_H_re_res',
                        'Z_H_im_res'
                    ]
                )
                # save frequency, the fitted impedance and the residual
                for n in range(self.data.freq.shape[0]):
                    writer.writerow(
                        [
                            self.data.freq[n], 
                            self.data.mu_Z_re[n],
                            self.data.mu_Z_im[n], 
                            self.data.mu_Z_H_im_agm[n],
                            self.data.band_re_agm[n], 
                            self.data.band_im_agm[n],
                            self.data.res_H_re[n], 
                            self.data.res_H_im[n]
                        ]
                    )
                
        else: # save result for simple and bayesian run
            with open(path, 'w', newline='') as save_file:
                writer = csv.writer(save_file)
                writer.writerow(['freq','mu_Z_re','mu_Z_im','Z_re_res','Z_im_res'])
                # save frequency, the fitted impedance and the residual
                for n in range(self.data.freq.shape[0]):
                    writer.writerow(
                        [
                            self.data.freq[n], 
                            self.data.mu_Z_re[n],
                            self.data.mu_Z_im[n], 
                            self.data.res_re[n],
                            self.data.res_im[n]
                        ]
                    )
    
class Figure_Canvas(object):
    def __init__(self, parent=None, width=7.5, height=6.5, dpi=100):
        # create Figure under matplotlib.pyplot
        # deactivate the popping of another figure panel
        plt.ioff()
#        plt.rc('text', usetex=True)
        plt.rc('font', family='serif', size = 20)
        plt.rc('xtick', labelsize = 15)
        plt.rc('ytick', labelsize = 15)
        self.fig = plt.figure(figsize=(width, height), dpi=dpi) 
        # initalizing parent
        self.axes = self.fig.add_subplot(111) # using add_subplot method
        self.fig.tight_layout()
        self.fig.subplots_adjust(left=0.14, bottom=0.13, right=0.9, top=0.9)

    def EIS_data(self, entry):
        # plot the data
        if entry.method == 'BHT':# for BHT run
            self.axes.plot(
                entry.mu_Z_re, 
                -entry.mu_Z_im, 
                'k', 
                label='$Z_\mu$(Regressed)', 
                linewidth=3
            )
            self.axes.plot(
                entry.mu_Z_H_re_agm, 
                -entry.mu_Z_H_im_agm, 
                'b', 
                label='$Z_H$(Hilbert transform)', 
                linewidth=3
            )
            self.axes.plot(entry.Z_prime, -entry.Z_double_prime, 'or')
            self.axes.legend(frameon=False, fontsize=15, loc='upper left')

            self.plot_data = pd.DataFrame(
                data=dict(
                    mu_Z_re=entry.mu_Z_re,
                    mu_Z_im=-entry.mu_Z_im,
                    mu_Z_H_re_agm=entry.mu_Z_H_re_agm,
                    mu_Z_H_im_agm=-entry.mu_Z_H_im_agm,
                    Z_prime=entry.Z_prime,
                    Z_double_prime=-entry.Z_double_prime
                )
            )
            
        elif entry.method != 'none':# for simple or bayesian run
            self.axes.plot(entry.mu_Z_re, -entry.mu_Z_im, 'k', linewidth=2)
            self.axes.plot(entry.Z_prime, -entry.Z_double_prime, 'or')

            self.plot_data = pd.DataFrame(
                data=dict(
                    mu_Z_re=entry.mu_Z_re,
                    mu_Z_im=-entry.mu_Z_im,
                    Z_prime=entry.Z_prime,
                    Z_double_prime=-entry.Z_double_prime
                )
            )
        
        else:# no computation for imported data yet
            self.axes.plot(entry.Z_prime, -entry.Z_double_prime, 'or')

            self.plot_data = pd.DataFrame(
                data=dict(
                    Z_prime=entry.Z_prime,
                    Z_double_prime=-entry.Z_double_prime
                )
            )
        
        self.axes.set_xlabel('$Z^{\prime}$')
        self.axes.set_ylabel('-$Z^{\prime \prime}$')
        self.axes.ticklabel_format(axis="both", style="sci", scilimits=(0,0))
        self.axes.axis('equal')

    def Magnitude(self, entry):
        # plot magnitude
        if entry.method == 'BHT':# for BHT run
            self.axes.semilogx(
                entry.freq, 
                np.absolute(entry.mu_Z_re + 1j * entry.mu_Z_im), 
                'k', 
                label='$Z_\mu$(Regressed)', 
                linewidth=2
            )
            self.axes.semilogx(
                entry.freq, 
                np.absolute(entry.mu_Z_H_re_agm + 1j * entry.mu_Z_H_im_agm),
                'b', 
                label='$Z_H$(Hilbert transform)', 
                linewidth=2
            )
            self.axes.semilogx(entry.freq, np.absolute(entry.Z_exp), 'or')
            self.axes.legend(frameon=False, fontsize=15, loc='upper left')

            self.plot_data = pd.DataFrame(
                data=dict(
                    freq=entry.freq,
                    mu_Z=np.absolute(entry.mu_Z_re + 1j * entry.mu_Z_im),
                    mu_Z_H_agm=np.absolute(entry.mu_Z_H_re_agm + 1j * entry.mu_Z_H_im_agm),
                    Z_exp=np.absolute(entry.Z_exp)
                )
            )
            
        elif entry.method != 'none':# for simple or bayesian run
            self.axes.semilogx(
                entry.freq, 
                np.absolute(entry.mu_Z_re + 1j * entry.mu_Z_im), 
                'k', 
                linewidth=3
            )
            self.axes.semilogx(entry.freq, np.absolute(entry.Z_exp), 'or')

            self.plot_data = pd.DataFrame(
                data=dict(
                    freq=entry.freq,
                    mu_Z=np.absolute(entry.mu_Z_re + 1j * entry.mu_Z_im),
                    Z_exp=np.absolute(entry.Z_exp)
                )
            )
            
        else:# no computation for imported data yet
            self.axes.semilogx(entry.freq, np.absolute(entry.Z_exp), 'or')

            self.plot_data = pd.DataFrame(
                data=dict(
                    freq=entry.freq,
                    Z_exp=np.absolute(entry.Z_exp)
                )
            )
        
        self.axes.set_xlim([min(entry.freq), max(entry.freq)])    
        self.axes.set_xlabel('$f$')
        self.axes.set_ylabel('$|Z|$')
        
    def Phase(self, entry):
        # plot phase angle
        if entry.method == 'BHT':# for BHT run
            self.axes.semilogx(
                entry.freq, 
                np.angle(entry.mu_Z_re + 1j * entry.mu_Z_im, deg=True),
                'k', 
                label='$Z_\mu$(Regressed)', 
                linewidth=2
            )
            self.axes.semilogx(
                entry.freq, 
                np.angle(entry.mu_Z_H_re_agm + 1j * entry.mu_Z_H_im_agm, deg=True),
                'b', 
                label='$Z_H$(Hilbert transform)', 
                linewidth=2
            )
            self.axes.semilogx(entry.freq, np.angle(entry.Z_exp, deg = True), 'or')
            self.axes.legend(frameon = False, fontsize = 15, loc = 'upper left')

            self.plot_data = pd.DataFrame(
                data=dict(
                    freq=entry.freq,
                    mu_Z=np.angle(entry.mu_Z_re + 1j * entry.mu_Z_im),
                    mu_Z_H_agm=np.angle(entry.mu_Z_H_re_agm + 1j * entry.mu_Z_H_im_agm),
                    Z_exp=np.angle(entry.Z_exp)
                )
            )
        
        elif entry.method != 'none':# for simple or bayesian run
            self.axes.semilogx(
                entry.freq, 
                np.angle(entry.mu_Z_re + 1j * entry.mu_Z_im, deg=True), 
                'k', 
                linewidth=3
            )
            self.axes.semilogx(entry.freq, np.angle(entry.Z_exp, deg=True), 'or')

            self.plot_data = pd.DataFrame(
                data=dict(
                    freq=entry.freq,
                    mu_Z=np.angle(entry.mu_Z_re + 1j * entry.mu_Z_im),
                    Z_exp=np.angle(entry.Z_exp)
                )
            )
            
        else:# no computation for imported data yet
            self.axes.semilogx(entry.freq, np.angle(entry.Z_exp, deg=True), 'or')

            self.plot_data = pd.DataFrame(
                data=dict(
                    freq=entry.freq,
                    Z_exp=np.angle(entry.Z_exp)
                )
            )
        
        self.axes.set_xlim([min(entry.freq), max(entry.freq)])    
        self.axes.set_xlabel('$f$')
        self.axes.set_ylabel('$angle/^\circ$')

    def Re_data(self, entry):
        # plot Re part
        if entry.method == 'BHT':# for BHT run
            self.axes.fill_between(
                entry.freq, 
                entry.mu_Z_H_re_agm - 3 * entry.band_re_agm, 
                entry.mu_Z_H_re_agm + 3 * entry.band_re_agm,  
                facecolor='lightgrey'
            )
            self.axes.semilogx(
                entry.freq, 
                entry.mu_Z_re, 
                'k', 
                label='$Z_\mu$(Regressed)', 
                linewidth=3
            )
            self.axes.semilogx(
                entry.freq, 
                entry.mu_Z_H_re_agm, 
                'b', 
                label='$Z_H$(Hilbert transform)', 
                linewidth=3
            )
            self.axes.semilogx(entry.freq, entry.Z_prime, 'or')
            self.axes.legend(frameon = False, fontsize = 15, loc = 'upper left')

            self.plot_data = pd.DataFrame(
                data=dict(
                    freq=entry.freq,
                    mu_Z_H_re_agm_ll=(entry.mu_Z_H_re_agm - 3 * entry.band_re_agm),
                    mu_Z_H_re_agm_ul=(entry.mu_Z_H_re_agm + 3 * entry.band_re_agm),
                    mu_Z_re=entry.mu_Z_re,
                    mu_Z_re_agm=entry.mu_Z_H_re_agm,
                    Z_prime=entry.Z_prime
                )
            )
        
        elif entry.method != 'none':# for simple or bayesian run
            self.axes.semilogx(entry.freq, entry.mu_Z_re, 'k', linewidth=3)
            self.axes.semilogx(entry.freq, entry.Z_prime, 'or')

            self.plot_data = pd.DataFrame(
                data=dict(
                    freq=entry.freq,
                    mu_Z_re=entry.mu_Z_re,
                    Z_prime=entry.Z_prime
                )
            )
            
        else:
            self.axes.semilogx(entry.freq, entry.Z_prime, 'or')

            self.plot_data = pd.DataFrame(
                data=dict(
                    freq=entry.freq,
                    Z_prime=entry.Z_prime
                )
            )
        
        self.axes.set_xlim([min(entry.freq), max(entry.freq)])    
        self.axes.set_xlabel('$f$')
        self.axes.set_ylabel('$Z^{\prime}$')
        
    def Im_data(self, entry):
        # plot Re part
        if entry.method == 'BHT':# for BHT run
            self.axes.fill_between(
                entry.freq, 
                -entry.mu_Z_H_im_agm - 3 * entry.band_im_agm, 
                -entry.mu_Z_H_im_agm + 3 * entry.band_im_agm,  
                facecolor='lightgrey'
            )
            self.axes.semilogx(
                entry.freq, 
                -entry.mu_Z_im, 
                'k', 
                label='$Z_\mu$(Regressed)', 
                linewidth=3
            )
            self.axes.semilogx(
                entry.freq, 
                -entry.mu_Z_H_im_agm, 
                'b', 
                label='$Z_H$(Hilbert transform)', 
                linewidth=3
            )
            self.axes.semilogx(entry.freq, -entry.Z_double_prime, 'or')
            self.axes.legend(frameon = False, fontsize = 15, loc = 'upper left')

            self.plot_data = pd.DataFrame(
                data=dict(
                    freq=entry.freq,
                    mu_Z_H_im_agm_ll=(-entry.mu_Z_H_im_agm - 3 * entry.band_im_agm),
                    mu_Z_H_im_agm_ul=(-entry.mu_Z_H_im_agm + 3 * entry.band_im_agm),
                    mu_Z_im=-entry.mu_Z_im,
                    mu_Z_im_agm=-entry.mu_Z_H_im_agm,
                    Z_double_prime=-entry.Z_double_prime
                )
            )
            
        elif entry.method != 'none':# for simple or bayesian run
            self.axes.semilogx(entry.freq, -entry.mu_Z_im, 'k')
            self.axes.semilogx(entry.freq, -entry.Z_double_prime, 'or')

            self.plot_data = pd.DataFrame(
                data=dict(
                    freq=entry.freq,
                    mu_Z_im=-entry.mu_Z_im,
                    Z_double_prime=-entry.Z_double_prime
                )
            )
            
        
        else:   
            self.axes.semilogx(entry.freq, -entry.Z_double_prime, 'or')
        
            self.plot_data = pd.DataFrame(
                data=dict(
                    freq=entry.freq,
                    Z_double_prime=-entry.Z_double_prime
                )
            )
            
        self.axes.set_xlim([min(entry.freq), max(entry.freq)])    
        self.axes.set_xlabel('$f$')
        self.axes.set_ylabel('-$Z^{\prime \prime}$')
        
    def Re_residual(self, entry):
        # plot the residual in the Re part
        if entry.method == 'none' :#or str(entry.ui.data_used_choice.currentText()) == 'Im Data':
            return
        
        elif entry.method == 'BHT':# for BHT run
            self.axes.fill_between(
                entry.freq, 
                -3 * entry.band_re_agm, 
                3 * entry.band_re_agm,  
                facecolor='lightgrey'
            )
            self.axes.semilogx(entry.freq, entry.res_H_re, 'or')
            self.axes.set_xlabel('$f$')
            self.axes.set_ylabel('$R_{\infty}+Z^{\prime}_{H}-Z^{\prime}_{exp}$')
            y_max = max(3*entry.band_re_agm)

            self.plot_data = pd.DataFrame(
                data=dict(
                    freq=entry.freq,
                    band_re_agm_ll=(-3 * entry.band_re_agm),
                    bane_re_agm_ul=(3 * entry.band_re_agm),
                    res_H_re=entry.res_H_re
                )
            )
        
        else:# for simple or bayesian run
            self.axes.semilogx(entry.freq, entry.res_re, 'or')
            self.axes.set_xlabel('$f$')
            self.axes.set_ylabel('$Z^{\prime}_{DRT}-Z^{\prime}_{exp}$')
            y_max = max(np.absolute(entry.res_re))

            self.plot_data = pd.DataFrame(
                data=dict(
                    freq=entry.freq,
                    res_re=entry.res_re
                )
            )
        
        self.axes.set_xlim([min(entry.freq), max(entry.freq)])    
        self.axes.set_ylim([-1.1*y_max, 1.1*y_max])
        self.axes.ticklabel_format(axis= 'y', style="sci", scilimits=(0,0))
          
    def Im_residual(self, entry):
        # plot the residual in the Im part
        if entry.method == 'none' :#or str(entry.ui.data_used_choice.currentText()) == 'Re Data':
            return
        
        elif entry.method == 'BHT':# for BHT run
            self.axes.fill_between(
                entry.freq, 
                -3 * entry.band_im_agm, 
                3 * entry.band_im_agm,  
                facecolor='lightgrey'
            )
            self.axes.semilogx(entry.freq, entry.res_H_im, 'or')
            self.axes.set_xlabel('$f$')
            self.axes.set_ylabel('$\omega L_0+Z^{\prime \prime}_{H}-Z^{\prime\prime}_{exp}$')
            y_max = max(3*entry.band_im_agm)

            self.plot_data = pd.DataFrame(
                data=dict(
                    freq=entry.freq,
                    band_im_agm_ll=(-3 * entry.band_im_agm),
                    bane_im_agm_ul=(3 * entry.band_im_agm),
                    res_H_im=entry.res_H_im
                )
            )
            
        else:# for simple or bayesian run
            self.axes.semilogx(entry.freq, entry.res_im, 'or')
            self.axes.set_xlabel('$f$')
            self.axes.set_ylabel('$Z^{\prime \prime}_{DRT}-Z^{\prime \prime}_{exp}$')
            y_max = max(np.absolute(entry.res_im))

            self.plot_data = pd.DataFrame(
                data=dict(
                    freq=entry.freq,
                    res_H_im=entry.res_im
                )
            )
            
        self.axes.set_xlim([min(entry.freq), max(entry.freq)])    
        self.axes.set_ylim([-1.1*y_max, 1.1*y_max])
        self.axes.ticklabel_format(axis = 'y', style="sci", scilimits=(0,0))
        
    def DRT_data(self, entry):
        # plot the DRT
        if entry.method == 'none':
            return
        
        elif entry.method == 'simple':
            self.axes.semilogx(entry.out_tau_vec, entry.gamma, 'k', linewidth=3)
            y_min = 0
            y_max = max(entry.gamma)

            self.plot_data = pd.DataFrame(
                data=dict(
                    out_tau_vec=entry.out_tau_vec,
                    gamma=entry.gamma
                )
            )
            
        elif entry.method == 'credit':
            self.axes.fill_between(
                entry.out_tau_vec, 
                entry.lower_bound, 
                entry.upper_bound,  
                facecolor='lightgrey'
            )
            self.axes.semilogx(entry.out_tau_vec, entry.gamma, color='black', label='MAP', linewidth=3)
            self.axes.semilogx(entry.out_tau_vec, entry.mean, color='blue', label='mean', linewidth=3)
            self.axes.semilogx(entry.out_tau_vec, entry.lower_bound, color='black')
            self.axes.semilogx(entry.out_tau_vec, entry.upper_bound, color='black')
            self.axes.legend(frameon = False, fontsize = 15)
            y_min = 0
            y_max = max(entry.upper_bound)

            self.plot_data = pd.DataFrame(
                data=dict(
                    out_tau_vec=entry.out_tau_vec,
                    gamma=entry.gamma,
                    mean=entry.mean,
                    lower_bound=entry.lower_bound,
                    upper_bound=entry.upper_bound
                )
            )
            
        elif entry.method == 'BHT':
            self.axes.semilogx(entry.out_tau_vec, entry.mu_gamma_fine_re, 'b', linewidth=3, label='$Mean Re$')
            self.axes.semilogx(entry.out_tau_vec, entry.mu_gamma_fine_im, 'k', linewidth=3, label='$Mean Im$')
            y_min = min(np.concatenate((entry.mu_gamma_fine_re,entry.mu_gamma_fine_im)))
            y_max = max(np.concatenate((entry.mu_gamma_fine_re,entry.mu_gamma_fine_im)))

            self.plot_data = pd.DataFrame(
                data=dict(
                    out_tau_vec=entry.out_tau_vec,
                    mu_gamma_fine_re=entry.mu_gamma_fine_re,
                    mu_gamma_fine_im=entry.mu_gamma_fine_im
                )
            )
        
        self.axes.set_xlabel(r'$\tau/s$')
        self.axes.set_ylabel(r'$\gamma/\Omega$')
        self.axes.set_ylim([y_min, 1.1 * y_max])
        self.axes.set_xlim([min(entry.out_tau_vec), max(entry.out_tau_vec)])
    
    def Score(self, entry):
        # plot EIS score
        if entry.method != 'BHT':
            return
        
        else: # for BHT method
            Re_part = np.array([
                entry.out_scores['s_res_re'][0], 
                entry.out_scores['s_res_re'][1], 
                entry.out_scores['s_res_re'][2],
                entry.out_scores['s_mu_re'], 
                entry.out_scores['s_HD_re'], 
                entry.out_scores['s_JSD_re']
            ])
            Im_part = np.array([
                entry.out_scores['s_res_im'][0], 
                entry.out_scores['s_res_im'][1], 
                entry.out_scores['s_res_im'][2],
                entry.out_scores['s_mu_im'], 
                entry.out_scores['s_HD_im'], 
                entry.out_scores['s_JSD_im']
            ])
            x = np.arange(6)  # the label locations
            width = 0.35  # the width of the bars
            
            self.axes.bar(x - width/2, Re_part*100, width, label='Re part', color = 'blue')
            self.axes.bar(x + width/2, Im_part*100, width, label='Im part', color = 'black')
            self.axes.plot([-0.5,5.5], [100,100], '--k')

            self.plot_data = pd.DataFrame(
                data=dict(
                    x_lb=(x - width / 2),
                    x_ub=(x + width / 2),
                    re=Re_part * 100,
                    im=Im_part * 100
                )
            )
            
            self.axes.legend(frameon = False, fontsize = 15)
            self.axes.set_ylim([0, 125])
            self.axes.set_xlim([-0.5, 5.5])
            self.axes.set_xticks(x) 
            self.axes.set_xticklabels((
                r'$s_{1\sigma}$', 
                r'$s_{2\sigma}$', 
                r'$s_{3\sigma}$', 
                r'$s_{\mu}$', 
                r'$s_{\rm HD}$', 
                r'$s_{\rm JSD}$'
            ))
            self.axes.set_yticks([0,50,100]) 
            self.axes.set_ylabel(r'$\rm Scores (\%)$')


# ---- ElectroChem Bridge v28: run-id output folders + settings capture ----
def _bridge_safe_name(text, fallback="DRT_run"):
    import re
    text = str(text or "").strip()
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_.")
    return text or fallback


def _bridge_unique_run_id(user_run_id=None, settings_seed=None):
    if user_run_id:
        return _bridge_safe_name(user_run_id, fallback="DRT_run")
    now = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    digest = hashlib.sha1(str(settings_seed or uuid.uuid4().hex).encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"DRT_{now}_{digest}"


def _bridge_make_unique_dir(parent_dir, run_id):
    parent_dir = os.path.abspath(parent_dir)
    os.makedirs(parent_dir, exist_ok=True)
    base = _bridge_safe_name(run_id, fallback="DRT_run")
    out = os.path.join(parent_dir, base)
    if not os.path.exists(out):
        os.makedirs(out, exist_ok=False)
        return out, base
    for i in range(2, 10000):
        candidate = os.path.join(parent_dir, f"{base}_{i:03d}")
        if not os.path.exists(candidate):
            os.makedirs(candidate, exist_ok=False)
            return candidate, f"{base}_{i:03d}"
    raise RuntimeError(f"Could not create a unique DRT output folder under {parent_dir}")


def _bridge_write_settings(run_dir, settings):
    os.makedirs(run_dir, exist_ok=True)
    json_path = os.path.join(run_dir, "DRT_run_settings.json")
    txt_path = os.path.join(run_dir, "DRT_run_settings.txt")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, sort_keys=True, default=str)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("ElectroChem Bridge / headless pyDRTtools run settings\n")
        f.write("=" * 72 + "\n")
        for k in sorted(settings.keys()):
            v = settings[k]
            if isinstance(v, (list, tuple)):
                v = ", ".join(str(x) for x in v)
            f.write(f"{k}: {v}\n")
    # A simple manifest is convenient to inspect in Excel.
    csv_path = os.path.join(run_dir, "DRT_input_files_manifest.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["index", "input_file"])
        for i, p in enumerate(settings.get("input_files", []), start=1):
            writer.writerow([i, p])
    return json_path, txt_path
# ---- End ElectroChem Bridge v28 helpers ----

if __name__ == "__main__":
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument(
        "input_dir",
        type=str,
        help=(
            "Name of the directory containing the input data, which should "
            "be a collection of CSV files formatted in three columns in the order "
            "(1) Frequency, (2) Real Z, (3) Imaginary Z"
        )
    )
    parser.add_argument(
        "--plot_types",
        type=str,
        nargs='+',
        choices=[
            "eis",
            "magnitude",
            "phase",
            "re",
            "im",
            "re_residual",
            "im_residual",
            "drt",
            "score"
        ],
        default=["eis"], 
        help=(
            "Plots to generate and save as PNG. "
            "The raw data used to generated the plots "
            "will also be save as a CSV"
        )
    )
    parser.add_argument(
        "--run_types",
        type=str,
        nargs='+',
        choices=[
            "simple",
            "bayesian",
            "bht"
        ],
        default=["simple"], 
        help="Analysis types to perform"
    )
    parser.add_argument(
        "--export_types",
        type=str,
        nargs='+',
        choices=[
            "drt",
            "eis",
        ],
        default=["drt"], 
        help="Data to export as CSV"
    )
    parser.add_argument(
        "--rbf_type",
        type=str,
        choices=[
            "gaussian",
            "c0",
            "c2",
            "c4",
            "c6",
            "inverse_quadratic",
            "inverse_quadric",
            "cauchy"
        ],
        default="gaussian",
        help="Kernel type"
    )
    parser.add_argument(
        "--data",
        type=str,
        choices=["re", "im", "combined"],
        default="combined",
        help="Data to use"
    )
    parser.add_argument(
        "--derivative",
        type=int,
        choices=[1, 2],
        default=1,
        help="Derivative order"
    )
    parser.add_argument(
        "--inductance",
        type=str,
        choices=["with", "without", "discard"],
        default="without",
        help="Treatment for inductance"
    )
    parser.add_argument(
        "--regularization",
        type=float,
        default=1.0E-3,
        help="Regularization parameter"
    )
    parser.add_argument(
        "--shape_control",
        type=str,
        choices=["fwhm", "shape"],
        default="fwhm",
        help="Shape control"
    )
    parser.add_argument(
        "--coefficient",
        type=float,
        default=0.5,
        help="Coefficient for FWHM"
    )
    parser.add_argument(
        "--sample_number",
        type=int,
        default=2000,
        help="Number of samples"
    )
    parser.add_argument(
        "--run_id",
        type=str,
        default=None,
        help=(
            "Optional name for the DRT output subfolder. If omitted, a unique "
            "timestamp/UUID run folder is created automatically."
        )
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default=None,
        help=(
            "Optional parent output folder for DRT runs. Default is <input_dir>/DRT. "
            "The actual plots/results are written to <output_dir>/<run_id>."
        )
    )

    args = parser.parse_args()

    plot_types = []
    for plot_type in args.plot_types:
        if plot_type == "eis":
            plot_type = "EIS_data"
        elif plot_type == "magnitude":
            plot_type = "Magnitude"
        elif plot_type == "phase":
            plot_type = "Phase"
        elif plot_type == "re":
            plot_type = "Re_data"
        elif plot_type == "im":
            plot_type = "Im_data"
        elif plot_type == "re_residual":
            plot_type = "Re_residual"
        elif plot_type == "im_residual":
            plot_type = "Im_residual"
        elif plot_type == "drt":
            plot_type = "DRT_data"
        elif plot_type == "score":
            plot_type = "Score"
        plot_types.append(plot_type)

    if args.rbf_type == "gaussian":
        rbf_type = "Gaussian"
    elif args.rbf_type == "c0":
        rbf_type = "C0 Matern"
    elif args.rbf_type == "c2":
        rbf_type = "C2 Matern"
    elif args.rbf_type == "c4":
        rbf_type = "C4 Matern"
    elif args.rbf_type == "c6":
        rbf_type = "C6 Matern"
    elif args.rbf_type in {"inverse_quadratic", "inv_quadratic"}:
        rbf_type = "Inverse Quadratic"
    elif args.rbf_type in {"inverse_quadric", "inv_quadric"}:
        rbf_type = "Inverse Quadric"
    elif args.rbf_type == "cauchy":
        rbf_type = "Cauchy"

    if args.data == "re":
        data = "Re Data"
    elif args.data == "im":
        data = "Im Data"
    elif args.data == "combined":
        data = "Combined Re-Im Data"

    if args.derivative == 1:
        derivative = "1st order"
    elif args.derivative == 2:
        derivative = "2nd order"

    if args.inductance == "without":
        # "Fitting w/o Inductance"
        inductance = 0
    elif args.inductance == "with":
        # "Fitting with Inductance"
        inductance = 1
    elif args.inductance == "discard":
        # "Discard Inductive Data"
        inductance = 2
    if args.shape_control == "fwhm":
        shape_control = "FWHM Coefficient"
    elif args.shape_control == "shape":
        shape_control = "Shape Factor"

    input_dir = os.path.abspath(args.input_dir)
    input_files = sorted(glob.glob(os.path.join(input_dir, "*.csv")))
    if not input_files:
        raise FileNotFoundError(f"No CSV files were found in DRT input directory: {input_dir}")

    settings_seed = {
        "input_dir": input_dir,
        "run_types": list(args.run_types),
        "plot_types": list(args.plot_types),
        "export_types": list(args.export_types),
        "rbf_type": args.rbf_type,
        "data": args.data,
        "derivative": args.derivative,
        "inductance": args.inductance,
        "regularization": args.regularization,
        "shape_control": args.shape_control,
        "coefficient": args.coefficient,
        "sample_number": args.sample_number,
    }
    run_id = _bridge_unique_run_id(args.run_id, settings_seed=settings_seed)
    drt_parent = os.path.abspath(args.output_dir) if args.output_dir else os.path.join(input_dir, "DRT")
    run_dir, final_run_id = _bridge_make_unique_dir(drt_parent, run_id)

    settings = dict(settings_seed)
    settings.update({
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "run_id_requested": args.run_id,
        "run_id": final_run_id,
        "output_parent_dir": drt_parent,
        "output_run_dir": run_dir,
        "input_files": [os.path.abspath(p) for p in input_files],
        "python_executable": sys.executable,
        "script_path": os.path.abspath(__file__),
        "cwd": os.getcwd(),
        "mapped_rbf_type": rbf_type,
        "mapped_data": data,
        "mapped_derivative": derivative,
        "mapped_inductance": inductance,
        "mapped_shape_control": shape_control,
    })
    _bridge_write_settings(run_dir, settings)
    print(f"DRT output run folder: {run_dir}")
    print(f"DRT settings saved: {os.path.join(run_dir, 'DRT_run_settings.json')}")

    for input_file in input_files:
        base_path, file_name = os.path.split(input_file)
        file_prefix = os.path.splitext(file_name)[0]

        # Remove header into the run-specific output folder, so each run is self-contained.
        tmp_name = os.path.join(run_dir, f"{file_prefix}_TMP.csv")
        pd.read_csv(input_file).to_csv(tmp_name, header=False, index=False)

        for run_type in args.run_types:
            headless_drt = Headless_pyDRTtools_GUI(
                tmp_name,
                rbf_type,
                data,
                inductance,
                derivative,
                args.regularization,
                shape_control,
                args.coefficient,
                args.sample_number
            )
            try:
                if run_type == "simple":
                    headless_drt.simple_run_callback()
                elif run_type == "bayesian":
                    headless_drt.bayesian_run_callback()
                elif run_type == "bht":
                    headless_drt.BHT_run_callback()
            except Exception as exc:
                print(f"WARNING: DRT run failed for {file_name} ({run_type}): {exc}")
                continue

            output_prefix = os.path.join(run_dir, f"{file_prefix}_{run_type}")
            for plot_type in plot_types:
                if plot_type == "Score" and run_type != "bht":
                    print("WARNING: skipping 'Score' plot, which is only possible with a BHT run")
                    continue
                headless_drt.plotting_callback(
                    plot_type,
                    output_prefix=output_prefix
                )

            for export_type in args.export_types:
                if export_type == 'eis':
                    headless_drt.export_EIS(os.path.join(run_dir, f"{file_prefix}_{run_type}_EIS.csv"))
                elif export_type == 'drt':
                    headless_drt.export_DRT(os.path.join(run_dir, f"{file_prefix}_{run_type}_DRT.csv"))

        try:
            os.remove(tmp_name)
        except OSError:
            pass

    print(f"Finished DRT run_id={final_run_id}")
    print(f"All DRT outputs saved under: {run_dir}")
