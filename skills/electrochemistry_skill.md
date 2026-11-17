# SciLink Electrochemistry Skill

## Overview

### Identity

**Registered skill name:** `electrochemistry_skill`
**Domain:** electrochemical thermodynamics, kinetics, transport, voltammetry, impedance, adsorption, double layers, microelectrodes, hydrodynamic methods, SECM, corrosion, batteries, sensors, electrodeposition, electrocrystallization, stripping, PalmSens commissioning, FaradaIQ campaigns, and FaradaIQ canonical HDF5 bundles
**Revision:** `7.0-guided-hybrid-analysis`
**Execution boundary:** analysis only

This skill provides technique-aware scientific guidance for OCP, CA, CP, CV, LSV, DPV, SWV, EIS, GEIS, deposition, stripping, SECCM, PalmSens AC Dummy Cell commissioning, and multimodal electrochemical datasets.

The skill must analyze the actual attached data and campaign history. It must not require the user to convert valid CSV, JSON, HDF5, or FaradaIQ bundle data into another format before analysis.

### Non-negotiable scientific boundaries

- Never overwrite raw data.
- Never initiate, authorize, or imply automatic hardware execution.
- Preserve `approved=false` in every generated experiment plan.
- Never invent electrode identity, reference electrode, electrolyte, area, concentration, temperature, circuit, or units.
- Never run or interpret DRT without source EIS or GEIS.
- Never interpret an electrical dummy cell as an electrochemical reaction.
- Never infer mechanism from one metric.
- Separate observed values, calculated values, fitted values, and hypotheses.
- Preserve provenance for every result.
- Use **applied potential**, not “field,” unless an electric-field quantity was measured.

### Canonical SciLink section contract

This file intentionally uses only these level-2 headings:

- `Overview`
- `Planning`
- `Implementation`
- `Analysis`
- `Validation`
- `Interpretation`

All domain subsections are level 3 or deeper. Do not rewrite them as additional level-2 headings.

---

### Books-integrated knowledge scope

The user-provided electrochemistry collection has been translated into analysis rules, equation gates, QA checks, and interpretation safeguards. The skill now covers:

- electrochemical potential, equilibrium, activities, formal potentials, and reference scales
- liquid-junction potentials and reference-electrode limitations
- Butler-Volmer, Tafel, Marcus-type, and Frumkin-corrected kinetic reasoning
- diffusion, migration, convection, and Nernst-Planck transport
- planar, spherical, cylindrical, thin-layer, porous, and finite-domain diffusion
- double-layer charging, point of zero charge, specific adsorption, and non-ideal interfaces
- surface-confined and irreversibly adsorbed voltammetry
- coupled E, EC, CE, ECE, DISP, catalytic, proton-coupled, and adsorption mechanisms
- potential-step, current-step, pulse, square-wave, stripping, and hydrodynamic methods
- ultramicroelectrodes, arrays, rough electrodes, porous electrodes, and nanoparticle events
- SECM imaging, approach curves, feedback, generation-collection, potentiometric probes, and hybrid modes
- digital simulation, boundary conditions, mesh convergence, and model identifiability
- corrosion, batteries, fuel cells, capacitors, sensors, semiconductor electrodes, and photoelectrochemistry
- electrochemistry coupled with photoelectron and other surface-sensitive measurements

This is a scientific decision skill, not a replacement for the source books. It must apply only the subset relevant to the attached experiment.

### Equation and model gate

Before using any named equation or theory, state or internally verify:

```text
geometry
controlled variable
initial condition
boundary condition
transport regime
kinetic regime
temperature
units
electrode area
concentration or activity
electron stoichiometry
time or frequency window
supporting-electrolyte condition
ohmic-drop treatment
reference-potential basis
```

If a required input or assumption is missing, perform descriptive analysis and list what blocks quantitative use.

Do not use a famous equation merely because its variables can be found in the file.

### Electrochemical cause hierarchy

When explaining a signal, consider these classes before choosing a mechanism:

1. wiring, range, compliance, reference, or acquisition artifact
2. uncompensated resistance and double-layer charging
3. mass transport by diffusion, migration, convection, or porous transport
4. heterogeneous electron-transfer kinetics
5. homogeneous chemical reaction coupled to electron transfer
6. adsorption, film growth, changing active area, or surface reconstruction
7. phase transformation, nucleation, dissolution, corrosion, or gas evolution
8. sample heterogeneity, geometry, and multimodal registration

A single trace seldom distinguishes all classes.

### Potential terminology

Distinguish:

- electrode potential as recorded
- potential versus the stated reference electrode
- formal potential under the actual medium
- standard potential under defined standard states
- overpotential relative to a stated equilibrium potential
- corrected interfacial potential after a documented iR correction
- work function or binding energy from surface spectroscopy

Never mix these quantities without an explicit conversion and provenance.

## Planning

### First-pass evidence inventory

Before selecting an analysis agent or model:

1. Load metadata.
2. List every authorized file.
3. Read every completed campaign-stage CSV and its paired JSON context.
4. Read the campaign stage manifest.
5. Identify stage title, stage ID, technique, plan parameters, row count, columns, and units.
6. Identify the cell or circuit description.
7. Determine whether the task is planning, live review, next-step selection, or post-campaign conclusion.
8. Determine whether all required completed stages are present.
9. Preserve the concise experimental title for each stage.
10. Build a stage table before numerical analysis.

Do not analyze only the latest stage when the campaign contract requires all completed stages.

### Required stage table

Create an internal table with:

```text
stage_id
experimental_title
technique
plan_parameters
data_file
context_file
row_count
columns
units
analysis_route
analysis_status
limitations
```

### Technique routing

Use the declared technique and verify it from the waveform and columns.

Allowed canonical techniques:

```text
OCP
CA
CP
CV
DEPOSITION
DPV
EIS
GEIS
LSV
STRIPPING
SWV
```

Contextual records such as Approach curve, Position/Takedata, camera images, and stage metadata are not primary electrochemical techniques.

### Analysis-mode guidance

This skill provides scientific guidance. It does not require one fixed
application execution path.

FaradaIQ may offer two application modes:

```text
Standard SciLink analysis
Optional deterministic electrochemistry
```

The selected application mode should be recorded in provenance.

#### Standard SciLink analysis

This is the preferred default for exploratory and general analysis.

In this mode:

- SciLink may inspect files and metadata,
- SciLink may use `select_agent`,
- SciLink may use `run_analysis`,
- SciLink may generate and execute analysis code,
- SciLink may compare alternative approaches,
- SciLink may ask concise questions when a missing fact materially changes the
  analysis,
- SciLink may use literature and multimodal context,
- SciLink should remain available for follow-up.

Use the electrochemistry skill as scientific guidance rather than as a rigid
tool-blocking policy.

Start with the technique, waveform, columns, units, metadata, and scientific
question. Prefer direct electrochemical quantities when they answer the
question. Use fitting, simulation, deconvolution, or agent-based analysis when
they add scientific value.

#### Optional deterministic electrochemistry

When the user explicitly enables a deterministic electrochemistry option in the
application, FaradaIQ may calculate technique-specific metrics before SciLink
starts.

In that mode:

- use the supplied deterministic results as evidence,
- inspect raw data and metadata when needed,
- identify questionable or internally inconsistent derived values,
- do not assume deterministic results are automatically correct,
- allow SciLink to explain limitations and recommend additional analysis,
- follow the application-provided tool availability without demanding a
  different mode.

The skill itself does not require deterministic mode.

#### Technique-aware tool selection

Electrochemical data should be analyzed as electrochemistry, even when a
general curve-analysis tool is used.

A general analysis agent may be used when it can implement the scientifically
appropriate workflow. Agent name alone does not determine scientific validity.

Do not assume that every one-dimensional curve is a spectrum.

For CV, LSV, CA, CP, OCP, EIS, GEIS, DPV, SWV, stripping, and
electrodeposition:

1. identify the waveform and independent variable,
2. preserve sweep, cycle, pulse, stage, point, and time identity,
3. calculate direct observables,
4. fit or simulate only when useful for the question,
5. evaluate physics, residuals, uncertainty, and identifiability,
6. clearly separate measured, calculated, fitted, and interpreted results.

#### Voltammetry guidance

For ordinary CV and LSV, begin with sweep-aware electrochemical analysis:

- potential and current ranges,
- sweep direction,
- peak or wave potentials and currents,
- onset criteria,
- charge or current integration,
- cycle stability,
- scan-rate or concentration trends,
- current-range and status checks.

A full CV loop should not automatically be represented as a sum of generic
spectral peaks.

However, fitting or deconvolution may be used when:

- the user requests it,
- overlapping electrochemical processes require it,
- a mechanistic or empirical model is justified,
- digital simulation is scientifically appropriate,
- direct metrics alone do not answer the question.

When a general curve-analysis route is used, explicitly guide it toward the
appropriate electrochemical task. Do not force Gaussian, Lorentzian, Voigt,
sech, or arbitrary components merely to improve fit quality.

