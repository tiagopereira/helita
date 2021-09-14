"""
Set of programs to read and interact with output from Multifluid/multispecies


TODO:
    Fix the memory leak...
        The following code:
            dd = eb.EbysusData(...)
            del dd
        does not actually free the dd object. It does not run dd.__del__().
        This can be proven by defining EbysusData.__del__() to print something
        (which is what happens if you edit file_memory.py to set DEBUG_MEMORY_LEAK=True).
        You can also turn off all the file_memory.py caches and memory by
        setting a flag when initializing dd: dd = eb.EbysusData(..., _force_disable_memory=True).

        This leak could be caused by an attribute of dd pointing to dd without using weakref.

        It is also possible that there isn't a leak, because Python can collect objects in circular
        reference chains as long as none of the objects in the chain have defined a __del__ method.
        So it is possible that there is a circular reference which gets collected when __del__ is
        not defined (when DEBUG_MEMORY_LEAK=False), but then can't get collected when __del__ is defined...

        A short-term solution is to hope python's default garbage collection routines
        will collect the garbage often enough, or to do import gc; and gc.collect() sometimes.

        In the long-term, we should find which attribute of dd points to dd, and fix it.

"""

# import built-in modules
import os
import time
import warnings
import collections

# import local modules
from .bifrost import (
    BifrostData, Rhoeetab, Bifrost_units, Cross_sect,
    read_idl_ascii, subs2grph,
)
from .load_mf_quantities         import load_mf_quantities
from .load_quantities            import load_quantities
from .load_arithmetic_quantities import load_arithmetic_quantities
from .load_fromfile_quantities   import load_fromfile_quantities
from . import document_vars
from . import file_memory
from . import fluid_tools
from .units import (
    UNI, USI, UCGS, UCONST,
    Usym, Usyms, UsymD,
    U_TUPLE,
    UNI_length, UNI_time, UNI_mass,
    UNI_speed, UNI_rho, UNI_nr, UNI_hz
)
try:
  from . import cstagger
except ImportError:
  warnings.warn("failed to import helita.sim.cstagger; running stagger with stagger_kind='cstagger' will crash.")

# import external public modules
import numpy as np

# import external private modules
try:
    from at_tools import atom_tools as at
except ImportError:
    warnings.warn('failed to import at_tools.atom_tools; some functions in helita.sim.ebysus may crash')
try:
    from at_tools import fluids as fl
except ImportError:
    warnings.warn('failed to import at_tools.fluids; some functions in helita.sim.ebysus may crash')

# set defaults:
from .load_arithmetic_quantities import (
    DEFAULT_STAGGER_KIND,
)
from .load_mf_quantities import (
    MATCH_PHYSICS, MATCH_AUX
)
MATCH_TYPE_DEFAULT = MATCH_PHYSICS  # can change this one. Tells whether to match physics or aux.
                               # match physics -> try to return physical value.
                               # match aux     -> try to return value matching aux.
## list of functions from fluid_tools which will be set as methods of the EbysusData class.
## for example, for dd=EbysusData(...),
### dd.get_mass(*args, **kw) == fluid_tools.get_mass(dd, *args, **kw).
FLUIDTOOLS_EBYSUSDATA_FUNCS = \
    ['get_species_name', 'get_fluid_name', 'get_mass', 'get_charge',
    'get_cross_tab', 'get_cross_sect', 'get_coll_type',
    'i_j_same_fluid', 'iter_fluid_SLs']

AXES = ('x', 'y', 'z')


