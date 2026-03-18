#!/usr/bin/env python3
import abc
import json
from datetime import date, datetime, time, timedelta
from enum import Enum
from io import StringIO
from math import pi
from pathlib import Path
from functools import partial
import sys

import numpy as np
import pandas as pd
from original_constants import constants
from scipy.special import erf
from scipy.integrate import quad
from scipy.optimize import nnls
from scipy.interpolate import interp1d
import h5py

from particle import calculate_air_mean_free_path, cunningham_correction, air_viscosity, diffusion, tube_loss, diameters_from_mobility, original_diameters_from_mobility
import grid
import charging

from IPython import embed
from IPython.core.debugger import set_trace

def changed_over_fraction(*args, fraction=0.01):

    if len(args) % 2 != 0:
        raise ValueError("Must have pairs of arguments to compare")

    for i in range(0,len(args),2):
        start, end = args[i], args[i+1]

        if (start == 0) & (end == 0):
            continue

        if start == 0:
            return True


        if (start - end)/start > fraction:
            return True

    return False

def dump_hdf5(filename, **data):
    
    with h5py.File(filename, 'w') as f:
        for name,d in data.items():
            f.create_dataset(name, data=d)

class Grid(Enum):
    first = 1
    second = 2
    independent = 3 # also requires dlok (default 0.07)

class DMA():

    def __init__(self, length, outer_radius, inner_radius, sheath_flow,
        excess_flow, aerosol_flow_in, aerosol_flow_out, loss_factor,
        fitted_length, voltage_channels, index_start, index_end, invert=False):

        self.length=length # [m]
        self.outer_radius=outer_radius # [m]
        self.inner_radius= inner_radius # [m]

        # All flows given in l/min -> [m^3/s]

        self.sheath_flow = l_per_min_to_si_base(sheath_flow)
        self.excess_flow = l_per_min_to_si_base(excess_flow)
        self.aerosol_flow_in = l_per_min_to_si_base(aerosol_flow_in)
        self.aerosol_flow_out = l_per_min_to_si_base(aerosol_flow_out)

        #A pipe length calibrated so that diffusion loss is accounted for.
        #Usually different from real length.
        #(Unless your dma is a single straight tube with laminar flow)
        self.fitted_length = fitted_length

        self.loss_factor = loss_factor
        self.voltage_channels = voltage_channels
        self.index_start = index_start
        self.index_end = index_end
        self.invert = invert

    def update_measured_sheath_flow(self, sheath_flow):
        self.sheath_flow = sheath_flow
        self.excess_flow = self.sheath_flow + self.aerosol_flow_in - self.aerosol_flow_out

    def calculate_mobilities(self, voltages):
        self.mobilities = ((self.sheath_flow + self.excess_flow)/2 *
                np.log(self.outer_radius / self.inner_radius)/(2*pi*self.length) * (1 / np.abs(voltages)))
        return self.mobilities 

    def process_scans(self, parameters, scans, polarity):
    
        values = scans[self.index_start:self.index_end,:,:]

        if self.invert:
            values = np.flip(values,0)

        voltages = values[:,0,:]

        voltages = polarity*np.abs(voltages)

        concentrations = values[:,1,:]/self.loss_factor
    
        return voltages, concentrations

    def diffusion_loss(self, diameter, temperature, pressure):
        return tube_loss(diameter, temperature, pressure, 
            self.fitted_length, self.aerosol_flow_in)

class CPC():

    def __init__(self, **kwargs):
        self.__dict__.update(**kwargs)
        return

    @abc.abstractmethod
    def loss(self, diameter, temperature, pressure):
        pass

class TSI3025(CPC):

    def __init__(self):
        self.pipe_length = 0.1881
        self.pipe_flow = 5.0/1e6
        self.diameter_cutoff = 1.7e-9
        self.diameter_slope = 4.75e-10
        self.cutoff = self.diameter_cutoff

    def loss(self, diameter, temperature, pressure):
    
        diffusion_loss=tube_loss(diameter,temperature,pressure,self.pipe_length, self.pipe_flow)

        cutoff_loss=1.0-np.exp((self.diameter_cutoff-diameter)/self.diameter_slope)

        if np.isscalar(cutoff_loss):
            if(diameter < self.diameter_cutoff):
                cutoff_loss = 0
        else:
            cutoff_loss[diameter < self.diameter_cutoff] = 0

        return diffusion_loss*cutoff_loss 