A phrase such as `fit this CV` should be interpreted from the scientific
context. It may mean extracting direct features, testing scan-rate laws,
performing digital simulation, or fitting a justified electrochemical model.

#### OCP guidance

Start with stability, drift, noise, offset, settling, discontinuities, and
connection quality.

A relaxation model may be used when the transient visibly supports it and the
model addresses the scientific question. Do not fit an exponential solely
because the axes are potential and time.

#### Impedance guidance

For EIS and GEIS, preserve complex impedance and use both real and imaginary
components when fitting an equivalent circuit.

Select circuit complexity from the data, metadata, experiment, and
identifiability. For a known dummy-cell circuit, prioritize the documented
circuit, but allow diagnostic alternatives when the ideal model fails and the
deviation is scientifically investigated.

#### Quality guidance

Do not use R² as the sole acceptance criterion.

R² may be reported alongside:

- residual structure,
- parameter uncertainty,
- model assumptions,
- physical plausibility,
- replicate behavior,
- sensitivity to preprocessing,
- information criteria when meaningful.

A low R² may indicate a poor model rather than poor data. A high R² does not
establish mechanism.

### Retry and fallback policy

Use retries only when they are likely to improve scientific correctness.

Appropriate reasons include:

- malformed generated code,
- incorrect column selection,
- wrong sign convention,
- unsupported model assumptions,
- identifiable unit conversion errors,
- incomplete use of series metadata,
- a clearly better physically justified model.

Avoid repeated attempts that only add components or lower thresholds without
improving the electrochemical interpretation.

When an approach fails:

1. diagnose whether the problem is data, metadata, implementation, or model;
2. preserve the best valid intermediate results;
3. try a scientifically different approach when justified;
4. stop and report limitations when further retries are unlikely to help.

Do not silently accept a mismatched model merely because it is the best
available numerical fit.


### Managed Next-or-Conclude transactions

When the operator requests a definitive Next-or-Conclude decision:

- analyze every unique completed stage,
- do not return completed stages as a new runnable campaign,
- update per-stage data quality,
- return `experiment_plan=null` when existing evidence answers the goal,
- return one new experiment only when a specific unresolved quality criterion or scientific question requires it,
- do not repeat a completed stage unless the reason for repetition is explicit and evidence-based.

---

### Expanded experimental metadata

Collect when available:

```text
working electrode material, geometry, area, roughness, and pretreatment
reference electrode type, filling solution, bridge, age, and calibration
counter electrode material, area, and compartment
cell geometry, working-reference separation, Luggin position, and shielding
solvent, supporting electrolyte, analyte, concentration, ionic strength, pH
water content, dissolved gases, viscosity, density, conductivity, and temperature
stirring, flow, rotation rate, channel dimensions, or convection history
instrument model, current range, bandwidth, filters, sampling interval, and compliance
iR compensation method and fraction
electrode rotation, microelectrode radius, SECM RG, tip radius, and separation
sample mass, loading, geometric area, active-material mass, and normalization basis
```

Do not silently use nominal electrode area as electrochemically active area.

### Reference-electrode and junction plan

Before comparing potentials across cells or media:

- verify the reference electrode and potential scale
- identify bridge and junction composition
- check reference drift before and after the experiment when possible
- report conversions to RHE, SHE, Fc/Fc+, or another scale with the exact equation and conditions
- do not assume a universal liquid-junction correction
- in nonaqueous work, prefer an internal or external redox standard appropriate to the medium
- distinguish a pseudo-reference from a stable reference electrode

A potential conversion is invalid when the source reference is unknown.

### Electrolyte, activity, and solvation plan

Use concentration-based equations only when activity effects are negligible or explicitly accepted as an approximation.

Flag:

- high ionic strength
- weakly supported media
- ion pairing or aggregation
- multivalent ions
- strong complexation
- changing pH
- mixed solvents
- large viscosity changes
- low dielectric solvents
- precipitation or hydrolysis
- concentration-dependent speciation

When transport or formal potential changes with composition, consider viscosity, activity, ion association, and solvation before assigning a change in electron-transfer kinetics.

### Voltammetry design and controls

For chemical CV or LSV, prefer:

- blank electrolyte
- electrode-preparation replicate
- redox standard when appropriate
- scan-rate series
- concentration series when mechanism or transport is under study
- quiet-time and OCP record
- positive and negative potential-window checks
- current-range and compliance review
- repeated cycles to separate conditioning from stable response
- iR assessment
- temperature record
- randomized acquisition order for condition comparisons

Do not use one scan rate to claim diffusion control or surface confinement.

### Mechanism-discrimination plan

When an E, EC, CE, ECE, DISP, catalytic, adsorption, or proton-coupled pathway is proposed, design comparisons using some combination of:

- scan rate
- switching potential
- concentration
- substrate or reagent concentration
- pH or proton activity
- temperature
- electrode size
- rotation or flow rate
- pulse time
- hold time
- reverse-scan behavior
- product detection
- digital simulation

A mechanism label requires multiple independent observables.

### Hydrodynamic and microelectrode plan

For rotating disk, rotating ring-disk, channel, wall-jet, microdisk, nanoelectrode, or array measurements, record geometry and flow conditions.

Verify:

- laminar-flow regime where required
- rotation rate in rad/s or rpm with correct conversion
- kinematic viscosity
- disk and ring dimensions
- collection efficiency
- microelectrode radius and sheath ratio
- overlap of diffusion layers in arrays
- steady-state time relative to `a^2/D`

Do not apply planar diffusion formulas automatically to microelectrodes.

### SECM planning

For SECM or SECCM-related local probe work, record:

```text
tip material and radius a
insulating sheath radius and RG
mediator identity and concentration
tip potential and substrate potential
bulk tip current iT,infinity
approach speed
normalized distance L = d/a
probe tilt
surface topography
mode: feedback, SG/TC, TG/SC, redox competition, potentiometric, AC, or hybrid
```

Acquire an approach curve on a known conductor and insulator when feasible.

Separate electrochemical contrast from topographic cross-talk.

### Application-specific planning

For corrosion:

- stabilize OCP
- use near-OCP polarization for polarization resistance
- avoid damaging scans before the final measurement
- record scan direction and sweep rate
- define exposed area and crevice masking
- identify passivation, pitting, and hysteresis protocols

For batteries and capacitors:

- define active mass, areal loading, voltage limits, rest periods, current convention, and cycle number
- report whether capacity is based on charge, discharge, mass, area, or volume
- record temperature and formation history
- distinguish two-electrode full-cell quantities from three-electrode electrode quantities

For sensors:

- predefine calibration range, blanks, replicates, interferents, recovery, and matrix
- include independent validation samples
- do not calculate detection limits from a single blank

For semiconductor and photoelectrochemistry:

- record illumination spectrum, intensity, illuminated area, chopping, dark controls, and reference scale
- identify whether capacitance measurements satisfy Mott-Schottky assumptions
- monitor photocorrosion and drift


### Electrodeposition and electrocrystallization planning

Treat electrodeposition as a coupled sequence that may include:

```text
double-layer charging
electron transfer
nucleation
two-dimensional or three-dimensional growth
mass transport
diffusion-zone overlap
surface-area evolution
film resistance
passivation
gas evolution
dissolution or stripping
```

Do not select a nucleation model until the waveform, substrate, electrolyte,
applied potential, and transient shape have been inspected.

#### Required deposition metadata

Attempt to recover:

```text
working-electrode material
substrate preparation
geometric area
roughness or active-area information
reference electrode and potential scale
counter electrode
electrolyte composition
metal-ion concentration and speciation
supporting electrolyte
pH
temperature
agitation or flow
applied potential or current waveform
preconditioning and OCP history
sampling interval
current range
iR compensation
deposition duration
replicate identity
stripping protocol
post-deposition characterization
```

Do not invent missing values.

#### Required controls

Prefer, when available:

- blank electrolyte without depositing ion
- substrate-only control
- OCP or no-deposition control
- low-overpotential and high-overpotential conditions
- stepped-potential or stepped-current series
- replicate depositions
- charge-matched comparisons
- hydrodynamic or convection control
- temperature and pH checks
- stripping or dissolution recovery
- independent mass, thickness, morphology, and composition measurements

#### Model-candidate planning

Consider these candidate families only when their assumptions are plausible:

1. direct non-model transient metrics
2. Cottrell planar diffusion
3. Scharifker-Hills instantaneous three-dimensional nucleation
4. Scharifker-Hills progressive three-dimensional nucleation
5. Scharifker-Mostany nucleation and diffusion-zone overlap
6. Heerman-Tarallo finite-rate nucleation and growth
7. Bewick-Fleischmann-Thirsk two-dimensional or three-dimensional growth
8. Avrami-type transformed-fraction analysis
9. mixed or multi-stage nucleation and growth
10. film-resistance, passivation, porous-growth, or moving-boundary models
11. numerical simulation when analytical models are inadequate

Do not select a model from visual resemblance alone.

#### Model eligibility decision

Before fitting, determine:

```text
is there a defensible deposition onset?
is there a clear current maximum?
are Imax and tmax identifiable?
is the current sign convention known?
is the transient dominated by deposition rather than charging?
is convection negligible or controlled?
is gas evolution absent or separable?
is passivation absent or explicitly modeled?
is the surface area approximately stable over the fitted region?
is the deposition geometry compatible with the candidate model?
are concentration, diffusion coefficient, area, and material constants known
when absolute nucleation parameters are requested?
```

When these conditions are not satisfied, retain direct metrics and label the
ideal nucleation model inapplicable or inconclusive.


### Digital-simulation plan

A simulation-ready description must define:

```text
reaction network
electron stoichiometry
geometry
initial concentrations
diffusion coefficients or transport model
homogeneous rate constants
heterogeneous kinetic law
double-layer or migration treatment
waveform
initial and boundary conditions
spatial and temporal discretization
observable mapping
```

Do not permit a simulation to compensate for missing experimental metadata with invented parameters.

## Implementation

### General CSV handling

Read the stage context JSON before interpreting CSV columns.

Preserve all columns for QA even when only two columns are used for a plot.

Do not discard current-range, status, timing, or instrument-quality columns before checking them.

Treat `point` as an index unless metadata define another role.

### OCP column contract

For columns:

```text
point, t_s, E_V
```

use:

```text
x = t_s
y = E_V
point = row index
```

Do not fit OCP by default.

Calculate directly:

- initial potential
- final potential
- mean
- median
- maximum absolute potential
- peak-to-peak range
- full-window linear drift
- terminal-window drift
- terminal-window standard deviation
- discontinuities
- repeated plateaus
- quantization
- missing or repeated timestamps

A short connection-check OCP can contain too few points for kinetic relaxation fitting. Report the measured values rather than forcing a time constant.

### CV column contract

For columns such as:

```text
point
E_V
I_A
current_range
current_in_range
reading_status
timing_status
sdk_noise
```

use:

```text
x = E_V
y = I_A
```

but retain the other columns for QA.

Do not assume `sdk_noise` is a pointwise current uncertainty. Use it as a weight only when metadata explicitly define its units and meaning.

NaN values in optional diagnostic columns do not invalidate otherwise valid `E_V` and `I_A` rows.

Cathodic current may be negative. Negative current is physically valid and must not be prohibited by non-negative peak-amplitude constraints.

### CV segmentation

Segment CV using the sign of the potential increment.

Identify:

- starting segment
- positive-going sweep
- negative-going sweep
- return sweep
- vertices
- duplicate vertex points
- cycle boundaries

Use plan parameters to verify:

- start potential
- vertex potentials
- scan rate
- cycles
- step size

### EIS column contract

Prefer:

```text
freq_Hz
Zreal_ohm
Zimag_ohm
minus_Zimag_ohm
phase_deg
Z_ohm
```

Determine the stored imaginary convention from:

- column names
- phase sign
- consistency between `Zimag_ohm` and `minus_Zimag_ohm`
- physical capacitive behavior

For a standard capacitive Nyquist display, plot `-Im(Z)` as positive.

Do not flip signs twice.

Do not use PalmSens convenience capacitance columns as a substitute for fitting the declared circuit. They may be reported as secondary instrument-derived quantities.

### PalmSens AC Dummy Cell definition

Recognize the user’s AC Dummy Cell as:

```text
Rs + (Rp || C)
```

with nominal values:

```text
Rs = 1,000 Ω
Rp = 10,000 Ω
C = 10 µF
```

The exact complex impedance is:

```text
Z(ω) = Rs + Rp / (1 + jωRpC)
```

Derived nominal values:

```text
low-frequency resistance = Rs + Rp = 11,000 Ω
tau = Rp*C = 0.1 s
fc = 1/(2*pi*tau) = 1.59154943 Hz
Nyquist apex Z' = Rs + Rp/2 = 6,000 Ω
Nyquist apex -Z'' = Rp/2 = 5,000 Ω
```

This accessory has no chemical reference electrode.

### Exact dummy-cell EIS fit

Fit real and imaginary impedance simultaneously.

Use the physical complex convention:

```text
Zphysical = Zreal - j*(-Zimag_display)
```

when the file stores positive capacitive `minus_Zimag_ohm`.

Use positive bounds:

```text
Rs > 0
Rp > 0
C > 0
```

Recommended initial values:

```text
Rs = 1e3 Ω
Rp = 1e4 Ω
C = 1e-5 F
```

Fit in log-parameter space when supported to guarantee positivity.

Residual vector:

```text
[
 Re(Zmodel)-Zreal_measured,
 -Im(Zmodel)-minus_Zimag_measured
]
```

Report:

- `Rs`
- `Rp`
- `C`
- `Rs+Rp`
- `tau`
- `fc`
- real-component RMSE
- imaginary-component RMSE
- residual structure
- parameter uncertainty when available
- frequency range
- apex frequency
- high- and low-frequency endpoint coverage

Do not replace the ideal capacitor with a CPE unless the exact ideal-capacitor model fails materially and the reason is documented.

### FaradaIQ canonical HDF5 bundles

Recognize root schema values such as:

```text
faradaiq-canonical-h5-bundle-v6
```

Open HDF5 files read-only.

Read in this order:

1. root attributes
2. `/manifest_json`
3. `/tables`
4. each table’s `columns_json`
5. each table’s `record_json`
6. `/metadata`
7. `/media` only when needed

Use `columns_json` as the authoritative column order.

Use structured metadata such as:

```text
point
cycle
method
group_index
series_index
sweep_index
canonical_key
x_position
y_position
z_position
coordinate_status
```

Do not infer coordinates when `coordinate_status` says they are unavailable.

Pair SECCM records by structured point identity, not lexical filename order.

Treat derived DRT as secondary and link it to source EIS.

### FaradaIQ bundle patterns

Support at least:

```text
Approach:
distance_m, point_index, current

Position:
time_s, point_index, current, potential

Position with coordinates:
time_s, point_index, x_position_m, y_position_m, z_position_m,
current, potential

CA:
time_s, point_index, current, potential

CV:
time_s, potential_V, current_A

EIS:
frequency_Hz, Zreal_ohm, Zimag_ohm, Zmod_ohm,
phase_deg, neg_Zimag_ohm

DRT:
tau_s, gamma_ohm
```

### Deterministic artifact behavior

Do not call `save_file` in a managed FaradaIQ transaction when the prompt prohibits file writing.

Return the required strict JSON object to FaradaIQ.

When normal file output is authorized, preserve:

```text
analysis_summary.json
qa_report.json
point_metrics.csv
fit_results.json
plot_recipe.json
electrochemistry_analysis_report.html
```

---

### Thermodynamic calculations

For:

```text
O + ne <-> R
```

the activity-based Nernst relation is:

```text
E = E0_formal + (R*T/(n*F))*ln(a_O/a_R)
```

Use a formal potential when medium-specific activity and complexation effects are embedded in the measured equilibrium.

At 298.15 K, the base-10 slope is approximately:

```text
59.16/n mV per decade
```

only under the stated equilibrium and temperature assumptions.

Do not infer concentration directly from potential without validating selectivity, activities, reference stability, and equilibrium.

### Kinetic calculations

For a simple interfacial reaction, Butler-Volmer may be written:

```text
i = i0*[exp(alpha_a*n*F*eta/(R*T))
 - exp(-alpha_c*n*F*eta/(R*T))]
```

Use only when:

- a meaningful equilibrium potential is known
- current is corrected or assessed for mass transport and iR drop
- active area and current convention are known
- the reaction is adequately represented by the assumed elementary or effective step

For a valid single Tafel branch:

```text
eta = a + b*log10(|i|)
```

Report branch, sign convention, fit interval, current normalization, and whether the interval is free from transport limitation and passivation.

Do not force `alpha_a + alpha_c = 1` unless the selected kinetic model requires it.

Marcus or Marcus-Hush reasoning may be used qualitatively or quantitatively only when reorganization-energy, driving-force, electronic-coupling, and transport assumptions are appropriate. Do not use it as a decorative alternative fit.

Frumkin corrections require an interfacial potential or double-layer model. Do not infer intrinsic kinetics from apparent potential dependence when specific adsorption or diffuse-layer effects are unresolved.

### Transport implementation

The general ionic flux contains diffusion, migration, and convection contributions:

```text
J_i = -D_i*grad(c_i)
 - z_i*F*D_i*c_i*grad(phi)/(R*T)
 + c_i*v
```

Neglect migration only when supporting electrolyte and geometry justify it.

Use Fickian diffusion only when concentration-independent diffusivity and continuum transport are reasonable.

Characteristic diffusion time:

```text
t_D ~ L^2/D
```

Use it to test whether a finite layer, thin layer, microelectrode, or semi-infinite approximation is plausible.

### Cottrell and chronocoulometry

For planar semi-infinite diffusion after an ideal potential step:

```text
i(t) = n*F*A*C*sqrt(D)/(sqrt(pi*t))
```

Exclude switching, charging, bandwidth, and convection regions.

For an Anson-style charge analysis:

```text
Q(t) = 2*n*F*A*C*sqrt(D*t/pi) + Q_dl + n*F*A*Gamma
```