class EbysusData(BifrostData):

    """
    Class to hold data from Multifluid/multispecies simulations
    in native format.
    """

    def __init__(self, *args, fast=True, match_type=MATCH_TYPE_DEFAULT,
                 N_memmap=200, mm_persnap=True, 
                 do_caching=True, cache_max_MB=10, cache_max_Narr=20,
                 _force_disable_memory=False,
                 **kwargs):
        ''' initialize EbysusData object.

        N_memmap: int (default 0)
            keep the N_memmap most-recently-created memmaps stored in self._memory_numpy_memmap.
            -1  --> try to never forget any memmaps.
                    May increase (for this python session) the default maximum number of files
                    allowed to be open simultaneously. Tries to be conservative about doing so.
                    See file_memory.py for more details.
            0   --> never remember any memmaps.
                    Turns off remembering memmaps.
                    Not recommended; causes major slowdown.
            >=1 --> remember up to this many memmaps.

        mm_persnap: True (default) or False
            whether to delete all memmaps in self._memory_memmap when we set_snap to a new snap.

        fast: True (default) or False
            whether to be fast.
            True -> don't create memmaps for all simple variables when snapshot changes.
            False -> do create memmaps for all simple variables when snapshot changes.
                     Not recommended; causes major slowdown.
                     This option is included in case legacy code assumes values
                     via self.var, or self.variables[var], instead of self.get_var(var).
                     As long as you use get_var to get var values, you can safely use fast=True.

        match_type: 0 (default) or 1
            whether to try to match physical answer (0) or aux data (1).
            Applicable to terms which can be turned on or off. e.g.:
            if do_hall='false':
                match_type=0 --> return result as if do_hall is turned on. (matches actual physics)
                match_type=1 --> return result as if do_hall is off. (matches aux file data)
            Only applies when explicitly implemented in load quantities files, e.g. load_mf_quantities.

        do_caching: True (default) or False
            whether to allow any type of caching (maintaining a short list of recent results of get_var).
            if False, the with_caching() function will skip caching and self.cache will be ignored.
            can be enabled or disabled at any point; does not erase the current cache.
        cache_max_MB: 10 (default) or number
            maximum number of MB of data which cache is allowed to store at once.
        cache_max_Narr: 20 (default) or number
            maximum number of arrays which cache is allowed to store at once.

        _force_disable_memory: False (default) or True
            if True, disable ALL code from file_memory.py.
            Very inefficient; however, it is useful for debugging file_memory.py.

        *args and **kwargs go to helita.sim.bifrost.BifrostData.__init__
        '''
        # set values of some attrs (e.g. from args & kwargs passed to __init__)
        self.match_type = match_type

        setattr(self, file_memory.NMLIM_ATTR, N_memmap)
        setattr(self, file_memory.MM_PERSNAP, mm_persnap)

        self.do_caching = do_caching and not _force_disable_memory
        self._force_disable_memory = _force_disable_memory
        if not _force_disable_memory:
            self.cache  = file_memory.Cache(obj=self, max_MB=cache_max_MB, max_Narr=cache_max_Narr)
        self.caching    = lambda: self.do_caching and not self.cache.is_NoneCache()  # (used by load_mf_quantities)
        setattr(self, document_vars.LOADING_LEVEL, -1) # tells how deep we are into loading a quantity now.

        self.panic=False

        # figure out snapname. If it doesn't agree with snapname (optionally) entered in args, crash.
        with EnterDirectory(kwargs.get('fdir', os.curdir)):
            snapname = get_snapname()
        if len(args) >= 1:
            if args[0] != snapname:
                snapname_errmsg = "snapname from args ('{}') disagrees with snapname from mhd.in ('{}')!"
                # it will read from arg and won't raise error if mhd.in does not match args. 
                warnings.warn(snapname_errmsg.format(args[0], snapname))
                snapname = args[0]
                #raise ValueError(snapname_errmsg.format(args[0], snapname))


        # call BifrostData.__init__
        super(EbysusData, self).__init__(snapname,*args[1:], fast=fast, **kwargs)

        # set up self.att
        self.att = {}
        tab_species = self.mf_tabparam['SPECIES']
        self.mf_nspecies = len(tab_species)
        self.mf_total_nlevel=0
        for row in tab_species:
            # example row looks like: ['01', 'H', 'H_2.atom']
            mf_ispecies = int(row[0])
            self.att[mf_ispecies] = at.Atom_tools(atom_file=row[2], fdir=self.fdir)
            self.mf_total_nlevel += self.att[mf_ispecies].params.nlevel

        # read minimal amounts of data, to finish initializing.
        self._init_vars_get(firstime=True)
        self._init_coll_keys()



    def _init_coll_keys(self):
        '''initialize self.coll_keys as a dict for better efficiency when looking up collision types.
        self.coll_keys will be a dict with keys (ispecies, jspecies) values (collision type).
        collision types are:
            'CL' ("coulomb"; whether coulomb collisions are allowed between these species)
            'EL' ("elastic"; previous default in ebysus)
            'MX' ("maxwell"; this one is usable even if we don't have cross section file)
            Note that MX and EL are (presently) mutually exclusive.
        '''
        _enforce_symmetry_in_collisions = False
        # ^^ whether to manually put   (B,A):value   if  (A,B):value    is in coll_keys.
        # disabled now because presently, ebysus simulation does not enforce
        # that symmetry; e.g. it is possible to have (1,2):'EL' and (2,1):'MX',
        # though I don't know what that combination would mean...  - SE May 26 2021

        # begin processing:
        result = dict()
        if 'COLL_KEYS' in self.mf_tabparam: 
            x = self.mf_tabparam['COLL_KEYS']
            for tokenline in x:      # example tokenline: ['01', '02', 'EL']
                ispec, jspec, collkey = tokenline
                ispec, jspec = int(ispec), int(jspec)
                key = (ispec, jspec)
                try:
                    result[key] += [collkey]
                except KeyError:
                    result[key] = [collkey]
        if _enforce_symmetry_in_collisions:
            for key in list(result.keys()): #list() because changing size of result
                rkey = (key[1], key[0])  # reversed
                if rkey not in result.keys():
                    result[rkey] = result[key]

        self.coll_keys = result

    def _set_snapvars(self,firstime=False):

        if os.path.exists('%s.io' % self.file_root):
            self.snaprvars = ['r']
            self.snappvars = ['px', 'py', 'pz']
        else:
            self.snapvars = ['r', 'px', 'py', 'pz']

        self.snapevars = ['e']
        self.mhdvars = []
        if (self.do_mhd):
            self.mhdvars = ['bx', 'by', 'bz']
        self.auxvars = self.params['aux'][self.snapInd].split()

        self.compvars = ['ux', 'uy', 'uz', 's', 'ee']

        self.varsmfc = [v for v in self.auxvars if v.startswith('mfc_')]
        self.varsmf = [v for v in self.auxvars if v.startswith('mf_')]
        self.varsmm = [v for v in self.auxvars if v.startswith('mm_')]
        self.varsmfr = [v for v in self.auxvars if v.startswith('mfr_')]
        self.varsmfp = [v for v in self.auxvars if v.startswith('mfp_')]
        self.varsmfe = [v for v in self.auxvars if v.startswith('mfe_')]

        if (self.mf_epf):
            # add internal energy to basic snaps
            #self.snapvars.append('e')
            # make distiction between different aux variable
            self.mf_e_file = self.root_name + '_mf_e'
        else:  # one energy for all fluid
            self.mhdvars.insert(0, 'e')
            self.snapevars = []

        if hasattr(self, 'with_electrons'):
            if self.with_electrons:
                self.mf_e_file = self.root_name + '_mf_e'
                # JMS This must be implemented
                self.snapelvars=['r', 'px', 'py', 'pz', 'e']

        for var in (
                self.varsmfr +
                self.varsmfp +
                self.varsmfe +
                self.varsmfc +
                self.varsmf +
                self.varsmm):
            self.auxvars.remove(var)

        #if hasattr(self, 'mf_total_nlevel'):
        #    if self.mf_total_nlevel == 1:
        #        self.snapvars.append('e')

        if os.path.exists('%s.io' % self.file_root):
            self.simple_vars = self.snaprvars + self.snappvars + \
                self.snapevars + self.mhdvars + self.auxvars + \
                self.varsmf + self.varsmfr + self.varsmfp + self.varsmfe + \
                self.varsmfc + self.varsmm
        else:
            self.simple_vars = self.snapvars + self.snapevars + \
                self.mhdvars + self.auxvars + self.varsmf + \
                self.varsmfr + self.varsmfp + self.varsmfe + \
                self.varsmfc + self.varsmm

        self.auxxyvars = []
        # special case for the ixy1 variable, lives in a separate file
        if 'ixy1' in self.auxvars:
            self.auxvars.remove('ixy1')
            self.auxxyvars.append('ixy1')

        for var in self.auxvars:
            if any(i in var for i in ('xy', 'yz', 'xz')):
                self.auxvars.remove(var)
                self.vars2d.append(var)

        '''self.compvars = ['ux', 'uy', 'uz', 's', 'rup', 'dxdbup', 'dxdbdn',
                            'dydbup', 'dydbdn', 'dzdbup', 'dzdbdn', 'modp']
        if (self.do_mhd):
            self.compvars = self.compvars + ['bxc', 'byc', 'bzc', 'modb']'''

    def set_snap(self,snap,*args__set_snap,**kwargs__set_snap):
        '''call set_snap from BifrostData,
        but also if mm_persnap, then delete all the memmaps in memory..
        '''
        if getattr(self, file_memory.MM_PERSNAP, False) and np.shape(self.snap)==():
            if hasattr(self, file_memory.MEMORY_MEMMAP):
                delattr(self, file_memory.MEMORY_MEMMAP)
        super(EbysusData, self).set_snap(snap, *args__set_snap, **kwargs__set_snap)

    def _read_params(self,firstime=False):
        ''' Reads parameter file specific for Multi Fluid Bifrost '''
        super(EbysusData, self)._read_params(firstime=firstime)

        self.nspecies_max = 28
        self.nlevels_max = 28

        # get misc. params (these have no default values. Make error if we can't get them).
        errmsg = 'read_params: could not find {} in idl file!'
        self.mf_epf          = self.get_param('mf_epf',          error_prop=KeyError(errmsg.format('mf_epf'))          )
        self.mf_nspecies     = self.get_param('mf_nspecies',     error_prop=KeyError(errmsg.format('mf_nspecies'))     )
        self.with_electrons  = self.get_param('mf_electrons',    error_prop=KeyError(errmsg.format('mf_electrons'))    )
        self.mf_total_nlevel = self.get_param('mf_total_nlevel', error_prop=KeyError(errmsg.format('mf_total_nlevel')) )

        # get param_file params (these have default values).
        ## mf_param_file
        param_file = self.get_param('mf_param_file', default='mf_params.in',
                        warning='mf_param_file not found in this idl file; trying to use mf_params.in')
        file = os.path.join(self.fdir, param_file.strip())
        self.mf_tabparam = read_mftab_ascii(file, obj=self)
        ## mf_eparam_file
        do_ohm_ecol = self.get_param('do_ohm_ecol', 0)
        warning = 'mf_eparam_file parameter not found; trying to use mf_eparams.in' if do_ohm_ecol else None
        eparam_file = self.get_param('mf_eparam_file', default='mf_eparams.in', warning=warning)
        file = os.path.join(self.fdir, eparam_file.strip())
        try:
            self.mf_etabparam = read_mftab_ascii(file, obj=self)
        except FileNotFoundError:
            # if do_ohm_ecol, crash; otherwise quietly ignore error.
            if do_ohm_ecol:
                raise

    def _init_vars(self, firstime=False, fast=None, *args__get_simple_var, **kw__get_simple_var):
        """
        Initialises variables (common for all fluid)
        
        fast: None, True, or False.
            whether to only read density (and not all the other variables).
            if None, use self.fast instead.

        *args and **kwargs go to _get_simple_var
        """
        fast = fast if fast is not None else self.fast
        if self._fast_skip_flag is True:
            return
        elif self._fast_skip_flag is False:
            self._fast_skip_flag = True #swaps flag to True, then runs the rest of the code (this time around).
        #else, fast_skip_flag is None, so the code should never be skipped.
        #as long as fast is False, fast_skip_flag should be None.

        self.mf_common_file = (self.root_name + '_mf_common')
        if os.path.exists('%s.io' % self.file_root):
            self.mfr_file = (self.root_name + '_mfr_{iS:}_{iL:}')
            self.mfp_file = (self.root_name + '_mfp_{iS:}_{iL:}')
        else:
            self.mf_file = (self.root_name + '_mf_{iS:}_{iL:}')
        self.mfe_file = (self.root_name + '_mfe_{iS:}_{iL:}')
        self.mfc_file = (self.root_name + '_mfc_{iS:}_{iL:}')
        self.mm_file = (self.root_name + '_mm_{iS:}_{iL:}')
        self.mf_e_file = (self.root_name + '_mf_e')
        self.aux_file = (self.root_name)

        self.variables = {}

        self.set_mfi(None, None)
        self.set_mfj(None, None)

        if not firstime:
            self._init_vars_get(firstime=False, *args__get_simple_var, **kw__get_simple_var)
            
    def _init_vars_get(self, firstime=False, *args__get_simple_var, **kw__get_simple_var):
        '''get vars for _init_vars.'''
        varlist = ['r'] if self.fast else self.simple_vars
        for var in varlist:
            try:
                # try to get var via _get_simple_var.
                self.variables[var] = self._get_simple_var(var,
                    *args__get_simple_var, **kw__get_simple_var)
            except Exception as error:
                # if an error occurs, then...
                if var=='r' and firstime:
                    # RAISE THE ERROR
                    ## Many methods depend on self.r being set. So if we can't get it, the code needs to crash.
                    raise
                elif isinstance(error, ValueError) and (self.mf_ispecies < 0 or self.mf_ilevel < 0):
                    # SILENTLY HIDE THE ERROR.
                    ## We assume it came from doing something like get_var('r', mf_ispecies=-1),
                    ##  which is is _supposed_ to fail. We hope it came from that, at least....
                    ## To be cautious / help debugging, we will store any such errors in self._hidden_errors.
                    if not hasattr(self, '_hidden_errors'):
                        self._hidden_errors = []
                    if not hasattr(self, '_hidden_errors_max_len'):
                        self._hidden_errors_max_len = 100  # don't keep track of more than this many errors.
                    errmsg = "during _init_vars_get, with var='{}', {}".format(var, self.quick_look())
                    errmsg.format(var, self.snap, self.ifluid, self.jfluid)
                    self._hidden_errors += [(errmsg, error)]
                    if len(self._hidden_errors) > self._hidden_errors_max_len:
                        del self._hidden_errors[0]
                else:
                    # MAKE A WARNING but don't crash the code.
                    ## Note: warnings with the same exact contents will only appear once per session, by default.
                    ## You can change this behavior via, e.g.: import warnings; warnings.simplefilter('always')
                    errmsg = error if (self.verbose or firstime) else type(error).__name__
                    warnings.warn("init_vars failed to read variable '{}' due to: {}".format(var, errmsg))
            else:
                # if there was no error, then set self.var to the result.
                ## also set self.variables['metadata'] to self._metadata.
                ## this ensures we only pull data from self.variables when
                ## it is the correct snapshot, ifluid, and jfluid.
                setattr(self, var, self.variables[var])
                self.variables['metadata'] = self._metadata()

        rdt = self.r.dtype
        if self.stagger_kind == 'cstagger':
            if (self.nz>1):
                cstagger.init_stagger(self.nz, self.dx, self.dy, self.z.astype(rdt),
                                  self.zdn.astype(rdt), self.dzidzup.astype(rdt),
                                  self.dzidzdn.astype(rdt))
                self.cstagger_exists = True   # we can use cstagger methods!
            else:
                self.cstagger_exists = False
                #cstagger.init_stagger_mz1(self.nz, self.dx, self.dy, self.z.astype(rdt))
                #self.cstagger_exists = True  # we must avoid using cstagger methods.
        else: 
            self.cstagger_exists = True

    # fluid-setting functions
    set_mf_fluid = fluid_tools.set_mf_fluid
    set_mfi      = fluid_tools.set_mfi
    set_mfj      = fluid_tools.set_mfj
    set_fluids   = fluid_tools.set_fluids
    # docstrings for fluid-setting functions
    for func in [set_mf_fluid, set_mfi, set_mfj]:
        func.__doc__ = func.__doc__.replace('obj', 'self')

    del func # (we don't want func to remain in the EbysusData namespace beyond this point.)

    def _metadata(self, none=None, with_nfluid=2):
        '''returns dict of metadata for self. Including snap, ifluid, jfluid, and more.
        if self.snap is an array, set result['snaps']=snap and result['snap']=snaps[self.snapInd].

        none: any value (default None)
            metadata attrs which are not yet set will be set to this value.
        with_nfluid: 2 (default), 1, or 0.
            tells which fluids to include in the result.
            2 -> ifluid and jfluid. 1 -> just ifluid. 0 -> no fluids.
        '''
        METADATA_ATTRS = ['ifluid', 'jfluid', 'snap', 'iix', 'iiy', 'iiz', 'match_type', 'panic']
        if with_nfluid < 2:
            del METADATA_ATTRS[1]  # jfluid
        if with_nfluid < 1:
            del METADATA_ATTRS[0]  # ifluid
        # get attrs
        result = {attr: getattr(self, attr, none) for attr in METADATA_ATTRS}
        # if snap is array, set snaps=snap, and snap=snaps[self.snapInd]
        if result['snap'] is not none:
            if len(np.shape(result['snap'])) > 0:
                result['snaps'] = result['snap']              # snaps is the array of snaps
                result['snap'] = result['snap'][self.snapInd] # snap is the single snap
        return result

    def quick_look(self):
        '''returns string with snap, ifluid, and jfluid.'''
        x = self._metadata(none='(not set)')
        result = 'ifluid={}, jfluid={}, snap={}'.format(x['ifluid'], x['jfluid'], x['snap'])
        snaps = x.get('snaps', None)
        if snaps is not None:
            result += ', snaps={}'.format('<list of {} items from min={} to max={}>'.format(
                                        np.size(snaps), np.min(snaps), np.max(snaps)))
        return result

    def __repr__(self):
        '''makes prettier repr of self'''
        return '<{} with {}>'.format(object.__repr__(self), self.quick_look())

    def _metadata_is_consistent(self, alt_metadata, none=None):
        '''return whether alt_metadata is consistent with self._metadata().
        They "are consistent" if alt_metadata is a subset of self._metadata().
        i.e. if for all keys in alt_metadata, alt_metadata[key]==self._metadata[key].
        (Even works if contents are numpy arrays. See _dict_is_subset function for details.)
        '''
        return file_memory._dict_is_subset(alt_metadata, self._metadata(none=none))

    def _metadata_matches(self, alt_metadata, none=None):
        '''return whether alt_metadata matches self._metadata().
        They "match" if:
            for fluid (either ifluid or jfluid) which exists in alt_metadata,
                self._metadata()[fluid] must have the same value.
            all other keys in each dict are the same and have the same value.
        '''
        self_metadata = self._metadata(none=none)
        for ifluid in ['ifluid', 'jfluid']:
            SL = alt_metadata.get(ifluid, None)
            if SL is not None:
                if not fluid_tools.fluid_equals(SL, self_metadata[ifluid]):
                    return False
            #else: ifluid is not in alt_metadata, so it doesn't need to be in self_metadata.
        # << if we reached this line, then we know ifluid and jfluid "match" between alt and self.
        return file_memory._dict_equals(alt_metadata, self_metadata, ignore_keys=['ifluid', 'jfluid'])

    @fluid_tools.maintain_fluids
    @file_memory.maintain_attrs('match_type')
    @file_memory.with_caching(cache=False, check_cache=True, cache_with_nfluid=None)
    @document_vars.quant_tracking_top_level
    def _load_quantity(self, var, panic=False):
        '''helper function for get_var; actually calls load_quantities for var.
        Also, restores self.ifluid and self.jfluid afterwards.
        Also, restores self.match_type afterwards.
        '''
        __tracebackhide__ = True  # hide this func from error traceback stack
        # look for var in self.variables, if metadata is appropriate.
        if var in self.variables and self._metadata_matches(self.variables.get('metadata', dict())):
            return self.variables[var]
        
        # load quantities.
        val = load_fromfile_quantities(self, var, panic=panic, save_if_composite=False)
        if val is None:
            val = load_quantities(self, var, PLASMA_QUANT='',
                    CYCL_RES='', COLFRE_QUANT='', COLFRI_QUANT='',
                    IONP_QUANT='', EOSTAB_QUANT='', TAU_QUANT='',
                    DEBYE_LN_QUANT='', CROSTAB_QUANT='',
                    COULOMB_COL_QUANT='', AMB_QUANT='')
        if val is None:
            val = load_mf_quantities(self,var)
        if val is None:
            val = load_arithmetic_quantities(self,var)
        return val

    def get_var(self, var, snap=None, iix=None, iiy=None, iiz=None,
                mf_ispecies=None, mf_ilevel=None, mf_jspecies=None, mf_jlevel=None,
                ifluid=None, jfluid=None, panic=False, 
                match_type=None, check_cache=True, cache=False, cache_with_nfluid=None,
                *args, **kwargs):
        """
        Reads a given variable from the relevant files.

        >>> Use self.get_var('') for help.
        >>> Use self.vardocs() to prettyprint the available variables and what they mean.

        sets fluid-related attributes (e.g. self.ifluid) based on fluid-related kwargs.

        returns the data for the variable (as a 3D array with axes 0,1,2 <-> x,y,z).

        Parameters
        ----------
        var - string
            Name of the variable to read.
        snap - integer, optional
            Snapshot number to read. By default reads the loaded snapshot;
            if a different number is requested, will load that snapshot
            by running self.set_snap(snap).
        mf_ispecies - integer, or None (default)
            Species ID
            if None, set using other fluid kwargs (see ifluid, iSL, iS).
            if still None, use self.mf_ispecies
        mf_ilevel - integer, or None (default)
            Ionization level
            if None, set using other fluid kwargs (see ifluid, iSL, iL).
            if still None, use self.mf_ilevel
        ifluid - tuple of integers, or None (default)
            if not None: (mf_ispecies, mf_ilevel) = ifluid
        match_type - None (default), 0, or 1.
            whether to try to match physics (0) or aux (1) where applicable.
            see self.__init__.doc for more help.
        cache - False (default) or True
            whether to cache (store in memory) the result.
            (if result already in memory, bring to "front" of list.)
        check_cache - True (default) or False
            whether to check cache to see if the result already exists in memory.
            When possible, return existing result instead of repeating calculation.
        cache_with_nfluid - None (default), 0, 1, or 2
            if not None, cache result and associate it with this many fluids.
            0 -> neither; 1 -> just ifluid; 2 -> both ifluid and jfluid.
        **kwargs may contain the following:
            iSL    - alias for ifluid
            jSL    - alias for jfluid
            iS, iL - alias for ifluid[0], ifluid[1]
            jS, jL - alias for jfluid[0], jfluid[1]
        extra **kwargs are passed to NOWHERE.
        extra *args are passed to NOWHERE.
        """     

        if var == '' and not document_vars.creating_vardict(self):
            help(self.get_var)

        if var in AXES:
            return getattr(self, var)

        if match_type is not None:
            self.match_type = match_type

        # set fluids as appropriate to kwargs
        kw__fluids = dict(mf_ispecies=mf_ispecies, mf_ilevel=mf_ilevel, ifluid=ifluid,
                          mf_jspecies=mf_jspecies, mf_jlevel=mf_jlevel, jfluid=jfluid,
                          **kwargs)
        self.set_fluids(**kw__fluids)

        # set snapshot as needed
        if snap is not None:
            if not np.array_equal(snap, self.snap):
                self.set_snap(snap)
        self.panic=panic

        # set iix, iiy, iiz appropriately
        slices_names_and_vals = (('iix', iix), ('iiy', iiy), ('iiz', iiz))
        original_slice = [iix if iix is not None else getattr(self, slicename, slice(None))
                           for slicename, iix in slices_names_and_vals]
        self.set_domain_iiaxes(iix=iix, iiy=iiy, iiz=iiz, internal=True)

        # set caching kwargs appropriately (see file_memory.with_caching() for details.)
        kw__caching = dict(check_cache=check_cache, cache=cache, cache_with_nfluid=cache_with_nfluid)

        # >>>>> actually get the value of var <<<<<
        val = self._load_quantity(var, panic=panic, **kw__caching)

        # do post-processing (function is defined in bifrost.py)
        val = self._get_var_postprocess(val, var=var, original_slice=original_slice)
        return val

    @document_vars.quant_tracking_simple('SIMPLE_VARS')
    def _get_simple_var(self, var, order='F', mode='r', panic=False, *args, **kwargs):
        """
        Gets "simple" variable (ie, only memmap, not load into memory).

        Parameters:
        -----------
        var - string
            Name of the variable to read. Must be Bifrost internal names.
        order - string, optional
            Must be either 'C' (C order) or 'F' (Fortran order, default).
        mode - string, optional
            numpy.memmap read mode. By default is read only ('r'), but
            you can use 'r+' to read and write. DO NOT USE 'w+'.
        panic - False (default) or True.
            whether we are trying to read a '.panic' file.

        *args and **kwargs go to NOWHERE.

        Minor Deprecation Notice:
        -------------------------
        Support for entering fluids args/kwargs (mf_ispecies, mf_ilevel, mf_jspecies, mf_jlevel)
            directly into _get_simple_var has been deprecated as of July 6, 2021.
        As an alternative, use self.set_fluids() (or self.set_mfi() and self.set_mfj()),
            before calling self._get_simple_var().

        Returns
        -------
        result - numpy.memmap array
            Requested variable.
        """
        # handle documentation for simple_vars
        ## set documentation for vardict, if var == ''.
        if var == '':
            _simple_vars_msg = ('Quantities which are stored by the simulation. These are '
                                'loaded as numpy memmaps by reading data files directly.')
            docvar = document_vars.vars_documenter(self, 'SIMPLE_VARS', None, _simple_vars_msg)
            # TODO (maybe): ^^^ use self.simple_vars, instead of None, for QUANT_VARS (args[2])
            #    However, that might not be viable, depending on when self.simple_vars is assigned
            for x in AXES:
                docvar('b'+x, x+'-component of magnetic field [simu. units]',
                              nfluid=0, uni=U_TUPLE(UNI.b, UsymD(usi='T', ucgs='G')))
            docvar('r', 'mass density of ifluid [simu. units]', nfluid=1, uni=UNI_rho)
            for x in AXES:
                docvar('p'+x, x+'-component of momentum density of ifluid [simu. units]',
                              nfluid=1, uni=UNI_speed * UNI_rho)
            units_e = dict(uni_f=UNI.e, usi_name=Usym('J') / Usym('m')**3)  #ucgs_name= ???
            docvar('e', 'energy density of ifluid [simu. units]. Use -1 for electrons.',
                        nfluid=1, **units_e)
            return None

        if var not in self.simple_vars:
            return None

        # >>>>> here is where we decide which file and what part of the file to load as a memmap <<<<<
        filename, kw__get_mmap = self._get_simple_var_file_info(var, order=order, mode=mode, panic=panic, *args, **kwargs)
        
        # actually get the memmap and return result.
        result = get_numpy_memmap(filename, **kw__get_mmap)
        return result

    def _get_simple_var_file_info(self, var, order='F', mode='r', panic=False, *args, **kwargs):
        '''gets file info but does not read memmap; helper function for _get_simple_var.'''

        # set currSnap, currStr = (current single snap, string for this snap)
        if np.shape(self.snap) != ():  # self.snap is list; pick snapInd value from list.
            currSnap = self.snap[self.snapInd]
            currStr = self.snap_str[self.snapInd]
        else:                         # self.snap is single snap.
            currSnap = self.snap
            currStr = self.snap_str

        # check if we are reading .scr (snap < 0), snap0 (snap == 0), or "normal" snap (snap > 1)
        if currSnap > 0:      # reading "normal" snap
            _reading_scr = False
            #currStr = currStr
        elif currSnap == 0:   # reading snap0
            _reading_scr = False
            currStr = ''
        else: #currSnap < 0   # reading .scr
            _reading_scr = True
            currStr = ''

        self.mf_arr_size = 1
        iS = str(self.mf_ispecies).zfill(2)   # ispecies as str. min 2 digits. (E.g.  3 --> '03')
        iL = str(self.mf_ilevel).zfill(2)     # ilevel as str.   min 2 digits. (E.g. 14 --> '14')
        iSL = dict(iS=iS, iL=iL)

        # -------- figure out file name and idx (used to find offset in file). --------- #

        if os.path.exists('%s.io' % self.file_root):
            # in this case, we are reading an ebysus-like snapshot.
            _reading_ebysuslike_snap = True
            
            # check if var is a simple var from snaps.
            _reading_snap_not_aux = True       # whether we are reading '.snap' (not '.aux')
            if (var in self.mhdvars and self.mf_ispecies > 0) or (
                    var in ['bx', 'by', 'bz']):  # magnetic field, or a fluid-specific mhd simple variable)
                idx      = self.mhdvars.index(var)
                filename = os.path.join('mf_common', self.mf_common_file)
            elif var in self.snaprvars and self.mf_ispecies > 0:  # mass density (for non-electron fluid)
                idx      = self.snaprvars.index(var)
                filename = os.path.join('mf_{iS:}_{iL:}', 'mfr', self.mfr_file).format(**iSL)
            elif var in self.snappvars and self.mf_ispecies > 0:  # momentum density (for non-electron fluid)
                idx      = self.snappvars.index(var)
                filename = os.path.join('mf_{iS:}_{iL:}', 'mfp', self.mfp_file).format(**iSL)
            elif var in self.snapevars and self.mf_ispecies > 0:  # energy density (for non-electron fluid)
                idx      = self.snapevars.index(var)
                filename = os.path.join('mf_{iS:}_{iL:}', 'mfe', self.mfe_file).format(**iSL)
            elif var in self.snapevars and self.mf_ispecies < 0:  # energy density (for electrons)
                idx      = self.snapevars.index(var)
                filename = os.path.join('mf_e', self.mf_e_file)
            else: # var is not a simple var from snaps.
                # check if var is from aux.
                _reading_snap_not_aux = False  # we are reading '.aux' (not '.snap')
                if var in self.auxvars:    # global auxvars
                    idx      = self.auxvars.index(var)
                    filename = os.path.join('mf_common', self.aux_file)
                elif var in self.varsmf:   # ??
                    idx      = self.varsmf.index(var)
                    filename = os.path.join('mf_{iS:}_{iL:}', 'mfa', self.mf_file).format(**iSL)
                elif var in self.varsmfr:  # ??
                    idx      = self.varsmfr.index(var)
                    filename = os.path.join('mf_{iS:}_{iL:}', 'mfr',  self.mfr_file).format(**iSL)
                elif var in self.varsmfp:  # ??
                    idx      = self.varsmfp.index(var)
                    filename = os.path.join('mf_{iS:}_{iL:}', 'mfp',  self.mfp_file).format(**iSL)
                elif var in self.varsmfe:  # ??
                    idx      = self.varsmfe.index(var)
                    filename = os.path.join('mf_{iS:}_{iL:}', 'mfe',  self.mfe_file).format(**iSL)
                elif var in self.varsmfc:  # ??
                    idx      = self.varsmfc.index(var)
                    filename = os.path.join('mf_{iS:}_{iL:}', 'mfc',  self.mfc_file).format(**iSL)
                elif var in self.varsmm:   # two-fluid auxvars, e.g. mm_cross.
                    idx      = self.varsmm.index(var)
                    filename = os.path.join('mf_{iS:}_{iL:}', 'mm',  self.mm_file).format(**iSL)
                    # calculate important details for data's offset in file.
                    self.mf_arr_size = self.mf_total_nlevel
                    jdx=0 # count number of fluids with iSL < jSL.  ( (iS < jS) OR ((iS == jS) AND (iL < jL)) )
                    for ispecies in range(1,self.mf_nspecies+1):
                        nlevels=self.att[ispecies].params.nlevel
                        for ilevel in range(1,nlevels+1):
                            if (ispecies < self.mf_jspecies): 
                                jdx += 1
                            elif ((ispecies == self.mf_jspecies) and (ilevel < self.mf_jlevel)):
                                jdx += 1
                else:
                    errmsg = "Failed to find '{}' in simple vars for {}. (at point 1 in ebysus.py)"
                    errmsg = errmsg.format(var, self)
                    raise ValueError(errmsg)
        else:
            # in this case, we are reading a bifrost-like snapshot. (There is NO snapname.io folder.)
            _reading_ebysuslike_snap = True
            # check if var is a simple var from snaps.
            _reading_snap_not_aux = True       # whether we are reading '.snap' (not '.aux')
            if (var in self.mhdvars and self.mf_ispecies > 0) or (
                    var in ['bx', 'by', 'bz']):   # magnetic field, or a fluid-specific mhd simple variable)
                idx      = self.mhdvars.index(var)
                filename = self.mf_common_file
            elif var in self.snapvars and self.mf_ispecies > 0:  # snapvars
                idx      = self.snapvars.index(var)
                filename = self.mf_file.format(**iSL)
            elif var in self.snapevars and self.mf_ispecies > 0: # snapevars (non-electrons) (??)
                idx      = self.snapevars.index(var)
                filename = self.mfe_file.format(**iSL)
            elif var in self.snapevars and self.mf_ispecies < 0: # snapevars (electrons) (??)
                idx      = self.snapevars.index(var)
                filename = self.mf_e_file
            else: # var is not a simple var from snaps.
                # check if var is from aux.
                _reading_snap_not_aux = False  # we are reading '.aux' (not '.snap')
                if var in self.auxvars:    # global auxvars
                    idx      = self.auxvars.index(var)
                    filename = self.aux_file
                elif var in self.varsmf:   # ??
                    idx      = self.varsmf.index(var)
                    filename = self.mf_file.format(**iSL)
                elif var in self.varsmfr:  # ??
                    idx      = self.varsmfr.index(var)
                    filename = self.mfr_file.format(**iSL)
                elif var in self.varsmfp:  # ??
                    idx      = self.varsmfp.index(var)
                    filename = self.mfp_file.format(**iSL)
                elif var in self.varsmfe:  # ??
                    idx      = self.varsmfe.index(var)
                    filename = self.mfe_file.format(**iSL)
                elif var in self.varsmfc:  # ??
                    idx      = self.varsmfc.index(var)
                    filename = self.mfc_file.format(**iSL)
                elif var in self.varsmm:   # two-fluid auxvars, e.g. mm_cross. (??)
                    idx      = self.varsmm.index(var)
                    filename = self.mm_file.format(**iSL)
                    # calculate important details for data's offset in file.
                    self.mf_arr_size = self.mf_total_nlevel
                    jdx=0 # count number of fluids with iSL < jSL.  ( (iS < jS) OR ((iS == jS) AND (iL < jL)) )
                    for ispecies in range(1,self.mf_nspecies+1):
                        nlevels=self.att[ispecies].params.nlevel
                        for ilevel in range(1,nlevels+1):
                            if (ispecies < self.mf_jspecies):
                                jdx += 1
                            elif ((ispecies == self.mf_jspecies) and (ilevel < self.mf_jlevel)):
                                jdx += 1
                else:
                    errmsg = "Failed to find '{}' in simple vars for {}. (at point 2 in ebysus.py)"
                    errmsg = errmsg.format(var, self)
                    raise ValueError(errmsg)

        _snapdir = (self.file_root + '.io') if _reading_ebysuslike_snap else ''
        filename = os.path.join(_snapdir, filename)  # TODO: remove formats above; put .format(**iSL) here.

        if panic:
            _suffix_panic = '.panic' if _reading_snap_not_aux else '.aux.panic'
            filename = filename + _fsuffix_panic
        else: 
            _suffix_dotsnap = '.snap' if _reading_snap_not_aux else '.aux'
            _suffix_dotscr  = '.scr'  if _reading_scr else ''
            filename = filename + currStr + _suffix_dotsnap + _suffix_dotscr

        # -------- use filename and offset details to pick appropriate kwargs for numpy memmap --------- #

        # calculate info which numpy needs to read file as memmap.
        dsize = np.dtype(self.dtype).itemsize
        offset = self.nx * self.ny * self.nzb * idx * dsize * self.mf_arr_size

        # kwargs which will be passed to get_numpy_memmap.
        kw__get_mmap = dict(dtype=self.dtype, order=order, mode=mode,          # kwargs for np.memmap
                            offset=offset, shape=(self.nx, self.ny, self.nzb), # kwargs for np.memmap
                            obj=self if (self.N_memmap != 0) else None,        # kwarg for memmap management
                            )
        if (self.mf_arr_size == 1): # in case of mf_arr_size == 1, kw__get_mmap is already correct.
            pass
        elif var in self.varsmm:    # in case of var in varsmm, apply jdx info to offset.
            kw__get_mmap['offset'] += self.nx * self.ny * self.nzb * jdx * dsize
        else:                       # in case of (else), adjust the shape kwarg appropriately.
            kw__get_mmap['shape'] = (self.nx, self.ny, self.nzb, self.mf_arr_size)

        return (filename, kw__get_mmap)



    def get_var_if_in_aux(self, var, *args__get_var, **kw__get_var):
        """ get_var but only if it appears in aux (i.e. self.params['aux'][self.snapInd])
        
        if var not in aux, return None.
        *args and **kwargs go to get_var.
        """
        if var in self.params['aux'][self.snapInd].split():
            return self.get_var(var, *args__get_var, **kw__get_var)
        else:
            return None  

    def get_nspecies(self):
        return len(self.mf_tabparam['SPECIES'])

    def _get_match_type(self):
        if not hasattr(self, 'match_type'):
            setattr(self, 'match_type', MATCH_TYPE_DEFAULT)
        m = self.match_type
        if m not in [0,1]:
            raise ValueError('Expected self.match_type == 0 or 1 but got {}'.format(m))
        else:
            return m

    def match_physics(self):
        '''return whether self.match_type == MATCH_PHYSICS'''
        return self._get_match_type() == MATCH_PHYSICS

    def match_aux(self):
        '''return whether self.match_type == MATCH_AUX'''
        return self._get_match_type() == MATCH_AUX

    # ---  include methods from fluid_tools --- #

    def MaintainingFluids(self):
        return fluid_tools._MaintainingFluids(self)

    MaintainingFluids.__doc__ = fluid_tools._MaintainingFluids.__doc__.replace(
                                '_MaintainingFluids(dd', 'dd.MaintainingFluids(')  # set docstring
    MaintainFluids = MaintainingFluids  # alias

    def UsingFluids(self, **kw__fluids):
        return fluid_tools._UsingFluids(self, **kw__fluids)

    UsingFluids.__doc__ = fluid_tools._UsingFluids.__doc__.replace(
                                '_UsingFluids(dd, ', 'dd.UsingFluids(') # set docstring
    UseFluids = UsingFluids  # alias

