"""Helper functions for UI plotting and locking.

These accept the ControlUI instance as the first argument to avoid circular imports.
"""
from typing import Any
import time


def trim_plot(ui: Any) -> None:
    """Trim plot data on ui to the last ui._plot_max_seconds seconds."""
    try:
        if not getattr(ui, '_plot_x', None):
            return
        now = ui._plot_x[-1]
        cutoff = now - float(getattr(ui, '_plot_max_seconds', 300.0))
        idx = 0
        for i, t in enumerate(ui._plot_x):
            if t >= cutoff:
                idx = i
                break
        if idx > 0:
            ui._plot_x = ui._plot_x[idx:]
            ui._plot_y = ui._plot_y[idx:]
    except Exception:
        pass


def add_idle_point(ui: Any, v: float) -> None:
    """Append an idle sample to the plot and redraw.

    Assumes this is called from the main thread (or via root.after).
    """
    try:
        if ui._plot_line is None:
            return
        t_rel = time.time() - ui._plot_base_time
        ui._plot_x.append(t_rel)
        ui._plot_y.append(v)
        trim_plot(ui)
        ui._plot_line.set_data(ui._plot_x, ui._plot_y)
        ui.ax.relim()
        ui.ax.autoscale_view()
        ui.canvas.draw_idle()
    except Exception:
        pass


def add_measurement_point(ui: Any, plot_time: float, c: float) -> None:
    """Append a measurement sample to the plot and redraw.

    plot_time should already be converted to the same time base as idle samples.
    """
    try:
        if ui._plot_line is None:
            return
        ui._plot_x.append(float(plot_time))
        ui._plot_y.append(c)
        trim_plot(ui)
        ui._plot_line.set_data(ui._plot_x, ui._plot_y)
        ui.ax.relim()
        ui.ax.autoscale_view()
        ui.canvas.draw_idle()
    except Exception:
        pass


def set_ui_locked(ui: Any, locked: bool) -> None:
    """Disable/enable interactive controls on the UI."""
    try:
        state = 'disabled' if locked else 'normal'
        # HV controls
        try:
            ui.hv_set_scale.state(['disabled'] if locked else ['!disabled'])
        except Exception:
            pass
        try:
            ui.hv_set_entry.configure(state=state)
        except Exception:
            pass
        try:
            ui._hv_set_button.configure(state=state)
        except Exception:
            pass
        # blower controls
        try:
            ui.blower_set_scale.state(['disabled'] if locked else ['!disabled'])
        except Exception:
            pass
        try:
            ui.blower_set_entry.configure(state=state)
        except Exception:
            pass
        try:
            ui._blower_set_button.configure(state=state)
        except Exception:
            pass
        # measurement controls
        try:
            ui._start_button.configure(state='disabled' if locked else 'normal')
        except Exception:
            pass
        # save path entry: try to disable if present
        try:
            # some versions may not have stored the entry widget reference; guard accordingly
            if hasattr(ui, 'save_path_entry'):
                ui.save_path_entry.configure(state=state)
        except Exception:
            pass
    except Exception:
        pass