Use the intercept only with blanks or other evidence that separates double-layer charge from adsorbed reactant.

### Reversible planar CV

For a reversible, freely diffusing species under planar semi-infinite diffusion:

```text
i_p = 0.4463*n*F*A*C*sqrt(n*F*D*v/(R*T))
```

At 298.15 K, a common unit-specific form may be used only after documenting the units.

Do not estimate `D` when `A`, `C`, `n`, `T`, or the transport regime is unknown.

### Surface-confined and adsorbed CV

For an ideal reversible noninteracting surface-confined species:

```text
i_p = n^2*F^2*A*Gamma*v/(4*R*T)
Gamma = Q/(n*F*A)
FWHM = 3.53*R*T/(n*F)
```

At 298.15 K, the ideal FWHM is approximately:

```text
90.6/n mV
```

These are diagnostic limits, not universal acceptance criteria.

Broader, narrower, asymmetric, or split peaks can result from:

- lateral interactions
- heterogeneous adsorption sites
- a distribution of formal potentials
- slow electron transfer
- coupled chemistry
- film resistance
- uncompensated resistance
- coverage-dependent thermodynamics

Do not fit arbitrary Gaussian peaks to an adsorbed redox wave and call the widths mechanistic.

### Capacitive current

For an approximately constant interfacial capacitance during a linear sweep:

```text
i_C = A*C_dl*v
```

Use this only over a potential range without strong Faradaic current or changing capacitance.

Separate double-layer capacitance, pseudocapacitance, adsorption charge, and leakage.

### Rotating-disk and hydrodynamic relations

For a laminar rotating disk under standard Levich assumptions:

```text
i_L = 0.620*n*F*A*D^(2/3)*nu^(-1/6)*omega^(1/2)*C
```

where `omega` is angular velocity and `nu` is kinematic viscosity.

For mixed kinetic and transport control:

```text
1/i = 1/i_k + 1/i_L
```

Use signed or absolute currents consistently.

Do not apply Koutecky-Levich analysis when parallel reactions, changing selectivity, film transport, roughness, bubbles, or non-laminar flow dominate.

### Microdisk steady state

For an isolated inlaid disk microelectrode with radius `a`:

```text
i_ss approximately 4*n*F*D*C*a
```

Apply geometry corrections when RG, recessed geometry, finite cell size, or array overlap matters.

Do not use this relation for a band, sphere, hemisphere, cone, recessed disk, or strongly coupled array without correction.

### Pulse and stripping implementation

For DPV and SWV, preserve:

- step size
- pulse amplitude
- pulse width
- pulse period or frequency
- sampling times
- forward current
- reverse current
- net or differential current
- accumulation time and potential
- quiet time
- baseline method

Charging-current suppression depends on timing and instrumentation. It is not complete elimination.

For stripping, maintain the complete deposition and equilibration history with the stripping trace.

### Reference and iR implementation

Estimate uncompensated resistance from a documented method, such as:

- EIS high-frequency intercept
- current interrupt
- positive-feedback calibration
- instrument compensation record

Report both recorded and corrected potential.

Do not apply 100% positive-feedback compensation automatically.

Potential correction must follow the instrument current sign convention and must not be applied twice.

### Double-layer and adsorption models

Distinguish:

- compact-layer response
- diffuse-layer response
- differential capacitance
- point of zero charge
- potential of zero charge under the stated definition
- specific adsorption
- ion pairing
- pseudocapacitance
- film capacitance

Gouy-Chapman, Stern, Grahame, Frumkin, Langmuir, Temkin, and related models require explicit assumptions.

Do not interpret a CPE exponent as surface fractal dimension without independent support.

### EIS model library

Select the smallest plausible topology from:

```text
Rs-(Rct||Cdl)
Rs-(Rct||CPE)
Rs-(Rfilm||Cfilm)-(Rct||Cdl)
finite or semi-infinite diffusion
finite-length bounded diffusion
Gerischer-type reaction-diffusion
porous or transmission-line models
inductive adsorption or relaxation loops
```

For a CPE:

```text
Z_CPE = 1/[Q*(j*omega)^n]
```

`Q` is not directly a capacitance.

A Warburg-like slope is not sufficient evidence for semi-infinite diffusion.

### Coupled-reaction implementation

Represent proposed mechanisms explicitly, for example:

```text
E: O + ne <-> R
EC: O + ne <-> R; R -> P
CE: Y -> O; O + ne <-> R
ECE: O + ne <-> R; R -> Y; Y + ne <-> P
EC': O + ne <-> R; R + A -> O + products
DISP: coupled electron-transfer and disproportionation pathways
```

Use waveform simulation or analytical diagnostics only within their validity range.

Do not label a mechanism from peak separation alone.

### SECM implementation

Normalize when geometry and bulk current are known:

```text
I_T = i_T/i_T,infinity
L = d/a
RG = r_glass/a
kappa = k*a/D
```

Analyze:

- positive feedback at conductive or regenerative substrates
- negative feedback at insulating or blocking substrates
- kinetic feedback between these limits
- substrate-generation/tip-collection
- tip-generation/substrate-collection
- redox competition
- potentiometric probes
- AC and hybrid modes

Fit approach curves using the correct tip geometry and RG.

Do not convert stage Z directly to tip-substrate separation without contact or approach calibration.


### Electrodeposition and electrocrystallization implementation

#### Raw-data preservation and preprocessing

Always preserve:

```text
raw time
raw current
raw potential
instrument status
current range
applied waveform
unmodified current transient
```

Create corrected traces as additional data products.

Do not overwrite the raw transient.

A correction may include:

- measured blank-current subtraction
- independently justified double-layer contribution
- known instrument offset
- a documented time-zero correction
- uncompensated-resistance correction when the required quantities are known

Do not remove an early current spike merely because it reduces fit quality.

Report the raw and corrected traces together.

#### Time origin and induction time

Define separately:

```text
command time
measured potential-settling time
deposition onset
nucleation induction time
fitted model time origin
```

Do not silently shift time to force agreement with a nucleation model.

When an induction period exists, report it and fit only the scientifically
justified post-induction interval.

#### Direct transient quantities

Calculate, when meaningful:

```text
initial current
current at defined times
terminal current
Imax
tmax
induction time
integrated charge Q(t)
total deposition charge
current-decay exponent
early-time slope
late-time slope
noise
drift
range changes
compliance
transient reproducibility
```

Use numerical integration with the actual timestamps.

For deposition current represented as negative, report both signed charge and
the magnitude used for deposited-material calculations.

#### Faradaic material estimates

When electron number, molar mass, and efficiency are defensible:

```text
Q = integral(I dt)
m_theoretical = abs(Q) * M / (n F)
thickness_theoretical = m_theoretical / (rho * area)
```

Do not report mass or thickness as measured unless independently measured.

If current efficiency is known:

```text
m = efficiency * abs(Q) * M / (n F)
```

State the assumed oxidation state and electron number.

#### Cottrell diffusion baseline

For planar semi-infinite diffusion after a potential step:

```text
I(t) = n F A c sqrt(D) / sqrt(pi t)
```

Use Cottrell analysis only when:

- the potential step is sufficiently mass-transfer limiting,
- migration and convection are negligible,
- electrode area and concentration are known,
- the surface is approximately planar and stationary,
- nucleation, film resistance, adsorption, and coupled chemistry do not dominate.

Test linearity of `I` versus `t^(-1/2)` over a justified region.

Do not extrapolate the Cottrell law through the switching transient.

#### Scharifker-Hills normalized models

Use the Scharifker-Hills treatment only for approximately diffusion-controlled,
three-dimensional hemispherical nucleation with diffusion-zone overlap.

Define:

```text
x = t/tmax
y = (I/Imax)^2
```

The ideal instantaneous-nucleation relation is:

```text
y = 1.9542/x * [1 - exp(-1.2564*x)]^2
```

The ideal progressive-nucleation relation is:

```text
y = 1.2254/x * [1 - exp(-2.3367*x^2)]^2
```

Implementation requirements:

- use a defensible deposition onset,
- determine `Imax` and `tmax` with uncertainty,
- preserve the raw transient,
- show both normalized candidate curves,
- calculate residuals over a declared fitting window,
- do not overweight the immediate switching region,
- report whether the late-time tail follows the ideal model,
- compare instantaneous, progressive, and non-ideal alternatives,
- bootstrap or propagate uncertainty in `Imax` and `tmax`,
- do not classify solely from which ideal line looks closer by eye.

Do not apply Scharifker-Hills normalization when no clear maximum exists.

#### Scharifker-Mostany treatment

Consider Scharifker-Mostany when absolute or finite-rate nucleation parameters
are sought and the necessary physical inputs are available.

The selected implementation must explicitly define:

```text
nucleation-site density
nucleation rate
diffusion coefficient
bulk concentration
molar mass
deposit density
electron number
electrode area
molar volume
overlap treatment
time origin
```

Record the exact equation variant and parameter definitions used.

Do not mix parameters or constants from different published variants.