# include methods from fluid_tools in EbysusData object.
for func in FLUIDTOOLS_EBYSUSDATA_FUNCS:
    setattr(EbysusData, func, getattr(fluid_tools, func, None))

del func   # (we don't want func to remain in the ebysus.py namespace beyond this point.)


####################
#  LOCATING SNAPS  #
####################

SnapStuff = collections.namedtuple('SnapStuff', ('snapname', 'snaps'))

def get_snapstuff(dd=None):
    '''return (get_snapname(), available_snaps()).
    dd: None or EbysusData object.
        None -> do operations locally.
        else -> cd to dd.fdir, first.
    '''
    snapname = get_snapname(dd=dd)
    snaps    = get_snaps(snapname=snapname, dd=dd)
    return SnapStuff(snapname=snapname, snaps=snaps)

snapstuff = get_snapstuff   # alias

def get_snapname(dd=None):
    '''gets snapname by reading it from mhd.in'''
    with EnterDirectory(_get_dd_fdir(dd)):
        mhdin_ascii = read_idl_ascii('mhd.in')
        return mhdin_ascii['snapname']

snapname = get_snapname   # alias

def available_snaps(dd=None, snapname=None):
    '''list available snap numbers.
    Does look for: snapname_*.idl, snapname.idl (i.e. snap 0)
    Doesn't look for: .pan, .scr, .aux files.
    snapname: None (default) or str
        snapname parameter from mhd.in. If None, get snapname.
    if dd is not None, look in dd.fdir.
    '''
    with EnterDirectory(_get_dd_fdir(dd)):
        snapname = snapname if snapname is not None else get_snapname()
        snaps = [_snap_to_N(f, snapname) for f in os.listdir()]
        snaps = [s for s in snaps if s is not None]
        snaps = sorted(snaps)
        return snaps

