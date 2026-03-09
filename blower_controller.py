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

UPDATE_INTERVAL = float(get_float('BLOWER_UPDATE_INTERVAL', 0.05))

# Check if we're using a dummy blower device
_is_dummy_blower = isinstance(blowerdac_device, DummyBlowerDevice)

# PID and setpoint
_blower_setpoint_lock = threading.Lock()
_blower_setpoint = float(get_float('BLOWER_DEFAULT_SETPOINT', 4.0))
_pid = PID(0.005, 0.03, 0, setpoint=_blower_setpoint)
_pid.output_limits = (0,5)
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


def set_pid_params(kp: float, ki: float, kd: float):
    """Set PID parameters manually.
    
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
        
        # Lambda tuning (very robust, good for flow control)
        # Choose lambda (closed-loop time constant) = tau for moderate response
        lambda_cl = tau
        
        # PI controller: Kp = tau / (K * (lambda + theta))
        #                Ki = Kp / tau
        Kp = tau / (K * (lambda_cl + theta)) if K != 0 else 0
        Ki = Kp / tau if tau > 0 else 0
        Kd = 0  # No derivative for flow control
        
        # Sanity check - make sure gains are reasonable
        if abs(Kp) > 10:
            logging.warning(f"StepTune: Kp={Kp:.4f} seems too high, capping")
            Kp = 10 * (1 if Kp > 0 else -1)
            Ki = Kp / tau if tau > 0 else 0
        
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


def auto_tune_pid(setpoint: float = None, apply_result: bool = True,
                  output_low: float = 2.5, output_high: float = 3.5,
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
        
        if not _use_flow_pv or _flow_device is None:
            raise RuntimeError("Flowmeter not available for auto-tuning. "
                             "Enable BLOWER_USE_FLOW_PV in .env")
        
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
                _flow_device.step()
                pv = float(_flow_device.get_flow())
            except Exception as e:
                logging.exception("Failed to read flowmeter during auto-tune")
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