class TSI3010(CPC):

    def __init__(self, set_temperature):
        self.set_temperature = set_temperature
        
        if set_temperature == 21:
            self.a=1.4
            self.D1=6.5
            self.D2=1.9
            self.DP50=7.6
        elif set_temperature == 25:
            self.a=1.86
            self.D1=4.25
            self.D2=3.84
            self.DP50=5.7
        else:
            self.a=1.4
            self.D1=8.9
            self.D2=2.9
            self.DP50=10.5

        #nm -> m
        self.D1 = self.D1 * 1E-9
        self.D2 = self.D2 * 1E-9
        self.cutoff = self.D2*np.log(self.a-1)+self.D1


    def loss(self, diameter, temperature, pressure):

        #(calculation) not actually affected by temperature and pressure, but we want a consistent interface

        factor = 1-self.a*(1+np.exp((diameter-self.D1)/self.D2))**(-1)
        if np.isscalar(factor):
            if(diameter < self.cutoff):
                factor = 0
        else:
            factor[diameter < self.cutoff] = 0

        return factor

implemented_cpcs = {"TSI3025":TSI3025, "TSI3010":TSI3010}


def cutoff_quadrature(function, start, end, cutoff, args, tol=1e-6, vec_func=True, maxiter=100):     

    start_before_cutoff = start < cutoff
    end_before_cutoff = end < cutoff

    if end_before_cutoff:
        print("Null integral, {0}, {1}, {2}".format(start,end,cutoff))
        return 0,None

    if start_before_cutoff:
        start_actual = cutoff
    else:
        start_actual = start

    #if start_actual >= end:
    #    return 0, None

    #First channel test:
    #test_dp = np.linspace(start_actual, end, 10000)
    #function_values = function(np.log10(test_dp), *args)
    #out_dict = {"temperature":args[0], "pressure":args[1], "voltage":args[2]}
    
    #dump_hdf5("./testdata/mob_first_channel.h5", **{"dp":test_dp, "start":start, "start_actual":start_actual, "end":end, "transfer":function_values, **out_dict})
    
    #sys.exit()

    #End first channel test

    def scalar_integrand(x, *integrand_args):
        value = np.asarray(function(x, *integrand_args))
        if value.size != 1:
            raise ValueError("Integrand must return a scalar value for quad integration")
        return float(value.reshape(-1)[0])

    result, error = quad(
        scalar_integrand,
        np.log10(start_actual),
        np.log10(end),
        args=args,
        epsabs=tol,
        epsrel=tol,
        limit=maxiter,
    )

    return result, error