Reject absolute nucleation-density results when required material constants,
concentration, geometry, or diffusion coefficients are missing.

#### Heerman-Tarallo treatment

Use Heerman-Tarallo-type analysis for finite-rate nucleation with overlapping
three-dimensional diffusion zones when the transient contains enough
information to distinguish nucleation rate from site density.

Report:

```text
nucleation rate parameter
site-density parameter
dimensionless nucleation parameter
diffusion or transport inputs
parameter covariance
profile likelihood or bootstrap interval
instantaneous and progressive limiting behavior
```

Strong correlation between nucleation rate and site density must be reported.

Do not present both parameters as independently determined when the likelihood
surface is elongated or boundary-limited.

#### Bewick-Fleischmann-Thirsk treatment

Consider Bewick-Fleischmann-Thirsk models when two-dimensional layer growth,
three-dimensional growth, or charge-time behavior is physically plausible.

State:

- instantaneous or progressive nucleation assumption
- growth dimensionality
- interface-controlled or diffusion-controlled assumption
- whether current, charge, or transformed fraction is fitted
- the fitted time interval
- the physical meaning of every exponent and prefactor

Do not infer two-dimensional growth solely from an early-time power law.

#### Avrami-type treatment

Use an Avrami-type transformed fraction only when a defensible final transformed
quantity is available.

A generic form is:

```text
X(t) = 1 - exp[-(k*(t-t0))^n_A]
```

A charge-derived fraction may be used only when:

```text
X_Q(t) = [Q(t)-Q0] / [Q_infinity-Q0]
```

has a defensible `Q_infinity`.

Report `k`, the Avrami exponent, time origin, fitted interval, and uncertainty.

Do not map the Avrami exponent uniquely to a microscopic mechanism without
independent structural evidence.

#### Mixed and non-ideal transients

When ideal models fail, test explanations such as:

```text
multiple nucleation populations
changing active area
coalescence
film resistance
ohmic drop
depletion
natural or forced convection
gas evolution
adsorption
passivation
surface reconstruction
dendritic growth
porous growth
coupled chemical reactions
multiple depositing species
alloy codeposition
```

Prefer a descriptive multi-stage analysis over an overparameterized fit.

#### Potential-series implementation

For a stepped-potential deposition series, produce one row per condition with:

```text
applied potential
potential relative to OCP or formal potential
Imax
tmax
induction time
charge
terminal current
apparent transient exponent
Scharifker-Hills residuals
selected model
model parameters
fit validity
stripping recovery
morphology metrics
composition metrics
replicate statistics
```

Use applied potential, not electric field, unless field was measured.

#### Deposition-stripping linkage

Link deposition and stripping by experiment identity.

Calculate, where signs and reactions are known:

```text
deposition_charge = abs(integral deposition current)
stripping_charge = abs(integral stripping current)
charge_recovery = stripping_charge/deposition_charge
```

Do not label charge recovery as Faradaic efficiency when side reactions,
residual deposit, dissolution, or incomplete stripping are unresolved.


### Digital simulation implementation

For finite-difference, finite-element, random-walk, or other numerical models:

- save equations and reaction network
- save initial and boundary conditions
- save geometry and mesh
- report dimensionless variables
- perform time-step and mesh refinement
- demonstrate mass or charge conservation when applicable
- compare simulation to raw data and residuals
- expose fixed and fitted parameters
- report parameter correlations
- preserve code and software version

A simulation with many adjustable parameters is not stronger evidence than a simpler model.

### Corrosion implementation

Use polarization resistance near OCP only over a sufficiently small perturbation.

When Stern-Geary assumptions hold:

```text
i_corr = B/R_p
B = beta_a*beta_c/[2.303*(beta_a + beta_c)]
```

Use consistent Tafel-slope units.

Report scan direction, sweep rate, exposed area, OCP history, and passivation.

Do not infer corrosion rate from a broad polarization scan without checking mixed-potential and transport assumptions.

### Battery, fuel-cell, and capacitor implementation

Calculate with explicit normalization:

```text
capacity = integral(I dt)
specific capacity = capacity/active_mass
areal capacity = capacity/geometric_area
coulombic efficiency = Q_discharge/Q_charge
```

Report sign convention and cycle.

For capacitors, distinguish:

- gravimetric, areal, volumetric, and device capacitance
- single-electrode versus two-electrode conversion
- iR drop
- leakage
- pseudocapacitance

Do not multiply or divide by electrode-number factors without documenting the cell configuration.

For GITT or PITT, require geometry, active-material thermodynamics, stable relaxation, and the assumptions of the selected diffusion model.

### Semiconductor and photoelectrochemical implementation

Mott-Schottky analysis requires:

- depletion approximation
- appropriate semiconductor model
- known area and dielectric constant
- stable frequency response
- minimal surface-state and Helmholtz-layer distortion
- correct potential scale

Do not infer carrier density or flat-band potential from one arbitrary frequency without checking frequency dispersion.

### Surface-spectroscopy coupling

When electrochemistry is coupled with photoelectron spectroscopy:

- preserve binding-energy calibration
- assess sample charging
- distinguish initial-state and final-state effects
- record vacuum transfer, air exposure, rinsing, drying, and elapsed time
- consider surface sensitivity and information depth
- check beam damage
- distinguish ex situ state from operando electrochemical state

Do not equate an XPS peak-area change directly with bulk electrochemical conversion.

## Analysis

### OCP analysis

For ordinary electrochemical OCP:

- report initial, final, mean, median, drift, noise, and stabilization,
- do not infer a unique chemical cause for an OCP shift,
- fit a relaxation model only when the user asks for it, enough points exist, and the waveform supports it.

For a dummy-cell connection check:

- interpret OCP as zero-bias continuity and stability,
- expect a small potential near zero,
- inspect steps, quantization, clipping, and range changes,
- do not describe it as electrode equilibration or redox relaxation.

A step between repeated plateaus is not evidence for a single exponential.

### Dummy-cell CV analysis

Treat the CV as a wiring and response check, not a redox spectrum.

Expected behavior:

- bounded current
- approximately linear current-potential relation at 10 mV/s for this 0.1 s circuit
- resistance near 11 kΩ from branch slope
- positive current at positive potential
- negative current at negative potential
- high forward/reverse symmetry
- no redox peaks
- no irreversible growth
- no compliance event

Calculate per sweep segment:

- potential start and end
- current minimum and maximum
- slope `dI/dE`
- apparent resistance `1/(dI/dE)`
- intercept
- linear-regression R² as a descriptive linearity metric
- forward/reverse slope agreement
- current symmetry at matched absolute potentials
- current at zero potential
- vertex transient
- cycle repeatability
- status and clipping flags

Do not use Gaussian peaks for the dummy-cell CV.

Do not call a negative cathodic current an unphysical negative Gaussian.

### General CV analysis

For chemical systems, first use non-model metrics:

- anodic and cathodic peak potentials
- anodic and cathodic peak currents
- peak separation
- midpoint potential
- onset potential
- integrated charge
- current at selected potentials
- hysteresis
- cycle retention
- scan-rate scaling

Use peak fitting only when overlapping peaks require it and the model is physically justified.

Do not add peaks merely because a verifier requests a higher R².

### CA and CP analysis

Report:

- applied step
- initial response
- current or potential at defined times
- terminal response
- drift
- noise
- charge
- compliance
- range changes

Use Cottrell, Sand, or nucleation models only when their assumptions and metadata are satisfied.

### EIS and GEIS analysis

Before fitting:

- verify frequency ordering
- verify frequency range
- verify imaginary sign
- check duplicates
- inspect amplitude and DC bias
- inspect phase
- inspect endpoint coverage
- inspect inductive or contact artifacts
- inspect drift and nonstationarity
- inspect optional status columns

Required plots:

- Nyquist
- Bode magnitude
- Bode phase
- real and imaginary residuals

Do not select a circuit only from low chi-square.

### Dummy-cell EIS analysis

For the known PalmSens circuit, the exact `Rs + (Rp || C)` model is the primary analysis.

Also calculate model-independent estimates when coverage allows:

- high-frequency real-axis intercept
- low-frequency real-axis intercept
- semicircle diameter
- maximum `-Zimag`
- apex frequency
- apex real component
- phase maximum or minimum under the file convention

Compare against:

```text
Rs ≈ 1 kΩ
Rs+Rp ≈ 11 kΩ
Rp ≈ 10 kΩ
C ≈ 10 µF
tau ≈ 0.1 s
fc ≈ 1.59 Hz
apex Z' ≈ 6 kΩ
apex -Z'' ≈ 5 kΩ
```

A frequency range near 10 kHz to 0.1 Hz provides adequate coverage for this circuit when data quality is good.

Do not call an endpoint an intercept if an asymptote is not approached.

### DRT

DRT is optional for the dummy cell.

If used, expect one dominant relaxation near 0.1 s.

Do not use DRT instead of the exact circuit.

Do not interpret multiple DRT peaks as multiple electrochemical mechanisms without checking regularization and frequency coverage.

### SECCM and spatial analysis

Preserve:

- point index
- acquisition order
- coordinates and coordinate source
- raw trace identity
- approach metrics
- CV, CA, EIS, and DRT linkage

Generate both spatial and acquisition-order views.

Do not claim a spatial effect before testing acquisition drift.

### Multimodal evidence

Camera images may identify the visible accessory or wiring arrangement.

A camera image cannot prove:

- electrical continuity
- correct lead assignment
- fitted component values
- commissioning pass

Use electrical data for commissioning status.

---

### Equilibrium, reference, and activity analysis

Report whether measured potential differences can be attributed to:

- true equilibrium composition
- reference drift
- junction potential
- complexation
- pH
- activity changes
- gas partial pressure
- mixed potential
- surface conditioning

Do not compare formal potentials across different solvents, supporting electrolytes, temperatures, or reference systems without qualification.

### Kinetic-versus-transport analysis

Before reporting a heterogeneous rate constant:

- establish the transport model
- assess iR distortion
- verify area
- assess adsorption and coupled chemistry
- use multiple time scales or scan rates
- inspect parameter sensitivity

A peak shift with scan rate may arise from kinetics, iR, chemical reaction, nucleation, or film resistance.

### Weak-support and migration analysis

When supporting electrolyte is low:

- retain migration in the transport model
- consider changes in conductivity and junction potential
- avoid conventional Randles-Sevcik or Cottrell interpretation without correction
- compare cationic, anionic, and neutral species behavior
- report ionic-strength changes during reaction when material

### Surface-confined and adsorption analysis

Use:

- `i_p` versus `v`
- charge versus scan rate
- FWHM and asymmetry
- peak separation
- coverage dependence
- concentration dependence
- open-circuit desorption or aging
- repeated-cycle retention
- blank and blocking controls

Do not infer uniform adsorption sites from a narrow peak, or interaction strength from width alone.

### Coupled mechanism analysis

For EC, CE, ECE, DISP, or catalytic behavior, inspect:

- reverse peak loss or recovery
- peak-ratio variation with scan rate
- switching-potential dependence
- reagent-concentration dependence
- kinetic-zone transitions
- product waves
- steady-state catalytic plateaus
- simulation residuals
- mass balance and product evidence

Use `consistent with` unless competing mechanisms are quantitatively excluded.

### Pulse and stripping analysis

For DPV, SWV, and stripping:

- analyze raw forward and reverse components when available
- inspect baseline and background subtraction
- report accumulation history
- assess saturation and memory
- test matrix effects and interferents
- quantify replicate precision
- distinguish analytical calibration from mechanistic interpretation

Do not compare peak heights collected with different pulse parameters as if directly equivalent.

### Microelectrode and nanoelectrode analysis

Check:

- steady-state versus transient regime
- radial versus planar diffusion
- electrode radius and shape
- RG or recess
- array overlap
- charging-current fraction
- stochastic collision or blockage events
- bandwidth and sampling adequacy

For nanoparticle-impact data, require event-detection rules, false-positive assessment, baseline stability, and event-amplitude distributions.

### Hydrodynamic analysis

For RDE or channel systems:

- verify current versus square root of rotation or flow variable
- inspect intercepts and curvature
- test whether selectivity changes with rotation
- separate kinetic, transport, and film limitations
- propagate uncertainty in viscosity, rotation, area, and concentration

A linear Koutecky-Levich plot is not alone proof of electron number.

### Double-layer and non-Faradaic analysis

Analyze capacitance versus potential, frequency, electrolyte, and history where available.

Distinguish:

- double-layer charging
- specific adsorption
- surface redox pseudocapacitance
- dielectric film response
- leakage
- roughness
- porous distribution of time constants

Do not subtract a polynomial background that removes broad Faradaic or adsorption features.

### Reference-electrode diagnostics

Flag:

- unexpected OCP drift across all samples
- potential jumps synchronized with cable motion
- reference contamination
- salt-bridge blockage
- changing junction composition
- pseudo-reference drift
- conversion errors between scales

Use standards or before/after checks to separate sample change from reference change.

### Corrosion analysis

Report:

- OCP or corrosion potential
- polarization resistance
- anodic and cathodic Tafel regions when defensible
- passive current
- passivation and breakdown potentials
- pitting and repassivation indicators
- hysteresis
- localized versus uniform attack evidence
- EIS parameter evolution

Do not claim a corrosion mechanism from one polarization curve.

### Battery and capacitor analysis

Report:

- voltage profile
- capacity and normalization
- coulombic efficiency
- energy efficiency
- capacity retention
- rate response
- hysteresis
- differential capacity only with appropriate smoothing and provenance
- rest relaxation
- EIS versus state of charge
- replicate cells

Separate formation cycles from steady cycling.

Do not compare half-cell and full-cell capacities without stating the reference and limiting electrode.

### Sensor and electroanalytical analysis

For calibration:

- preserve individual standards and blanks
- fit only the validated range
- report slope, intercept, uncertainty, residuals, and weighting
- test heteroscedasticity
- report LOD/LOQ method
- assess recovery, selectivity, and matrix effects
- use independent validation data where available

Do not report selectivity from absence of visible interference in one trace.

### Semiconductor and photoelectrochemical analysis

Inspect:

- dark versus illuminated current
- chopped-light transients
- wavelength and intensity dependence
- onset definition
- stability and photocorrosion
- capacitive versus Faradaic photocurrent
- Mott-Schottky frequency dispersion
- surface-state effects
- gas or product evidence

Do not call photocurrent quantum efficiency without photon-flux calibration and product or charge accounting.


### Electrodeposition and electrocrystallization analysis

Use this analysis order:

1. verify waveform and sign convention
2. inspect raw current, potential, and instrument-status channels
3. identify switching, induction, nucleation, growth, and terminal regions
4. calculate direct transient and charge metrics
5. compare blanks and replicates
6. test Cottrell behavior when eligible
7. test Scharifker-Hills instantaneous and progressive normalized curves
8. fit Scharifker-Mostany, Heerman-Tarallo, BFT, or Avrami only when eligible
9. inspect residuals and parameter identifiability
10. compare applied-potential and composition series
11. link stripping, mass, thickness, morphology, and composition
12. separate observations from mechanistic assignments

#### Required Scharifker-Hills outputs

When the model is attempted, report:

```text
Imax with uncertainty
tmax with uncertainty
normalization window
instantaneous-model residual metric
progressive-model residual metric
late-time residual behavior
classification confidence
reasons for model rejection or ambiguity
```

Return one of:

```text
INSTANTANEOUS_LIKE
PROGRESSIVE_LIKE
MIXED_OR_TRANSITIONAL
NON_IDEAL
INSUFFICIENT_DATA
```

The suffix `LIKE` is required because normalized agreement does not uniquely
prove a microscopic nucleation mechanism.

#### Model-comparison table

For every candidate model, include:

```text
model
eligible
assumptions satisfied
fit interval
free parameters
residual metric
information criterion when meaningful
parameter uncertainty
parameter correlation
boundary hits
physical consistency
selection status
rejection reason
```

Do not select a more complicated model solely because it has lower residuals.

#### Potential and composition trends

For potential or composition series, test whether changes in:

```text
induction time
Imax
tmax
charge
nucleation classification
nucleation-rate parameter
site-density parameter
growth exponent
stripping recovery
morphology
composition
```

are reproducible and monotonic.

Check confounders:

```text
concentration
diffusion
convection
pH drift
temperature
surface preparation
electrode area
iR drop
current range
acquisition order
aging
```

Do not attribute every trend to applied potential.

#### Alloy and multicomponent deposition

For alloy or multicomponent deposition:

- preserve individual-ion concentrations and speciation
- identify possible underpotential or induced codeposition
- link charge to composition cautiously
- avoid assigning current fractions to elements without independent evidence
- compare composition with total charge, potential, and morphology
- report selective dissolution or stripping
- recognize that deposit composition may evolve with time and depth

#### Morphology and composition linkage

Use microscopy, profilometry, spectroscopy, elemental mapping, diffraction, or
mass measurements as independent evidence.

Compare:

```text
nucleus density
particle-size distribution
coverage
roughness
film thickness
porosity
dendrite metrics
phase identity
elemental composition
spatial uniformity
```

Do not claim that a current-transient model alone establishes morphology.


### SECM and local-probe analysis

For each approach curve or image:

- normalize to bulk tip current when valid
- record tip radius and RG
- fit conductor, insulator, or finite-kinetic models
- report distance calibration
- identify tilt and topographic artifacts
- compare forward and reverse scans
- inspect drift and hysteresis
- quantify spatial resolution relative to tip size and height
- preserve raw current and stage coordinates

For generation-collection modes, report collection efficiency and transport delay.

For biological, membrane, film, battery, catalyst, and photoactive samples, avoid assigning local current to one chemical species without mediator selectivity and controls.

### Digital-simulation analysis

Compare models using:

- predictive residuals
- identifiability
- parameter stability
- conservation checks
- grid independence
- information criteria
- independent observables
- sensitivity analysis

Reject a model that fits only after unphysical parameter values or undocumented baseline terms.

