# Python module for storing filter information and tools for projecting spectra
# onto filters.  Also includes tools for convolving spectra.
#
# Assumed input units are erg/s/cm^2/AA and AA

import numpy as np
import os
try:
    from pkg_resources import resource_filename, resource_listdir
except ImportError:
    pass
from .yanny import read as yanny_read
from .reference_spectra import vega, solar, sedpydir

__all__ = ["Filter", "load_filters", "list_available_filters", "getSED",
           "air2vac", "vac2air", "Lbol"]

lightspeed = 2.998e18  # AA/s


class Filter(object):
    """This class operates on filter transmission files.  It reads SDSS-style
    yanny files (these are easy to create) or simple 2-column ascii files
    containing filter transmission curves. It caches a number of useful filter
    quantities.  Methods are provided to project a source spectrum onto the
    filter and return the magnitude, AB or Vega.

    :param kname: (default: 'sdss_r0')
        The kcorrect style name of the filter, excluing '.par',
        e.g. sdss_r0.

    :param nick: (optional)
        A nickname to associate with the filter.

    :param directory: (optional)
        The path to the directory containing the filter file.  If not given
        then the sedpy/data/filters/ directory will be searched.
    """
    ab_gnu = 3.631e-20  # AB reference spctrum in erg/s/cm^2/Hz
    npts = 0

    def __init__(self, kname='sdss_r0', nick=None, directory=None, **extras):
        """Constructor.
        """
        self.name = kname
        if nick is None:
            self.nick = kname
        else:
            self.nick = nick

        if directory is None:
            try:
                self.filename = resource_filename('sedpy', '/data/filters/' +
                                              kname + '.par')
            except:
                self.filename = os.path.join(sedpydir, '/data/filters/', kname + '.par')
        else:
            self.filename = os.path.join(directory, kname+'.par')

        if isinstance(self.filename, str):
            if not os.path.isfile(self.filename):
                raise ValueError('Filter transmission file {0} does '
                                 'not exist!'.format(self.filename))
            try:
                self.load_kfilter(self.filename)
            except:
                self.load_filter(self.filename)

        self.get_properties()

    def __repr__(self):
        return '{}({})'.format(self.__class__, self.name)
                        
    def load_kfilter(self, filename):
        """Read a filter in kcorrect (yanny) format and populate the wavelength
        and transmission arrays.

        :param filename:
            The fully qualified path and filename of the yanny file that
            contains the filter transmission.
        """

        ff = yanny_read(filename, one=True)
        wave = ff['lambda']
        trans = ff['pass']
        # Clean negatives, NaNs, and Infs, then sort, then store
        ind = np.where(np.isfinite(trans) & (trans >= 0.0))[0]
        order = wave[ind].argsort()
        self.npts = ind.shape[0]
        self.wavelength = wave[ind[order]]
        self.transmission = trans[ind[order]]
        self._remove_extra_zeros()

    def load_filter(self, filename):
        """Read a filter in simple two column ascii format and populate the
        wavelength and transmission arrays.  The first column is wavelength in
        AA and the second column is transmission (detector signal per photon)
        
        :param filename:
            The fully qualified path and filename of the file that contains the
            filter transmission.
        """
        wave, trans = np.genfromtxt(filename, usecols=(0,1), unpack=True)
        # Clean negatives, NaNs, and Infs, then sort, then store
        ind = np.isfinite(trans) & (trans >= 0.0)
        order = wave[ind].argsort()
        self.npts = ind.sum()
        self.wavelength = wave[ind][order]
        self.transmission = trans[ind][order]
        self._remove_extra_zeros()

    def _remove_extra_zeros(self):
        """Remove extra leading or trailing zero transmission points.
        """
        v = np.argwhere(self.transmission)
        lo, hi = max(v.min() - 1 , 0), min(v.max() + 1, len(self.transmission))
        self.wavelength = self.wavelength[lo:hi]
        self.transmission = self.transmission[lo:hi]


    def gridify_transmission(self, dlnlam, wmin=1e2, wmax=1e7):

        # find min and max of the filter in a global wavelength grid given by
        # wmin, wmax, and dlnlam
        ind_min = int(np.floor((np.log(self.wavelength.min()) - np.log(wmin)) / dlnlam))
        ind_max = int(np.ceil((np.log(self.wavelength.max()) - np.log(wmin)) / dlnlam))
        lnlam = np.linspace(ind_min * dlnlam + np.log(wmin),
                            ind_max * dlnlam + np.log(wmin), ind_max - ind_min)
        lam = np.exp(lnlam)
        trans = np.interp(lam, self.wavelength, self.transmission,
                          left=0., right=0.)

        valid = slice(self.ind_wmin, self.ind_wmax)
        self.wavelength = lam[valid]
        self.transmission = trans[valid]
        self.dwave = np.gradient(lam)[valid]

        self.get_properties(gridded=True)


    def get_properties(self, **extras):
        """Determine and store a number of properties of the filter and store
        them in the object.  These properties include several 'effective'
        wavelength definitions and several width definitions, as well as the
        in-band absolute AB solar magnitude, the Vega and AB reference
        zero-point detector signal, and the conversion between AB and Vega
        magnitudes.

        See Fukugita et al. (1996) AJ 111, 1748 for discussion and definition
        of many of these quantities.
        """
        # Calculate some useful integrals
        i0 = np.trapz(self.transmission * np.log(self.wavelength),
                      np.log(self.wavelength))
        i1 = np.trapz(self.transmission,
                      np.log(self.wavelength))
        i2 = np.trapz(self.transmission * self.wavelength,
                      self.wavelength)
        i3 = np.trapz(self.transmission,
                      self.wavelength)

        self.wave_effective = np.exp(i0 / i1)
        self.wave_pivot = np.sqrt(i2 / i1)
        self.wave_mean = self.wave_effective
        self.wave_average = i2 / i3
        self.rectangular_width = i3 / self.transmission.max()

        i4 = np.trapz(self.transmission *
                      (np.log(self.wavelength / self.wave_effective))**2.0,
                      np.log(self.wavelength))
        self.gauss_width = (i4 / i1)**(0.5)
        self.effective_width = (2.0 * np.sqrt(2. * np.log(2.)) *
                                self.gauss_width *
                                self.wave_effective)
        # self.norm  = np.trapz(transmission,wavelength)

        # Get zero points and AB to Vega conversion
        self.ab_zero_counts = self.obj_counts(self.wavelength,
                                              self.ab_gnu * lightspeed /
                                              self.wavelength**2, **extras)
        # If blue enough get AB mag of vega
        if self.wave_mean < 1e6:
            self.vega_zero_counts = self.obj_counts(vega[:,0], vega[:,1], **extras)
            self._ab_to_vega = -2.5 * np.log10(self.ab_zero_counts /
                                               self.vega_zero_counts)
        else:
            self.vega_zero_counts = float('NaN')
            self._ab_to_vega = float('NaN')
        # If blue enough get absolute solar magnitude
        if self.wave_mean < 1e5:
            self.solar_ab_mag = self.ab_mag(solar[:,0], solar[:,1], **extras)
        else:
            self.solar_ab_mag = float('NaN')

            
    @property
    def ab_to_vega(self):
        """The conversion from AB to Vega systems for this filter.  It has the
        sense

        :math:`m_{Vega} = m_{AB} + Filter().ab_to_vega`
        """
        return self._ab_to_vega
            
    def display(self, normalize=False, ax=None):
        """Plot the filter transmission curve.
        """
        if self.npts > 0:
            if ax is None:
                import matplotlib.pyplot as pl
                fig, ax = pl.subplots()
                ax.title(self.nick)
                fig.show()
            if normalize:
                ax.plot(self.wavelength, self.transmission / self.transmission.max())
            else:
                ax.plot(self.wavelength, self.transmission)
            return ax

    def obj_counts_hires(self, sourcewave, sourceflux, sourceflux_unc=0):
        """Project source spectrum onto filter and return the detector signal.

        :param sourcewave:
            Spectrum wavelength (in AA), ndarray of shape (nwave).  Must be
            monotonic increasing.

        :param sourceflux:
            Associated flux (assumed to be in erg/s/cm^2/AA), ndarray of shape
            (nspec,nwave).

        :returns counts:
            Detector signal(s) (nspec).
        """
        assert sourcewave[1] > sourcewave[0], "``sourcewave`` not in ascending order."
        # Interpolate filter transmission to source spectrum
        newtrans = np.interp(sourcewave, self.wavelength, self.transmission,
                             left=0., right=0.)

        # Integrate lambda*f_lambda*R
        if True in (newtrans > 0.):
            positive = np.where(newtrans > 0.)[0]
            edge = (positive.min() < 1) | (positive.max() >= (len(newtrans)-1))
            # assert ~edge, "Source spectrum does not span filter."
            ind = slice(max(positive.min() - 1, 0),
                        min(positive.max() + 2, len(sourcewave)))
            counts = np.trapz(sourcewave[ind] * newtrans[ind] *
                              sourceflux[..., ind],
                              sourcewave[ind], axis=-1)
            return np.squeeze(counts)
        else:
            return float('NaN')

    def obj_counts_lores(self, sourcewave, sourceflux, sourceflux_unc=0):
        """Project source spectrum onto filter and return the detector
        signal. This method differs from ``obj_counts_hires`` in that the
        source spectrum is interpolated onto the transmission spectrum instead
        of vice-versa, which is necessary with the source spectrum does not
        adequately sample features in the transmission spectrum.

        :param sourcewave:
            Spectrum wavelength (in AA), ndarray of shape (nwave).  Must be
            monotonic increasing.

        :param sourceflux:
            Associated flux (assumed to be in erg/s/cm^2/AA), ndarray of shape
            (nspec,nwave).

        :returns counts:
            Detector signal(s) (nspec).
        """
        sourceflux = np.squeeze(sourceflux)
        assert sourceflux.ndim == 1, "Only a single source allowed."
        assert sourcewave[1] > sourcewave[0], "``sourcewave`` not in ascending order."
        # Interpolate source spectrum to filter transmission
        newflux = np.interp(self.wavelength, sourcewave, sourceflux,
                            left=0., right=0.)

        # Integrate lambda*f_lambda*R
        if True in (newflux > 0.):
            counts = np.trapz(self.wavelength * self.transmission * newflux,
                              self.wavelength)
            return np.squeeze(counts)
        else:
            return float('NaN')



    def obj_counts_grid(self, sourceflux):
        """Project source spectrum onto filter and return the detector
        signal. This method differs from ``obj_counts_*res`` in that the source
        spectrum is assumed to be interpolated onto a logarithmic grid in
        lambda with spacing, min and max given by ...

        :param sourceflux:
            Associated flux (assumed to be in erg/s/cm^2/AA), ndarray of shape
            (nspec,nwave).

        :returns counts:
            Detector signal(s) (nspec).
        """
        assert len(sourceflux) == 

        valid = slice(self.ind_wmin, self.ind_wmax)
        counts = np.sum(sourceflux[valid] * self.transmission *
                        self.wavelength * self.dwave)
        
        raise(NotImplementedError)
    

    

    def obj_counts(self, sourcewave, sourceflux, lores=False, gridded=False, **extras):
        """Project a spectrum onto a filter and return the detector signal.
        This method uses the keywords `lores` and `gridded` to choose between
        the various projection algorithms.
        """
        if gridded:
            counts = self.obj_counts_grid(sourceflux, **extras)
        elif lores:
            counts = self.obj_counts_lores(sourcewave, sourceflux, **extras)
        else:
            counts = self.obj_counts_hires(sourcewave, sourceflux, **extras)
    
    def ab_mag(self, sourcewave, sourceflux, **extras):
        """Project source spectrum onto filter and return the AB magnitude.

        :param sourcewave:
            Spectrum wavelength (in AA), ndarray of shape (nwave).

        :param sourceflux:
            Associated flux (assumed to be in erg/s/cm^2/AA), ndarray of shape
            (nobj,nwave).

        :returns mag:
            AB magnitude of the source.
        """
        counts = self.obj_counts(sourcewave, sourceflux, **extras)
        return -2.5 * np.log10(counts / self.ab_zero_counts)

    def vega_mag(self, sourcewave, sourceflux, **extras):
        """Project source spectrum onto filter and return the Vega magnitude.

        :param sourcewave:
            Spectrum wavelength (in AA), ndarray of shape (nwave).

        :param sourceflux:
            Associated flux (assumed to be in erg/s/cm^2/AA), ndarray of shape
            (nobj,nwave).

        :returns mag:
            Vega magnitude of the source.
        """
        counts = self.obj_counts(sourcewave, sourceflux, **extras)
        return -2.5 * np.log10(counts / self.vega_zero_counts)

