# import built-in modules
import os
import fnmatch
import functools
import warnings

# import external public modules
import numpy as np
from astropy.io import fits
from scipy import interpolate, ndimage


''' --------------------------- defaults --------------------------- '''

IMPORT_FAILURE_WARNINGS = False    # whether to warn (immediately) when an optional module fails to import.
  # Either way, we will still raise ImportFailedError upon accessing a module which failed to import.


''' --------------------------- writing snaps --------------------------- '''

def writefits(obj, varname, snap=None, instrument = 'MURaM', 
              name='ar098192', origin='HGCR    ', z_tau51m = None): 

  if varname[:2] == 'lg': 
    varnamefits='lg('+varname[2:]+')'
  else: 
    varnamefits = varname

  hdu = fits.PrimaryHDU(np.transpose(obj.datafits))
  #hdu.header['NAXIS']  = (np.ndim(obj.datafits), 'Number of data axes') 
  #hdu.header['NAXIS1'] = np.shape(obj.datafits)[0]
  #hdu.header['NAXIS2'] = np.shape(obj.datafits)[1]
  #hdu.header['NAXIS3'] = np.shape(obj.datafits)[2]
  hdu.header['INSTRUME']= (instrument,  'Data generated by the %s code'%instrument ) 
  hdu.header['OBJECT'] = (name, '%s run name'%instrument )
  hdu.header['BTYPE']  = (varnamefits,'Data variable')
  hdu.header['BUNIT']  = (obj.fitsunits, 'Data unit' )# SI units
  hdu.header['CDELT1'] = (obj.dxfits.min(), '[Mm] x-coordinate increment')
  hdu.header['CDELT2'] = (obj.dyfits.min(), '[Mm] y-coordinate increment')
  hdu.header['CDELT3'] = (obj.dzfits.min(), '[Mm] z-coordinate increment')
  hdu.header['CRPIX1'] = (1,'Reference pixel x-coordinate')
  hdu.header['CRPIX2'] = (1,'Reference pixel y-coordinate')
  hdu.header['CRPIX3'] = (1,'Reference pixel z-coordinate')
  hdu.header['CRVAL1'] = (obj.xfits.min(),'[Mm] Position ref-pixel x-coordinate')
  hdu.header['CRVAL2'] = (obj.yfits.min(),'[Mm] Position ref-pixel y-coordinate')
  hdu.header['CRVAL3'] = (obj.zfits.min(),'[Mm] Position ref-pixel z-coordinate')
  hdu.header['CTYPE1'] = ('x       ', '[Mm] Label for x-coordinate')
  hdu.header['CTYPE2'] = ('y       ', '[Mm] Label for y-coordinate')
  hdu.header['CTYPE3'] = ('z       ', '[Mm] Label for z-coordinate')
  hdu.header['CUNIT1'] = ('Mm      ', 'Unit for x-coordinate')
  hdu.header['CUNIT2'] = ('Mm      ', 'Unit for y-coordinate')
  hdu.header['CUNIT3'] = ('Mm      ', 'Unit for z-coordinate')
  hdu.header['RUNID']  = ('hgcr    ', 'Run ID for identification of input files')
  hdu.header['ELAPSED']= (obj.time, '[s] Time of snapshot')
  hdu.header['DATA_LEV']= (2, 'Data level')
  hdu.header['ZTAU51'] = (z_tau51m, ' [Mm] Average height of tau(500nm)=1')
  hdu.header['ORIGIN'] = (origin, 'Origin of data')
  
  #hdu.header.insert('ORIGIN',('COMMENT', 'Variables from .idl file:                                               '),after=True)
  hdu.header['MX']     = (np.shape(obj.datafits)[0], 'mx')
  hdu.header['MY']     = (np.shape(obj.datafits)[1], 'my')
  hdu.header['MZ']     = (np.shape(obj.datafits)[2], 'mz')
  hdu.header['NDIM']   = (np.ndim(obj.datafits), 'ndim')
  hdu.header['DX']     = (obj.dxfits.min(), 'dx')
  hdu.header['DY']     = (obj.dyfits.min(), 'dy')
  hdu.header['DZ']     = (obj.dzfits.min(), 'dz')
  hdu.header['T']      = (obj.time, 't')
  hdu.header['ISNAP']  = (obj.snap, 'isnap')
  hdu.header['AUX']    = ('', 'aux')

  #hdu.header['COMMENT']= 'Non-uniform z-coordinate                                                ' 
  hdu1 = fits.ImageHDU(obj.zfits)
  hdu1.header['XTENSION']= ('IMAGE   ', 'IMAGE extension')
  hdu1.header['BITPIX']  = (-32, 'Number of bits per data pixel')
  hdu1.header['NAXIS']   = (1, 'Number of data axes')
  hdu1.header['NAXIS1']  = np.size(obj.zfits)   
  hdu1.header['PCOUNT']  = (0, 'No Group Parameters')
  hdu1.header['GCOUNT']  = (1, 'One Data Group')
  hdu1.header['EXTNAME'] = ' z-coordinate'                                                      
  hdu1.header['BTYPE']   = ('z       ', 'Data variable')
  hdu1.header['BUNIT']   = ('Mm      ', 'Unit for z-coordinate')
  hdul = fits.HDUList([hdu,hdu1])
  hdul.writeto(instrument+'_'+name+'_'+varname+'_'+inttostring(obj.snap,ts_size=3)+'.fits')