### Multimodal surface analysis

When correlating electrochemistry with XPS, Raman, microscopy, or spectroscopy:

- align sample history and timestamps
- distinguish surface from bulk sensitivity
- preserve spatial and depth mismatch
- check air-transfer and beam-induced changes
- use independent normalization
- distinguish correlation from causal sequence

A change in binding energy can reflect oxidation state, charging, screening, coordination, or calibration.

## Validation


### Electrodeposition-model validation

#### Scharifker-Hills eligibility gate

Mark Scharifker-Hills as `INELIGIBLE` when any critical condition applies:

- no identifiable deposition onset
- no clear current maximum
- current maximum caused by range switching or compliance
- charging transient dominates the available data
- strong convection or rotation is present but not modeled
- gas evolution dominates
- passivation or film resistance controls the transient
- surface area changes too strongly for the ideal geometry
- multiple reactions or alloy components cannot be separated
- time resolution is insufficient around `tmax`
- fewer than enough post-onset points to resolve the rise and decay
- `Imax` or `tmax` uncertainty is too large for stable normalization

#### Model-quality checks

For every fitted deposition model, verify:

- finite parameters and uncertainties
- no unexplained boundary hits
- residuals lack large systematic structure
- fitted interval is declared
- time origin is declared
- raw and corrected data are both available
- parameter definitions match the implemented equations
- replicate behavior is reported
- model ranking is stable to reasonable preprocessing choices
- conclusions survive uncertainty in `Imax`, `tmax`, area, and baseline
- fitted values are physically plausible

A high R² is not sufficient.

#### Identifiability

Use covariance, bootstrap, profile likelihood, or sensitivity analysis.

Flag:

```text
NON_IDENTIFIABLE
WEAKLY_IDENTIFIED
CORRELATED_PARAMETERS
BOUNDARY_LIMITED
PREPROCESSING_SENSITIVE
```

Do not report a precise nucleation density or rate when identifiability fails.

#### Charge and material balance

Where data permit, compare:

```text
deposition charge
stripping charge
measured deposit mass
theoretical Faradaic mass
measured thickness
composition-weighted electron balance
```

Report unresolved charge assigned to side reactions, residual deposit, or
measurement uncertainty.

#### Replicate validation

At minimum report:

- number of replicates
- excluded replicates and reasons
- central tendency
- dispersion
- outliers
- between-electrode variation
- acquisition-order drift

Do not promote a mechanism from one transient when replicates disagree.


### Canonical-heading validation

At skill-load QA, confirm that every level-2 heading is exactly one of:

```text
Overview
Planning
Implementation
Analysis
Validation
Interpretation
```

If any other level-2 heading exists, the skill package is invalid.

### Stage completeness

For post-campaign review:

- verify every completed stage in the manifest was analyzed,
- list stage IDs used,
- list stages not analyzed and why,
- do not mix OCP, CV, and EIS rows into one dataset,
- preserve each stage’s title.

### OCP validation

Do not evaluate OCP quality using generic model R².

Use:

- absolute potential
- terminal drift
- terminal noise
- discontinuities
- clipping
- completeness
- connection consistency

For a short dummy-cell OCP, direct numerical QA is sufficient.

### CV validation

Do not evaluate dummy-cell CV by spectral peak count.

Use:

- expected path
- bounded current
- branch slope
- resistance estimate
- linearity
- symmetry
- cycle completion
- status columns
- compliance
- clipping

For chemical CV, negative cathodic current is valid.

### EIS validation

Use:

- frequency coverage
- DC bias and amplitude
- exact-circuit parameter physicality
- real and imaginary RMSE
- residual structure
- endpoint and apex consistency
- replicate consistency when available
- Kramers-Kronig only when the available implementation and data support it

Do not apply a universal R² threshold to complex EIS.

### Dummy-cell comparison calculations

For each fitted quantity:

```text
signed_error = fitted - nominal
relative_error_percent = 100*(fitted-nominal)/nominal
absolute_relative_error_percent = abs(relative_error_percent)
```

Use manufacturer tolerances when available.

When tolerances are unavailable, label any comparison band as provisional rather than certified.

### Commissioning status

Return:

- `PASS`
- `PASS_WITH_WARNING`
- `FAIL`
- `INSUFFICIENT_DATA`

A `PASS` requires:

- usable OCP connection evidence,
- usable CV wiring-response evidence,
- adequate EIS frequency coverage,
- physically valid exact-circuit fit,
- values reasonably consistent with nominal components,
- no critical compliance, clipping, or corruption.

A camera image alone cannot produce a pass.

### Hard-stop conditions

Stop model escalation and return a warning when:

- the requested model is not scientifically required,
- code generation fails twice,
- the tool routes OCP to peak fitting,
- the tool routes dummy-cell CV to Gaussian fitting,
- the fit requires cathodic current to be positive,
- the verifier recommends extra peaks solely to improve R²,
- the model component metadata do not match the implemented model,
- residual or uncertainty calculations are not finite,
- the model ignores required columns or stage context.

### Regression behavior for the supplied V229 campaign pattern

For a campaign with:

```text
OCP: point, t_s, E_V
CV: point, E_V, I_A, optional diagnostic columns
EIS: freq_Hz, Zreal_ohm, positive capacitive minus_Zimag_ohm
```

the expected routes are:

```text
OCP -> direct stability and connection metrics
CV -> direct segmented linearity, symmetry, and apparent resistance
EIS -> exact complex Rs + (Rp || C) fit
```

The expected routes are not:

```text
OCP -> single exponential CurveFittingAgent
CV -> Gaussian spectral peak model
EIS -> generic spectral model
```

---

### Equation-validity cards

For every quantitative equation used, save or report:

```text
equation name
measured inputs
assumed inputs
units
temperature
geometry
transport regime
kinetic regime
valid time or potential interval
excluded data
uncertainty
known violations
```

If known violations are material, label the result provisional or do not calculate it.

### Dimensional and scale checks

Verify:

- volts versus millivolts
- amperes versus current density
- centimeters versus meters
- mol/L versus mol/cm3
- rpm versus rad/s
- dynamic versus kinematic viscosity
- Hz versus rad/s
- geometric versus active area
- radius versus diameter
- one-electrode versus two-electrode normalization

Reject results with dimensionally inconsistent formulas.

### Instrument and cell validation

Check:

- current-range transitions
- compliance
- ADC clipping
- aliasing
- filter settings
- reference-input impedance
- counter-electrode polarization
- cable shielding and grounding
- working-reference separation
- uncompensated resistance
- potentiostat stability with highly capacitive or resistive loads
- synchronization between control and measured signals

Do not explain an instrument artifact with electrochemical chemistry.

### Reference and potential-scale validation

A converted potential must retain:

```text
original value
original reference
conversion equation
temperature
pH or activity where required
standard or calibration source
converted value
uncertainty
```

Do not convert from an unknown reference.

### Mass-transport validation

For diffusion coefficients or kinetic constants:

- test fit-window dependence
- test geometry alternatives
- inspect residuals
- compare independent methods
- verify concentration and area
- test convection sensitivity
- assess migration and finite-volume effects

### Adsorption validation

For surface coverage:

- subtract or estimate double-layer charge
- verify electron stoichiometry
- verify area basis
- inspect scan-rate independence of integrated charge
- assess desorption or film loss
- report whether coverage is apparent or absolute

### Hydrodynamic validation

Verify rotation or flow calibration, viscosity, laminar assumptions, and collection efficiency.

Curvature in Levich or Koutecky-Levich plots must be reported, not hidden by selecting a favorable subset.

### SECM validation

Require:

- stable bulk tip current
- known or fitted tip geometry
- distance calibration
- approach speed slow enough for the assumed steady state
- no crash or contact artifact
- appropriate substrate boundary condition
- repeatable approach curves
- drift assessment

A visually sharp image is not proof of electrochemical spatial resolution.

### Simulation validation

Require:

- reproducible code
- finite outputs
- mesh and time-step convergence
- mass or charge conservation where applicable
- synthetic-data recovery test for fitted parameters
- uncertainty or sensitivity analysis
- independent validation when possible

Do not use simulation complexity as evidence of truth.

### Corrosion validation

Do not extrapolate Tafel branches through passivation, diffusion limitation, or gas-bubble regions.

Report whether the polarization scan itself may have altered the surface.

### Battery and capacitor validation

Check:

- capacity integration boundaries
- active-mass uncertainty
- current sign
- cycle indexing
- voltage-window consistency
- missing rest periods
- temperature drift
- cell-to-cell variability
- balancing and excess capacity
- device versus material normalization

### Sensor validation

Require enough blanks and calibration levels for the stated LOD or LOQ method.

Check carryover, drift, recovery, selectivity, and matrix matching.

### Multimodal validation

For surface spectroscopy:

- verify energy calibration
- inspect charging and line-shape constraints
- record background and sensitivity-factor methods
- avoid over-precise oxidation-state fractions
- report air exposure and beam dose

### Books-integration regression safeguards

The expanded knowledge must not override the v3 routing protections:

