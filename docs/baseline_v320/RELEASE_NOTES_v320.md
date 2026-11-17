# FaradaIQ Studio v320 release notes

FaradaIQ Studio v320 is a focused PowerPoint reliability update built from v319. It does not change Mott-Schottky, EIS, or DRT scientific calculations.

## Fixed

- Fixed a Windows Matplotlib mathtext failure that could abort the v319 potential-resolved PowerPoint appendix while rendering logarithmic Bode and DRT axes.
- The Mott-Schottky summary deck now retains the potential-resolved Nyquist overlay, Bode magnitude and phase overlays, DRT overlay, and DRT potential map / dominant-relaxation slide when those source datasets are available.
- Overlay footer/version text now follows the active release identity rather than being hard-coded to v319.
- Added a regression test that emulates a mathtext-hostile log-axis backend.

## Scientific behavior

No acquisition, EIS fitting, Mott-Schottky interpretation, or DRT inversion semantics were redesigned in this release.