class DMPS_System():

    def __init__(self, **keywords):
        self.final_grid_method = keywords["final_grid_method"]
        self.default_temperature = keywords["default_temperature"] # [K]
        self.default_pressure = keywords["default_pressure"] # [Pa]
        self.force_defaults = keywords["force_defaults"]
        self.amount_of_dmas = keywords["amount_of_dmas"] # 1 or 2
        self.max_charge = keywords["max_charge"]
        self.high_voltage_polarity = keywords["high_voltage_polarity"] # -1, 1
       
        #Approximate values for sampling pipe for loss estimation
        self.pipe_length = keywords["pipe_length"] # [m]
        self.pipe_flow = l_per_min_to_si_base(keywords["pipe_flow"]) # [m^3/s]
        self.pipe_inner_diameter = keywords["pipe_inner_diameter"] # m

        self.dmas = {}
        self.cpcs = {}

    def set_dma(self, dma, number):
        self.dmas[number] = dma

    def set_cpc(self, cpc, number):
        self.cpcs[number] = cpc

    def sample_line_loss(self, diameter, temperature, pressure):
        return tube_loss(diameter, temperature, pressure, self.pipe_length, self.pipe_flow)

    @staticmethod
    def generate_from_json(filepath):
        with filepath.open('r') as infile:
            data = json.load(infile)

            dmps_specific = {key:item for key,item in data.items() if key not in ["dmas", "cpcs"]}

            result = DMPS_System(**dmps_specific)

            for number,specs in enumerate(data["dmas"]):
                result.set_dma(DMA(**specs), number)

            for number, specs in enumerate(data["cpcs"]):
                cpc = implemented_cpcs[specs["type"]]
                options = specs.get("options", {})    
                result.set_cpc(cpc(**options), number)

            return result

    def process(self,base,date, temperature_column="DMPS{} T", pressure_column="pressure", flow_column="DMPS{} sheath flow rate"):

        files = get_dmps_filenames(base, date)

        intervals, variables, scans = read_dmps_dat_file(files["data"])
        flows = read_dmps_flw_file(files["flow"], date)

        scanlines = scans[0].shape[-1]

        diameters = {}
        voltages = {}
        concentrations ={}
        temperature = {}
        pressure = {}

        for index, dma in self.dmas.items():
            temperature_name = temperature_column.format(index+1)
            
            #Separate and calculate mobilities from the scan variables
            if self.force_defaults:
                temperature[index] = self.default_temperature
                pressure[index] = self.default_pressure
            else:
                #Temperature should change depending on dma...
                temperature[index] = variables[index][temperature_name].values
                pressure[index] = variables[index][pressure_column].values

            #TODO Make mobility calculation dependent on updated sheat flow (ie. put into below loop)
            voltages[index], concentrations[index]  = dma.process_scans(variables[index], scans[index], polarity=self.high_voltage_polarity)

            #diameters[index] = diameters_from_mobility(mobilities, temperature[index], pressure[index])
            #TEST: Use same method as matlab
            #diameters[index] = original_diameters_from_mobility(mobilities[index], temperature[index], pressure[index])

        #dma1 = {key:value for key, value in self.dmas[0].__dict__.items() if not key.startswith('__') and not callable(key)}
        #dump_hdf5("./testdata/mobilities.h5", **{"mob_peak1":mobilities[0], "mob_peak2":mobilities[1], **dma1})

        #Interpolate flow measurements to scan end times
        #The original matlab version extrapolated linearly outside the measurements.
        #This just forward/backward fills the nearest measured value.
        #In practice the results should not change much, but ours is "safer" for longer times.
        #Both are just "best effort" guesses. If it is a problem, using data from several adjoining days might help it.
        #BUT NB: THIS RESULTS IN VERY SLIGHTLY DIFFERENT FLOW RATES COMPARED TO THE MATLAB VERSION
        ends = pd.DatetimeIndex(intervals["start"])
        interpolated_flows = flows.reindex(flows.index.union(ends)).interpolate('time').bfill().ffill().reindex(ends)

        measured_sheath_flow = {}
        diameters = {}
        mobilities = {}


        for index, dma in self.dmas.items():
            column = flow_column.format(index+1)
            #Unit conversion to match the dma units
            measured_sheath_flow[index] = l_per_min_to_si_base(interpolated_flows[column]).values
        
        #Should consider preallocating...
        diameter_array = []
        dlogs = []
        distributions = []
        used_laitematriisi = []
        totals = np.zeros(scanlines)
        fraction_scan_no = 0

        for scan in range(scanlines):

            concentration = []

            for index, dma in self.dmas.items():
                dma.update_measured_sheath_flow(measured_sheath_flow[index][scan])
                mobilities[index] = dma.calculate_mobilities(voltages[index][:,scan])
                diameters[index] = original_diameters_from_mobility(mobilities[index], temperature[index][scan], pressure[index][scan])

            #Debugging get intermediary steps
            #dma1 = {key:value for key, value in self.dmas[0].__dict__.items() if not key.startswith('__') and not callable(key)}
            #dump_hdf5("./testdata/mobilities.h5", **{"mob_peak1":mobilities[0], "mob_peak2":mobilities[1], **dma1})
            #dump_hdf5("./testdata/raw_diameters.h5", **{"dp_peak1":diameters[0], "dp_peak2":diameters[1]})

            if len(self.dmas) > 1:
                combined_diameters = grid.make_grid(diameters[0], diameters[1])
            else:
                combined_diameters = np.sort(diameters[0])

            grid.check_grid(combined_diameters)

            edges, dlogdp = grid.make_bin_edges(combined_diameters)
            logedges = np.log10(edges)

            diameter_array.append(combined_diameters)
            dlogs.append(dlogdp)

            #TODO make this hack work for more than two dmas, also check dma flows for change
            #For 10.04.2018 it works since matlab says temp and pressure are the only changes
            change_args = []
            for idx in self.dmas:
                change_args.extend([temperature[idx][fraction_scan_no], temperature[idx][scan]])
                change_args.extend([pressure[idx][fraction_scan_no], pressure[idx][scan]])
            significant_change = changed_over_fraction(*change_args, fraction=0.01)
            
            if significant_change or scan == 0:
                fractions = []
                for index in range(len(self.dmas)):
                    dma = self.dmas[index]
                    volts = voltages[index][:,scan]
                    fraction = np.zeros([volts.shape[0], combined_diameters.shape[0]])

                    for i in range(volts.shape[0]):
                        for j in range(logedges.shape[0]-1):
                            fraction[i,j], errors = cutoff_quadrature(fraction_of_transferred, edges[j], edges[j+1], self.cpcs[index].cutoff,
                                                      args=(temperature[index][scan], pressure[index][scan], volts[i], dma, self.cpcs[index], self),
                                                      tol=1e-6, vec_func=True, maxiter=100)
                            
                            fraction[i,j] = fraction[i,j]/(np.log10(edges[j+1])-np.log10(edges[j]))              

                    fractions.append(fraction)
                    fraction_scan_no = scan
            
            
                fractions = np.concatenate(fractions)

            used_laitematriisi.append(fractions)

            for index in range(len(self.dmas)):
                concentration.append(concentrations[index][:,scan])
            
            concentration = np.concatenate(concentration)

            #Maybe something should be done if residual is too large?
            size_distribution, residual = nnls(fractions, concentration)

            if scan > 0:
                #interpolate to common grid
                #interpolation done in log space, because the original code did it in logspace...
                #Which is also where the addition 1e-30 is from, since 0 is not representable in logspace
                interpolator = interp1d(np.log10(combined_diameters), np.log10(size_distribution + 1e-30), fill_value = "extrapolate")
                size_distribution = 10**(interpolator(np.log10(diameter_array[0])))

            size_distribution[size_distribution < 1e-10] = 0
            totals[scan] = np.sum(size_distribution)
            size_distribution = size_distribution/(dlogdp)

            distributions.append(size_distribution)

        final_distributions = np.stack(distributions)

        laitematriisit = np.stack(used_laitematriisi)

        return {"times":ends, "sizes":np.stack(diameter_array), "size_distribution":final_distributions, "totals":totals, "laitematriisit":laitematriisit}