snaps      = available_snaps   # alias
get_snaps  = available_snaps   # alias
list_snaps = available_snaps   # alias

def snaps_info(dd=None, snapname=None):
    '''returns string with length of snaps, as well as min and max.'''
    snaps = get_snaps(dd=dd, snapname=snapname)
    return 'There are {} snaps, from {} (min) to {} (max)'.format(len(snaps), min(snaps), max(snaps))

class EnterDir:
    '''context manager for remembering directory.
    upon enter, cd to directory. upon exit, restore original working directory.
    '''
    def __init__(self, directory=os.curdir):
        self.cwd       = os.path.abspath(os.getcwd())
        self.directory = directory

    def __enter__ (self):
        os.chdir(self.directory)

    def __exit__ (self, exc_type, exc_value, traceback):
        os.chdir(self.cwd)

EnterDirectory = EnterDir  #alias

def _get_dd_fdir(dd=None):
    '''return dd.fdir if dd is not None, else os.curdir.'''
    if dd is not None:
        fdir = dd.fdir
    else:
        fdir = os.curdir
    return fdir

def _snap_to_N(name, base, sep='_', ext='.idl'):
    '''returns N as number given snapname (and basename) if possible, else None.
    for all strings in exclude, if name contains string, return None.
    E.g. _snap_to_N('s_075.idl', 's') == 75
    E.g. _snap_to_N('s.idl', 's')     == 0
    E.g. _snap_to_N('notasnap', 's')  == None
    '''
    if not name.startswith(base):
        return None
    namext = os.path.splitext(name)
    if   namext[1] != ext :
        return None
    elif namext[0] == base:
        return 0
    else:
        try:
            snapN = int(namext[0][len(base+sep):])
        except ValueError:
            return None
        else:
            return snapN

# include methods (and some aliases) for getting snaps in EbysusData object
EbysusData.get_snapstuff   = get_snapstuff
EbysusData.get_snapname    = get_snapname
EbysusData.available_snaps = available_snaps
EbysusData.get_snaps       = available_snaps
EbysusData.snaps_info      = snaps_info

#############################
#  MAKING INITIAL SNAPSHOT  #
#############################