# ------------
# Useful utilities
# -------------


def load_filters(filternamelist, **kwargs):
    """Given a list of filter names, this method returns a list of Filter
    objects.

    :param filternamelist:
        List of strings giving names of the filters.

    :returns filterlist:
        A list of filter objects.
    """
    return [Filter(f, **kwargs) for f in filternamelist]


def getSED(sourcewave, sourceflux, filterlist=None, **kwargs):
    """Takes wavelength vector, a flux array and list of Filter objects and
    returns the SED in AB magnitudes.

    :param sourcewave:
        Spectrum wavelength (in AA), ndarray of shape (nwave).

    :param sourceflux:
        Associated flux (assumed to be in erg/s/cm^2/AA), ndarray of shape
        (nsource,nwave).

    :param filterlist:
        List of filter objects, of length nfilt.

    :returns sed:
        array of broadband magnitudes, of shape (nsource, nfilter).
    """
    if filterlist is None:
        return None
    sourceflux = np.atleast_2d(sourceflux)
    sedshape = list(sourceflux.shape[:-1]) + [len(filterlist)]
    sed = np.zeros(sedshape)
    for i, f in enumerate(filterlist):
        sed[..., i] = f.ab_mag(sourcewave, sourceflux, **kwargs)
    return np.squeeze(sed)