def write_output(filename, times, sizes, size_distribution, totals, **kwargs):
    
    with h5py.File(filename, 'w') as f:

        f.create_dataset("Particle diameter", data=sizes)
        f.create_dataset("Total concentration", data=totals)
        f.create_dataset("Concentration dNdlogDp", data=size_distribution)

        if len(kwargs) > 0:
            for name, data in kwargs.items():
                f.create_dataset(name, data=data)

        #Storing the dates as strings
        h5str = h5py.special_dtype(vlen=str)

        dset = f.create_dataset("Time", (len(times),), dtype=h5str)
        dset[:] = list(times.strftime("%Y-%m-%d %H:%M:%S"))

    # Also save a pandas-compatible CSV for use with aerosol plotting tools.
    # Format: DatetimeIndex rows, particle diameter (m) columns, dN/dlogDp values.
    csv_path = Path(filename).with_suffix('.csv')
    time_index = pd.DatetimeIndex(times)
    # sizes may vary per scan; use the first scan's diameters as columns
    diameters = sizes[0] if sizes.ndim > 1 else sizes
    df = pd.DataFrame(size_distribution, index=time_index, columns=diameters)
    df.to_csv(csv_path)

    # Plot size distribution surface plot and save as PNG
    try:
        import matplotlib
        matplotlib.use('Agg')  # non-interactive backend
        import matplotlib.pyplot as plt
        from aerosol.plotting import plot_aerosol_dist

        png_path = Path(filename).with_suffix('.png')
        fig, ax = plt.subplots(figsize=(12, 5))

        img, cbar = plot_aerosol_dist(df, ax)

        fig.tight_layout()
        fig.savefig(str(png_path), dpi=150, bbox_inches='tight')
        plt.close(fig)
    except Exception:
        pass

def epsilon(x):
    #This is also suprisingly lot of time
    return -x*(1-erf(x))+(1/np.sqrt(pi))*np.exp(-x**2)

