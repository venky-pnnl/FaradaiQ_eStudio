# Project and method credits

FaradaIQ eStudio was designed by Venkateshkumar Prabhakaran.

The projects and people below contributed tools, methods, or services used in FaradaIQ eStudio. Consult their original licenses, notices, and service terms.

| Project | People and teams | Contribution |
|---|---|---|
| pySECCM tools | Benjamin A. Helfrecht (PNNL) | Original SECCM parser design and accompanying plotting tools |
| [pyDRTtools](https://github.com/ciuccislab/pyDRTtools) | Ciucci Lab, Francesco Ciucci, Adeleke Maradesa, and contributors | Distribution of relaxation times tools |
| [DearEIS](https://github.com/vyrjana/DearEIS) and [pyimpspec](https://github.com/vyrjana/pyimpspec) | Ville Yrjänä and contributors | Impedance analysis and optional GUI |
| [PySimpleEChem](https://github.com/kevinsmia1939/PySimpleEChem) | Kavin Teenakul and contributors | Cyclic voltammetry analysis integration |
| [SciLink](https://github.com/ziatdinovmax/SciLink) | Maxim Ziatdinov and the PNNL SciLink team (PNNL contact: Sarah Allec) | Reviewed scientific reasoning and analysis |
| [Edison Scientific](https://github.com/EdisonScientific/edison-client-docs) | Edison Scientific team and contributors | Scientific service integration |
| [impedance.py](https://github.com/ECSHackWeek/impedance.py) | ECSHackWeek contributors | Impedance model support |
| [galvani](https://github.com/echemdata/galvani) | echemdata contributors | Electrochemical file support |
| [Scientific Python](https://scientific-python.org/) | NumPy, SciPy, pandas, Matplotlib communities | Arrays, numerical methods, tables, and plots |
| [HDF5 / h5py](https://www.h5py.org/) | The HDF Group and h5py contributors | Structured scientific data and metadata storage |
| [Desktop and reports](https://www.tcl.tk/) | Tcl/Tk, Streamlit, Pillow, Plotly, Altair, python-pptx, openpyxl communities | User interface, graphics, and scientific reports |

The eStudio package includes the original pySECCM attribution and usage notes in its `pyseccm_tools` folder. PySimpleEChem retains its GPL license in the [upstream license file](https://github.com/kevinsmia1939/PySimpleEChem/blob/main/LICENSE). Keep all bundled license and notice files. External services retain their respective terms.

## Journal and software references

Include the release or commit of each software tool you used. Select the DRT method references according to the algorithm and regularization method reported in the article.

- **pySECCM parsing and plotting tools.** Helfrecht, B. A. (n.d.). *pySECCM parsing and plotting tools* [Computer software]. [Bundled parser notes](../pyseccm_tools/README-CV)
- **pyDRTtools and radial basis functions.** Wan, T. H., Saccoccio, M., Chen, C., & Ciucci, F. (2015). Influence of the discretization methods on the distribution of relaxation times deconvolution: Implementing radial basis functions with DRTtools. *Electrochimica Acta, 184*, 483–499. https://doi.org/10.1016/j.electacta.2015.09.097
- **Bayesian DRT.** Ciucci, F., & Chen, C. (2015). Analysis of electrochemical impedance spectroscopy data using the distribution of relaxation times: A Bayesian and hierarchical Bayesian approach. *Electrochimica Acta, 167*, 439–454. https://doi.org/10.1016/j.electacta.2015.03.123
- **DRT regularization.** Maradesa, A., Py, B., Wan, T. H., Effat, M. B., & Ciucci, F. (2023). Selecting the regularization parameter in the distribution of relaxation times. *Journal of The Electrochemical Society, 170*(3), 030502. https://doi.org/10.1149/1945-7111/acbca4
- **DearEIS.** Yrjänä, V. (2022). DearEIS: A GUI program for analyzing impedance spectra. *Journal of Open Source Software, 7*(80), 4808. https://doi.org/10.21105/joss.04808
- **pyimpspec.** Yrjänä, V. (n.d.). *pyimpspec* [Computer software]. https://github.com/vyrjana/pyimpspec
- **PySimpleEChem.** Teenakul, K. (n.d.). *PySimpleEChem* [Computer software]. https://github.com/kevinsmia1939/PySimpleEChem
- **SciLink.** Yao, L., Samantray, S., Ghosh, A., Roccapriore, K., Kovarik, L., Allec, S., & Ziatdinov, M. (2025). *Operationalizing serendipity: Multi-agent AI workflows for enhanced materials characterization with theory-in-the-loop* [Preprint]. arXiv. https://arxiv.org/abs/2508.06569
- **Edison Scientific.** Edison Scientific. (n.d.). *Edison Scientific client documentation*. https://github.com/EdisonScientific/edison-client-docs
- **impedance.py.** Murbach, M. D., Gerwe, B., Dawson-Elli, N., & Tsui, L.-K. (2020). impedance.py: A Python package for electrochemical impedance analysis. *Journal of Open Source Software, 5*(52), 2349. https://doi.org/10.21105/joss.02349