def allsnap2fits(dd,iz0=116,z_tau51m=0.03045166015624971,rootname = "result_prim_0.*",patern='.'):
  varlist=['ux','uy','uz','bx','by','bz','lge','lgne','lgpg','lgrho','lgtg']
  listOfFiles = os.listdir('.') # folder
  snaplist=[int(entry[entry.find(patern)+1:]) for entry in listOfFiles if fnmatch.fnmatch(entry, rootname)]
  for snap in snaplist: 
    snapname = '.{:07d}'.format(snap)
    for var in varlist: 
      print(var)
      dens = dd.trasn2fits(var,snap,iz0=116,z_tau51m=0.03045166015624971)

def inttostring(ii,ts_size=7):
  '''convert int to string with length ts_size or longer, padding with leading 0s as necessary.

  Note: SE changed on Jan 11, 2021 to use zfill.
  this changes behavior on negative integers (to be more intuitive,
  e.g. -00007 instead of 0000-7). If that's a problem, restore old version.
  '''
  # new version (new as of Jan 11, 2021)
  return str(ii).zfill(ts_size)

  """# old version:
  str_num = str(ii)

  for bb in range(len(str_num),ts_size,1):
    str_num = '0'+str_num
  
  return str_num
  """


''' --------------------------- units --------------------------- '''

def units_title(obj): 
  '''
  Units and constants in SI
  '''
  obj.unisi_title={}
  obj.unisi_title['tg']     = ' K '
  obj.unisi_title['l']      = ' m '
  obj.unisi_title['pg']     = ' N m^(-2) '
  obj.unisi_title['rho']    = ' kg m^(-3) '
  obj.unisi_title['u']      = ' m s^(-1) '
  obj.unisi_title['b']      = ' T ' # Tesla
  obj.unisi_title['e']      = ' J m^(-3) '
  obj.unisi_title['t']      = ' s ' # seconds

def convertcsgsi(obj):
  
  import scipy.constants as const

  '''
  Conversion from cgs units to SI
  '''

  obj.unisi={}
  obj.unisi['proton'] = 1.67262158e-27 # kg
  #obj.uni['kboltz'] = 1.380658e-16 
  obj.unisi['c']      = 299792.458 * 1e3 #m/s
  obj.unisi['tg']     = obj.uni['tg'] # K
  obj.unisi['t']      = obj.uni['t'] # seconds
  obj.unisi['l']      = obj.uni['l'] * const.centi # m
  obj.unisi['j']      = 1.0 # current density  

  try:  
      obj.unisi['rho']    = obj.uni['rho'] * const.gram / const.centi**3 # kg m^-3 
      obj.unisi['pg']     = obj.unisi['rho'] * (obj.unisi['l'] / obj.unisi['t'])**2
      obj.unisi['u']      = obj.uni['u'] * const.centi # m/s
      obj.unisi['ee']     = obj.unisi['u']**2
      obj.unisi['e']      = obj.unisi['rho'] * obj.unisi['ee'] 
      obj.unisi['b']      = obj.uni['b'] * 1e-4 # T
  except Exception:  
    if obj.verbose: 
        print('Some unisi did not run')