def transfer_function(diameters, temperature, pressure, voltage, max_charge, dma : DMA):
    #Pretty much a translation of the matlab code.

    #TODO transfer most of this to the dma class since it is not variable in the integration calls (only updated when flows are updated)
    #It also forms the bulk of the function runtime.
    charges = np.arange(1,np.abs(max_charge)+1)
    if max_charge < 0:
        charges = -charges

    diameter_ratio = np.log(dma.inner_radius/dma.outer_radius)

    beta=(dma.aerosol_flow_out+dma.aerosol_flow_in)/(dma.excess_flow+dma.sheath_flow)

    delta=-(dma.aerosol_flow_out-dma.aerosol_flow_in)/(dma.aerosol_flow_out+dma.aerosol_flow_in)

    gammas=(dma.inner_radius/dma.outer_radius)**2

    gkappa=dma.length*dma.outer_radius/((dma.outer_radius**2)-(dma.inner_radius**2))

    gammai=(0.25*(1-gammas*gammas)*(1-gammas)*(1-gammas)+(5/18)*
    (1-gammas*gammas*gammas)*(1-gammas)*np.log(gammas)+(1/12)*
    (1-gammas*gammas*gammas*gammas)*np.log(gammas)*np.log(gammas))/((1-gammas)*
    (-0.5*(1+gammas)*np.log(gammas)-(1-gammas))*(-0.5*(1+gammas)*
    np.log(gammas)-(1-gammas)))

    gabeta=(4.0*(1+beta)*(1+beta)*(gammai+(1/((2*(1+beta)*
    gkappa)*(2*(1+beta)*gkappa)))))/(1-gammas)

    zeta=charges[None,:]*(constants.e*cunningham_correction(diameters,temperature,pressure)[:,None]/(3*pi*air_viscosity(temperature)*diameters[:,None]))

    zetap=4.0*voltage*pi*dma.length*zeta/((dma.excess_flow+dma.sheath_flow)*diameter_ratio)

    rhota = np.sqrt(zetap/charges[None,:]*(gabeta*diameter_ratio*constants.Boltzmann*temperature/(constants.e*voltage)))

    sqrt2 = np.sqrt(2)

    #Diffuusiotermi
    teea1=(rhota/(sqrt2*beta*(1.-delta))*
    (epsilon(np.abs(zetap-(1+beta))/(sqrt2*rhota))+
    epsilon(np.abs(zetap-(1-beta))/(sqrt2*rhota))-
    epsilon(np.abs(zetap-(1+beta*delta))/(sqrt2*rhota))-
    epsilon(np.abs(zetap-(1-beta*delta))/(sqrt2*rhota))))		

    #Kolmio ei diffuusiota
    teea2=(1/(2*beta*(1-delta))*(np.abs(zetap-(1+beta))+
    np.abs(zetap-(1-beta))-np.abs(zetap-(1+beta*delta))-
    np.abs(zetap-(1-beta*delta))))
    
    return teea2+teea1


def fraction_of_transferred(diameter_in, temperature, pressure, voltage, dma, cpc, dmps):

    diameter = 10**np.atleast_1d(diameter_in) #test with normal space integration

    cpcloss = cpc.loss(diameter, temperature, pressure)
    dmaloss = dma.diffusion_loss(diameter, temperature, pressure)
    inletloss = dmps.sample_line_loss(diameter, temperature,pressure)
    losses = cpcloss*dmaloss*inletloss

    charged_fraction = charging.charging_efficiency(diameter, max_charge=dmps.max_charge, polarity=dmps.high_voltage_polarity, temperature=temperature)

    transferred_fraction = transfer_function(diameter, temperature, pressure, voltage, -dmps.high_voltage_polarity*dmps.max_charge, dma)
    transferred_fraction[transferred_fraction < 0 ] = 0    

    result = np.sum(charged_fraction * transferred_fraction, axis=1) * losses

    return result


def l_per_min_to_si_base(value):
    # l/min -> m^3/s
    return value/1000/60

def get_dmps_filenames(base : Path, date : date):
    front = "DM{:%y%m%d}".format(date)
    datfile = base / (front+".DAT")
    flwfile = base / (front+".FLW")
    return {"data":datfile, "flow":flwfile}