def write_mf_data(rootname, inputs, mfstr, **kw_ifluid):
    '''write density, momentum, or energy for fluid indicated by kw_ifluid.
    rootname = (should be set equal to the value of parameter 'snapname' in mhd.in)
    inputs = list of arrays, each having shape (nx, ny, nz). This is the data to write.
    mfstr = string indicating type of data. 'mfr', 'mfp', or 'mfe'.
    **kw_ifluid: kwargs indicating fluid.
    '''
    # interpret fluid kwargs
    mf_ispecies, mf_ilevel = fluid_tools._interpret_kw_ifluid(**kw_ifluid, None_ok=False)
    if mf_ispecies < 1:
        print('(WWW) species should start with 1')
    if mf_ilevel < 1:
        print('(WWW) levels should start with 1')
    # check that all arrays are finite; warn if one is not.
    for arr in inputs:
        if not np.isfinite(arr).all():
            nonfinite_errmsg = 'at least one non-finite value detected in write_mfr! for iSL={}'
            warnings.warn(nonfinite_errmsg.format((mf_ispecies, mf_ilevel)))
    # calculate names of directory and saveloc.
    directory = os.path.join(
                    '{}.io'.format(rootname),
                    'mf_%02i_%02i' % (mf_ispecies,mf_ilevel),
                    mfstr
                )
    saveloc   = os.path.join(
                    directory,
                    '%s_%s_%02i_%02i.snap' % (rootname, mfstr, mf_ispecies, mf_ilevel)
                )
    # calculate shape for memmap
    shape = (*(inputs[0].shape), len(inputs))   # (nx, ny, nz, (1 or 3))
    # save memmap
    if not os.path.exists(directory):
        os.makedirs(directory)
    data = np.memmap(saveloc, dtype='float32', mode='w+', order='f', shape=shape)
    for i, arr in enumerate(inputs):
        data[...,i] = arr
    data.flush()

def write_mfr(rootname,inputdata,mf_ispecies=None,mf_ilevel=None,**kw_ifluid):
    '''write density. (Useful when using python to make initial snapshot; e.g. in make_mf_snap.py)
    rootname = snapname (should be set equal to the value of parameter 'snapname' in mhd.in)
    inputdata = array of shape (nx, ny, nz)
        mass density [in ebysus units] of ifluid
    ifluid must be entered. If not entered, raise TypeError. ifluid can be entered via one of:
        - (mf_ispecies and mf_ilevel)
        - **kw_ifluid, via the kwargs (ifluid), (iSL), or (iS and iL)
    '''
    return write_mf_data(rootname, [inputdata], 'mfr',
                         mf_ispecies=mf_ispecies, mf_ilevel=mf_ilevel, **kw_ifluid)

def write_mfp(rootname,inputdatax,inputdatay,inputdataz,mf_ispecies=None,mf_ilevel=None, **kw_ifluid):
    '''write momentum. (Useful when using python to make initial snapshot; e.g. in make_mf_snap.py)
    rootname = snapname (should be set equal to the value of parameter 'snapname' in mhd.in)
    inputdata = arrays of shape (nx, ny, nz)
        momentum [in ebysus units] of ifluid
        inputdatax is x-momentum, px; (px, py, pz) = (inputdatax, inputdatay, inputdataz)
    ifluid must be entered. If not entered, raise TypeError. ifluid can be entered via one of:
        - (mf_ispecies and mf_ilevel)
        - **kw_ifluid, via the kwargs (ifluid), (iSL), or (iS and iL)
    '''
    return write_mf_data(rootname, [inputdatax, inputdatay, inputdataz], 'mfp',
                         mf_ispecies=mf_ispecies, mf_ilevel=mf_ilevel, **kw_ifluid)

def write_mfpxyz(rootname,inputdataxyz,mf_ispecies,mf_ilevel,xyz):
    '''write component of momentum. (Useful when using python to make initial snapshot; e.g. in make_mf_snap.py)
    rootname = snapname (should be set equal to the value of parameter 'snapname' in mhd.in)
    inputdataxyz = array of shape (nx, ny, nz)
        momentum [in ebysus units] of ifluid, in x, y, OR z direction
        (direction determined by parameter xyz)
    mf_ispecies, mf_ilevel = int, int
        species number and level number for ifluid.
    xyz = 0 (for x), 1 (for y), 2 (for z)
        determines which axis to write momentum along; e.g. xyz = 0  ->  inputdataxyz is written to px.
    '''
    if mf_ispecies < 1:
        print('(WWW) species should start with 1')
    if mf_ilevel < 1:
        print('(WWW) levels should start with 1')
    directory = '%s.io/mf_%02i_%02i/mfp' % (rootname,mf_ispecies,mf_ilevel)
    nx, ny, nz = inputdataxyz.shape
    if not os.path.exists(directory):
        os.makedirs(directory)
    data = np.memmap(directory+'/%s_mfp_%02i_%02i.snap' % (rootname,mf_ispecies,mf_ilevel), dtype='float32', mode='w+', order='f',shape=(nx,ny,nz,3))
    data[...,xyz] = inputdataxyz
    data.flush()

def write_mfe(rootname,inputdata,mf_ispecies=None,mf_ilevel=None, **kw_ifluid):
    '''write energy. (Useful when using python to make initial snapshot; e.g. in make_mf_snap.py)
    rootname = snapname (should be set equal to the value of parameter 'snapname' in mhd.in)
    inputdata = array of shape (nx, ny, nz)
        energy [in ebysus units] of ifluid
    ifluid must be entered. If not entered, raise TypeError. ifluid can be entered via one of:
        - mf_ispecies and mf_ilevel
        - **kw_ifluid, via the kwargs (ifluid), (iSL), or (iS and iL)
    '''
    return write_mf_data(rootname, [inputdata], 'mfe',
                         mf_ispecies=mf_ispecies, mf_ilevel=mf_ilevel, **kw_ifluid)

def write_mf_common(rootname,inputdatax,inputdatay,inputdataz,inputdatae=None):
    '''write common (?? what is this ??). (Useful when using python to make initial snapshot; e.g. in make_mf_snap.py)
    rootname = snapname (should be set equal to the value of parameter 'snapname' in mhd.in)
    inputdata = arrays of shape (nx, ny, nz)
        data for common.
        inputdatax is x-common; (commonx, commony, commonz) = (inputdatax, inputdatay, inputdataz)
    inputdatae = array of shape (nx, ny, nz), or None (default)
        if non-None, written to common[...,3].
    '''
    directory = '%s.io/mf_common' % (rootname)
    nx, ny, nz = inputdatax.shape
    if not os.path.exists(directory):
        os.makedirs(directory)
    if np.any(inputdatae) == None:
        data = np.memmap(directory+'/%s_mf_common.snap' % (rootname), dtype='float32', mode='w+', order='f',shape=(nx,ny,nz,3))
        data[...,0] = inputdatax
        data[...,1] = inputdatay
        data[...,2] = inputdataz
    else:
        data = np.memmap(directory+'/%s_mf_common.snap' % (rootname), dtype='float32', mode='w+', order='f',shape=(nx,ny,nz,4))
        data[...,0] = inputdatae
        data[...,1] = inputdatax
        data[...,2] = inputdatay
        data[...,3] = inputdataz
    data.flush()

def write_mf_commonxyz(rootname,inputdataxyz,xyz):
    '''write common (?? what is this ??). (Useful when using python to make initial snapshot; e.g. in make_mf_snap.py)
    rootname = snapname (should be set equal to the value of parameter 'snapname' in mhd.in)
    inputdataxyz = array of shape (nx, ny, nz)
        data for common.
        (direction determined by parameter xyz)
    xyz = 0 (for x), 1 (for y), 2 (for z)
        determines which axis to write common along; e.g. xyz = 0  ->  inputdataxyz is written to commonx.
    '''
    directory = '%s.io/mf_common' % (rootname)
    nx, ny, nz = inputdataxyz.shape
    if not os.path.exists(directory):
        os.makedirs(directory)
    data = np.memmap(directory+'/%s_mf_common.snap' % (rootname), dtype='float32', mode='w+', order='f',shape=(nx,ny,nz,4))
    data[...,xyz] = inputdataxyz
    data.flush()

def write_mf_e(rootname,inputdata):
    ''' write electron energy. (Useful when using python to make initial snapshot; e.g. in make_mf_snap.py)
    rootname = snapname (should be set equal to the value of parameter 'snapname' in mhd.in)
    inputdata = array of shape (nx, ny, nz)
        energy [in ebysus units] of electrons.
    '''
    directory = '%s.io/mf_e/' % (rootname)
    nx, ny, nz = inputdata.shape
    if not os.path.exists(directory):
        os.makedirs(directory)
    data = np.memmap(directory+'/%s_mf_e.snap' % (rootname), dtype='float32', mode='w+', order='f',shape=(nx,ny,nz,1))
    data[...,0] = inputdata
    data.flush()

def calculate_fundamental_writeables(fluids, B, nr, v, tg, tge, uni):
    '''calculates the fundamental variables, in ebysus units, ready to be written to snapshot.

    Fluid-dependent results are saved to fluids; others are returned as dict.
    Electrons are not included in fluids; they are treated separately.

        Inputs
        ------
        fluids: an at_tools.fluids.Fluids object
            fluid-dependent results will be saved to attributes of this object.
            Also, the information in it is necessary to do the calculations.
        B : magnetic field [Gauss].
            a list of [Bx, By, Bz]; Bx, By, Bz can be constants, or arrays.
            result['B'] = B
        nr: number densities [per meter^3] of fluids
            a list of values ---> fluids[i].nr = nr[i] for i in range(len(fluids))
            a single value   ---> fluid.nr     = nr    for fluid in fluids
        v: velocities [meter per second] of fluids
            a list of vectors --> fluids[i].v  = v[i]  for i in range(len(fluids))
            a single vector   --> fluid.v      = v     for fluid in fluids
        tg: temperature [Kelvin] of fluids
            a list of values ---> fluids[i].tg = tg[i] for i in range(len(fluids))
            a single value   ---> fluid.tg     = tg    for fluid in fluids
        tge: temperature [Kelvin] of electrons
        uni: bifrost.Bifrost_units object
            this object is used to convert all results to ebysus units, before saving.
            (e.g., for v, really it will be fluids[i].v = v[i] / uni.usi_u)

        Outputs
        -------
        Edits fluids attributes, and returns result (a dict).
        All outputs (in result, and in fluid attributes) are in [ebysus units].
        Keys of result are:
            result['B']   = magnetic field. B[0] = Bx, B[1] = By, B[2] = Bz.
            result['ee']  = electron energy density
        Attributes of fluids containing fundamental calculated values are:
            fluids.rho    = mass densities of fluids.
            fluids.p      = momentum densities of fluids. fluids.p[i][x] is for fluid i, axis x.
            fluids.energy = energy densities of fluids.

        Side Effects
        ------------
        Additional attributes of fluids which are affected by this function are:
            fluids.nr     = number densities [cm^-3] of fluids.
            fluids.tg     = temperatures [K] of fluids.
            fluids.v      = velocities of fluids. fluids.v[i][x] is for fluid i, axis x.
            fluids.px     = fluids.p[:, 0, ...]. x-component of momentum densities of fluids.
            fluids.py     = fluids.p[:, 1, ...]. y-component of momentum densities of fluids.
            fluids.pz     = fluids.p[:, 2, ...]. z-component of momentum densities of fluids.

        Units for Outputs and Side Effects are [ebysus units] unless otherwise specified.
    '''
    orig_stack, orig_stack_axis = getattr(fluids, 'stack', None), getattr(fluids, 'stack_axis', None)
    fluids.stack      = True
    fluids.stack_axis = -1
    # global quantities
    B                = np.asarray(B)/uni.u_b                 # [ebysus units] magnetic field
    # fluid (and global) quantities
    fluids.assign_scalars('nr', (np.asarray(nr) / 1e6) )     # [cm^-3] number density of fluids
    nre              = np.sum(fluids.nr * fluids.ionization, axis=-1) # [cm^-3] number density of electrons
    fluids.assign_scalars('tg', tg)                          # [K] temperature of fluids
    tge              = tge                                   # [K] temperature of electrons
    def _energy(ndens, tg): #returns energy density [ebysus units]
        return (ndens * tg * uni.k_b / (uni.gamma-1)) / uni.u_e   
    fluids.energy    = _energy(fluids.nr, fluids.tg)         # [ebysus units] energy density of fluids
    energy_electrons = _energy(nre, tge)                     # [ebysus units] energy density of electrons
    # fluid quantities
    fluids.rho       = (fluids.nr * fluids.atomic_weight * uni.amu) / uni.u_r  # [ebysus units] mass density of fluids
    fluids.assign_vectors('v', (np.asarray(v) / uni.usi_u))                    # [ebysus units] velocity
    fluids.p         = fluids.v * fluids.rho                                   # [ebysus units] momentum density
    for x in AXES:
        setattr(fluids, 'p'+x, fluids.p[dict(x=0, y=1, z=2)[x]])  # sets px, py, pz
    # restore original stack, stack_axis of fluids object.
    if orig_stack is not None:
        fluids.stack      = orig_stack
    if orig_stack_axis is not None:
        fluids.stack_axis = orig_stack_axis
    return dict(B=B, ee=energy_electrons)

