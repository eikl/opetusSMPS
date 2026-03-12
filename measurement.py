"""Measurement sequence runner.

Usage:
    runner = MeasurementRunner(hv_module, cpc_module)
    runner.run_sequence(voltages=[100,200,300], settling=2.0, measure_time=5.0, save_path='out.csv')

This will for each voltage:
 - set HV setpoint
 - wait `settling` seconds
 - sample CPC via `cpc_module.get_concentration()` repeatedly for `measure_time` seconds
 - compute the average and write to CSV if save_path provided
"""
from __future__ import annotations

import time
import csv
from typing import Iterable, Optional


class MeasurementRunner:
    def __init__(self, hv_module, cpc_module, sample_interval: float = 0.2):
        self.hv = hv_module
        self.cpc = cpc_module
        self.sample_interval = float(sample_interval)

    def run_sequence(self, voltages: Iterable[float], settling: float, measure_time: float, save_path: Optional[str] = None,
                     sample_callback=None, progress_callback=None):
        results = []
        # compute total duration for progress reporting
        total_duration = sum((settling + measure_time) for _ in voltages)
        seq_start = time.time()
        # resolve hv setter function (flexible to accept high_voltage module or hardware.hv)
        if hasattr(self.hv, 'set_hv_setpoint'):
            _set_hv = lambda v: self.hv.set_hv_setpoint(v)
        elif hasattr(self.hv, 'set_voltage'):
            _set_hv = lambda v: self.hv.set_voltage(v)
        elif hasattr(self.hv, 'device') and hasattr(self.hv.device, 'set_voltage'):
            _set_hv = lambda v: self.hv.device.set_voltage(v)
        else:
            raise AttributeError("HV module has no set_hv_setpoint or set_voltage method")
        # prepare raw file if needed
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
            # open raw file for streaming sample writes
            raw_file = open(raw_path, 'w', newline='')
            raw_writer = csv.writer(raw_file)
            raw_writer.writerow(['voltage', 'timestamp_utc', 'concentration'])

        for v in voltages:
            # set HV (flexible)
            _set_hv(v)
            # settling: sample periodically so UI can plot and progress can update
            t_set_start = time.time()
            set_sample_idx = 0
            while time.time() - t_set_start < settling:
                try:
                    c = float(self.cpc.get_concentration())
                except Exception:
                    c = float('nan')
                # report settling sample to callback
                try:
                    if sample_callback:
                        elapsed = time.time() - seq_start
                        sample_callback(v, elapsed, c)
                except Exception:
                    pass
                # write raw sample if requested
                if raw_writer:
                    try:
                        ts = __import__('datetime').datetime.utcfromtimestamp(time.time()).isoformat() + 'Z'
                        raw_writer.writerow([v, ts, f"{c}"])
                        raw_file.flush()
                    except Exception:
                        pass
                # progress update based on total duration
                try:
                    if progress_callback:
                        frac = min(1.0, (time.time() - seq_start) / total_duration) if total_duration > 0 else 1.0
                        progress_callback(frac)
                except Exception:
                    pass
                set_sample_idx += 1
                time.sleep(self.sample_interval)
            # measurement
            samples = []
            t_start = time.time()
            sample_idx = 0
            while time.time() - t_start < measure_time:
                try:
                    c = float(self.cpc.get_concentration())
                except Exception:
                    c = float('nan')
                samples.append(c)
                # write raw sample if requested
                if raw_writer:
                    try:
                        ts = __import__('datetime').datetime.utcfromtimestamp(time.time()).isoformat() + 'Z'
                        raw_writer.writerow([v, ts, f"{c}"])
                        # flush to ensure data is on disk
                        raw_file.flush()
                    except Exception:
                        pass
                sample_idx += 1
                # report sample to callback (UI can plot) with elapsed since sequence start
                try:
                    if sample_callback:
                        elapsed = time.time() - seq_start
                        sample_callback(v, elapsed, c)
                except Exception:
                    pass
                # progress update based on total duration
                try:
                    if progress_callback:
                        frac = min(1.0, (time.time() - seq_start) / total_duration) if total_duration > 0 else 1.0
                        progress_callback(frac)
                except Exception:
                    pass
                time.sleep(self.sample_interval)
            # compute average ignoring nan
            valid = [x for x in samples if x == x]
            avg = sum(valid) / len(valid) if valid else float('nan')
            results.append((v, avg))

        if save_path:
            # expand user and ensure parent exists
            import os
            save_path_expanded = os.path.expanduser(save_path)
            parent = os.path.dirname(save_path_expanded)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(save_path_expanded, 'w', newline='') as f:
                w = csv.writer(f)
                w.writerow(['voltage', 'concentration_avg'])
                for v, a in results:
                    w.writerow([v, a])
            # close raw file if opened
            try:
                if raw_file:
                    raw_file.close()
            except Exception:
                pass

        # final progress update
        try:
            if progress_callback:
                progress_callback(1.0)
        except Exception:
            pass
        return results