def list_available_filters():
    """Return a list of filter names that are available by default, i.e. which
    have been installed in the sedpy/data/filters/ directory.
    """
    try:
        names = resource_listdir('sedpy', '/data/filters/')
    except:
        names = os.listdir(os.path.join(sedpydir, '/data/filters/'))

    parfiles = [n.replace('.par', '') for n in names if n[-4:] == '.par']
    return parfiles


def filter_dict(filterlist):
    fdict = {}
    for i, f in enumerate(filterlist):
        fdict[f.nick] = i
    return fdict

# ------------------
# Routines for spectra
# ------------------


def Lbol(wave, spec, wave_min=90, wave_max=1e6):
    """Calculate the bolometric luminosity of a spectrum or spectra.

    :param wave:
       The wavelength vector of length nwave.

    :param spec:
       The spectra, of shape (...,nsource, nwave), in F_lambda.

    :param wave_min:
       Minimum wavelength for the integral.

    :param max_wave:
       Maximum wavelength for the integral

    :returns lbol:
       The bolometric luminosity, integrated from wave_min to wave_max.  Array
       of length (...nsource)
    """
    inds = np.where(np.logical_and(wave < wave_max, wave >= wave_min))
    return np.trapz(spec[..., inds[0]], wave[inds])


def air2vac(air):
    """Convert from in-air wavelengths to vacuum wavelengths.  Based on Allen's
    Astrophysical Quantities.

    :param air:
        The in-air wavelengths.

    :returns vac:
        The corresponding vacuum wavelengths.
    """
    ss = 1e4 / air
    vac = air * (1 + 6.4328e-5 + 2.94981e-2 / (146 - ss**2) +
                 2.5540e-4 / (41 - ss**2))
    return vac