def globalvars(obj):
    
  import scipy.constants as const
  from astropy import constants as aconst
  from astropy import units
  
  '''
  global units
  '''

  obj.mu = 0.8
  obj.k_b = aconst.k_B.to_value('erg/K')  # 1.380658E-16 Boltzman's cst. [erg/K]
  obj.m_h = const.m_n / const.gram        # 1.674927471e-24
  obj.m_he = 6.65e-24
  obj.m_p = obj.mu * obj.m_h            # Mass per particle
  obj.m_e = aconst.m_e.to_value('g')

  obj.ksi_b = aconst.k_B.to_value('J/K')               # Boltzman's cst. [J/K]
  obj.msi_h = const.m_n                                # 1.674927471e-27
  obj.msi_he = 6.65e-27
  obj.msi_p = obj.mu * obj.msi_h                     # Mass per particle
  obj.msi_e = const.m_e  # 9.1093897e-31

  # Solar gravity
  obj.gsun = (aconst.GM_sun / aconst.R_sun**2).cgs.value  # solar surface gravity

  # --- physical constants and other useful quantities
  obj.clight = aconst.c.to_value('cm/s')   # Speed of light [cm/s]
  obj.hplanck = aconst.h.to_value('erg s') # Planck's constant [erg s]
  obj.hplancksi = aconst.h.to_value('J s') # Planck's constant [erg s]
  obj.kboltzmann = aconst.k_B.to_value('erg/K')  # Boltzman's cst. [erg/K]
  obj.amu = aconst.u.to_value('g')        # Atomic mass unit [g]
  obj.amusi = aconst.u.to_value('kg')     # Atomic mass unit [kg]
  obj.m_electron = aconst.m_e.to_value('g')  # Electron mass [g]
  obj.q_electron = aconst.e.esu.value     # Electron charge [esu]
  obj.qsi_electron = aconst.e.value       # Electron charge [C]
  obj.rbohr = aconst.a0.to_value('cm')    #  bohr radius [cm]
  obj.e_rydberg = aconst.Ryd.to_value('erg', equivalencies=units.spectral())
  obj.eh2diss = 4.478007          # H2 dissociation energy [eV]
  obj.pie2_mec = (np.pi * aconst.e.esu **2 / (aconst.m_e * aconst.c)).cgs.value
  # 5.670400e-5 Stefan-Boltzmann constant [erg/(cm^2 s K^4)]
  obj.stefboltz = aconst.sigma_sb.cgs.value
  obj.mion = obj.m_h            # Ion mass [g]
  obj.r_ei = 1.44E-7        # e^2 / kT = 1.44x10^-7 T^-1 cm
  obj.mu0si = aconst.mu0.to_value('N/A2')  # magnetic constant [SI units]

  # --- Aliases, for convenience
  obj.msi_electron = obj.msi_e
  obj.m_e = obj.m_electron
  obj.q_e = obj.q_electron
  obj.qsi_e = obj.qsi_electron

  # --- Unit conversions
  obj.ev_to_erg = units.eV.to('erg')
  obj.ev_to_j = units.eV.to('J')
  obj.nm_to_m = const.nano   # 1.0e-09
  obj.cm_to_m = const.centi  # 1.0e-02
  obj.km_to_m = const.kilo   # 1.0e+03
  obj.erg_to_joule = const.erg  # 1.0e-07
  obj.g_to_kg = const.gram   # 1.0e-03
  obj.micron_to_nm = units.um.to('nm')
  obj.megabarn_to_m2 = units.Mbarn.to('m2')
  obj.atm_to_pa = const.atm  # 1.0135e+05 atm to pascal (n/m^2)
  obj.dyne_cm2_to_pascal = (units.dyne / units.cm**2).to('Pa')
  obj.k_to_ev = units.K.to('eV', equivalencies=units.temperature_energy())
  obj.ev_to_k = 1. / obj.k_to_ev
  obj.ergd2wd = 0.1
  obj.grph = 2.27e-24
  obj.permsi = aconst.eps0.value  # Permitivitty in vacuum (F/m)
  obj.cross_p = 1.59880e-14
  obj.cross_he = 9.10010e-17

  # Dissociation energy of H2 [eV] from Barklem & Collet (2016)
  obj.di = obj.eh2diss

  obj.atomdic = {'h': 1, 'he': 2, 'c': 3, 'n': 4, 'o': 5, 'ne': 6, 'na': 7,
             'mg': 8, 'al': 9, 'si': 10, 's': 11, 'k': 12, 'ca': 13,
             'cr': 14, 'fe': 15, 'ni': 16}
  obj.abnddic = {'h': 12.0, 'he': 11.0, 'c': 8.55, 'n': 7.93, 'o': 8.77,
             'ne': 8.51, 'na': 6.18, 'mg': 7.48, 'al': 6.4, 'si': 7.55,
             's': 5.21, 'k': 5.05, 'ca': 6.33, 'cr': 5.47, 'fe': 7.5,
             'ni': 5.08}
  obj.weightdic = {'h': 1.008, 'he': 4.003, 'c': 12.01, 'n': 14.01,
               'o': 16.00, 'ne': 20.18, 'na': 23.00, 'mg': 24.32,
               'al': 26.97, 'si': 28.06, 's': 32.06, 'k': 39.10,
               'ca': 40.08, 'cr': 52.01, 'fe': 55.85, 'ni': 58.69}
  obj.xidic = {'h': 13.595, 'he': 24.580, 'c': 11.256, 'n': 14.529,
           'o': 13.614, 'ne': 21.559, 'na': 5.138, 'mg': 7.644,
           'al': 5.984, 'si': 8.149, 's': 10.357, 'k': 4.339,
           'ca': 6.111, 'cr': 6.763, 'fe': 7.896, 'ni': 7.633}
  obj.u0dic = {'h': 2., 'he': 1., 'c': 9.3, 'n': 4., 'o': 8.7,
           'ne': 1., 'na': 2., 'mg': 1., 'al': 5.9, 'si': 9.5, 's': 8.1,
           'k': 2.1, 'ca': 1.2, 'cr': 10.5, 'fe': 26.9, 'ni': 29.5}
  obj.u1dic = {'h': 1., 'he': 2., 'c': 6., 'n': 9.,  'o': 4.,  'ne': 5.,
           'na': 1., 'mg': 2., 'al': 1., 'si': 5.7, 's': 4.1, 'k': 1.,
           'ca': 2.2, 'cr': 7.2, 'fe': 42.7, 'ni': 10.5}


