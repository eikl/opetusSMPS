"""Blower controller service.

Provides a small API for blower control and simulation.

Exports:
 - start_blower()
 - set_setpoint(value)
 - get_setpoint()
 - get_parameter()
 - auto_tune_pid() - Automatic PID tuning using relay feedback method
 - get_pid_params() - Get current PID parameters
 - set_pid_params(kp, ki, kd) - Set PID parameters manually
"""
from hardware.blowerdac import device as blowerdac_device
from hardware.dummy_devices import DummyBlowerDevice
from config import get_float, get_str
from simple_pid import PID
import threading
import time
import logging
import math
import configparser
import os

_PID_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pid_config.ini')


def _save_pid_to_file(kp: float, ki: float, kd: float):
    """Write PID parameters to pid_config.ini."""
    cfg = configparser.ConfigParser()
    cfg['pid'] = {'kp': str(kp), 'ki': str(ki), 'kd': str(kd)}
    try:
        with open(_PID_CONFIG_FILE, 'w') as f:
            cfg.write(f)
        logging.info(f"PID parameters saved to {_PID_CONFIG_FILE}: Kp={kp}, Ki={ki}, Kd={kd}")
    except Exception:
        logging.exception('Failed to save PID parameters')


def _load_pid_from_file():
    """Load PID parameters from pid_config.ini if it exists.

    Returns dict with 'Kp','Ki','Kd' or None.
    """
    if not os.path.isfile(_PID_CONFIG_FILE):
        return None
    try:
        cfg = configparser.ConfigParser()
        cfg.read(_PID_CONFIG_FILE)
        kp = float(cfg.get('pid', 'kp'))
        ki = float(cfg.get('pid', 'ki'))
        kd = float(cfg.get('pid', 'kd'))
        logging.info(f"PID parameters loaded from {_PID_CONFIG_FILE}: Kp={kp}, Ki={ki}, Kd={kd}")
        return {'Kp': kp, 'Ki': ki, 'Kd': kd}
    except Exception:
        logging.exception('Failed to load PID parameters')
        return None


UPDATE_INTERVAL = float(get_float('BLOWER_UPDATE_INTERVAL', 0.05))

# Check if we're using a dummy blower device
_is_dummy_blower = isinstance(blowerdac_device, DummyBlowerDevice)

# PID and setpoint
_blower_setpoint_lock = threading.Lock()
_blower_setpoint = float(get_float('BLOWER_DEFAULT_SETPOINT', 4.0))
_pid = PID(0.005, 0.03, 0, setpoint=_blower_setpoint)
_pid.output_limits = (0,5)

# Load saved PID params from config file if available
_saved_pid = _load_pid_from_file()
if _saved_pid:
    _pid.Kp = _saved_pid['Kp']
    _pid.Ki = _saved_pid['Ki']
    _pid.Kd = _saved_pid['Kd']
    logging.info(f"Restored PID from config: Kp={_pid.Kp}, Ki={_pid.Ki}, Kd={_pid.Kd}")
# whether to use the flowmeter reading as the PID process variable
_use_flow_pv = False
try:
    v = get_str('BLOWER_USE_FLOW_PV', '0')
    _use_flow_pv = str(v).strip().lower() in ('1', 'true', 'yes', 'on')
except Exception:
    _use_flow_pv = False

# attempt to locate a flowmeter device if configured; do not raise here — caller may inspect
_flow_device = None
try:
    if _use_flow_pv:
        try:
            from hardware import flowmeter as _fmhw
            _flow_device = getattr(_fmhw, 'device', None)
        except Exception:
            # if the hardware.flowmeter module raised (for example, requested serial but failed),
            # leave _flow_device as None and let control loop fallback; a UI component may surface the error.
            logging.exception('Flowmeter import failed while configuring blower flow PV')
            _flow_device = None
except Exception:
    _flow_device = None