```text
OCP connection check -> direct metrics, not exponential fitting
PalmSens dummy CV -> segmented linear response, not Gaussian peaks
PalmSens dummy EIS -> exact complex Rs + (Rp || C) fit
FaradaIQ HDF5 -> manifest and structured records, read-only
DRT -> only with linked EIS or GEIS
```

## Interpretation


### Balanced interpretation

Treat deterministic metrics, fitted parameters, agent-generated analyses, and
literature context as complementary evidence.

Do not repeat a derived value merely because it appears in a preprocessing
file. Check it against:

- raw data,
- units,
- sweep direction,
- sign convention,
- file identity,
- experimental metadata,
- neighboring cycles or replicates.

When a supplied result appears inconsistent, say so and recalculate or qualify
it.

Avoid overly rigid statements about what SciLink did or did not use unless the
job provenance supports the statement.



### Electrodeposition interpretation language

Use:

- `instantaneous-nucleation-like normalized response`
- `progressive-nucleation-like normalized response`
- `mixed or non-ideal nucleation and growth`
- `consistent with diffusion-zone overlap`
- `apparent nucleation-rate parameter`
- `apparent active-site density under the selected model`
- `charge-derived theoretical deposit mass`
- `stripping-to-deposition charge recovery`
- `morphology is consistent with, but does not prove, the transient model`

Avoid:

- `Scharifker-Hills proves instantaneous nucleation`
- `the Avrami exponent uniquely identifies the mechanism`
- `charge recovery equals Faradaic efficiency` without complete accounting
- `current peak equals nucleus formation` without separating charging and growth
- `more negative applied potential always increases nucleation density`
- `model fit proves dendritic growth`

### Required electrodeposition structured output

When deposition data are present, extend the structured result with:

```json
{
  "electrodeposition": {
    "detected": true,
    "waveform": null,
    "applied_potential_V": null,
    "reference_scale": null,
    "raw_metrics": {
      "induction_time_s": null,
      "Imax_A": null,
      "tmax_s": null,
      "deposition_charge_C": null,
      "terminal_current_A": null
    },
    "preprocessing": {
      "blank_subtracted": false,
      "time_shift_s": 0.0,
      "corrections": [],
      "warnings": []
    },
    "model_eligibility": {
      "cottrell": "NOT_ASSESSED",
      "scharifker_hills": "NOT_ASSESSED",
      "scharifker_mostany": "NOT_ASSESSED",
      "heerman_tarallo": "NOT_ASSESSED",
      "bewick_fleischmann_thirsk": "NOT_ASSESSED",
      "avrami": "NOT_ASSESSED"
    },
    "scharifker_hills": {
      "instantaneous_residual": null,
      "progressive_residual": null,
      "classification": "INSUFFICIENT_DATA",
      "classification_confidence": null
    },
    "selected_model": null,
    "fit_parameters": {},
    "parameter_uncertainty": {},
    "identifiability": null,
    "stripping_recovery": null,
    "morphology_links": [],
    "composition_links": [],
    "limitations": []
  }
}
```


### Evidence hierarchy

Use these labels:

1. **Observed**
2. **Calculated**
3. **Fitted**
4. **Consistent with**
5. **Proposed mechanism**

Do not present levels 4 or 5 as direct observation.

### Dummy-cell interpretation

Use electrical language:

- connection stable
- response linear
- branch symmetry
- measured resistance
- fitted capacitance
- time constant
- characteristic frequency
- agreement with nominal circuit

Do not use:

- redox peak
- diffusion control
- charge transfer
- electrode kinetics
- adsorption
- corrosion
- catalysis

unless the dataset is from a real electrochemical cell rather than the dummy cell.

### Campaign conclusion logic

Conclude with `experiment_plan=null` when:

- all required completed stages were analyzed,
- OCP supports connection,
- CV supports expected wiring/response,
- EIS recovers the nominal circuit,
- no unresolved quality issue would change the scientific conclusion.

Recommend one additional experiment only when it resolves a named limitation.

### Required per-stage summary

For each stage report:

```text
stage_id
experimental_title
technique
data_quality_status
observed_results
calculated_results
fitted_results
limitations
supports_goal
```

### Required dummy-cell summary

Include:

```json
{
 "dummy_cell": {
 "detected": true,
 "model": "Rs + (Rp || C)",
 "nominal": {
 "Rs_ohm": 1000.0,
 "Rp_ohm": 10000.0,
 "C_F": 0.00001,
 "Rtotal_ohm": 11000.0,
 "tau_s": 0.1,
 "characteristic_frequency_Hz": 1.59154943
 },
 "ocp_check": {},
 "cv_check": {},
 "eis_check": {},
 "fit": {},
 "comparison_to_nominal": [],
 "overall_status": "INSUFFICIENT_DATA"
 }
}
```

### Final-answer style

- Lead with the definitive decision.
- Report values with units.
- Cite the stage titles.
- State limitations.
- Avoid promotional language.
- Do not state that a fit “proves” the circuit.
- Prefer “consistent with the nominal dummy-cell circuit.”
- Preserve strict JSON-only output when the managed transaction requires it.

### Minimal execution directive

```text
Inspect every authorized completed stage and its context. Use technique-aware electrochemical reasoning, preserve raw data and identities, calculate direct observables, and use fitting, simulation, literature, multimodal analysis, or alternative agents when they improve the scientific answer. For electrodeposition, apply Scharifker-Hills, Scharifker-Mostany, Heerman-Tarallo, Bewick-Fleischmann-Thirsk, Cottrell, or Avrami only when their assumptions and identifiability are reasonable, and link transient results to stripping, morphology, and composition. Use direct numerical
QA for OCP. Segment dummy-cell CV by potential direction and calculate linearity,
symmetry, and apparent resistance without Gaussian or spectral peak fitting.
Fit dummy-cell EIS once using the exact complex Rs + (Rp || C) model, compare
against 1 kΩ, 10 kΩ, 10 µF, 11 kΩ, 0.1 s, and 1.5915 Hz, and inspect real and
imaginary residuals. Preserve diagnostic columns and stage titles. Do not enter
generic curve-fitting retry loops. Analyze FaradaIQ HDF5 bundles read-only using
manifest, columns_json, record_json, and structured point identity. Return one
strict FaradaIQ JSON object when required, with approved=false and no hardware
authorization.
```

### Mechanism evidence matrix

Use a compact matrix when evaluating mechanisms:

| Evidence | Transport | Kinetics | Coupled chemistry | Adsorption/film | Instrument |
|---|---|---|---|---|---|
| scan-rate dependence | | | | | |
| concentration dependence | | | | | |
| reverse-scan behavior | | | | | |
| rotation/flow dependence | | | | | |
| charge balance | | | | | |
| EIS/DRT | | | | | |
| surface characterization | | | | | |
| controls and replicates | | | | | |

Populate with supporting, conflicting, or missing evidence.

Do not collapse mixed evidence into one definitive label.

### Thermodynamic language

Prefer:

- `formal potential under the stated medium`
- `apparent equilibrium shift`
- `consistent with activity or complexation changes`
- `potential referenced to...`

Avoid calling every midpoint potential a standard reduction potential.

### Kinetic language

Prefer:

- `apparent heterogeneous rate constant under the selected model`
- `transport-corrected kinetic current`
- `Tafel-like region`
- `consistent with slower electron transfer`

Avoid claiming intrinsic electron-transfer kinetics when iR, adsorption, or mass transport are unresolved.

### Adsorption and film language

Use:

- `apparent surface coverage`
- `surface-confined-like scan-rate scaling`
- `non-ideal adsorbate interactions may contribute`
- `film growth or changing active area is consistent with...`

Do not infer monolayer structure from charge alone.

### Solvation and electrolyte language

Use:

- `consistent with altered solvation, viscosity, activity, ion pairing, or speciation`
- `concentration-based interpretation is approximate`
- `weak supporting electrolyte may introduce migration`

Do not attribute every solvent trend to dielectric constant alone.

### SECM language

Use:

- `positive-feedback-like`
- `negative-feedback-like`
- `finite-kinetic approach response`
- `local mediator regeneration`
- `electrochemical contrast at the probe scale`
- `topographic cross-talk cannot be excluded`

Do not equate a local current map with concentration, rate constant, or composition without calibration.

### Battery, corrosion, and sensor language

Use cell-configuration-specific terms and report normalization.

Do not claim practical device performance from one laboratory electrode curve.

Do not claim corrosion protection from a lower current at one potential without long-term and localized-corrosion evidence.

Do not claim analytical selectivity or detection limit without the corresponding validation design.

### Multimodal surface language

Use:

- `surface-sensitive spectral change`
- `consistent with a change in chemical environment`
- `electrochemical and spectroscopic changes are correlated in time or condition`
- `ex situ transfer may have modified the surface`

Do not equate XPS binding-energy shifts uniquely with oxidation state.

### Source provenance

When a result depends on a theoretical model, record the model name and assumptions in provenance.

The skill may list the user-provided book or review that motivated an analysis rule, but it must not reproduce substantial copyrighted text.
