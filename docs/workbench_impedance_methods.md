# Impedance and DRT series methods

## Circuit fitting and comparison

The workbench uses $Z=Z'+iZ''$ with $\omega=2\pi f$. A series resistance $R_s$ precedes any number of series-connected parallel branches. Each RC branch contributes $(1/R+i\omega C)^{-1}$. Each R–CPE branch contributes $(1/R+Q(i\omega)^\alpha)^{-1}$, with $Q$ in S·sᵅ and $0\leq\alpha\leq1$. The optional series semi-infinite Warburg contributes $\sigma/\sqrt{i\omega}$, with $\sigma$ in Ω·s⁻⁰·⁵. This convention gives equal-magnitude real and imaginary Warburg components. The Nyquist view displays $-Z''$. Residuals retain the signed difference between fitted and measured impedance.

The solver minimizes squared real and imaginary residual components with equal absolute weight across all included points and spectra. Positive fitted parameters use logarithmic coordinates. Bounds and fixed values are in physical units. In a global fit, each shared free parameter contributes one variable to the joint optimization. Local free parameters contribute one variable per spectrum. Candidate scores use individual observation counts and free-parameter counts from the fitted problem. Compare models only within a common group with identical measured values and frequency masks.

The Gaussian score uses $N=2n$ for $n$ measured complex pairs and $k=p+1$ for $p$ free circuit coefficients plus one estimated common variance. Up to an additive constant common to candidates, the deviance is $N\log(\mathrm{RSS}/N)$, with a numerical floor at machine precision. AIC adds $2k$, AICc adds $2k(k+1)/(N-k-1)$ to AIC, and BIC adds $k\log N$ to deviance. AICc requires $N>k+1$. The usual likelihood interpretation assumes independent, identically distributed component errors. Rank deficiency, constrained optima, correlated or unequal component errors, and multiple local solutions can limit that interpretation. [Hurvich and Tsai, 1989](https://doi.org/10.1093/biomet/76.2.297) · [Schwarz, 1978](https://doi.org/10.1214/aos/1176344136)

Covariance uses the local least-squares linearization $\hat{s}^2(J^\mathsf{T}J)^{-1}$, where $\hat{s}^2=\mathrm{RSS}/(N-p)$ and $J$ is the residual Jacobian. The calculation uses the singular-value decomposition and propagates through the logarithmic transform to physical units. Marginal intervals are the estimate plus or minus the selected Student-$t$ multiplier times its standard error. The workbench omits these intervals and covariance entries for a Jacobian with singular values at or below $10^{-8}$ of the largest or when a fitted coefficient is at a bound. Fixed values carry no inferred uncertainty. Identical branch types may exchange labels between local solutions. Strong curvature or residual dependence can make the local intervals inaccurate even at full rank. [Bates and Watts, 1980](https://doi.org/10.1111/j.2517-6161.1980.tb01094.x)

Rank-deficient candidates retain residuals and diagnostic scores and are excluded from model ranking. Their free coefficients appear as diagnostic values that reproduce the fitted trace. The physical estimate field is blank.

## Discretized DRT

With $x=\ln(\tau/1\,\mathrm{s})$, the finite-grid model is

$$
Z(f)=R_s+\int_{x_\min}^{x_\max}\frac{\gamma(x)}{1+i\,2\pi f\tau(x)}\,dx .
$$

The workbench evaluates this series RC model using trapezoidal quadrature on a common equally spaced $x$ grid. The default endpoints derive from $1/(2\pi f_\max)$ and $1/(2\pi f_\min)$ across the included series, with the selected padding in decades. It solves for nonnegative $R_s$ and $\gamma$, using equally weighted real and imaginary residuals and a Tikhonov penalty $\lambda\lVert D_2\gamma\rVert_2^2$. The centered second difference divided by $\Delta x^{3/2}$ approximates the integrated squared curvature. This grid formulation follows the log-time kernel and regularization framework described by Wan and colleagues. [Wan et al., 2015](https://doi.org/10.1016/j.electacta.2015.09.097)

The export stores $\tau$ in **tau_seconds** and the density in **gamma_ohm**. Trapezoidal integration with respect to $d\ln\tau$ gives the represented polarization resistance in ohms. A distribution plotted against $\log_{10}\tau$ still uses the natural-log density convention. Its area in base-10 coordinates requires a factor of $\ln 10$.

The RC model assumes a capacitive response with a nonnegative distribution and includes the series resistance. It has no inductive or dedicated diffusion element. Positive imaginary rows remain in the complex fit and are counted in the diagnostics. Finite bandwidth and regularization limit resolution. A larger lambda favors a smoother distribution at a possible cost in reconstruction error. Interpret the lambda sweep, reconstructed impedance, grid padding, and peak stability together.

Peak candidates are interior local maxima above the selected fraction of the spectrum's maximum gamma in prominence. Minima between detected peaks divide the grid into integration basins. The areas reported for peaks integrate gamma with respect to natural-log time over those basins. Tracking minimizes total absolute separation in $\log_{10}\tau$ within the user’s maximum shift between adjacent spectra. Missing distributions and acquisitions without detected peaks retain their series positions and break existing tracks. Condition ordering requires a coordinate for every selected acquisition. Identifying a physical process requires evidence beyond the resulting peaks.

## Frequency masks, lin-KK and condition maps

Masks use inclusive keep limits and inclusive exclusion intervals in Hz. Original row numbers are one-based data positions before masking or step grouping. Invalid rows and excluded rows have separate reasons in the exported selection table. Circuit and DRT residuals carry the retained row numbers. Supported input headers declare frequency in Hz or a supported multiple and impedance in ohms or a supported multiple. Convert compound or area-normalized impedance units to ohms before analysis.

The lin-KK tab calls the existing optional eStudio backend for selected spectra with at least three distinct valid frequencies after masking. The backend uses the complex fit, the default $\mu=0.85$ threshold, and a series capacitive term. With $n$ distinct frequencies and $M+2$ fitted coefficients, the workbench caps $M$ at $\min(100,2n-4)$ to preserve positive residual degrees of freedom. Each spectrum retains the available readiness data and diagnostics or an error message. [Schönleber, Klotz and Ivers-Tiffée, 2014](https://doi.org/10.1016/j.electacta.2014.01.034)

Map coordinates come from explicit supplied values, unit-bearing scalar records, constant unit-bearing data columns, or consecutive condition steps when selected. The workbench rejects conflicting recorded coordinates within an acquisition and checks for a common reference basis before combining acquisitions. Potential and time coordinates retain a declared basis from their headers, records, or supplied values. To combine acquisitions measured on different reference scales, convert their coordinates to a common basis and supply the converted values and basis. The maps plot actual observations and keep repeated step acquisitions separate. Run receipts preserve the selected source tags and original table paths.