''' --------------------------- coordinate transformations --------------------------- '''

def polar2cartesian(r, t, grid, x, y, order=3):
    '''
    Converts polar grid to cartesian grid
    '''
    
    X, Y = np.meshgrid(x, y)

    new_r = np.sqrt(X * X + Y * Y)
    new_t = np.arctan2(X, Y)

    ir = interpolate.interp1d(r, np.arange(len(r)), bounds_error=False, fill_value=0.0)
    it = interpolate.interp1d(t, np.arange(len(t)), bounds_error=False, fill_value=0.0)
    new_ir = ir(new_r.ravel())
    new_it = it(new_t.ravel())

    new_ir[new_r.ravel() > r.max()] = len(r) - 1
    new_ir[new_r.ravel() < r.min()] = 0

    return ndimage.map_coordinates(grid, np.array([new_ir, new_it]),
                           order=order).reshape(new_r.shape)

def cartesian2polar(x, y, grid, r, t, order=3):
    '''
    Converts cartesian grid to polar grid
    '''

    R, T = np.meshgrid(r, t)

    new_x = R * np.cos(T)
    new_y = R * np.sin(T)

    ix = interpolate.interp1d(x, np.arange(len(x)), bounds_error=False)
    iy = interpolate.interp1d(y, np.arange(len(y)), bounds_error=False)

    new_ix = ix(new_x.ravel())
    new_iy = iy(new_y.ravel())

    new_ix[new_x.ravel() > x.max()] = len(x) - 1
    new_ix[new_x.ravel() < x.min()] = 0

    new_iy[new_y.ravel() > y.max()] = len(y) - 1
    new_iy[new_y.ravel() < y.min()] = 0

    return ndimage.map_coordinates(grid, np.array([new_ix, new_iy]),
                           order=order).reshape(new_x.shape)


