"""Launch DearEIS with a best-effort FaradaIQ global DearPyGui theme."""
from __future__ import annotations
import argparse
import os
import runpy
import sys


def _install_faradaiq_dpg_theme() -> None:
    try:
        import dearpygui.dearpygui as dpg
    except Exception:
        return
    original_show = getattr(dpg, "show_viewport", None)
    if not callable(original_show) or getattr(dpg, "_faradaiq_theme_bridge", False):
        return

    def themed_show(*args, **kwargs):
        try:
            theme_name = str(os.environ.get("FARADAIQ_THEME", "High Contrast")).lower()
            if "light" in theme_name:
                window_bg, child_bg, text, border, accent, selected = (244,247,251,255), (255,255,255,255), (16,32,51,255), (154,169,186,255), (23,105,170,255), (47,111,173,255)
            elif "contrast" in theme_name:
                window_bg, child_bg, text, border, accent, selected = (0,0,0,255), (5,5,5,255), (255,255,255,255), (255,255,255,255), (255,242,0,255), (0,71,255,255)
            else:
                window_bg, child_bg, text, border, accent, selected = (15,23,36,255), (22,34,53,255), (230,237,247,255), (82,100,126,255), (94,167,224,255), (45,95,139,255)
            with dpg.theme() as global_theme:
                with dpg.theme_component(dpg.mvAll):
                    dpg.add_theme_color(dpg.mvThemeCol_WindowBg, window_bg)
                    dpg.add_theme_color(dpg.mvThemeCol_ChildBg, child_bg)
                    dpg.add_theme_color(dpg.mvThemeCol_PopupBg, child_bg)
                    dpg.add_theme_color(dpg.mvThemeCol_Text, text)
                    dpg.add_theme_color(dpg.mvThemeCol_Border, border)
                    dpg.add_theme_color(dpg.mvThemeCol_Button, child_bg)
                    dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, selected)
                    dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, accent)
                    dpg.add_theme_color(dpg.mvThemeCol_Header, selected)
                    dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, accent)
                    dpg.add_theme_color(dpg.mvThemeCol_FrameBg, window_bg)
                    dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, child_bg)
                    dpg.add_theme_color(dpg.mvThemeCol_CheckMark, accent)
                    dpg.add_theme_color(dpg.mvThemeCol_SliderGrab, accent)
                    dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 2)
                    dpg.add_theme_style(dpg.mvStyleVar_WindowBorderSize, 1)
            dpg.bind_theme(global_theme)
        except Exception as exc:
            print(f"FaradaIQ DearEIS theme bridge warning: {exc}")
        return original_show(*args, **kwargs)

    dpg.show_viewport = themed_show
    dpg._faradaiq_theme_bridge = True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="")
    args, extra = parser.parse_known_args(argv)
    _install_faradaiq_dpg_theme()
    sys.argv = ["deareis"] + ([args.project] if args.project else []) + list(extra)
    runpy.run_module("deareis", run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
