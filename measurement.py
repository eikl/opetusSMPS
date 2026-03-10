"""Measurement sequence runner.

Usage:
    runner = MeasurementRunner(hv_module, cpc_module)
    runner.run_sequence(voltages=[100,200,300], settling=2.0, measure_time=5.0,
                        save_path='out.csv', repetitions=1)

When repetitions > 1 (or -1 for continuous), the voltage list is scanned
repeatedly.  Setting repetitions=-1 loops until ``stop_event`` is set.

Output files (when save_path is given):
  - ``<base>_raw.csv``  – every individual CPC sample
  - ``<base>.DAT``      – inversion-compatible format (see base.py)
"""
from __future__ import annotations

import os
import time
import csv
import threading
from datetime import datetime
from typing import Iterable, List, Optional

from config import get_float, get_str


def _read_dma_params() -> dict:
    """Read DMA / system parameters from .env for .DAT header lines."""
    return {
        'length': float(get_float('DMA_LENGTH', 0.11)),
        'inner_radius': float(get_float('DMA_INNER_RADIUS', 0.025)),
        'outer_radius': float(get_float('DMA_OUTER_RADIUS', 0.033)),
        'sheath_flow': float(get_float('DMA_SHEATH_FLOW', 3.0)),
        'aerosol_flow': float(get_float('DMA_AEROSOL_FLOW', 0.3)),
        'cpc_type': str(get_str('CPC_TYPE', 'TSI3010') or 'TSI3010'),
        'default_temperature_K': float(get_float('DEFAULT_TEMPERATURE', 293.15)),
        'default_pressure_Pa': float(get_float('DEFAULT_PRESSURE', 101325)),
    }


def _try_read_sensors() -> dict:
    """Best-effort read of live temperature (°C) and pressure (hPa).

    Returns dict with keys 'temperature_C', 'pressure_hPa', 'rh',
    'sheath_flow_lpm'.  Values are None when hardware is unavailable.
    """
    result = {'temperature_C': None, 'pressure_hPa': None,
              'rh': None, 'sheath_flow_lpm': None}
    # differential pressure sensor gives temperature
    try:
        from hardware import diff_pressure_meter as _dpm
        dev = getattr(_dpm, 'device', None)
        if dev is not None:
            dev.step()
            result['temperature_C'] = float(dev.get_temperature())
    except Exception:
        pass
    # flowmeter for sheath flow
    try:
        from hardware import flowmeter as _fm
        dev = getattr(_fm, 'device', None)
        if dev is not None:
            dev.step()
            result['sheath_flow_lpm'] = float(dev.get_flow())
    except Exception:
        pass
    return result


def _format_dat_parameter_line(dma: dict, sensor: dict,
                                counting_time: float) -> str:
    """Build the tab-separated parameter line expected by base.py.

    Column order (from base.py ``parameters_to_dataframe``):
      Sheath flow, Aerosol flow, DMA inner radius, DMA outer radius,
      DMA length, total counting time, CPC type, pressure (hPa),
      DMPS1 RH, DMPS1 T (°C), DMPS2 RH, DMPS2 T (°C),
      Aerosol inlet RH, Aerosol inlet T (°C), 0
    """
    temp_C = sensor.get('temperature_C')
    if temp_C is None:
        temp_C = dma['default_temperature_K'] - 273.15

    pressure_hPa = sensor.get('pressure_hPa')
    if pressure_hPa is None:
        pressure_hPa = dma['default_pressure_Pa'] / 100.0

    rh = sensor.get('rh') if sensor.get('rh') is not None else 0.0

    sheath = sensor.get('sheath_flow_lpm')
    if sheath is None:
        sheath = dma['sheath_flow']

    cols = [
        f"{sheath:.4f}",                   # Sheath flow  [l/min]
        f"{dma['aerosol_flow']:.4f}",      # Aerosol flow [l/min]
        f"{dma['inner_radius']:.6f}",      # inner radius [m]
        f"{dma['outer_radius']:.6f}",      # outer radius [m]
        f"{dma['length']:.6f}",            # DMA length   [m]
        f"{counting_time:.1f}",            # total counting time [s]
        dma['cpc_type'],                   # CPC type string
        f"{pressure_hPa:.2f}",             # pressure     [hPa]
        f"{rh:.1f}",                       # DMPS1 RH
        f"{temp_C:.2f}",                   # DMPS1 T      [°C]
        f"{rh:.1f}",                       # DMPS2 RH  (duplicate for single DMA)
        f"{temp_C:.2f}",                   # DMPS2 T   (duplicate for single DMA)
        f"{rh:.1f}",                       # Aerosol inlet RH
        f"{temp_C:.2f}",                   # Aerosol inlet T [°C]
        "0",                               # Null column
    ]
    return "\t".join(cols)