def set_setpoint(value: float):
    global _blower_setpoint
    with _blower_setpoint_lock:
        _blower_setpoint = float(value)
        _pid.setpoint = _blower_setpoint


def get_setpoint() -> float:
    with _blower_setpoint_lock:
        return float(_blower_setpoint)


def get_parameter() -> float:
    return float(blowerdac_device.get_parameter())

def _control_loop():
    while True:
        try:
            # Skip control if auto-tuning is in progress
            if _is_tuning:
                time.sleep(UPDATE_INTERVAL)
                continue
                
            if _is_dummy_blower:
                # Disable PID for dummy blower - use setpoint directly
                with _blower_setpoint_lock:
                    voltage = _blower_setpoint
                blowerdac_device.set_voltage(voltage)
            else:
                # Real blower: use PID control
                # choose process variable: flowmeter reading if enabled and available, else blower parameter
                if _use_flow_pv and (_flow_device is not None):
                    try:
                        pv = float(_flow_device.get_flow())
                    except Exception:
                        logging.exception('Failed to read flowmeter for control PV; falling back to blower parameter')
                        pv = float(blowerdac_device.get_parameter())
                else:
                    pv = float(blowerdac_device.get_parameter())
                pid_out = _pid(pv)
                #print('dac voltage set to:', pid_out)
                blowerdac_device.set_voltage(pid_out)
        except Exception:
            logging.exception('Blower control loop error')
        time.sleep(UPDATE_INTERVAL)
def start_blower():
    global _blower_thread, _blower_sim_thread
    if globals().get('_blower_thread') is not None and _blower_thread.is_alive():
        return
    _blower_thread = threading.Thread(target=_control_loop, daemon=True)
    _blower_thread.start()
    # do not start a simulator thread (simulation removed)

# Tuning state
_tuning_lock = threading.Lock()
_is_tuning = False
_tuning_result = None  # Will hold dict with Kp, Ki, Kd after tuning


def get_pid_params() -> dict:
    """Get current PID parameters.
    
    Returns:
        dict with keys 'Kp', 'Ki', 'Kd'
    """
    return {
        'Kp': _pid.Kp,
        'Ki': _pid.Ki,
        'Kd': _pid.Kd
    }


def save_pid_params(kp: float = None, ki: float = None, kd: float = None):
    """Save PID parameters to pid_config.ini.

    If arguments are None, saves the current PID values.
    """
    kp = kp if kp is not None else _pid.Kp
    ki = ki if ki is not None else _pid.Ki
    kd = kd if kd is not None else _pid.Kd
    _save_pid_to_file(kp, ki, kd)


def load_pid_params() -> dict:
    """Load PID parameters from pid_config.ini if it exists."""
    return _load_pid_from_file()


def set_pid_params(kp: float, ki: float, kd: float):
    """Set PID parameters manually and save to config file.
    
    Args:
        kp: Proportional gain
        ki: Integral gain
        kd: Derivative gain
    """
    _pid.Kp = kp
    _pid.Ki = ki
    _pid.Kd = kd
    _pid.reset()
    logging.info(f"PID parameters set to Kp={kp}, Ki={ki}, Kd={kd}")
    save_pid_params(kp, ki, kd)


def is_tuning() -> bool:
    """Check if auto-tuning is in progress."""
    with _tuning_lock:
        return _is_tuning


def get_tuning_result() -> dict:
    """Get the result of the last auto-tuning.
    
    Returns:
        dict with 'Kp', 'Ki', 'Kd', 'Ku', 'Tu' or None if no tuning done
    """
    with _tuning_lock:
        return _tuning_result


