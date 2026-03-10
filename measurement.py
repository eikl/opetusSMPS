"""Measurement sequence runner.

Usage:
    runner = MeasurementRunner(hv_module, cpc_module)
    runner.run_sequence(voltages=[100,200,300], settling=2.0, measure_time=5.0,
                        save_path='out.csv', repetitions=1)

When repetitions > 1 (or -1 for continuous), the voltage list is scanned
repeatedly.  Setting repetitions=-1 loops until ``stop_event`` is set.

The averaged CSV has one column per voltage and one row per scan repetition.
"""
from __future__ import annotations

import time
import csv
import threading
from typing import Iterable, List, Optional


class MeasurementRunner:
    def __init__(self, hv_module, cpc_module, sample_interval: float = 0.2):
        self.hv = hv_module
        self.cpc = cpc_module
        self.sample_interval = float(sample_interval)

    def run_sequence(self, voltages: Iterable[float], settling: float, measure_time: float,
                     save_path: Optional[str] = None,
                     sample_callback=None, progress_callback=None,
                     repetitions: int = 1,
                     stop_event: threading.Event = None):
        """Run the voltage-scan sequence.

        Args:
            voltages: Iterable of voltage setpoints.
            settling: Seconds to wait after setting voltage.
            measure_time: Seconds to sample CPC at each voltage.
            save_path: Path for the averaged CSV (optional).
            sample_callback: ``(voltage, elapsed, concentration)`` per sample.
            progress_callback: ``(fraction)`` for overall progress.
            repetitions: Number of full scans.  Use -1 for continuous.
            stop_event: ``threading.Event`` – set it externally to stop a
                continuous run gracefully.

        Returns:
            List of per-scan results: ``[[(v, avg), ...], ...]``
        """
        voltages = list(voltages)
        continuous = (repetitions == -1)
        total_reps = 1 if continuous else max(1, repetitions)

        # resolve hv setter function
        if hasattr(self.hv, 'set_hv_setpoint'):
            _set_hv = lambda v: self.hv.set_hv_setpoint(v)
        elif hasattr(self.hv, 'set_voltage'):
            _set_hv = lambda v: self.hv.set_voltage(v)
        elif hasattr(self.hv, 'device') and hasattr(self.hv.device, 'set_voltage'):
            _set_hv = lambda v: self.hv.device.set_voltage(v)
        else:
            raise AttributeError("HV module has no set_hv_setpoint or set_voltage method")

        # open raw file if saving
        raw_file = None
        raw_writer = None
        if save_path:
            import os
            save_path_expanded = os.path.expanduser(save_path)
            base, ext = os.path.splitext(save_path_expanded)
            raw_path = f"{base}_raw{ext or '.csv'}"
            parent = os.path.dirname(save_path_expanded)
            if parent:
                os.makedirs(parent, exist_ok=True)
            raw_file = open(raw_path, 'w', newline='')
            raw_writer = csv.writer(raw_file)
            raw_writer.writerow(['repetition', 'voltage', 'timestamp_utc', 'concentration'])

        all_results: List[List[tuple]] = []
        seq_start = time.time()
        rep_index = 0

        while True:
            # check stop condition
            if not continuous and rep_index >= total_reps:
                break
            if stop_event is not None and stop_event.is_set():
                break

            scan_results = []
            step_duration = settling + measure_time
            total_duration_this_rep = step_duration * len(voltages)

            for vi, v in enumerate(voltages):
                if stop_event is not None and stop_event.is_set():
                    break

                _set_hv(v)

                # --- settling ---
                t_set_start = time.time()
                while time.time() - t_set_start < settling:
                    if stop_event is not None and stop_event.is_set():
                        break
                    try:
                        c = float(self.cpc.get_concentration())
                    except Exception:
                        c = float('nan')
                    if sample_callback:
                        try:
                            sample_callback(v, time.time() - seq_start, c)
                        except Exception:
                            pass
                    if raw_writer:
                        try:
                            ts = __import__('datetime').datetime.utcfromtimestamp(time.time()).isoformat() + 'Z'
                            raw_writer.writerow([rep_index + 1, v, ts, f"{c}"])
                            raw_file.flush()
                        except Exception:
                            pass
                    # progress
                    if progress_callback and not continuous:
                        try:
                            completed_steps = rep_index * len(voltages) + vi
                            frac_in_step = min(1.0, (time.time() - t_set_start) / step_duration) if step_duration > 0 else 1.0
                            overall = (completed_steps + frac_in_step) / (total_reps * len(voltages))
                            progress_callback(min(1.0, overall))
                        except Exception:
                            pass
                    time.sleep(self.sample_interval)

                # --- measurement ---
                samples = []
                t_meas_start = time.time()
                while time.time() - t_meas_start < measure_time:
                    if stop_event is not None and stop_event.is_set():
                        break
                    try:
                        c = float(self.cpc.get_concentration())
                    except Exception:
                        c = float('nan')
                    samples.append(c)
                    if raw_writer:
                        try:
                            ts = __import__('datetime').datetime.utcfromtimestamp(time.time()).isoformat() + 'Z'
                            raw_writer.writerow([rep_index + 1, v, ts, f"{c}"])
                            raw_file.flush()
                        except Exception:
                            pass
                    if sample_callback:
                        try:
                            sample_callback(v, time.time() - seq_start, c)
                        except Exception:
                            pass
                    if progress_callback and not continuous:
                        try:
                            completed_steps = rep_index * len(voltages) + vi
                            frac_in_step = min(1.0, (settling + time.time() - t_meas_start) / step_duration) if step_duration > 0 else 1.0
                            overall = (completed_steps + frac_in_step) / (total_reps * len(voltages))
                            progress_callback(min(1.0, overall))
                        except Exception:
                            pass
                    time.sleep(self.sample_interval)

                valid = [x for x in samples if x == x]
                avg = sum(valid) / len(valid) if valid else float('nan')
                scan_results.append((v, avg))

            all_results.append(scan_results)
            rep_index += 1

            # for continuous mode, keep going (loop re-checks stop_event)
            if continuous:
                total_reps += 1  # keep total_reps in sync for bookkeeping

        # ---- write averaged CSV ----
        if save_path and all_results:
            import os
            save_path_expanded = os.path.expanduser(save_path)
            parent = os.path.dirname(save_path_expanded)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(save_path_expanded, 'w', newline='') as f:
                w = csv.writer(f)
                # header row: voltages
                w.writerow([v for v, _ in all_results[0]])
                # one row per repetition
                for scan in all_results:
                    w.writerow([a for _, a in scan])

        # close raw file
        if raw_file:
            try:
                raw_file.close()
            except Exception:
                pass

        # final progress
        if progress_callback:
            try:
                progress_callback(1.0)
            except Exception:
                pass

        return all_results
