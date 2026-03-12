import tkinter as tk
from tkinter import ttk
import threading
import time
from ui_helpers import add_idle_point, add_measurement_point, set_ui_locked, trim_plot
from tkinter import filedialog, messagebox
import configparser
from config import get_float
from config import get_str
try:
    import matplotlib
    matplotlib.use('TkAgg')
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    import matplotlib.pyplot as plt
    _HAS_MATPLOTLIB = True
except Exception:
    _HAS_MATPLOTLIB = False


# Lazy import to avoid circular imports at module import time; main will import UI after starting simulators


class ControlUI:
    def __init__(self, root, hv_module, blower_module):
        self.root = root
        self.hv = hv_module
        self.blower = blower_module

        root.title("opetusSMPS")

        mainframe = ttk.Frame(root, padding="10")
        mainframe.grid(column=0, row=0, sticky=(tk.N, tk.W, tk.E, tk.S))

        # HV controls
        ttk.Label(mainframe, text="High Voltage (V)").grid(column=0, row=0, sticky=tk.W)
        # precise variable stores the true value; display var shows rounded text
        self.hv_set_var = tk.DoubleVar(value=self.hv.get_hv_setpoint())
        self.hv_set_display_var = tk.StringVar()
        # initialize display with rounded value
        self._hv_display_decimals = 1
        self.hv_set_display_var.set(f"{self.hv_set_var.get():.{self._hv_display_decimals}f}")
        self.hv_set_scale = ttk.Scale(
            mainframe,
            orient=tk.HORIZONTAL,
            length=300,
            from_=0.0,
            to=5000.0,
            variable=self.hv_set_var,
            command=self._hv_scale_moved,
        )
        self.hv_set_scale.grid(column=0, row=1, sticky=(tk.W, tk.E))
        # entry shows rounded display var; Set button applies precise value
        self.hv_set_entry = ttk.Entry(mainframe, textvariable=self.hv_set_display_var, width=10)
        self.hv_set_entry.grid(column=1, row=1, sticky=tk.W)
        self._hv_set_button = ttk.Button(mainframe, text="Set HV", command=self._hv_set_from_entry)
        self._hv_set_button.grid(column=2, row=1)

        self.hv_current_var = tk.StringVar(value="Setpoint: -- V")
        ttk.Label(mainframe, textvariable=self.hv_current_var).grid(column=0, row=2, sticky=tk.W)

        # HV measured voltage display (read from supply)
        self.hv_measured_var = tk.StringVar(value="Measured: -- V")
        ttk.Label(mainframe, textvariable=self.hv_measured_var).grid(column=1, row=2, sticky=tk.W)

        # HV status register display
        self.hv_status_var = tk.StringVar(value="HV Status: --")
        self._hv_status_label = ttk.Label(mainframe, textvariable=self.hv_status_var)
        self._hv_status_label.grid(column=2, row=2, columnspan=2, sticky=tk.W)
    
        # Blower controls
        ttk.Label(mainframe, text="Blower setpoint (units)").grid(column=0, row=3, sticky=tk.W)
        self.blower_set_var = tk.DoubleVar(value=self.blower.get_setpoint())
        self.blower_set_display_var = tk.StringVar()
        self._blower_display_decimals = 2
        self.blower_set_display_var.set(f"{self.blower_set_var.get():.{self._blower_display_decimals}f}")
        self.blower_set_scale = ttk.Scale(
            mainframe,
            orient=tk.HORIZONTAL,
            length=300,
            from_=0.0,
            to=200.0,
            variable=self.blower_set_var,
            command=self._blower_scale_moved,
        )
        self.blower_set_scale.grid(column=0, row=4, sticky=(tk.W, tk.E))
        self.blower_set_entry = ttk.Entry(mainframe, textvariable=self.blower_set_display_var, width=10)
        self.blower_set_entry.grid(column=1, row=4, sticky=tk.W)
        self._blower_set_button = ttk.Button(mainframe, text="Set blower", command=self._blower_set_from_entry)
        self._blower_set_button.grid(column=2, row=4)

        self.blower_current_var = tk.StringVar(value=f"Current: {self.blower.get_parameter():.2f}")
        ttk.Label(mainframe, textvariable=self.blower_current_var).grid(column=0, row=5, sticky=tk.W)
        print(self.blower.get_parameter())
        
        # PID auto-tune button (debug mode only, added later after debug_mode check)
        self._autotune_btn = None
        
        # CPC concentration display
        try:
            from cpc_controller import get_concentration
            conc = get_concentration()
        except Exception:
            conc = 0.0
        self.cpc_current_var = tk.StringVar(value=f"CPC: {conc:.2f}")
        ttk.Label(mainframe, textvariable=self.cpc_current_var).grid(column=0, row=6, sticky=tk.W)
        # Flowmeter current display (will be populated if hardware.flowmeter.device is present)
        try:
            from hardware import flowmeter as _fmhw
            has_flow = getattr(_fmhw, 'device', None) is not None
        except Exception as _fm_err:
            has_flow = False
            # if the user requested a real flowmeter in config but the import failed, show a dialog
            try:
                use_serial = str(get_str('USE_SERIAL_MASSFLOW', '0')).strip().lower() in ('1', 'true', 'yes', 'on')
            except Exception:
                use_serial = False
            if use_serial:
                # schedule a dialog to inform the user that flowmeter connection failed
                def _show_flow_error():
                    try:
                        messagebox.showerror('Flowmeter connection', f"Flowmeter requested in config but failed to initialize: {_fm_err}")
                    except Exception:
                        pass
                # root is available in this init; schedule after 0 so the UI can finish constructing
                root.after(0, _show_flow_error)
        if has_flow:
            try:
                self.flow_current_var = tk.StringVar(value="Flow: --")
                ttk.Label(mainframe, textvariable=self.flow_current_var).grid(column=1, row=6, sticky=tk.W)
            except Exception:
                self.flow_current_var = tk.StringVar(value="Flow: --")
        else:
            self.flow_current_var = tk.StringVar(value="Flow: n/a")
            ttk.Label(mainframe, textvariable=self.flow_current_var).grid(column=1, row=6, sticky=tk.W)
        
        # Aerosol flow display (from differential pressure meter SDP811)
        try:
            from hardware import diff_pressure_meter as _dpmhw
            has_aerosol_flow = getattr(_dpmhw, 'device', None) is not None
        except Exception as _dp_err:
            has_aerosol_flow = False
        
        # Check if debug mode is enabled
        _debug_mode = False
        try:
            _debug_str = get_str('DEBUG_MODE', '0')
            _debug_mode = str(_debug_str).lower() in ('1', 'true', 'yes', 'on')
        except Exception:
            pass
        
        if has_aerosol_flow:
            self.aerosol_flow_var = tk.StringVar(value="Aerosol Flow: --")
            ttk.Label(mainframe, textvariable=self.aerosol_flow_var).grid(column=2, row=6, sticky=tk.W)
            # Calibration button - only visible in debug mode
            if _debug_mode:
                self._calibrate_aerosol_btn = ttk.Button(
                    mainframe, text="Calibrate @ 1 LPM", command=self._calibrate_aerosol_flow
                )
                self._calibrate_aerosol_btn.grid(column=3, row=6, sticky=tk.W)
        else:
            self.aerosol_flow_var = tk.StringVar(value="Aerosol Flow: n/a")
            ttk.Label(mainframe, textvariable=self.aerosol_flow_var).grid(column=2, row=6, sticky=tk.W)
        
        # PID auto-tune button - only visible in debug mode
        if _debug_mode:
            self._autotune_btn = ttk.Button(
                mainframe, text="Auto-Tune PID", command=self._start_pid_autotune
            )
            self._autotune_btn.grid(column=3, row=5, sticky=tk.W)
            # PID params display
            self._pid_params_var = tk.StringVar(value="PID: --")
            ttk.Label(mainframe, textvariable=self._pid_params_var).grid(column=1, row=5, sticky=tk.W)
            self._update_pid_params_display()
        
        # determine idle sampling interval from cpc module if available
        try:
            import cpc_controller as _cpc_mod
            self._idle_sample_interval = float(getattr(_cpc_mod, 'UPDATE_INTERVAL', 0.2))
        except Exception:
            self._idle_sample_interval = 0.2
        # maximum time window to plot (seconds); read from config if available
        try:
            from config import get_float
            self._plot_max_seconds = float(get_float('PLOT_MAX_SECONDS', 300.0))
        except Exception:
            self._plot_max_seconds = 300.0


        # Measurement controls
        ttk.Label(mainframe, text="Measurements").grid(column=0, row=7, sticky=tk.W)
        # Voltage selection: min, max and number of steps
        ttk.Label(mainframe, text="Voltage minimum (V)").grid(column=0, row=8, sticky=tk.W)
        # default min/max/steps: if CPC is simulated, center defaults around the configured peak
        default_min = 100.0
        default_max = 300.0
        default_steps = 3
        # no simulator available: keep default min/max/steps
        self.voltage_min_var = tk.DoubleVar(value=default_min)
        ttk.Entry(mainframe, textvariable=self.voltage_min_var, width=10).grid(column=1, row=8, sticky=tk.W)

        ttk.Label(mainframe, text="Voltage maximum (V)").grid(column=0, row=9, sticky=tk.W)
        self.voltage_max_var = tk.DoubleVar(value=default_max)
        ttk.Entry(mainframe, textvariable=self.voltage_max_var, width=10).grid(column=1, row=9, sticky=tk.W)

        ttk.Label(mainframe, text="Steps (int)").grid(column=0, row=10, sticky=tk.W)
        self.voltage_steps_var = tk.IntVar(value=default_steps)
        ttk.Entry(mainframe, textvariable=self.voltage_steps_var, width=10).grid(column=1, row=10, sticky=tk.W)

        ttk.Label(mainframe, text="Settling time (s)").grid(column=0, row=11, sticky=tk.W)
        self.settling_var = tk.DoubleVar(value=2.0)
        ttk.Entry(mainframe, textvariable=self.settling_var, width=10).grid(column=1, row=11, sticky=tk.W)

        ttk.Label(mainframe, text="Measurement time (s)").grid(column=0, row=12, sticky=tk.W)
        self.measure_time_var = tk.DoubleVar(value=5.0)
        ttk.Entry(mainframe, textvariable=self.measure_time_var, width=10).grid(column=1, row=12, sticky=tk.W)

        self.save_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(mainframe, text="Save to CSV", variable=self.save_var).grid(column=0, row=13, sticky=tk.W)
        self.save_path_var = tk.StringVar(value="measurements.csv")
        ttk.Entry(mainframe, textvariable=self.save_path_var, width=30).grid(column=1, row=13, columnspan=2, sticky=(tk.W, tk.E))

        self.measure_status_var = tk.StringVar(value="Idle")
        ttk.Label(mainframe, textvariable=self.measure_status_var).grid(column=0, row=14, sticky=tk.W)
        # Load config button
        self._loaded_config_var = tk.StringVar(value="")
        self._load_button = ttk.Button(mainframe, text="Load .ini", command=self._load_ini_config)
        self._load_button.grid(column=1, row=14, sticky=tk.W)
        ttk.Label(mainframe, textvariable=self._loaded_config_var).grid(column=0, row=15, columnspan=2, sticky=(tk.W, tk.E))

        self._start_button = ttk.Button(mainframe, text="Start Measurement", command=self._start_measurement)
        self._start_button.grid(column=2, row=14)

        # Timeseries plot area for CPC
        if _HAS_MATPLOTLIB:
            # live timeseries plot
            self.fig, self.ax = plt.subplots(figsize=(5, 2))
            self.ax.set_title('CPC concentration')
            self.ax.set_xlabel('time (s)')
            self.ax.set_ylabel('concentration')
            self._plot_line, = self.ax.plot([], [], '-o')
            self.canvas = FigureCanvasTkAgg(self.fig, master=mainframe)
            self.canvas.get_tk_widget().grid(column=0, row=16, columnspan=3)
            self._plot_x = []
            self._plot_y = []
            # base time for idle plotting (seconds)
            self._plot_base_time = time.time()

            # (flow plotting removed per user request)

            # result plot: voltage vs average concentration
            self.fig2, self.ax2 = plt.subplots(figsize=(5, 2))
            self.ax2.set_title('Measurement result')
            self.ax2.set_xlabel('voltage (V)')
            self.ax2.set_ylabel('average concentration')
            self._result_line, = self.ax2.plot([], [], 's-')
            self._result_canvas = FigureCanvasTkAgg(self.fig2, master=mainframe)
            self._result_canvas.get_tk_widget().grid(column=0, row=17, columnspan=3)
            self._result_x = []
            self._result_y = []
        else:
            self._plot_line = None
            self._result_line = None
            ttk.Label(mainframe, text="Matplotlib not available; no plot").grid(column=0, row=16, columnspan=3)

        # Progress bar for overall measurement
        self.progress = ttk.Progressbar(mainframe, orient='horizontal', mode='determinate')
        self.progress.grid(column=0, row=19, columnspan=3, sticky=(tk.W, tk.E))

        for child in mainframe.winfo_children():
            child.grid_configure(padx=5, pady=5)

        # start background thread to update current values
        self._stop_event = threading.Event()
        # flag that indicates a measurement run is active
        self._measure_running = False
        # last time an idle sample was appended
        self._last_idle_sample = 0.0
        self._updater = threading.Thread(target=self._updater_loop, daemon=True)
        self._updater.start()
        # Start periodic HV status polling (every 5 s)
        self._poll_hv_status()

    # UI locking/refactoring moved to ui_helpers.set_ui_locked

    def _hv_scale_moved(self, val):
        # Update the precise variable and the rounded display string while sliding.
        try:
            v = float(val)
            self.hv_set_var.set(v)
            # update rounded display
            self.hv_set_display_var.set(f"{v:.{self._hv_display_decimals}f}")
        except Exception:
            pass

    def _hv_set_from_entry(self):
        # Apply the value shown in the display entry as the precise setpoint.
        try:
            v = float(self.hv_set_display_var.get())
            # update precise var and apply
            self.hv_set_var.set(v)
            print(f"UI: Setting HV setpoint to {v} V")
            self.hv.set_hv_setpoint(v)
            print(f"UI: HV setpoint command sent")
        except Exception as e:
            print(f"UI: Error setting HV: {e}")
            import traceback
            traceback.print_exc()

    def _blower_scale_moved(self, val):
        try:
            v = float(val)
            self.blower_set_var.set(v)
            self.blower_set_display_var.set(f"{v:.{self._blower_display_decimals}f}")
        except Exception:
            pass

    def _blower_set_from_entry(self):
        try:
            v = float(self.blower_set_display_var.get())
            self.blower_set_var.set(v)
            self.blower.set_setpoint(v)
        except Exception:
            pass

    def _calibrate_aerosol_flow(self):
        """Calibrate aerosol flow sensor assuming current flow is 1 L/min.
        
        The user should set the actual flow to exactly 1 L/min using an external
        reference before clicking the calibration button.
        """
        try:
            from hardware import diff_pressure_meter as dpm
            
            dp_dev = getattr(dpm, 'device', None)
            if dp_dev is None:
                messagebox.showerror("Calibration Error", "Differential pressure meter not available")
                return
            
            # Calculate the calibration factor
            try:
                factor = dpm.calibrate_for_1lpm(dp_dev)
            except ValueError as e:
                messagebox.showerror("Calibration Error", str(e))
                return
            
            # Apply the new calibration factor to the device
            dp_dev.set_calibration_factor(factor)
            
            # Save to .env file
            dpm.save_calibration_to_env(factor)
            
            messagebox.showinfo(
                "Calibration Complete",
                f"Calibration factor set to {factor:.6f}\n\n"
                f"Saved to .env file.\n"
                f"Aerosol flow should now read ~1.0 L/min."
            )
        except Exception as e:
            messagebox.showerror("Calibration Error", f"Failed to calibrate: {e}")

    def _update_pid_params_display(self):
        """Update the PID parameters display label."""
        try:
            import blower_controller
            params = blower_controller.get_pid_params()
            self._pid_params_var.set(
                f"PID: P={params['Kp']:.4f} I={params['Ki']:.4f} D={params['Kd']:.4f}"
            )
        except Exception:
            pass

    def _start_pid_autotune(self):
        """Start automatic PID tuning in a background thread."""
        import blower_controller
        
        # Check if already tuning
        if blower_controller.is_tuning():
            messagebox.showwarning("Auto-Tune", "Auto-tuning is already in progress")
            return
        
        # Confirm with user
        current_setpoint = self.blower.get_setpoint()
        if not messagebox.askyesno(
            "Auto-Tune PID",
            f"This will temporarily take over blower control to tune the PID.\n\n"
            f"Current setpoint: {current_setpoint:.2f}\n\n"
            f"The process will induce oscillations around the setpoint.\n"
            f"This may take up to 2 minutes.\n\n"
            f"Continue?"
        ):
            return
        
        # Disable button during tuning
        if self._autotune_btn:
            self._autotune_btn.config(state='disabled')
        
        # Create progress window
        progress_win = tk.Toplevel(self.root)
        progress_win.title("PID Auto-Tune")
        progress_win.geometry("400x150")
        progress_win.transient(self.root)
        
        ttk.Label(progress_win, text="Step response tuning...").pack(pady=10)
        progress_var = tk.DoubleVar(value=0)
        progress_bar = ttk.Progressbar(progress_win, variable=progress_var, maximum=1.0, length=350)
        progress_bar.pack(pady=10)
        status_var = tk.StringVar(value="Starting...")
        ttk.Label(progress_win, textvariable=status_var).pack(pady=5)
        
        def update_progress(progress, message):
            """Callback to update progress from tuning thread."""
            def _update():
                progress_var.set(progress)
                status_var.set(message)
            self.root.after(0, _update)
        
        def tuning_thread():
            try:
                result = blower_controller.auto_tune_pid(
                    apply_result=True,
                    callback=update_progress
                )
                
                def show_result():
                    progress_win.destroy()
                    if self._autotune_btn:
                        self._autotune_btn.config(state='normal')
                    self._update_pid_params_display()
                    
                    if result.get('success'):
                        messagebox.showinfo(
                            "Auto-Tune Complete",
                            f"PID parameters updated:\n\n"
                            f"Kp = {result['Kp']:.6f}\n"
                            f"Ki = {result['Ki']:.6f}\n"
                            f"Kd = {result['Kd']:.6f}\n\n"
                            f"Process gain (K) = {result.get('K', 0):.4f} flow/V\n"
                            f"Time constant (τ) = {result.get('tau', 0):.2f}s\n"
                            f"Dead time (θ) = {result.get('theta', 0):.2f}s"
                        )
                    else:
                        messagebox.showerror(
                            "Auto-Tune Failed",
                            "Could not determine PID parameters.\n"
                            "Make sure the blower responds to voltage changes."
                        )
                
                self.root.after(0, show_result)
                
            except Exception as e:
                def show_error():
                    progress_win.destroy()
                    if self._autotune_btn:
                        self._autotune_btn.config(state='normal')
                    messagebox.showerror("Auto-Tune Error", str(e))
                
                self.root.after(0, show_error)
        
        # Start tuning in background thread
        threading.Thread(target=tuning_thread, daemon=True).start()

    def _updater_loop(self):
        while not self._stop_event.is_set():
            try:
                hv_sp = self.hv.get_hv_setpoint()
                self.hv_current_var.set(f"Setpoint: {hv_sp:.1f} V")
                blower_p = self.blower.get_parameter()
                self.blower_current_var.set(f"Current: {blower_p:.2f}")
                # update CPC concentration if available
                try:
                    from cpc_controller import get_concentration
                    conc = get_concentration()
                    # update label on main thread
                    self.root.after(0, lambda v=conc: self.cpc_current_var.set(f"CPC: {v:.2f}"))
                    # if not currently measuring, append to the plot so the plot updates continuously
                    if (not self._measure_running) and self._plot_line is not None:
                        now = time.time()
                        if now - self._last_idle_sample >= self._idle_sample_interval:
                            self._last_idle_sample = now
                            def _add_idle_point(v=conc):
                                try:
                                    t_rel = time.time() - self._plot_base_time
                                    self._plot_x.append(t_rel)
                                    self._plot_y.append(v)
                                    # trim old points to keep only last _plot_max_seconds
                                    trim_plot(self)
                                    self._plot_line.set_data(self._plot_x, self._plot_y)
                                    self.ax.relim()
                                    self.ax.autoscale_view()
                                    self.canvas.draw_idle()
                                except Exception:
                                    pass
                            self.root.after(0, _add_idle_point)
                except Exception:
                    pass
                # update flow label if flowmeter is present (no plotting)
                try:
                    from hardware import flowmeter as _fmhw
                    dev = getattr(_fmhw, 'device', None)
                    if dev is not None:
                        try:
                            # Call step() to update the flow reading from the sensor
                            dev.step()
                            fval = float(dev.get_flow())
                        except Exception:
                            fval = None
                        def _set_flow_label(v=fval):
                            try:
                                if v is None:
                                    self.flow_current_var.set("Flow: err")
                                else:
                                    self.flow_current_var.set(f"Flow: {v:.3f}")
                            except Exception:
                                pass
                        self.root.after(0, _set_flow_label)
                except Exception:
                    pass
                # update aerosol flow label if differential pressure meter is present
                try:
                    from hardware import diff_pressure_meter as _dpmhw
                    dp_dev = getattr(_dpmhw, 'device', None)
                    if dp_dev is not None:
                        try:
                            # Call step() to update the reading from the sensor
                            dp_dev.step()
                            aerosol_flow = float(dp_dev.get_aerosol_flow())
                        except Exception:
                            aerosol_flow = None
                        def _set_aerosol_flow_label(v=aerosol_flow):
                            try:
                                if v is None:
                                    self.aerosol_flow_var.set("Aerosol Flow: err")
                                else:
                                    self.aerosol_flow_var.set(f"Aerosol Flow: {v:.3f}")
                            except Exception:
                                pass
                        self.root.after(0, _set_aerosol_flow_label)
                except Exception:
                    pass
                # flow polling removed from updater (UI shows label only)
            except Exception:
                pass
            time.sleep(0.2)

    def _poll_hv_status(self):
        """Poll the HV status register and measured voltage every 5 seconds."""
        import threading
        def _query():
            try:
                # Read measured voltage
                try:
                    measured_v = self.hv.get_hv_voltage()
                    v_text = f"Measured: {measured_v:.1f} V"
                except Exception:
                    v_text = "Measured: err"
                def _update_voltage():
                    self.hv_measured_var.set(v_text)
                self.root.after(0, _update_voltage)

                # Read status register
                status = self.hv.get_hv_status()
                if status is None:
                    text = 'HV Status: no response'
                else:
                    bits = status.get('bits', {})
                    flags = [name for name, active in bits.items() if active]
                    if flags:
                        text = 'HV Status: ' + ', '.join(flags)
                    else:
                        text = 'HV Status: OK (all clear)'
                    # Highlight faults in red
                    fault_names = {'Fault', 'Over voltage', 'Over current',
                                   'Over temperature', 'Supply rail out of range'}
                    has_fault = any(bits.get(f, False) for f in fault_names)
                    def _update_ui():
                        self.hv_status_var.set(text)
                        try:
                            self._hv_status_label.configure(
                                foreground='red' if has_fault else '')
                        except Exception:
                            pass
                    self.root.after(0, _update_ui)
                    # Schedule next poll
                    self.root.after(5000, self._poll_hv_status)
                    return
            except Exception as e:
                text = f'HV Status: error ({e})'
            # If we get here (error / None), update and reschedule
            def _update_fallback():
                self.hv_status_var.set(text)
                try:
                    self._hv_status_label.configure(foreground='red')
                except Exception:
                    pass
            self.root.after(0, _update_fallback)
            self.root.after(5000, self._poll_hv_status)
        threading.Thread(target=_query, daemon=True).start()

    def _start_measurement(self):
        # run measurement in background thread
        def _worker():
            try:
                from measurement import MeasurementRunner
                import cpc_controller as cpc
                # set status on main thread and mark measurement running
                def _mark_running():
                    self.measure_status_var.set("Running")
                    self._measure_running = True
                    # lock UI controls while running
                    try:
                        set_ui_locked(self, True)
                    except Exception:
                        pass
                self.root.after(0, _mark_running)
                # construct voltages from min/max/steps inputs
                try:
                    vmin = float(self.voltage_min_var.get())
                except Exception:
                    vmin = 0.0
                try:
                    vmax = float(self.voltage_max_var.get())
                except Exception:
                    vmax = vmin
                try:
                    steps_between = int(self.voltage_steps_var.get())
                except Exception:
                    steps_between = 0
                # clamp invalid steps to zero
                if steps_between < 0:
                    steps_between = 0
                # if vmin==vmax, produce single point
                voltages = []
                if vmin == vmax:
                    voltages = [float(vmin)]
                else:
                    # interpret steps_between as the number of points between min and max
                    # total points = steps_between + 2 (including endpoints)
                    total_points = max(2, steps_between + 2)
                    # ensure ascending order for linspace
                    low, high = (vmin, vmax) if vmin < vmax else (vmax, vmin)
                    span = float(high) - float(low)
                    for i in range(total_points):
                        frac = i / (total_points - 1)
                        voltages.append(low + frac * span)
                    # if original order was descending, reverse to preserve user intent
                    if vmin > vmax:
                        voltages = list(reversed(voltages))
                # validate settling/measurement times
                try:
                    settling = float(self.settling_var.get())
                except Exception:
                    settling = 0.0
                try:
                    measure_time = float(self.measure_time_var.get())
                except Exception:
                    measure_time = 0.0
                if settling < 0.0:
                    settling = 0.0
                if measure_time <= 0.0:
                    # surface error and stop the worker; unlock UI
                    def _set_error_and_unlock():
                        try:
                            self.measure_status_var.set("Error: measurement time must be > 0")
                        except Exception:
                            pass
                        self._measure_running = False
                        try:
                            set_ui_locked(self, False)
                        except Exception:
                            pass
                    self.root.after(0, _set_error_and_unlock)
                    return
                settling = float(self.settling_var.get())
                measure_time = float(self.measure_time_var.get())
                # if saving is enabled but path is empty, treat as no-save
                save_path = None
                if self.save_var.get():
                    sp = (self.save_path_var.get() or "").strip()
                    if sp:
                        save_path = sp
                runner = MeasurementRunner(self.hv, cpc, sample_interval=getattr(cpc, 'UPDATE_INTERVAL', 0.2))

                # prepare plot data
                # clear previous plot on main thread so x-axis starts fresh for this run
                def _clear_plot():
                    try:
                        if self._plot_line is not None:
                            # keep existing pre-measurement data; start with fresh measurement offset
                            # we do not remove pre-measurement idle data here per user request
                            # but trim old points to keep plot length bounded
                            if getattr(self, '_plot_x', None) is None:
                                self._plot_x = []
                                self._plot_y = []
                            self._plot_line.set_data(self._plot_x, self._plot_y)
                            self.ax.relim()
                            self.ax.autoscale_view()
                            self.canvas.draw_idle()
                        # reset progress bar
                        self.progress['value'] = 0.0
                        # reset measurement->plot time offset so first measurement sample anchors to the plot timebase
                        self._measurement_plot_offset = None
                        # flow plotting removed; nothing to clear
                        # clear previous result plot so the user sees only the latest run
                        if getattr(self, '_result_line', None) is not None:
                            self._result_x = []
                            self._result_y = []
                            try:
                                self._result_line.set_data(self._result_x, self._result_y)
                                self.ax2.relim()
                                self.ax2.autoscale_view()
                                self._result_canvas.draw_idle()
                            except Exception:
                                pass
                    except Exception:
                        pass
                self.root.after(0, _clear_plot)

                def sample_cb(voltage, t_rel, c):
                    # Convert measurement-relative time (t_rel) into plot time base so x-axis is continuous
                    def _upd():
                        try:
                            if self._plot_line is None:
                                return
                            # if we haven't computed the measurement->plot offset yet, compute it now
                            if getattr(self, '_measurement_plot_offset', None) is None:
                                # seq_start_wall_time ~= now - t_rel
                                seq_start_wall = time.time() - float(t_rel)
                                # offset so that plot_time = t_rel + offset => equals wall_time - _plot_base_time
                                self._measurement_plot_offset = seq_start_wall - self._plot_base_time
                            plot_time = float(t_rel) + self._measurement_plot_offset
                            self._plot_x.append(plot_time)
                            self._plot_y.append(c)
                            # trim old points to keep only last _plot_max_seconds
                            trim_plot(self)
                            self._plot_line.set_data(self._plot_x, self._plot_y)
                            self.ax.relim()
                            self.ax.autoscale_view()
                            self.canvas.draw_idle()
                        except Exception:
                            pass
                    self.root.after(0, _upd)

                total_steps = len(voltages) * max(1, int((settling + measure_time) / max(self.hv and getattr(cpc, 'UPDATE_INTERVAL', 0.2), 0.001)))
                # progress callback: fraction 0.0-1.0
                def progress_cb(frac):
                    def _upd():
                        try:
                            self.progress['value'] = frac * 100.0
                        except Exception:
                            pass
                    self.root.after(0, _upd)

                # run the sequence (this may raise on file errors)
                results = runner.run_sequence(voltages, settling, measure_time, save_path, sample_callback=sample_cb, progress_callback=progress_cb)
                # update status on main thread with last average and clear running flag
                def _set_done_status():
                    try:
                        if results:
                            last = results[-1]
                            self.measure_status_var.set(f"Last: V={last[0]}, avg={last[1]:.3f}")
                            # update the measurement-result plot (voltage vs avg concentration)
                            try:
                                if getattr(self, '_result_line', None) is not None:
                                    # replace data with results from this run
                                    self._result_x = [r[0] for r in results]
                                    self._result_y = [r[1] for r in results]
                                    self._result_line.set_data(self._result_x, self._result_y)
                                    self.ax2.relim()
                                    self.ax2.autoscale_view()
                                    self._result_canvas.draw_idle()
                            except Exception:
                                pass
                        else:
                            self.measure_status_var.set("No results")
                    except Exception:
                        self.measure_status_var.set("Done")
                    self._measure_running = False
                    try:
                        set_ui_locked(self, False)
                    except Exception:
                        pass
                self.root.after(0, _set_done_status)
            except Exception as e:
                # surface errors on the main thread (avoid directly touching Tk variables from worker thread)
                def _set_error():
                    self.measure_status_var.set(f"Error: {e}")
                    self._measure_running = False
                def _set_error_and_unlock():
                    _set_error()
                    try:
                        set_ui_locked(self, False)
                    except Exception:
                        pass
                self.root.after(0, _set_error_and_unlock)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    # plot trimming moved to ui_helpers.trim_plot

    def stop(self):
        self._stop_event.set()

    def _load_ini_config(self):
        """Open a file dialog to load measurement parameters from an INI file.

        Expected section: [measurement]
        Keys supported:
          voltage_min, voltage_max, steps, settling, measurement_time, save, save_path
        """
        try:
            path = filedialog.askopenfilename(title="Select measurement INI", filetypes=[("INI files", "*.ini"), ("All files", "*")])
            if not path:
                return
            cfg = configparser.ConfigParser()
            cfg.read(path)
            section = 'measurement'
            vals = cfg[section] if section in cfg else cfg.defaults() if cfg.defaults() else {}
            # read values with fallbacks
            def getfloat(key, fallback):
                try:
                    if key in vals:
                        return float(vals.get(key))
                except Exception:
                    pass
                return fallback
            def getint(key, fallback):
                try:
                    if key in vals:
                        return int(vals.get(key))
                except Exception:
                    pass
                return fallback
            def getbool(key, fallback):
                try:
                    if key in vals:
                        v = vals.get(key)
                        if isinstance(v, bool):
                            return v
                        return v.lower() in ('1', 'true', 'yes', 'on')
                except Exception:
                    pass
                return fallback

            # apply to UI vars on main thread
            def _apply():
                try:
                    vmin = getfloat('voltage_min', self.voltage_min_var.get())
                    vmax = getfloat('voltage_max', self.voltage_max_var.get())
                    steps = getint('steps', self.voltage_steps_var.get())
                    settling = getfloat('settling', self.settling_var.get())
                    mtime = getfloat('measurement_time', self.measure_time_var.get())
                    save = getbool('save', self.save_var.get())
                    spath = vals.get('save_path', self.save_path_var.get()) if hasattr(vals, 'get') else self.save_path_var.get()
                    self.voltage_min_var.set(vmin)
                    self.voltage_max_var.set(vmax)
                    self.voltage_steps_var.set(steps)
                    self.settling_var.set(settling)
                    self.measure_time_var.set(mtime)
                    self.save_var.set(bool(save))
                    self.save_path_var.set(spath)
                    # show loaded filename (basename)
                    import os
                    self._loaded_config_var.set(os.path.basename(path))
                except Exception:
                    # silently ignore errors while applying; surface minimal status
                    try:
                        self._loaded_config_var.set("<invalid ini>")
                    except Exception:
                        pass
            self.root.after(0, _apply)
        except Exception:
            try:
                self._loaded_config_var.set("<error>")
            except Exception:
                pass


def run_ui(hv_module, blower_module):
    root = tk.Tk()
    app = ControlUI(root, hv_module, blower_module)
    try:
        root.mainloop()
    finally:
        app.stop()