def get_interval(line, template = "%m-%d-%Y %H:%M:%S"):
    """
    If there is more than two, should probably use regexp.
    And yes the default template is weird. It was deduced by looking at the filenames and comparing the dates within.
    """
    start, end = line.split("' '")
    start = datetime.strptime(start[1:], template)
    end = datetime.strptime(end[:-2], template)
    return (start, end)


def parameters_to_dataframe(parameters):
    
    #These might change in the future, hardcoded for now.
    columns = ["Sheath flow", "Aerosol flow", "DMA inner radius", "DMA outer radius", "DMA length", "total counting time",
         "CPC type", "pressure","DMPS1 RH", "DMPS1 T","DMPS2 RH", "DMPS2 T", "Aerosol inlet RH", "Aerosol inlet T", "Null"]

    result = {}
    for key,param in parameters.items():
        if not param:  # skip empty channels (single-DMA)
            continue
        text = StringIO("".join(param))
        result[key] = pd.read_table(text, names=columns, header=None)
        
        #some might require unit conversions
        result[key]["DMPS1 T"] = result[key]["DMPS1 T"] + 273.15
        result[key]["DMPS2 T"] = result[key]["DMPS2 T"] + 273.15
        result[key]["pressure"] = result[key]["pressure"]*100

    return result

def scans_to_ndarrays(scans):
    
    result = {}

    for key, data in scans.items():
        if not data:  # skip empty channels (single-DMA)
            continue
        result[key] = []
        for scan in data:
            arr = np.genfromtxt(StringIO("".join(scan)))
            arr = np.atleast_2d(arr)
            result[key].append(arr)

    # Only keep scans that match the expected number of voltage steps
    for key in list(result.keys()):
        if not result[key]:
            continue
        expected = result[key][0].shape[0]
        result[key] = [s for s in result[key] if s.shape[0] == expected]

    for key, data in result.items():
        result[key] = np.dstack(data)

    return result

def read_dmps_dat_file(filepath):

    intervals = []
    parameters = {0:[],1:[]}
    scans = {0:[], 1:[]}

    frame = 0
    next_parameter_line = True

    #So there are two dma + cpc pairs in the same file. The scans alternate.
    #Technically the amount of channels is defined in a config.
    #However the different versions of the config file are not saved.
    #I.e. you can't trust it on older/newer, different instrument data.
    with filepath.open('r') as infile:
        for line in infile:

            if line.startswith("'"):
                #it's a new scan
                interval = get_interval(line)
                next_parameter_line = True

                if (len(intervals) > 0) and (interval == intervals[-1]):
                    #This is the second dma simultaneous scan.
                    frame = 1
                    scans[frame].append([])
                    continue

                frame = 0
                scans[frame].append([])
                intervals.append(interval)
                continue

            if next_parameter_line:
                parameters[frame].append(line)
                next_parameter_line = False
                continue

            scans[frame][-1].append(line)


    intervals = pd.DataFrame(intervals, columns=["start", "end"])
    parameters = parameters_to_dataframe(parameters)
    scans = scans_to_ndarrays(scans)

    return intervals, parameters, scans

def read_dmps_flw_file(filepath, date):

    #These might change in the future, hardcoded for now.
    columns = ["Delta hours", 
    "DMPS1 sheath flow rate",
    "DMPS1 sheat flow temperature",
    "DMPS1 sheath flow pressure",
    "DMPS2 sheath flow rate",
    "DMPS2 sheath flow temperature",
    "DMPS2 sheath flow pressure",
    "DMPS2 aerosol flow",
    "DMPS1 aerosol flow",
    "Flow speed in the main sampling line",
    "Nephelometer inlet RH",
    "Nephelometer inlet T",
    "random1",
    "random2",
    "random3"]


    table = pd.read_table(filepath, header=None, names=columns, sep=r'\s+')
    basedatetime = datetime.combine(date, time())
    times = [basedatetime + timedelta(hours=x) for x in table["Delta hours"]]
    table.index = pd.DatetimeIndex(times)
    
    return table

def test():
    #voltage = 10
    #max_charge = 6
    #temperature = 273
    #pressure = 0.950e5
    system = DMPS_System.generate_from_json(Path('./dmps_template.json'))
    base = Path('./testdata')
    mydate = date(2026, 3, 17)
    outfile = './testdata/fresh_start.h5'
    data = system.process(base,mydate)
    #return data
    write_output(outfile, **data)
    return system, data

if __name__ == "__main__":
    a,b = test()