def refine(s,q,factor=2,unscale=lambda x:x):
    """
    Given 1D function q(s), interpolate so we have factor x many points.
    factor = 2 by default
    """
    ds = s[-1]-s[0]
    ss = np.arange(factor*len(s)+1)/(factor*len(s))*ds+s[0]
    if ds > 0.0:
        qq = unscale(np.interp(ss, s, q))
        return ss, qq
    elif ds < 0.0:
        qq = unscale(np.interp(ss[::-1], s[::-1], q[::-1]))
        qq = qq[::-1]
        return ss, qq


''' --------------------------- info about arrays --------------------------- '''

def stats(arr, advanced=True, finite_only=True):
    '''return dict with min, mean, max.
    if advanced, also include:
        std, median, size, number of non-finite points (e.g. np.inf or np.nan).
    if finite_only:
        only treat the finite parts of arr; ignore nans and infs.
    '''
    arr = arr_orig = np.asanyarray(arr)
    if finite_only or advanced:  # then we need to know np.isfinite(arr)
        finite = np.isfinite(arr)
        n_nonfinite = arr.size - np.count_nonzero(finite)
    if finite_only and n_nonfinite > 0:
        arr = arr[finite]
    result = dict(min=np.nanmin(arr), mean=np.nanmean(arr), max=np.nanmax(arr))
    if advanced:
        result.update(dict(std=np.nanstd(arr), median=np.nanmedian(arr),
                           size=arr.size, nonfinite=n_nonfinite))
    return result

def print_stats(arr_or_stats, advanced=True, fmt='{: .2e}', sep=' | ', return_str=False):
    '''calculate and prettyprint stats about array.
    arr_or_stats: dict (stats) or array-like.
        dict --> treat dict as stats of array.
        array --> calculate stats(arr, advanced=advanced)
    fmt: str
        format string for each stat.
    sep: str
        separator string between each stat.
    return_str: bool
        whether to return string instead of printing.
    '''
    fmtkey = '{:>6s}' if '\n' in sep else '{}'
    _stats = arr_or_stats if isinstance(arr_or_stats, dict) else stats(arr_or_stats, advanced=advanced)
    result = sep.join([f'{fmtkey.format(key)}: {fmt.format(val)}' for key, val in _stats.items()])
    return result if return_str else print(result)

def finite_op(arr, op):
    '''returns op(arr), hitting only the finite values of arr.
    if arr has only finite values,
        finite_op(arr, op) == op(arr).
    if arr has some nonfinite values (infs or nans),
        finite_op(arr, op) == op(arr[np.isfinite(arr)])
    '''
    arr = np.asanyarray(arr)
    finite = np.isfinite(arr)
    if np.count_nonzero(finite) < finite.size:
        return op(arr[finite])
    else:
        return op(arr)

def finite_min(arr):
    '''returns min of all the finite values of arr.'''
    return finite_op(arr, np.min)

def finite_mean(arr):
    '''returns mean of all the finite values of arr.'''
    return finite_op(arr, np.mean)

def finite_max(arr):
    '''returns max of all the finite values of arr.'''
    return finite_op(arr, np.max)

def finite_std(arr):
    '''returns std of all the finite values of arr.'''
    return finite_op(arr, np.std)

def finite_median(arr):
    '''returns median of all the finite values of arr.'''
    return finite_op(arr, np.median)


''' --------------------------- strings --------------------------- '''

