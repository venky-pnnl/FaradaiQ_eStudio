# FaradaIQ Studio v320 PowerPoint presentation QA

The v320 presentation regression requires the summary deck for potential-dependent Mott-Schottky/EIS data to retain the MT-specific analysis and add four electrochemical overlay slides:

1. Potential-resolved EIS overlay: Nyquist.
2. Potential-resolved EIS overlays: Bode magnitude and phase.
3. Potential-resolved DRT overlay.
4. DRT potential map and dominant relaxation trend.

The same representative potentials are highlighted across Nyquist, Bode, and DRT. All spectra remain visible as low-emphasis background curves where appropriate. The DRT map uses completed analyzed DRT distributions, not raw DRT-input staging CSVs.

Visual QA must render the final PPTX and check for clipping, unreadable legends, empty panels, and slide-canvas overflow.
