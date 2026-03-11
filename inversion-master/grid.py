import numpy as np

def make_grid(diameters1, diameters2):

    result = np.concatenate([diameters1[diameters1 < diameters2.min()], diameters2])
    difflog = np.diff(np.log10(result))
    
    #Culling based on some criteria...
    mdlog= np.mean(difflog)/3
    removals = np.where(difflog < mdlog)
    result = np.delete(result, removals)

    return result

def make_bin_edges(original_diameters):

    diameters = np.log10(original_diameters)

    diffs = np.diff(diameters)

    middles = (diameters[1:] + diameters[:-1])/2.0
    first = [diameters[0] - diffs[0]/2.0]
    last = [diameters[-1] + diffs[-1]/2.0]

    limits = np.concatenate([first, middles, last])
    dlogdp = np.diff(limits)
    limits = 10**limits

    return limits,dlogdp

def check_grid(diameters):

    if np.min(np.diff(np.log10(diameters)))<1e-6:
        raise ValueError("Some channels are too close to each other. Remove overlapping channels")
    
    