def pretty_nbytes(nbytes, fmt='{:.2f}'):
  '''returns nbytes as a string with units for improved readability.
  E.g. pretty_nbytes(20480, fmt='{:.1f}') --> '10.0 kB'.
  '''
  n_u_bytes = nbytes
  u = ''
  for u_next in ['k', 'M', 'G', 'T']:
    n_next = n_u_bytes / 1024
    if n_next < 1:
      break
    else:
      n_u_bytes = n_next
      u = u_next
  return '{fmt} {u}B'.format(fmt=fmt, u=u).format(n_u_bytes)


''' --------------------------- import error handling --------------------------- '''

class ImportFailedError(ImportError):
  pass

class ImportFailed():
  '''set modules which fail to import to be instances of this class;
  initialize with modulename, additional_error_message.
  when attempting to access any attribute of the ImportFailed object,
    raises ImportFailedError('. '.join(modulename, additional_error_message)).
  Also, if IMPORT_FAILURE_WARNINGS, make warning immediately when initialized.

  Example:
  try:
    import zarr
  except ImportError:
    zarr = ImportFailed('zarr', 'This module is required for compressing data.')

  zarr.load(...)   # << attempt to use zarr
  # if zarr was imported successfully, it will work fine.
  # if zarr failed to import, this error will be raised:
  >>> ImportFailedError: zarr. This module is required for compressing data.
  '''
  def __init__(self, modulename, additional_error_message=''):
    self.modulename = modulename
    self.additional_error_message = additional_error_message
    if IMPORT_FAILURE_WARNINGS:
      warnings.warn(f'Failed to import module {modulename}.{additional_error_message}')

  def __getattr__(self, attr):
    str_add = str(self.additional_error_message)
    if len(str_add) > 0:
      str_add = '. ' + str_add
    raise ImportFailedError(self.modulename + str_add)


''' --------------------------- vector rotations --------------------------- '''