def write_fundamentals(rootname, fluids, B, ee, zero=0):
    '''writes fundamental quantities using write funcs (write_mfr, write_mfp, etc).
    Fundamental quantities are:
        magnetic field, electron energy,
        fluids energy densities, fluids mass densities, fluids momentum densities.

    Inputs
    ------
    rootname: string
        rootname = snapname (should be set equal to the value of parameter 'snapname' in mhd.in)
    fluids: an at_tools.fluids.Fluids object
        The following attributes of fluids will be written. They should be in [ebysus units]:
            fluids.rho    = mass densities of fluids.
            fluids.p      = momentum densities of fluids. fluids[i].p[x] is for fluid i, axis x.
            fluids.energy = energy densities of fluids.
    B   : magnetic field
    ee  : electron energy density
    zero: a number or array
        zero will be added to all data before it is written.
        Suggestion: use zero = np.zeros((nx, ny, nz)).
        This ensure all data will be the correct shape, and will be reshaped if it is a constant.

    Example Usage:
    --------------
    # This is an example which performs the same task as a simple make_mf_snap.py file.
    import at_tools.fluids as fl
    import helita.sim.ebysus as eb
    uni           = eb.Bifrost_units('mhd.in')   # get units
    # put code here which sets the values for:
    #   nx, ny, nz, mf_param_file, snapname   # << these are all from 'mhd.in'; suggestion: read via RunTools.loadfiles.
    #   B, nr, velocities, tg, tge            # << these are physical values; you can choose here what they should be.
    # once those values are set, we can run the following:
    fluids        = fl.Fluids(mf_param_file=mf_param_file)  # get fluids
    # calculate the values of the fundamental quantities, in [ebysus units]:
    global_quants = eb.calculate_fundamental_writeables(fluids, B, nr, velocities, tg, tge, uni)
    zero          = np.zeros((nx,ny,nz))
    # write the values (thus, completing the process of making the initial snapshot):
    eb.write_fundamentals(rootname, fluids, **global_quants, zero=zero)
    '''
    ## Fluid Densities ##
    for fluid in fluids:
        write_mfr(rootname, zero+fluid.rho, ifluid=fluid.SL)
    ## Fluid Momenta ##
    for fluid in fluids:
        write_mfp(rootname, zero+fluid.p[0], zero+fluid.p[1], zero+fluid.p[2], ifluid=fluid.SL)
    ## Fluid Energies ##
    for fluid in fluids:
        write_mfe(rootname, zero+fluid.energy, ifluid=fluid.SL)
    ## Electron Energy ##
    write_mf_e(rootname, zero+ee)
    ## Magnetic Field ##
    write_mf_common(rootname, zero+B[0], zero+B[1], zero+B[2])


def printi(fdir='./',rootname='',it=1):
    '''?? print data about snapshot i ?? (seems to not work though; SE checked on Mar 2, 2021).'''
    dd=EbysusData(rootname,fdir=fdir,verbose=False)
    nspecies=len(dd.mf_tabparam['SPECIES'])
    for ispecies in range(0,nspecies):
        aa=at.Atom_tools(atom_file=dd.mf_tabparam['SPECIES'][ispecies][2],fdir=fdir)
        nlevels=aa.params.nlevel
        print('reading %s'%dd.mf_tabparam['SPECIES'][ispecies][2])
        for ilevel in range(1,nlevels+1):
            print('ilv = %i'%ilevel)
            r=dd.get_var('r',it,mf_ilevel=ilevel,mf_ispecies=ispecies+1) * dd.params['u_r']
            print('dens=%6.2E,%6.2E g/cm3'%(np.min(r),np.max(r)))
            r=dd.get_var('nr',it,mf_ilevel=ilevel,mf_ispecies=ispecies+1) 
            print('ndens=%6.2E,%6.2E 1/cm3'%(np.min(r),np.max(r)))
            ux=dd.get_var('ux',it,mf_ilevel=ilevel,mf_ispecies=ispecies+1) * dd.params['u_u'] / 1e5
            print('ux=%6.2E,%6.2E km/s'%(np.min(ux),np.max(ux)))
            uy=dd.get_var('uy',it,mf_ilevel=ilevel,mf_ispecies=ispecies+1) * dd.params['u_u'] / 1e5
            print('uy=%6.2E,%6.2E km/s'%(np.min(uy),np.max(uy)))
            uz=dd.get_var('uz',it,mf_ilevel=ilevel,mf_ispecies=ispecies+1) * dd.params['u_u'] / 1e5
            print('uz=%6.2E,%6.2E km/s'%(np.min(uz),np.max(uz)))
            tg=dd.get_var('mfe_tg',it,mf_ilevel=ilevel,mf_ispecies=ispecies+1)
            print('tg=%6.2E,%6.2E K'%(np.min(tg),np.max(tg)))
            ener=dd.get_var('e',it,mf_ilevel=ilevel,mf_ispecies=ispecies+1) * dd.params['u_e']
            print('e=%6.2E,%6.2E erg'%(np.min(ener),np.max(ener)))

    bx=dd.get_var('bx',it) * dd.params['u_b']
    print('bx=%6.2E,%6.2E G'%(np.min(bx),np.max(bx)))
    by=dd.get_var('by',it) * dd.params['u_b']
    print('by=%6.2E,%6.2E G'%(np.min(by),np.max(by)))
    bz=dd.get_var('bz',it) * dd.params['u_b']
    print('bz=%6.2E,%6.2E G'%(np.min(bz),np.max(bz)))
    va=dd.get_var('va',it) * dd.params['u_u'] / 1e5
    print('va=%6.2E,%6.2E km/s'%(np.min(va),np.max(va)))

###################
#  READING FILES  #
###################

@file_memory.manage_memmaps(file_memory.MEMORY_MEMMAP)
@file_memory.remember_and_recall(file_memory.MEMORY_MEMMAP, ORDERED=True)
def get_numpy_memmap(filename, **kw__np_memmap):
    '''makes numpy memmap; also remember and recall (i.e. don't re-make memmap for the same file multiple times.)'''
    return np.memmap(filename, **kw__np_memmap)

@file_memory.remember_and_recall('_memory_mftab')
def read_mftab_ascii(filename):
    '''
    Reads mf_tabparam.in-formatted (command style) ascii file into dictionary.
    This is most commonly used for reading mf_param_file such as mf_params.in.
    '''
    convert_to_ints = False   # True starting when we hit key=='COLLISIONS_MAP'
    colstartkeys = ['COLLISIONS_MAP', 'COLISIONS_MAP'] # or another key in colstartkeys.
    params = dict()
    # go through the file, add stuff to dictionary
    with open(filename) as fp:
        for line in fp:
            line, _, comment = line.partition('#')  # remove comments (#)
            line, _, comment = line.partition(';')  # remove comments (;)
            tokens = line.split()                   # split by whitespace
            if len(tokens) == 0:
                continue
            elif len(tokens) == 1:
                key = tokens[0]
                params[key] = []
                for colstart in colstartkeys:
                    if key.startswith(colstart):
                        convert_to_ints = True
            else:
                if convert_to_ints:
                    tokens = [int(token) for token in tokens]
                params[key] += [tokens]

    for key in params.keys():
        params[key] = np.array(params[key])

    return params

read_mf_param_file = read_mftab_ascii   # alias

#############################
#  WRITING PARAMETER FILES  #
#############################

def coll_keys_generate(mf_param_file='mf_params.in', as_str=True):
    '''generates COLL_KEYS such that all collisions will be turned on.

    COLL_KEYS look like:
        II    JJ   TT
    where II is ispecies, JJ is jspecies, TT is ('MX', 'EL', or 'CL'), and this line means:
        turn on TT collisions between II ions and JJ (any level).
    'EL' --> "elastic". This should only be used when we have the collisions tables.
    'MX' --> "maxwell". Assume "maxwell molecules" (velocity-independent collision frequency).
    'CL' --> "coulomb". For ion-ion collisions. (Only applies to ion-ion collisions).

    if as_str, return a string which can be copy-pasted into an mf_param_file.
    Otherwise, return an 2D array with result[i] = [AAi, BBi, TTi].
    '''
    x = read_mftab_ascii(mf_param_file)
    def levels_ions_neutrals(atomfile):
        '''returns (levels of ions in atomfile, levels of neutrals in atomfile)'''
        fluids = fl.Fluids([atomfile])
        return (fluids.ions().level_no, fluids.neutrals().level_no)

    species = {iS: levels_ions_neutrals(file) for (iS, elem, file) in x['SPECIES']}
    tables  = collections.defaultdict(list)
    for (neuS, ionS, ionL, file) in x['CROSS_SECTIONS_TABLES']:
        tables[(neuS, ionS)].append(ionL)   # tables keys (neuS, ionS); vals lists of ionL.
    def table_exists(neuS, ionS, ion_levels):
        '''tells whether a table exists between neutralSpecie and ionSpecie,
        at at least one of the levels in ion_levels.
        '''
        for ionL in tables.get((neuS, ionS), []):
            if int(ionL) in ion_levels:    # (note that ion_levels are ints).
                return True
        return False
    coll_keys = []
    for (iS, (ilevels_ion, ilevels_neu)) in species.items():
        if len(ilevels_ion) == 0: # if there are no i ions,
            continue   # continue, because no coll_keys start with iS in this case.
        for (jS, (jlevels_ion, jlevels_neu)) in species.items():
            # ion-neutral collisions:
            if len(jlevels_neu) >= 1:
                if table_exists(jS, iS, ilevels_ion):
                    coll_keys.append((iS, jS, 'EL'))
                else:
                    coll_keys.append((iS, jS, 'MX'))
            # ion-ion collisions:
            make_CL = False
            if iS == jS:
                if len(ilevels_ion) >= 2:   # ilevels_ion == jlevels_ion
                    make_CL = True
            else:
                if len(jlevels_ion) >= 1:
                    make_CL = True
            if make_CL:
                coll_keys.append((iS, jS, 'CL'))
    if not as_str:
        return np.array(coll_keys)
    else:
        fmtstr = '        {}      {}   {}'
        result = 'COLL_KEYS\n'
        result += '\n'.join([fmtstr.format(*collkey_row) for collkey_row in coll_keys])
        return result

