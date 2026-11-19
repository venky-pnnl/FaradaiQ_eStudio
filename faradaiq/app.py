"""Application entry point for FaradaIQ Studio v320."""
from __future__ import annotations

from typing import Sequence

from ._legacy import legacy_module

def main(argv: Sequence[str] | None = None) -> int | None:
    """Launch the FaradaIQ GUI through the stable core implementation.

    ``argv`` is accepted for future CLI expansion; the current Tkinter app uses
    the same environment-variable and launcher behavior as prior releases.
    """
    module = legacy_module()
    entry = getattr(module, "_v341_main_final", None) or getattr(module, "_v320_main_final", None) or getattr(module, "_v319_main_final", None) or getattr(module, "_v318_main_final", None) or getattr(module, "_v317_main_final", None) or getattr(module, "_v316_main_final", None) or getattr(module, "_v315_main_final", None) or getattr(module, "_v314_main_final", None) or getattr(module, "_v311_main_final", None) or getattr(module, "_v310_main_final", None) or getattr(module, "_v309_main_final", None) or getattr(module, "_v308_main_final", None) or getattr(module, "_v307_main_final", None) or getattr(module, "_v304_main_final", None) or getattr(module, "_v303_main_final", None) or getattr(module, "_v302_main_final", None) or getattr(module, "_v300_main_final", None) or getattr(module, "_v299_main_final", None) or getattr(module, "_v298_main_final", None) or getattr(module, "_v297_main_final", None) or getattr(module, "_v296_main_final", None) or getattr(module, "_v295_main_final", None) or getattr(module, "_v294_main_final", None) or getattr(module, "_v293_main_final", None) or getattr(module, "_v292_main_final", None) or getattr(module, "_v291_main_final", None) or getattr(module, "_v290_main_final", None) or getattr(module, "_v273_main_final", None) or getattr(module, "_v262_main_final", None) or getattr(module, "_v256_main_final", None) or getattr(module, "_v255_main_final", None) or getattr(module, "_v254_main_final", None) or getattr(module, "_v253_main_final", None) or getattr(module, "_v252_main_final", None) or getattr(module, "_v250_main_final", None) or getattr(module, "_v247_main_final", None) or getattr(module, "_v246_main_final", None) or getattr(module, "_v245_main_final", None) or getattr(module, "_v244_main_final", None) or getattr(module, "_v240_main_final", None) or getattr(module, "_v239_main_final", None) or getattr(module, "_v237_main_final", None) or getattr(module, "_v233_main_final", None) or getattr(module, "_v232_main_final", None) or getattr(module, "_v229_main_final", None) or getattr(module, "_v228_main_final", None) or getattr(module, "_v227_main_final", None) or getattr(module, "_v226_main_final", None) or getattr(module, "_v225_main_final", None) or getattr(module, "_v222_main_final", None) or getattr(module, "_v219_main_final", None) or getattr(module, "_v218_main_final", None) or getattr(module, "_v120_main_final", None) or getattr(module, "_v118_main_final", None) or getattr(module, "_v113_main_final", None)
    if entry is None:
        raise RuntimeError("No supported FaradaIQ GUI entry point was found in the legacy module.")
    return entry()

if __name__ == "__main__":
    raise SystemExit(main())