def rotation_align(vecs_source, vecs_destination):
  ''' Return the rotation matrix which aligns vecs_source to vecs_destination.
  
  vecs_source, vecs_destination: array of vectors, or length 3 list of scalars
      array of 3d vectors for source, destination.
      Both will be cast to numpy arrays via np.asarray.
      The inputs can be any number of dimensions,
          but the last dimension should represent x,y,z,
          E.g. the shape should be (..., 3).
      NOTE: the np.stack function may be helpful in constructing this input.
          E.g. for Bx, By, Bz, use np.stack([Bx, By, Bz], axis=-1).
          This works for any same-shaped Bx, By, Bz arrays or scalars.
          
  Note: a divide by 0 error indicates that at least one of the rotations will be -I;
      i.e. the vectors were originally parallel, but in opposite directions.
  
  Returns: array which, when applied to vecs_source, aligns them with vecs_destination.
      The result will be an array of 3x3 matrices.
      For applying the array, see rotation_apply(), or use:
          np.sum(result * np.expand_dims(vec, axis=(-2)), axis=-1)
          
  Example:
  # Bx, By, Bz each have shape (100, 70, 50), and represent the x, y, z components of B.
  # ux, uy, uz each have shape (100, 70, 50), and represent the x, y, z components of u.
  B_input = np.stack([Bx, By, Bz], axis=-1)  # >> B_input has shape (100, 70, 50, 3)
  u_input = np.stack([ux, uy, uz], axis=-1)  # >> u_input has shape (100, 70, 50, 3)
  d_input = [0, 0, 1]                        # "rotate to align with z"
  result = rotation_align(B_input, d_input)  # >> result has shape (100, 70, 50, 3, 3)
  # << result tells how to rotate such that B aligns with z
  rotation_apply(result, B_input)
  # >>> matrix of [Bx', By', Bz'], which has Bx' == By' == 0, Bz' == |B|
  rotation_apply(result, u_input)
  # >>> matrix of [ux', uy', uz'], where u' is in the coord. system with B in the z direction.
  
  # instead of rotation_apply(v1, v2), can use np.sum(v1 * np.expand_dims(v2, axis=(-2)), axis=-1).
  
  Rotation algorithm based on Rodrigues's rotation formula.
  Adapted from https://stackoverflow.com/a/59204638
  '''
  # bookkeeping - whether to treat as masked arrays
  if np.ma.isMaskedArray(vecs_source) or np.ma.isMaskedArray(vecs_destination):
    stack   = np.ma.stack
    asarray = np.ma.asarray
  else:
    stack   = np.stack
    asarray = np.asarray
  # bookkeeping - dimensions
  vec1 = asarray(vecs_source)
  vec2 = asarray(vecs_destination)
  vec1 = np.expand_dims(vec1, axis=tuple(range(0, vec2.ndim - vec1.ndim)))
  vec2 = np.expand_dims(vec2, axis=tuple(range(0, vec1.ndim - vec2.ndim)))
  # magnitudes, products
  mag = lambda u: np.linalg.norm(u, axis=-1, keepdims=True)   # magnitude of u with vx, vy, vz = v[...,0], v[...,1], v[...,2]
  a = vec1 / mag(vec1)
  b = vec2 / mag(vec2)
  def cross(a, b):
    '''takes the cross product along the last axis.
    np.cross(a, b, axis=-1) can't handle masked arrays so we write out the cross product explicitly here.
    '''
    ax, ay, az = a[..., 0], a[..., 1], a[..., 2]
    bx, by, bz = b[..., 0], b[..., 1], b[..., 2]
    rx = ay * bz - az * by
    ry = az * bx - ax * bz
    rz = ax * by - ay * bx
    return stack([rx, ry, rz], axis=-1)

  v = cross(a, b)  # a x b,  with axis -1 looping over x, y, z.
  c = np.sum(a * b, axis=-1)   # a . b,  with axis -1 looping over x, y, z.
  # building kmat
  v_x, v_y, v_z = (v[...,i] for i in (0,1,2))
  zero = np.zeros_like(v_x)
  kmat = stack([
                stack([zero, -v_z,  v_y], axis=-1),
                stack([ v_z, zero, -v_x], axis=-1),
                stack([-v_y,  v_x, zero], axis=-1),
               ], axis=-2)
  _I = np.expand_dims(np.eye(3), axis=tuple(range(0, kmat.ndim - 2)))
  _c = np.expand_dims(c, axis=tuple(range(np.ndim(c), kmat.ndim)))     # _c = c with dimensions added appropriately.
  # implementation of Rodrigues's formula
  result = _I + kmat + np.matmul(kmat, kmat) * 1 / (1 + _c)   # ((1 - c) / (s ** 2))    # s**2 = 1 - c**2    (s ~ sin, c ~ cos)      # s := mag(v)

  # handle the c == -1 case.  wherever c == -1, vec1 and vec2 are parallel with vec1 == -1 * vec2.
  flipvecs = (c == -1)
  result[flipvecs,:,:] = -1 * np.eye(3)
  return result

def rotation_apply(rotations, vecs):
  '''apply the rotations to vecs.
  
  rotations: array of 3x3 rotation matrices.
      should have shape (..., 3, 3)
  vecs: array of vectors.
      should have shape (..., 3)
      
  shapes should be consistent,
  E.g. rotations with shape (10, 7, 3, 3), vecs with shape (10, 7, 3).
  
  returns rotated vectors.
  '''
  return np.sum(rotations * np.expand_dims(vecs, axis=(-2)), axis=-1)


''' --------------------------- plotting --------------------------- '''

def extent(xcoords, ycoords):
  '''returns extent (to go to imshow), given xcoords, ycoords. Assumes origin='lower'.
  Use this method to properly align extent with middle of pixels.
  (Noticeable when imshowing few enough pixels that individual pixels are visible.)
  
  xcoords and ycoords should be arrays.
  (This method uses their first & last values, and their lengths.)

  returns extent == np.array([left, right, bottom, top]).
  '''
  Nx = len(xcoords)
  Ny = len(ycoords)
  dx = (xcoords[-1] - xcoords[0])/Nx
  dy = (ycoords[-1] - ycoords[0])/Ny
  return np.array([*(xcoords[0] + np.array([0 - dx/2, dx * Nx + dx/2])),
                   *(ycoords[0] + np.array([0 - dy/2, dy * Ny + dy/2]))])