def write_idlparamsfile(snapname,mx=1,my=1,mz=1):
    '''Write default .idl file'''
    default_idl=[
     '; ************************* From   params ************************* \n',
     '             mx =         {}                                        \n'.format(mx),
     '             my =         {}                                        \n'.format(my),
     '             mz =         {}                                        \n'.format(mz),
     '             mb =         5                                         \n',
     '          nstep =        10                                         \n',
     '     nstepstart =         0                                         \n',
     '          debug =         0                                         \n',
     '       time_lim = -1.000E+00                                        \n',
     '          tstop = -1.00000000E+00                                   \n',
     'mf_total_nlevel =         5                                         \n',
     '   mf_electrons =    0                                              \n',
     '        mf_epf =    1                                               \n',
     '   mf_nspecies =         2                                          \n',
     ' mf_param_file = "mf_params.in"                                     \n',
     '; ************************* From parallel ************************* \n',
     '    periodic_x =    1                                               \n',
     '    periodic_y =    1                                               \n',
     '    periodic_z =    0                                               \n',
     '          ndim =    3                                               \n',
     '       reorder =    1                                               \n',
     '; ************************* From    units ************************* \n',
     '           u_l =  1.000E+08                                         \n',
     '           u_t =  1.000E+02                                         \n',
     '           u_r =  1.000E-07                                         \n',
     '           u_p =  1.000E+05                                         \n',
     '           u_u =  1.000E+06                                         \n',
     '          u_kr =  1.000E-01                                         \n',
     '          u_ee =  1.000E+12                                         \n',
     '           u_e =  1.000E+05                                         \n',
     '          u_te =  1.000E+11                                         \n',
     '          u_tg =  1.212E+04                                         \n',
     '           u_B =  1.121E+03                                         \n',
     '; ************************* From  stagger ************************* \n,'
     '      meshfile =             "{}.mesh"                     \n'.format(snapname),
     '            dx =  1.000E+00                                         \n',
     '            dy =  1.000E+00                                         \n',
     '            dz =  2.993E-02                                         \n',
     '; ************************* From timestep ************************* \n',
     '           Cdt =  0.030                                             \n',
     '            dt =  1.e-11                                            \n',
     '             t =  0.0                                               \n',
     ' timestepdebug =    0                                               \n',
     '; ************************* From      mhd ************************* \n',
     '           nu1 =  0.100                                             \n',
     '           nu2 =  0.300                                             \n',
     '           nu3 =  0.800                                             \n',
     '          nu_r =  0.100                                             \n',
     '        nu_r_z =  9.990E+02                                         \n',
     '       nu_r_mz =  0.100                                             \n',
     '         nu_ee =  0.100                                             \n',
     '       nu_ee_z =  9.990E+02                                         \n',
     '      nu_ee_mz =  0.100                                             \n',
     '       nu_e_ee =  0.000                                             \n',
     '     nu_e_ee_z =  9.990E+02                                         \n',
     '    nu_e_ee_mz =  0.000                                             \n',
     '   symmetric_e =    0                                               \n',
     '   symmetric_b =    0                                               \n',
     '          grav = -2.740                                             \n',
     '          eta3 =  3.000E-01                                         \n',
     '        ca_max =  0.000E+00                                         \n',
     '      mhddebug =    0                                               \n',
     '        do_mhd =    1                                               \n',
     '      mhdclean =        -1                                          \n',
     '   mhdclean_ub =    0                                               \n',
     '   mhdclean_lb =    0                                               \n',
     '  mhdclean_ubx =    0                                               \n',
     '  mhdclean_lbx =    0                                               \n',
     '  mhdclean_uby =    0                                               \n',
     '  mhdclean_lby =    0                                               \n',
     '    do_e_joule =    1                                               \n',
     '  do_ion_joule =    1                                               \n',
     '          nue1 =  0.050                                             \n',
     '          nue2 =  0.100                                             \n',
     '          nue3 =  0.050                                             \n',
     '          nue4 =  0.000                                             \n',
     '; ************************* From       io ************************* \n',
     '      one_file =    0                                               \n',
     '      snapname =                  "{}"                     \n'.format(snapname),
     '         isnap =         0                                          \n',
     '  large_memory =    1                                               \n',
     '         nsnap = 100000000                                          \n',
     '          nscr =       250                                          \n',
     '           aux = " nel mfe_tg etg "                                 \n',
     '        dtsnap =  5.000E-09                                         \n',
     '        newaux =    0                                               \n',
     '    rereadpars =   1000000                                          \n',
     '         dtscr =  1.000E+04                                         \n',
     '         tsnap =  0.0                                               \n',
     '          tscr =  0.00000000E+00                                    \n',
     '   boundarychk =    0                                               \n',
     '   print_stats =    0                                               \n',
     '; ************************* From     math ************************* \n',
     '         max_r =    5                                               \n',
     '      smooth_r =    3                                               \n',
     '   divhc_niter = 1000                                               \n',
     '     divhc_cfl =  0.400                                             \n',
     '       divhc_r =  0.180                                             \n',
     '     divhc_vxr =  0.000                                             \n',
     '     divhc_vyr =  0.000                                             \n',
     '     divhc_vzr =  0.950                                             \n',
     '     divhc_tol =  1.000E-05                                         \n',
     '; ************************* From   quench ************************* \n',
     '          qmax =  8.000                                             \n',
     '; ************************* From      eos ************************* \n',
     '         gamma =  1.667                                             \n',
     '      eosdebug =    0                                               \n',
     '; ************************* From     collisions utils ************* \n',
     '        do_col =    0                                               \n',
     '     col_debug =    0                                               \n',
     '       do_qcol =    1                                               \n',
     '       do_ecol =    0                                               \n',
     'col_calc_nu_in =    1                                               \n',
     'col_const_nu_in = -1.000E+03                                        \n',
     '   col_cnu_max =  1.000E+03                                         \n',
     '     col_utiny = -1.000E-05                                         \n',
     'col_trans_tim0 =  0.000E+00                                         \n',
     '  col_trans_dt =  1.000E+00                                         \n',
     'col_trans_ampl =  1.000E-10                                         \n',
     '     col_tabin = "mf_coltab.in"                                     \n',
     '; ************************* From          collisions  ************* \n',
     '    qcol_method = "expl"                                            \n',
     'col_matrix_norm =    0                                              \n',
     '; ************************* From              ionrec  ************* \n',
      '   qri_method = "impl"                                             \n',
     '; ************************* From   mf_recion (utils)  ************* \n',
     '     do_recion =    0                                               \n',
     '  recion_debug =    0                                               \n',
     '     calc_freq =    1                                               \n',
     '     three_bdy =    1                                               \n',
     '    const_fion = -1.000E+00                                         \n',
     '    const_frec = -1.000E+00                                         \n',
     '  recion_tabin = "mf_reciontab.in"                                  \n',
     'recion_modname = "atomic"                                           \n',
     '; ************************* From     hall ************************* \n',
     '       do_hall = "false"                                            \n',
     '    tstep_hall = "ntsv"                                             \n',
     '     eta_hallo =  1.000E+00                                         \n',
     '     eta4_hall = [ 0.100,  0.100,  0.100 ]                          \n',
     'mts_max_n_hall =   10                                               \n',
     '; ************************* From Bierman  ************************* \n',
     '    do_battery =    0                                               \n',
     '       bb_bato =  1.000E+00                                         \n',
     'bb_extdyn_time = -1.000E+00                                         \n',
     '     bb_ext_bb =  0.000E+00                                         \n',
     'bb_debug_battery =    0                                             \n',
     '       do_qbat =    0                                               \n',
     '; ************************* From            ohm_ecol  ************* \n',
     '   do_ohm_ecol =    0                                               \n',
     '       do_qohm =    1                                               \n',
     'ec_ohm_ecoll_debug =    0                                           \n',
     ' ec_calc_nu_en =    1                                               \n',
     ' ec_calc_nu_ei =    1                                               \n',
     'ec_const_nu_en = -1.000E+00                                         \n',
     'ec_const_nu_ei = -1.000E+00                                         \n',
     '      ec_tabin = "mf_ecoltab.in"                                    \n',
     'mf_eparam_file = "mf_eparams.in"                                    \n',
     '; ************************* From  spitzer ************************* \n',
     '       spitzer = "impl"                                             \n',
     ' debug_spitzer =    0                                               \n',
     '  info_spitzer =    0                                               \n',
     '   spitzer_amp =  0.000                                             \n',
     '      theta_mg =  0.900                                             \n',
     '        dtgerr =  1.000E-05                                         \n',
     '      ntest_mg =         1                                          \n',
     '          tgb0 =  0.000E+00                                         \n',
     '          tgb1 =  0.000E+00                                         \n',
     '        tau_tg =  1.000E+00                                         \n',
     '   fix_grad_tg =    1                                               \n',
     '   niter_mg = [   2,    5,    5,    5,   30 ]                       \n',
     '          bmin =  1.000E-04                                         \n',
     '       kappaq0 =  0.000E+00                                         \n',
     '; ************************* From   genrad ************************* \n',
     '     do_genrad =    1                                               \n',
     '    genradfile =                  "qthresh.dat"                     \n',
     '  debug_genrad =    0                                               \n',
     ' incrad_detail =    0                                               \n',
     '   incrad_quad =    3                                               \n',
     '      dtincrad =  1.000E-03                                         \n',
     '  dtincrad_lya =  1.000E-04                                         \n',
     '  debug_incrad =    0                                               \n',
     '; ************************* From         ue_electric  ************* \n',
     'do_ue_electric =    1                                               \n',
     'ue_electric_debug =    0                                            \n',
     'ue_fudge_mass =  1.000E+00                                          \n',
     '       ue_incr =  0.000                                             \n',
     '     ue_dt_inc = -1.000E+00                                         \n',
     '         ue_nu = [ 0.000,  0.000,  0.000,  0.000,  0.000 ]          \n',
     '      eionsfrz =    1                                               \n',
     '; ************************* From   bc_lowerx_magnetic ************* \n',
     '  bctypelowerx = "mcccc"                                            \n',
     '     bcldebugx =    0                                               \n',
     '  nextrap_bclx =         1                                          \n',
     '  nsmooth_bclx =         0                                          \n',
     'nsmoothbyz_bcl =         0                                          \n',
     '; ************************* From   bc_upperx_magnetic ************* \n',
     ' bctypeupperx = "mcccc"                                             \n',
     '     bcudebugx =    0                                               \n',
     '  nextrap_bcux =         1                                          \n',
     '  nsmooth_bcux =         0                                          \n',
     'nsmoothbyz_bcu =         0                                          \n',
     '; ************************* From   bc_lowery_magnetic ************* \n',
     ' bctypelowery = "mcccc"                                             \n',
     '     bcldebugy =    0                                               \n',
     '  nextrap_bcly =         1                                          \n',
     '  nsmooth_bcly =         0                                          \n',
     'nsmoothbxz_bcl =         0                                          \n',
     '; ************************* From   bc_uppery_magnetic ************* \n',
     ' bctypeuppery = "mcccc"                                             \n',
     '     bcudebugy =    0                                               \n',
     '  nextrap_bcuy =         1                                          \n',
     '  nsmooth_bcuy =         0                                          \n',
     'nsmoothbxz_bcu =         0                                          \n',
     '; ************************* From   bc_lowerz_magnetic ************* \n',
     '  bctypelowerz = "mesec"                                            \n',
     '     bcldebugz =    0                                               \n',
     '  nextrap_bclz =         1                                          \n',
     '  nsmooth_bclz =         0                                          \n',
     'nsmoothbxy_bcl =         0                                          \n',
     '; ************************* From   bc_upperz_magnetic ************* \n',
     '  bctypeupperz = "mesec"                                            \n',
     '     bcudebugz =    0                                               \n',
     '  nextrap_bcuz =         1                                          \n',
     '  nsmooth_bcuz =         0                                          \n',
     'nsmoothbxy_bcu =         0                                          \n'
          ]
    out=open('{}.idl'.format(snapname),'w')
    out.writelines(default_idl)
    return
      
def keyword_update(inoutfile,new_values):
   ''' Updates a given number of fields with values on a snapname.idl file.
       These are given in a dictionary: fvalues = {field: value}.
       Reads from snapname.idl and writes back into the same file.'''
   lines = list()
   with open(inoutfile) as f:
     for line in f.readlines():
       if line[0] == '#' or line[0] == ';':
         continue
       elif line.find('=') < 0:
         continue
       else:
         ss = line.split('=')[0]
         ssv = ss.strip().lower()
         if ssv in list(new_values.keys()):
           line = '{} = {} \n'.format(ss,str(new_values[ssv]))
       lines.append(line)
       
   with open(inoutfile,"w") as f:
     f.writelines(lines)
      
