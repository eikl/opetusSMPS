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

import os
import time
import csv
from datetime import datetime
from typing import Iterable, Optional

from config import get_float, get_str


def _read_dma_params():
    """Read DMA geometry and defaults from .env config."""
    return {
        'length': float(get_float('DMA_LENGTH', 0.11)),
        'r_inner': float(get_float('DMA_INNER_RADIUS', 0.025)),
        'r_outer': float(get_float('DMA_OUTER_RADIUS', 0.033)),
        'sheath_flow': float(get_float('DMA_SHEATH_FLOW', 5.0)),
        'aerosol_flow': float(get_float('DMA_AEROSOL_FLOW', 1.0)),
        'cpc_type': str(get_str('CPC_TYPE', 'TSI3010')),
        'temperature': float(get_float('DEFAULT_TEMPERATURE', 293.15)),
        'pressure': float(get_float('DEFAULT_PRESSURE', 101325)),
    }


def _try_read_sensors():
    """Try to read real sheath flow, temperature, pressure from hardware.

    Returns dict with keys 'sheath_flow', 'temperature_C', 'pressure_hPa'.
    Values are None when hardware is unavailable.
    """
    result = {'sheath_flow': None, 'temperature_C': None, 'pressure_hPa': None}
    try:
        from hardware import flowmeter as _fmhw
        dev = getattr(_fmhw, 'device', None)
        if dev is not None:
            dev.step()
            result['sheath_flow'] = float(dev.get_flow())
    except Exception:
        pass
    return result


def _format_dat_parameter_line(dma, sheath_flow, counting_time):
    """Format one DAT parameter line.

    Columns (tab-separated):
      sheath_flow  aerosol_flow  r_inner  r_outer  length  counting_time
      cpc_type  pressure_hPa  RH1  T1_C  RH2  T2_C  RH_inlet  T_inlet_C  0
    """
    sensors = _try_read_sensors()
    sf = sensors['sheath_flow'] if sensors['sheath_flow'] is not None else dma['sheath_flow']
    temp_C = (sensors['temperature_C'] if sensors['temperature_C'] is not None
              else dma['temperature'] - 273.15)
    pres_hPa = (sensors['pressure_hPa'] if sensors['pressure_hPa'] is not None
                else dma['pressure'] / 100.0)
    af = dma['aerosol_flow']
    parts = [
        f"{sf:.4f}", f"{af:.4f}",
        f"{dma['r_inner']:.6f}", f"{dma['r_outer']:.6f}", f"{dma['length']:.6f}",
        f"{counting_time:.1f}", dma['cpc_type'],
        f"{pres_hPa:.2f}",
        "0.0", f"{temp_C:.2f}",   # DMPS1 RH, T
        "0.0", f"{temp_C:.2f}",   # DMPS2 RH, T
        "0.0", f"{temp_C:.2f}",   # Aerosol inlet RH, T
        "0",
    ]
    return "\t".join(parts)


def _format_flw_line(dma, scan_start_dt):
    """Format one FLW line.

    Columns (tab-separated):
      delta_hours  sheath1  T1  P1  sheath2  T2  P2
      aerosol_flow2  aerosol_flow1  flow_speed  RH  T  0  0  0
    """
    sensors = _try_read_sensors()
    sf = sensors['sheath_flow'] if sensors['sheath_flow'] is not None else dma['sheath_flow']
    temp_C = (sensors['temperature_C'] if sensors['temperature_C'] is not None
              else dma['temperature'] - 273.15)
    pres_hPa = (sensors['pressure_hPa'] if sensors['pressure_hPa'] is not None
                else dma['pressure'] / 100.0)
    af = dma['aerosol_flow']
    # delta hours from midnight
    midnight = scan_start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    delta_h = (scan_start_dt - midnight).total_seconds() / 3600.0
    parts = [
        f"{delta_h:.6f}",
        f"{sf:.4f}", f"{temp_C:.2f}", f"{pres_hPa:.2f}",
        f"{sf:.4f}", f"{temp_C:.2f}", f"{pres_hPa:.2f}",
        f"{af:.4f}", f"{af:.4f}",
        "0.0000",  # flow speed in main sampling line
        "0.0", f"{temp_C:.2f}",  # nephelometer RH, T
        "0", "0", "0",
    ]
    return "\t".join(parts)