def vac2air(vac):
    """Convert from vacuum wavelengths to in-air wavelengths.  Follows the SDSS
    statement of the IAU standard from Morton 1991 ApJS.

    vac2air(air2vac(wave)) yields wave to within 1 part in a million over the
    optical range.

    :param vac:
        The vacuum wavelengths.

    :returns air:
        The corresponding in-air wavelengths.
    """
    conv = (1.0 + 2.735182e-4 + 131.4182 / vac**2 + 2.76249e8 / vac**4)
    return vac / conv



def selftest():
    """Compare to the values obtained from the K-correct code
    (which uses a slightly different Vega spectrum)
    """
    filternames = ['galex_FUV',
                   'sdss_u0','sdss_g0','sdss_r0','sdss_i0',
                   'spitzer_irac_ch2']
    weff_kcorr = [1528.0,
                  3546.0, 4669.6, 6156.25, 7471.57,
                  44826.]
    msun_kcorr = [18.8462,
                  6.38989, 5.12388, 4.64505, 4.53257,
                  6.56205]
    ab2vega_kcorr = [2.3457,
                     0.932765, -0.0857, 0.155485, 0.369598,
                     3.2687]

    filterlist = loadFilters(filternames)
    for i in range(len(filterlist)):
        print(filterlist[i].wave_effective, filterlist[i].solar_ab_mag,
              filterlist[i].ab_to_vega)
        assert (abs(filterlist[i].wave_effective - weff_kcorr[i]) <
                (weff_kcorr[i] * 0.01))
        assert abs(filterlist[i].solar_ab_mag - msun_kcorr[i]) < 0.05
        # the below fails because of the vega spectrum used by k_correct
        # assert abs(filterlist[i].ab_to_vega+ab2vega_kcorr[i]) < 0.05