def write_mftab_ascii(filename, NSPECIES_MAX=28,
                      SPECIES=None, EOS_TABLES=None, REC_TABLES=None,
                      ION_TABLES=None, CROSS_SECTIONS_TABLES=None,
                      CROSS_SECTIONS_TABLES_I=None,
                      CROSS_SECTIONS_TABLES_N=None,
                      collist=np.linspace(1,
                                          28,
                                          28)):
    '''
    Writes mf_tabparam.in

        Parameters
        ----------
        filename - string
            Name of the file to write.
        NSPECIES_MAX - integer [28], maximum # of species
        SPECIES - list of strings containing the name of the atom files
        EOS_TABLES - list of strings containing the name of the eos
                    tables (no use)
        REC_TABLES - list of strings containing the name of the rec
                    tables (no use)
        ION_TABLES - list of strings containing the name of the ion
                    tables (no use)
        CROSS_SECTIONS_TABLES - list of strings containing the name of the
                    cross section files from VK between ion and neutrals
        CROSS_SECTIONS_TABLES_I - list of strings containing the name of the
                    cross section files from VK between ions
        CROSS_SECTIONS_TABLES_N - list of strings containing the name of the
                    cross section files from VK  between ions
        collist - integer vector of the species used.
                e.g., collist = [1,2,3] will include the H, He and Li

    '''

    if SPECIES is None:
        SPECIES=['H_2.atom', 'He_2.atom']
    if EOS_TABLES is None:
        EOS_TABLES=['H_EOS.dat', 'He_EOS.dat']
    if REC_TABLES is None:
        REC_TABLES=['h_rec.dat', 'he_rec.dat']
    if ION_TABLES is None:
        ION_TABLES=['h_ion.dat', 'he_ion.dat']
    if CROSS_SECTIONS_TABLES is None:
        CROSS_SECTIONS_TABLES=[[1, 1, 'p-H-elast.txt'],
                               [1, 2, 'p-He.txt'],
                               [2, 2, 'He-He.txt']]
    if CROSS_SECTIONS_TABLES_I is None:
        CROSS_SECTIONS_TABLES_I=[]
    if CROSS_SECTIONS_TABLES_N is None:
        CROSS_SECTIONS_TABLES_N=[]

    params = [
        'NSPECIES_MAX',
        'SPECIES',
        'EOS_TABLES',
        'REC_TABLES',
        'ION_TABLES',
        'COLISIONS_TABLES',
        'CROSS_SECTIONS_TABLES',
        'COLISIONS_MAP',
        'COLISIONS_TABLES_N',
        'CROSS_SECTIONS_TABLES_N',
        'COLISIONS_MAP_N',
        'COLISIONS_TABLES_I',
        'CROSS_SECTIONS_TABLES_I',
        'COLISIONS_MAP_I',
        'EMASK']
    coll_vars_i = [
        'p',
        'hei',
        'lii',
        'bei',
        'bi',
        'ci',
        'n_i',
        'oi',
        'fi',
        'nai',
        'mgi',
        'ali',
        'sii',
        'pi',
        's_i',
        'cli',
        'ari',
        'ki',
        'cai',
        'sci',
        'tii',
        'vi',
        'cri',
        'mni',
        'fei',
        'coi',
        'nii',
        'cui']
    coll_vars_n = [
        'h',
        'he',
        'li',
        'be',
        'b',
        'c',
        'n',
        'o',
        'f',
        'na',
        'mg',
        'al',
        'si',
        'p',
        's',
        'cl',
        'ar',
        'k',
        'ca',
        'sc',
        'ti',
        'v',
        'cr',
        'mn',
        'fe',
        'co',
        'ni',
        'cu']

    coll_tabs_in = []
    coll_tabs_n = []
    coll_tabs_i = []
    coll_vars_list = []

    for i in range(0, NSPECIES_MAX):
        for j in range(0, NSPECIES_MAX):
            coll_tabs_in.append(
                'momex_vk_' +
                coll_vars_i[i] +
                '_' +
                coll_vars_n[j] +
                '.dat')
            coll_tabs_i.append(
                'momex_vk_' +
                coll_vars_i[i] +
                '_' +
                coll_vars_i[j] +
                '.dat')
            coll_tabs_n.append(
                'momex_vk_' +
                coll_vars_n[i] +
                '_' +
                coll_vars_n[j] +
                '.dat')

    if (np.shape(collist) != np.shape(SPECIES)):
        print('write_mftab_ascii: WARNING the list of atom files is \n '
              'different than the selected list of species in collist')

    CROSS_SECTIONS_TABLES_I = []
    CROSS_SECTIONS_TABLES_N = []
    COLISIONS_MAP = np.zeros((NSPECIES_MAX, NSPECIES_MAX))
    COLISIONS_MAP_I = np.zeros((NSPECIES_MAX, NSPECIES_MAX))
    COLISIONS_MAP_N = np.zeros((NSPECIES_MAX, NSPECIES_MAX))
    EMASK_MAP = np.zeros((NSPECIES_MAX))

    for j in range(1, NSPECIES_MAX + 1):
        for i in range(1, j + 1):
            COLISIONS_MAP_I[j - 1, i - 1] = -1
            COLISIONS_MAP_N[j - 1, i - 1] = -1
            if (i in collist) and (j in collist):
                COLISIONS_MAP[i - 1, j - 1] = (i - 1) * NSPECIES_MAX + j
                coll_vars_list.append(coll_vars_n[i - 1])
                coll_vars_list.append(coll_vars_n[j - 1])
                if (i < j):
                    COLISIONS_MAP_I[i - 1, j - 1] = (i - 1) * NSPECIES_MAX + j
                    COLISIONS_MAP_N[i - 1, j - 1] = (i - 1) * NSPECIES_MAX + j

    for j in range(0, NSPECIES_MAX):
        EMASK_MAP[j] = 99

    for symb in SPECIES:
        symb = symb.split('_')[0]
        if not(symb.lower() in coll_vars_list):
            print('write_mftab_ascii: WARNING there may be a mismatch between'
                  'the atom files and selected species.\n'
                  'Check for species', symb.lower())

    f = open(filename, 'w')
    for head in params:
        f.write(head + "\n")
        if head == 'NSPECIES_MAX':
            f.write("\t" + str(NSPECIES_MAX) + "\n")
            f.write("\n")
        if head == 'SPECIES':
            li = 0
            for spc in SPECIES:
                symb = spc.split('_')[0]
                li += 1
                f.write(
                    "\t" +
                    str(li).zfill(2) +
                    "\t" +
                    symb +
                    "\t" +
                    spc +
                    "\n")
            f.write("\n")
        if head == 'EOS_TABLES':
            li = 0
            for eos in EOS_TABLES:
                f.write("\t" + str(li).zfill(2) + "\t" + eos + "\n")
                li += 1
            f.write("\n")
        if head == 'REC_TABLES':
            li = 0
            for rec in REC_TABLES:
                li += 1
                f.write("\t" + str(li).zfill(2) + "\t" + rec + "\n")
            f.write("\n")
        if head == 'ION_TABLES':
            li = 0
            for ion in ION_TABLES:
                li += 1
                f.write("\t" + str(li).zfill(2) + "\t" + ion + "\n")
            f.write("\n")
        if head == 'COLISIONS_TABLES':
            li = 0
            for coll in coll_tabs_in:
                li += 1
                if (li in COLISIONS_MAP):
                    f.write("\t" + str(li).zfill(2) + "\t" + str(coll) + "\n")
            f.write("\n")
        if head == 'COLISIONS_TABLES_I':
            li = 0
            for coll in coll_tabs_i:
                li += 1
                if (li in COLISIONS_MAP_I):
                    f.write("\t" + str(li).zfill(2) + "\t" + str(coll) + "\n")
            f.write("\n")
        if head == 'COLISIONS_TABLES_N':
            li = 0
            for coll in coll_tabs_n:
                li += 1
                if (li in COLISIONS_MAP_N):
                    f.write("\t" + str(li).zfill(2) + "\t" + str(coll) + "\n")
            f.write("\n")
        if head == 'CROSS_SECTIONS_TABLES':
            num_cs_tab = np.shape(CROSS_SECTIONS_TABLES)[:][0]
            for crs in range(0, num_cs_tab):
                f.write("\t" +
                        str(int(CROSS_SECTIONS_TABLES[crs][0])).zfill(2) +
                        "\t" +
                        str(int(CROSS_SECTIONS_TABLES[crs][1])).zfill(2) +
                        "\t" +
                        CROSS_SECTIONS_TABLES[crs][2] +
                        "\n")
            f.write("\n")
        if head == 'CROSS_SECTIONS_TABLES_N':
            num_cs_tab = np.shape(CROSS_SECTIONS_TABLES_N)[:][0]
            for crs in range(0, num_cs_tab):
                f.write("\t" +
                        str(int(CROSS_SECTIONS_TABLES_N[crs][0])).zfill(2) +
                        "\t" +
                        str(int(CROSS_SECTIONS_TABLES_N[crs][1])).zfill(2) +
                        "\t" +
                        CROSS_SECTIONS_TABLES_N[crs][2] +
                        "\n")
            f.write("\n")
        if head == 'CROSS_SECTIONS_TABLES_I':
            num_cs_tab = np.shape(CROSS_SECTIONS_TABLES_I)[:][0]
            for crs in range(0, num_cs_tab):
                f.write("\t" +
                        str(int(CROSS_SECTIONS_TABLES_I[crs][0])).zfill(2) +
                        "\t" +
                        str(int(CROSS_SECTIONS_TABLES_I[crs][1])).zfill(2) +
                        "\t" +
                        CROSS_SECTIONS_TABLES_I[crs][2] +
                        "\n")
            f.write("\n")
        if head == 'COLISIONS_MAP':
            f.write("#\t" + "\t".join(
                    [coll_vars_n[v].upper().ljust(2) for v in range(
                            0, NSPECIES_MAX)]) + "\n")
            for crs in range(0, NSPECIES_MAX):
                f.write("\t" + "\t".join(
                        [str(int(
                            COLISIONS_MAP[crs][v])).zfill(2) for v in range(
                                    0, NSPECIES_MAX)]) + "\n")
            f.write("\n")
        if head == 'COLISIONS_MAP_I':
            f.write("#\t" + "\t".join(
                    [coll_vars_n[v].upper().ljust(2) for v in range(
                            0, NSPECIES_MAX)]) + "\n")
            for crs in range(0, NSPECIES_MAX):
                f.write("\t" + "\t".join([str(int(
                        COLISIONS_MAP_I[crs][v])).zfill(2) for v in range(
                                0, NSPECIES_MAX)]) + "\n")
            f.write("\n")
        if head == 'COLISIONS_MAP_N':
            f.write("#\t" + "\t".join(
                    [coll_vars_n[v].upper().ljust(2) for v in range(
                            0, NSPECIES_MAX)]) + "\n")
            for crs in range(0, NSPECIES_MAX):
                f.write("\t" + "\t".join([str(int(
                        COLISIONS_MAP_N[crs][v])).zfill(2) for v in range(
                                0, NSPECIES_MAX)]) + "\n")
            f.write("\n")
        if head == 'EMASK':
            f.write("#\t" + "\t".join(
                    [coll_vars_n[v].upper().ljust(2) for v in range(
                            0, NSPECIES_MAX)]) + "\n")
            f.write("\t" + "\t".join([str(
                    int(EMASK_MAP[v])).zfill(2) for v in range(
                            0, NSPECIES_MAX)]) + "\n")
            f.write("\n")
    f.close()