def _format_flw_line(delta_hours: float, dma: dict, sensor: dict) -> str:
    """Build one whitespace-separated row for the .FLW file.

    Column order (from base.py ``read_dmps_flw_file``):
      Delta hours,
      DMPS1 sheath flow rate, DMPS1 sheath flow temperature, DMPS1 sheath flow pressure,
      DMPS2 sheath flow rate, DMPS2 sheath flow temperature, DMPS2 sheath flow pressure,
      DMPS2 aerosol flow, DMPS1 aerosol flow,
      Flow speed in main sampling line,
      Nephelometer inlet RH, Nephelometer inlet T,
      random1, random2, random3
    """
    sheath = sensor.get('sheath_flow_lpm')
    if sheath is None:
        sheath = dma['sheath_flow']

    temp_C = sensor.get('temperature_C')
    if temp_C is None:
        temp_C = dma['default_temperature_K'] - 273.15

    pressure_hPa = sensor.get('pressure_hPa')
    if pressure_hPa is None:
        pressure_hPa = dma['default_pressure_Pa'] / 100.0

    rh = sensor.get('rh') if sensor.get('rh') is not None else 0.0
    aerosol_flow = dma['aerosol_flow']

    cols = [
        f"{delta_hours:.6f}",
        f"{sheath:.4f}",        # DMPS1 sheath flow
        f"{temp_C:.2f}",        # DMPS1 sheath T
        f"{pressure_hPa:.2f}",  # DMPS1 sheath P
        f"{sheath:.4f}",        # DMPS2 sheath flow  (same – single DMA)
        f"{temp_C:.2f}",        # DMPS2 sheath T
        f"{pressure_hPa:.2f}",  # DMPS2 sheath P
        f"{aerosol_flow:.4f}",  # DMPS2 aerosol flow
        f"{aerosol_flow:.4f}",  # DMPS1 aerosol flow
        "0.0000",               # main sampling line speed (unused)
        f"{rh:.1f}",            # Neph RH
        f"{temp_C:.2f}",        # Neph T
        "0", "0", "0",          # random1-3
    ]
    return "\t".join(cols)


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
        dat_scans: List[dict] = []  # collect per-scan data for .DAT output
        dma_params = _read_dma_params() if save_path else None
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
            scan_start = datetime.now()
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

            # record scan for .DAT / .FLW output
            if save_path and dma_params:
                scan_end = datetime.now()
                sensor = _try_read_sensors()
                counting_time = len(voltages) * (settling + measure_time)
                dat_scans.append({
                    'start': scan_start,
                    'end': scan_end,
                    'sensor': sensor,
                    'counting_time': counting_time,
                    'data': list(scan_results),
                })

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

        # ---- write .DAT file (inversion-compatible) ----
        if save_path and dat_scans and dma_params:
            try:
                save_path_expanded = os.path.expanduser(save_path)
                base_name, _ = os.path.splitext(save_path_expanded)
                dat_path = base_name + '.DAT'
                with open(dat_path, 'w') as df:
                    for scan in dat_scans:
                        # interval line: 'MM-DD-YYYY HH:MM:SS' 'MM-DD-YYYY HH:MM:SS'
                        t0 = scan['start'].strftime("'%m-%d-%Y %H:%M:%S'")
                        t1 = scan['end'].strftime("'%m-%d-%Y %H:%M:%S'")
                        df.write(f"{t0} {t1}\n")
                        # parameter line (15 tab-separated columns)
                        df.write(_format_dat_parameter_line(
                            dma_params, scan['sensor'],
                            scan['counting_time']) + "\n")
                        # scan data: voltage <tab> concentration
                        for voltage, concentration in scan['data']:
                            df.write(f"{voltage}\t{concentration}\n")
            except Exception:
                pass

        # ---- write .FLW file (flow / environment log) ----
        if save_path and dat_scans and dma_params:
            try:
                save_path_expanded = os.path.expanduser(save_path)
                base_name, _ = os.path.splitext(save_path_expanded)
                flw_path = base_name + '.FLW'
                # midnight of the first scan's day
                midnight = dat_scans[0]['start'].replace(
                    hour=0, minute=0, second=0, microsecond=0)
                with open(flw_path, 'w') as ff:
                    for scan in dat_scans:
                        delta = (scan['start'] - midnight).total_seconds() / 3600.0
                        ff.write(_format_flw_line(
                            delta, dma_params, scan['sensor']) + "\n")
            except Exception:
                pass

        # final progress
        if progress_callback:
            try:
                progress_callback(1.0)
            except Exception:
                pass

        return all_results