class MeasurementRunner:
    def __init__(self, hv_module, cpc_module, sample_interval: float = 0.2):
        self.hv = hv_module
        self.cpc = cpc_module
        self.sample_interval = float(sample_interval)

    def run_sequence(self, voltages: Iterable[float], settling: float, measure_time: float, save_path: Optional[str] = None,
                     sample_callback=None, progress_callback=None, step_callback=None,
                     repetitions: int = 1, stop_event=None):
        """Run a voltage-scan measurement sequence.

        Args:
            repetitions: Number of scan repetitions. Use -1 for indefinite.
            stop_event: A threading.Event that, when set, aborts the sequence.
            step_callback: Called after each voltage step with scan_results_so_far.
        """
        voltages = list(voltages)
        continuous = (repetitions < 0)
        total_reps = max(1, repetitions) if not continuous else 1

        # resolve hv setter function
        if hasattr(self.hv, 'set_hv_setpoint'):
            _set_hv = lambda v: self.hv.set_hv_setpoint(v)
        elif hasattr(self.hv, 'set_voltage'):
            _set_hv = lambda v: self.hv.set_voltage(v)
        elif hasattr(self.hv, 'device') and hasattr(self.hv.device, 'set_voltage'):
            _set_hv = lambda v: self.hv.device.set_voltage(v)
        else:
            raise AttributeError("HV module has no set_hv_setpoint or set_voltage method")

        # resolve hv status reader function
        if hasattr(self.hv, 'get_hv_status'):
            _get_status = self.hv.get_hv_status
        elif hasattr(self.hv, 'get_status'):
            _get_status = self.hv.get_status
        elif hasattr(self.hv, 'device') and hasattr(self.hv.device, 'get_status'):
            _get_status = self.hv.device.get_status
        else:
            _get_status = lambda: None

        # prepare raw file if needed
        raw_file = None
        raw_writer = None
        dat_file = None
        flw_file = None
        dma = None
        if save_path:
            save_path_expanded = os.path.expanduser(save_path)
            base, ext = os.path.splitext(save_path_expanded)
            # Add unique numeric suffix to avoid overwriting existing files
            candidate = base
            counter = 1
            while (os.path.exists(f"{candidate}_raw{ext or '.csv'}")
                   or os.path.exists(f"{candidate}.DAT")
                   or os.path.exists(f"{candidate}.FLW")):
                candidate = f"{base}_{counter:03d}"
                counter += 1
            base = candidate
            raw_path = f"{base}_raw{ext or '.csv'}"
            parent = os.path.dirname(save_path_expanded)
            if parent:
                os.makedirs(parent, exist_ok=True)
            raw_file = open(raw_path, 'w', newline='')
            raw_writer = csv.writer(raw_file)
            raw_writer.writerow(['voltage', 'timestamp_utc', 'concentration', 'hv_status'])
            # Open DAT and FLW files for incremental writing
            dat_path = base + '.DAT'
            flw_path = base + '.FLW'
            dat_file = open(dat_path, 'w')
            flw_file = open(flw_path, 'w')
            dma = _read_dma_params()

        all_results = []
        rep_index = 0
        seq_start = time.time()

        # Cached status reader — queries serial at most every 5 seconds
        _cached_status = [None]
        _cached_status_time = [0.0]
        _STATUS_POLL_INTERVAL = 5.0

        def _get_cached_status():
            now = time.time()
            if now - _cached_status_time[0] >= _STATUS_POLL_INTERVAL:
                try:
                    _cached_status[0] = _get_status()
                except Exception:
                    pass
                _cached_status_time[0] = now
            return _cached_status[0]

        try:
            while True:
                rep_index += 1
                if not continuous and rep_index > total_reps:
                    break
                if stop_event is not None and stop_event.is_set():
                    break

                scan_results = []
                scan_start_dt = datetime.now()
                # Clear the result plot at the start of each new scan
                if step_callback is not None:
                    try:
                        step_callback([])
                    except Exception:
                        pass

                results = []
                scan_start_delay = float(get_float('SCAN_START_DELAY', 10.0))
                total_duration = scan_start_delay + sum((settling + measure_time) for _ in voltages)

                for vi, v in enumerate(voltages):
                    if stop_event is not None and stop_event.is_set():
                        break
                    _set_hv(v)

                    # wait extra delay after setting the first voltage of each scan
                    if vi == 0 and scan_start_delay > 0:
                        t_delay_start = time.time()
                        while time.time() - t_delay_start < scan_start_delay:
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
                            time.sleep(self.sample_interval)

                    # settling phase
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
                                st = _get_cached_status()
                                sb = f"0x{st['value']:02X}" if isinstance(st, dict) and 'value' in st else ''
                                raw_writer.writerow([v, ts, f"{c}", sb])
                                raw_file.flush()
                            except Exception:
                                pass
                        if progress_callback:
                            try:
                                frac = min(1.0, (time.time() - seq_start) / total_duration) if total_duration > 0 else 1.0
                                progress_callback(frac)
                            except Exception:
                                pass
                        time.sleep(self.sample_interval)

                    # measurement phase
                    samples = []
                    t_start = time.time()
                    while time.time() - t_start < measure_time:
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
                                st = _get_cached_status()
                                sb = f"0x{st['value']:02X}" if isinstance(st, dict) and 'value' in st else ''
                                raw_writer.writerow([v, ts, f"{c}", sb])
                                raw_file.flush()
                            except Exception:
                                pass
                        if sample_callback:
                            try:
                                sample_callback(v, time.time() - seq_start, c)
                            except Exception:
                                pass
                        if progress_callback:
                            try:
                                frac = min(1.0, (time.time() - seq_start) / total_duration) if total_duration > 0 else 1.0
                                progress_callback(frac)
                            except Exception:
                                pass
                        time.sleep(self.sample_interval)

                    # compute average ignoring nan
                    valid = [x for x in samples if x == x]
                    avg = sum(valid) / len(valid) if valid else float('nan')
                    results.append((v, avg))
                    scan_results.append((v, avg))
                    if step_callback is not None:
                        try:
                            step_callback(list(scan_results))
                        except Exception:
                            pass

                all_results.append(results)

                # Write this scan to DAT and FLW files
                if dat_file and results:
                    try:
                        scan_end_dt = datetime.now()
                        fmt = '%m-%d-%Y %H:%M:%S'
                        # interval line
                        dat_file.write(f"'{scan_start_dt.strftime(fmt)}' '{scan_end_dt.strftime(fmt)}'\n")
                        # parameter line
                        counting_time = len(voltages) * (settling + measure_time)
                        dat_file.write(_format_dat_parameter_line(dma, None, counting_time) + '\n')
                        # data lines: voltage \t concentration
                        for voltage, conc in results:
                            dat_file.write(f"{voltage}\t{conc}\n")
                        dat_file.flush()
                    except Exception as e:
                        print(f"Error writing DAT: {e}")
                if flw_file and results:
                    try:
                        flw_file.write(_format_flw_line(dma, scan_start_dt) + '\n')
                        flw_file.flush()
                    except Exception as e:
                        print(f"Error writing FLW: {e}")

        finally:
            # close all files
            for f in (raw_file, dat_file, flw_file):
                if f:
                    try:
                        f.close()
                    except Exception:
                        pass

        # write summary CSV
        if save_path:
            summary_path = base + (ext or '.csv')
            summary_parent = os.path.dirname(summary_path)
            if summary_parent:
                os.makedirs(summary_parent, exist_ok=True)
            with open(summary_path, 'w', newline='') as f:
                w = csv.writer(f)
                w.writerow(['scan', 'voltage', 'concentration_avg'])
                for scan_idx, scan in enumerate(all_results):
                    for v, a in scan:
                        w.writerow([scan_idx + 1, v, a])

        # final progress update
        if progress_callback:
            try:
                progress_callback(1.0)
            except Exception:
                pass

        return all_results[-1] if all_results else []