class StepResponseTuner:
    """Step response auto-tuner using first-order plus dead-time (FOPDT) model.
    
    This method applies a step change in output and measures the system response
    to identify the process gain (K), time constant (tau), and dead time (theta).
    Then uses Cohen-Coon or Lambda tuning rules for PID parameters.
    
    This works much better for flow control systems than relay-based methods.
    """
    
    def __init__(self, initial_output: float, step_output: float, 
                 settle_time: float = 30.0, max_time: float = 120.0):
        """Initialize the step response tuner.
        
        Args:
            initial_output: Starting output value (voltage)
            step_output: Output value after step (voltage)
            settle_time: Time to wait for initial settling (seconds)
            max_time: Maximum time for the test (seconds)
        """
        self.initial_output = initial_output
        self.step_output = step_output
        self.settle_time = settle_time
        self.max_time = max_time
        
        # State
        self.phase = 'settling'  # 'settling', 'step', 'measuring', 'done'
        self.start_time = None
        self.step_time = None
        self.initial_pv = None
        self.measurements = []  # (time, pv) tuples after step
        self.finished = False
        self.result = None
        self.output = initial_output
    
    def update(self, pv: float, current_time: float) -> float:
        """Update the tuner with current process variable.
        
        Args:
            pv: Current process variable (flow reading)
            current_time: Current time in seconds
            
        Returns:
            Output value to send to the actuator
        """
        if self.start_time is None:
            self.start_time = current_time
            self.output = self.initial_output
        
        elapsed = current_time - self.start_time
        
        if self.phase == 'settling':
            # Wait for system to settle at initial output
            if elapsed >= self.settle_time:
                self.initial_pv = pv
                self.step_time = current_time
                self.phase = 'step'
                self.output = self.step_output
                logging.info(f"StepTune: Step applied at t={elapsed:.1f}s, initial_pv={pv:.2f}")
            return self.output
        
        elif self.phase == 'step':
            # Record measurements after the step
            t_since_step = current_time - self.step_time
            self.measurements.append((t_since_step, pv))
            
            # Check if we've reached max time or system has settled
            if elapsed >= self.max_time or t_since_step >= (self.max_time - self.settle_time):
                self._calculate_pid()
            
            return self.output
        
        return self.output
    
    def _calculate_pid(self):
        """Calculate PID parameters from step response data."""
        if len(self.measurements) < 10:
            self._finish("Not enough measurements")
            return
        
        # Find the final steady-state value (average of last 20% of readings)
        n_final = max(5, len(self.measurements) // 5)
        final_values = [m[1] for m in self.measurements[-n_final:]]
        final_pv = sum(final_values) / len(final_values)
        
        # Calculate process gain K = delta_pv / delta_output
        delta_pv = final_pv - self.initial_pv
        delta_output = self.step_output - self.initial_output
        
        if abs(delta_output) < 0.001:
            self._finish("Step size too small")
            return
        
        K = delta_pv / delta_output  # Process gain (flow per volt)
        
        if abs(K) < 0.001:
            self._finish("Process gain too small - system not responding")
            return
        
        logging.info(f"StepTune: delta_pv={delta_pv:.2f}, delta_output={delta_output:.2f}, K={K:.4f}")
        
        # Find time constant (tau) using 63.2% method
        # tau is the time to reach 63.2% of the final change
        target_pv = self.initial_pv + 0.632 * delta_pv
        
        tau = None
        theta = 0  # Dead time (time before response starts)
        
        # Find when response starts (dead time) - when PV moves more than 5% of change
        threshold_pv = self.initial_pv + 0.05 * delta_pv if delta_pv > 0 else self.initial_pv + 0.05 * delta_pv
        for t, pv in self.measurements:
            if (delta_pv > 0 and pv > threshold_pv) or (delta_pv < 0 and pv < threshold_pv):
                theta = t
                break
        
        # Find time constant
        for t, pv in self.measurements:
            if (delta_pv > 0 and pv >= target_pv) or (delta_pv < 0 and pv <= target_pv):
                tau = t - theta
                break
        
        if tau is None or tau <= 0:
            # Estimate tau from the data if 63.2% point not reached
            tau = (self.measurements[-1][0] - theta) / 3
            logging.warning(f"StepTune: Could not find 63.2% point, estimating tau={tau:.2f}s")
        
        logging.info(f"StepTune: theta={theta:.2f}s, tau={tau:.2f}s")
        
        # Lambda tuning (conservative for flow control)
        # lambda_cl >= 3*tau gives smooth, non-oscillatory response
        lambda_cl = max(3.0 * tau, 1.0)
        
        # PI controller: Kp = tau / (K * (lambda + theta))
        #                Ki = Kp / (tau + theta) — integral tied to total lag
        Kp = tau / (K * (lambda_cl + theta)) if K != 0 else 0
        # Use integral time = tau (not tau alone for Ki = Kp/tau which is
        # too aggressive when tau is small)
        Ti = tau + theta if (tau + theta) > 0 else 1.0
        Ki = Kp / Ti
        Kd = 0  # No derivative for flow control
        
        # Sanity check - make sure gains are reasonable
        if abs(Kp) > 10:
            logging.warning(f"StepTune: Kp={Kp:.4f} seems too high, capping")
            Kp = 10 * (1 if Kp > 0 else -1)
            Ki = Kp / Ti
        
        logging.info(f"StepTune result: Kp={Kp:.6f}, Ki={Ki:.6f}, Kd={Kd:.6f}")
        
        self.result = {
            'Kp': abs(Kp),  # PID library expects positive gains
            'Ki': abs(Ki),
            'Kd': Kd,
            'K': K,
            'tau': tau,
            'theta': theta,
            'delta_pv': delta_pv,
            'delta_output': delta_output
        }
        
        self._finish(f"Success: K={K:.4f}, tau={tau:.2f}s, theta={theta:.2f}s -> Kp={abs(Kp):.6f}, Ki={abs(Ki):.6f}")
    
    def _finish(self, message: str):
        """Mark tuning as finished."""
        self.finished = True
        self.phase = 'done'
        logging.info(f"StepTune finished: {message}")


def _activate_flow_pv(flow_dev):
    """Switch the PID control loop to use a flow device as process variable.

    Called automatically after a successful auto-tune so that the tuned gains
    (which are based on flow dynamics) are actually used with flow feedback.
    """
    global _use_flow_pv, _flow_device
    _flow_device = flow_dev
    _use_flow_pv = True
    logging.info('PID control loop switched to flow feedback after auto-tune')


def auto_tune_pid(setpoint: float = None, apply_result: bool = True,
                  output_low: float = None, output_high: float = None,
                  settle_time: float = 20.0, max_time: float = 90.0, 
                  callback=None) -> dict:
    """Run automatic PID tuning using step response method.
    
    This method applies a step change in output voltage and measures the
    flow response to identify system dynamics, then calculates appropriate
    PID parameters using Lambda tuning rules.
    
    Args:
        setpoint: Not used (kept for compatibility), tuning uses step response
        apply_result: Whether to apply the tuned parameters automatically
        output_low: Lower output voltage for step test
        output_high: Higher output voltage for step test  
        settle_time: Time to wait at initial output before step (seconds)
        max_time: Maximum total tuning time (seconds)
        callback: Optional callback(progress, message) for status updates
    
    Returns:
        dict with 'Kp', 'Ki', 'Kd', 'K', 'tau', 'theta' and 'success' keys
    
    Raises:
        RuntimeError: If tuning is already in progress or flowmeter not available
    """
    global _is_tuning, _tuning_result
    
    with _tuning_lock:
        if _is_tuning:
            raise RuntimeError("Auto-tuning already in progress")
        _is_tuning = True
    
    try:
        # Check prerequisites
        if _is_dummy_blower:
            raise RuntimeError("Cannot auto-tune with dummy blower device")
        
        # Find a flow feedback device for tuning — try module-level flowmeter
        # first, then attempt to import one dynamically if BLOWER_USE_FLOW_PV
        # was not enabled in .env.
        tune_flow_device = _flow_device
        if tune_flow_device is None:
            # Try to grab the flowmeter device directly
            try:
                from hardware import flowmeter as _fmhw_tune
                tune_flow_device = getattr(_fmhw_tune, 'device', None)
            except Exception:
                pass
        if tune_flow_device is None:
            # Try differential pressure meter as last resort
            try:
                from hardware import diff_pressure_meter as _dpm_tune
                _dp_dev = getattr(_dpm_tune, 'device', None)
                if _dp_dev is not None:
                    # Wrap in a compatible interface (step/get_flow)
                    class _DPFlowAdapter:
                        def __init__(self, dev):
                            self._dev = dev
                        def step(self):
                            self._dev.step()
                        def get_flow(self):
                            return self._dev.get_aerosol_flow()
                    tune_flow_device = _DPFlowAdapter(_dp_dev)
            except Exception:
                pass
        if tune_flow_device is None:
            raise RuntimeError(
                "No flow feedback device available for auto-tuning. "
                "Connect a flowmeter (USE_I2C_FLOWMETER) or differential "
                "pressure meter (USE_I2C_PRESSURE) in .env"
            )
        
        # Determine step voltages around current operating point if not given
        if output_low is None or output_high is None:
            current_output = float(blowerdac_device.get_parameter())
            # Default: step ±0.5 V around the current DAC voltage,
            # clamped to the PID output limits (0–5 V).
            lo_limit, hi_limit = (_pid.output_limits[0] or 0.0,
                                  _pid.output_limits[1] or 5.0)
            if output_low is None:
                output_low = max(lo_limit, current_output - 0.5)
            if output_high is None:
                output_high = min(hi_limit, current_output + 0.5)
            # guarantee a meaningful step size
            if abs(output_high - output_low) < 0.2:
                output_high = min(hi_limit, output_low + 0.5)
        
        if callback:
            callback(0, f"Starting step response test: {output_low:.2f}V -> {output_high:.2f}V")
        
        logging.info(f"Starting PID step response tuning: {output_low}V -> {output_high}V")
        
        # Create step response tuner
        tuner = StepResponseTuner(
            initial_output=output_low,
            step_output=output_high,
            settle_time=settle_time,
            max_time=max_time
        )
        
        # Run tuning loop
        start_time = time.time()
        while not tuner.finished:
            current_time = time.time()
            elapsed = current_time - start_time
            
            # Read process variable
            try:
                tune_flow_device.step()
                pv = float(tune_flow_device.get_flow())
            except Exception as e:
                logging.exception("Failed to read flow device during auto-tune")
                raise RuntimeError(f"Flowmeter read failed: {e}")
            
            # Update tuner and get output
            output = tuner.update(pv, current_time)
            
            # Apply output to blower
            blowerdac_device.set_voltage(output)
            
            # Progress callback
            if callback:
                progress = min(elapsed / max_time, 0.99)
                phase = tuner.phase
                callback(progress, f"Phase: {phase}, PV={pv:.2f}, Output={output:.2f}V")
            
            time.sleep(UPDATE_INTERVAL)
        
        # Process result
        result = tuner.result or {}
        result['success'] = tuner.result is not None
        
        if result['success'] and apply_result:
            set_pid_params(result['Kp'], result['Ki'], result['Kd'])
            # Enable flow feedback so the PID loop uses the flowmeter
            # (tuned gains are based on flow dynamics, not raw voltage)
            _activate_flow_pv(tune_flow_device)
            if callback:
                callback(1.0, f"Applied: Kp={result['Kp']:.4f}, Ki={result['Ki']:.4f}, Kd={result['Kd']:.4f}")
        elif callback:
            if result['success']:
                callback(1.0, f"Tuning complete (not applied): Kp={result['Kp']:.4f}")
            else:
                callback(1.0, "Tuning failed - check logs")
        
        with _tuning_lock:
            _tuning_result = result
        
        return result
        
    finally:
        with _tuning_lock:
            _is_tuning = False
        
        # Return control to normal PID
        logging.info("Auto-tune finished, returning to normal PID control")
