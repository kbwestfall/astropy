# Licensed under a 3-clause BSD style license - see LICENSE.rst
"""
Defines a class used to store and interface with covariance matrices.
"""

from pathlib import Path

import numpy as np
from matplotlib import pyplot as plt
from scipy import sparse

from astropy import log, table
from astropy.io import fits
from astropy.units import Quantity

from .nddata import NDUncertainty

__all__ = ["Covariance"]


class Covariance(NDUncertainty):
    r"""
    A general utility for storing, manipulating, and file I/O of sparse
    covariance matrices.

    This subclasses from `~astropy.nddata.NDUncertainty` largely to enable more
    seamless integration into `~specutils.Spectrum1D`.

    Parameters
    ----------
    array : array-like, `~scipy.sparse.csr_matrix`, optional
        Covariance matrix to store. Input **must** be covariance data, not
        correlation data.  If None, the covariance object is instantiated as
        being empty.

    impose_triu : :obj:`bool`, optional
        Flag to force the array to only contain non-zero elements in its upper
        triangle. Covariance matrices are symmetric by definition, :math:`C_{ij}
        = C_{ji}`, so it's not necessary to keep both values. This flag will
        force a call to `~scipy.sparse.triu` when setting the covariance matrix.
        If False, the input matrix is **assumed** to only have the upper
        triangle of numbers.

    raw_shape : :obj:`tuple`, optional
        The covariance data is for a higher dimensional array with this shape.
        For example, if the covariance data is for a 2D image with shape
        ``(nx,ny)`` -- the shape of the covariance array is be ``(nx*ny,
        nx*ny)`` -- set ``raw_shape=(nx,ny)``. This is primarily used for
        reading and writing; see also :func:`transpose_raw_shape`.  If None, any
        higher dimensionality is ignored.

    unit : unit-like, optional
        Unit for the covariance values.

    Raises
    ------
    TypeError
        Raised if the input array not a `scipy.sparse.csr_matrix` object and
        cannot be converted to one.

    ValueError
        Raised if ``raw_shape`` is provided and the input covariance matrix
        ``array`` does not have the expected shape.  I.e., if
        ``raw_shape=(nx,ny)``, the covariance matrix must have the shape
        ``(nx*ny,nx*ny)``.

    Attributes
    ----------
    cov : `~scipy.sparse.csr_matrix`
        The covariance matrix stored in sparse format.

    shape : :obj:`tuple`
        Shape of the full array.

    raw_shape : :obj:`tuple`
        The covariance data is for a higher dimensional array with this shape.
        For example, if the covariance data is for a 2D image, this would be
        ``(nx,ny)`` and the shape of the covariance array would be ``(nx*ny,
        nx*ny)``.

    nnz : :obj:`int`
        The number of non-zero covariance matrix elements.

    var : `~numpy.ndarray`
        Array with the variance provided by the diagonal of the covariance
        matrix. This is only populated if necessary, either by being requested
        (:func:`variance`) or if needed to convert between covariance and
        correlation matrices.

    is_correlation : :obj:`bool`
        Flag that the covariance matrix has been saved as a variance vector and
        a correlation matrix.
    """

    def __init__(self, array=None, impose_triu=False, raw_shape=None, unit=None):
        # Check the input type
        if array is not None and not sparse.isspmatrix_csr(array):
            try:
                _array = sparse.csr_matrix(array)
            except ValueError:
                raise TypeError(
                    "Input covariance matrix is not a scipy.csr_matrix and could "
                    "not be converted to one."
                )
        else:
            _array = array

        super().__init__(array=_array, copy=False, unit=unit)

        self.cov = _array
        self.shape = None
        self.raw_shape = raw_shape
        self.nnz = None
        self.var = None
        self.is_correlation = False

        # Return empty object
        if self.cov is None:
            return

        # Set the number of non-zero covariance values
        self.nnz = self.cov.nnz

        # Set the shape of the full matrix
        self.shape = self.cov.shape
        if self.raw_shape is not None and np.prod(self.raw_shape) != self.shape[0]:
            raise ValueError(
                "Product of raw shape must match the covariance axis length."
            )

        # If requested, impose that the input matrix only have values in
        # its upper triangle.
        if impose_triu:
            self._impose_upper_triangle()

    @property
    def uncertainty_type(self):
        """``"cov"``: `Covariance` implements a covariance matrix."""
        return "cov"

    # Skip error propagation for now
    # TODO: Should these instead throw a NotImplementedError?
    def _propagate_add(self, other_uncert, result_data, correlation):
        return None

    def _propagate_subtract(self, other_uncert, result_data, correlation):
        return None

    def _propagate_multiply(self, other_uncert, result_data, correlation):
        return None

    def _propagate_divide(self, other_uncert, result_data, correlation):
        return None

    @property
    def quantity(self):
        """
        Return the full covariance matrix as an `~astropy.units.Quantity` object.
        """
        return Quantity(self.toarray(), self.unit, copy=False, dtype=self.cov.dtype)

    def _data_unit_to_uncertainty_unit(self, value):
        return value**2

    def __repr__(self):
        return f"<{self.__class__.__name__}; shape = {self.shape}>"

    # TODO: Override the array and array.setter methods!

    @classmethod
    def from_samples(cls, samples, cov_tol=None, rho_tol=None, **kwargs):
        r"""
        Build a covariance object using discrete samples.

        The covariance is generated using `~numpy.cov` for a set of discretely
        sampled data for an :math:`N`-dimensional parameter space.

        Parameters
        ----------
        samples : `~numpy.ndarray`
            Array with samples drawn from an :math:`N`-dimensional parameter
            space. The shape of the input array must be :math:`N_{\rm par}\times
            N_{\rm samples}`.

        cov_tol : :obj:`float`, optional
            Any covariance value less than this is assumed to be equivalent to
            (and set to) 0.

        rho_tol : :obj:`float`, optional
            Any correlation coefficient less than this is assumed to be
            equivalent to (and set to) 0.

        **kwargs : dict
            Passed directly to main instantiation method.

        Returns
        -------
        :class:`Covariance`
            An :math:`N_{\rm par}\times N_{\rm par}` covariance matrix built
            using the provided samples.

        Raises
        ------
        ValueError
            Raised if the input array is not 2D or if the number of samples (length
            of the second axis) is less than 2.
        """
        if samples.ndim != 2:
            raise ValueError("Input samples for covariance matrix must be a 2D array!")
        if samples.shape[1] < 2:
            raise ValueError("Fewer than two samples provided!")
        return Covariance.from_array(
            np.cov(samples), cov_tol=cov_tol, rho_tol=rho_tol, **kwargs
        )

    @classmethod
    def from_array(cls, covar, cov_tol=None, rho_tol=None, raw_shape=None, **kwargs):
        r"""
        Define a covariance object using a dense array.

        Note that the only difference between this construction method and the
        direct construction method is that it allows you to impose tolerances on
        the covariance value and/or correlation coefficients.

        Parameters
        ----------
        covar : array-like
            Array with the covariance data. The shape of the array must be
            square. Input can be any object that can be converted to a dense
            array using the object method ``toarray`` or using
            ``numpy.atleast_2d``.

        cov_tol : :obj:`float`, optional
            Any covariance value less than this is assumed to be equivalent to
            (and set to) 0.

        rho_tol : :obj:`float`, optional
            Any correlation coefficient less than this is assumed to be
            equivalent to (and set to) 0.

        raw_shape : :obj:`tuple`, optional
            The covariance data is for a higher dimensional array with this
            shape.  For example, if the covariance data is for a 2D image with
            shape ``(nx,ny)`` -- the shape of the covariance array is be
            ``(nx*ny, nx*ny)`` -- set ``raw_shape=(nx,ny)``. This is primarily
            used for reading and writing; see also :func:`transpose_raw_shape`.
            If None, any higher dimensionality is ignored.

        **kwargs : dict
            Passed directly to main instantiation method.

        Returns
        -------
        :class:`Covariance`
            The covariance matrix built using the provided array.

        Raises
        ------
        ValueError
            Raised if ``covar`` could not be converted to a dense array.
        """
        try:
            _covar = covar.toarray()
        except AttributeError:
            _covar = np.atleast_2d(covar)
        if not isinstance(_covar, np.ndarray) or _covar.ndim != 2:
            raise ValueError(
                "Could not convert input covariance data into a 2D dense array."
            )

        n = _covar.shape[0]
        if rho_tol is not None:
            variance = np.diag(_covar)
            rho = _covar / np.ma.sqrt(variance[:, None] * variance[None, :])
            rho[np.ma.absolute(rho).filled(0.0) < rho_tol] = 0.0
            _covar = rho.filled(0.0) * np.ma.sqrt(
                variance[:, None] * variance[None, :]
            ).filled(0.0)
        if cov_tol is not None:
            _covar[_covar < cov_tol] = 0.0

        indx = _covar > 0.0
        i, j = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
        return cls(
            array=sparse.coo_matrix(
                (_covar[indx].ravel(), (i[indx].ravel(), j[indx].ravel())), shape=(n, n)
            ).tocsr(),
            impose_triu=True,
            raw_shape=raw_shape,
            **kwargs,
        )

    # TODO: Fix doc, and test transpose
    @classmethod
    def from_fits(cls, source, var_ext="VAR", covar_ext="CORREL", quiet=False):
        r"""
        Read covariance data from a FITS file.

        This read operation matches the data saved to a FITS file using
        :func:`write`. The class can read covariance data written by other
        programs *as long as they have a commensurate format*. See the
        description of the :func:`write` method.

        If the extension names and column names are correct, :class:`Covariance`
        can read FITS files that were not produced explicitly by this method.
        This is useful for files that include the covariance data extensions
        among others.

        The method determines if the output data were reshaped by checking for
        the ``COVRWSHP`` header keyword in the extension with the correlation
        coefficients (``covar_ext``).

        .. warning::

            The storage of covariance matrices for higher dimensional data
            assume a row-major flattening of the relevant data arrays.

        Parameters
        ----------
        source : :obj:`str`, `Path`, `astropy.io.fits.HDUList`
            Initialize the object using an `astropy.io.fits.HDUList` object or
            path to a FITS file.

        var_ext : :obj:`int`, :obj:`str`, optional
            The name or index of the extension (see ``source``) with the
            variance data.  If None, the variance is taken as unity.

        covar_ext : :obj:`int`, :obj:`str`, optional
            The name or index of the extension with covariance data.  This
            **cannot** be None.

        quiet : :obj:`bool`, optional
            Suppress terminal output.

        Returns
        -------
        :class:`Covariance`
            The covariance matrix read from the provided source.

        Raises
        ------
        ValueError
            Raised if ``covar_ext`` is None.
        """
        # Check input
        if covar_ext is None:
            raise ValueError(
                "Must provide extension name or index with the covariance data."
            )

        # Open the provided source, if it hasn't been yet
        source_is_hdu = isinstance(source, fits.HDUList)
        hdu = source if source_is_hdu else fits.open(source)

        # Read shapes
        shape = eval(hdu[covar_ext].header["COVSHAPE"])
        raw_shape = (
            eval(hdu[covar_ext].header["COVRWSHP"])
            if "COVRWSHP" in hdu[covar_ext].header
            else None
        )

        # Read coordinate data
        if raw_shape is None:
            i = hdu[covar_ext].data["INDXI"]
            j = hdu[covar_ext].data["INDXJ"]
        else:
            ndim = hdu[covar_ext].data["INDXI"].shape[1]
            if len(raw_shape) != ndim:
                raise ValueError(
                    "Mismatch between COVRWSHP keyword and tabulated data."
                )
            i = np.ravel_multi_index(hdu[covar_ext].data["INDXI"].T, raw_shape)
            j = np.ravel_multi_index(hdu[covar_ext].data["INDXJ"].T, raw_shape)
        # WARNING: If the data is written correctly, it should always be true that i<=j
        rhoij = hdu[covar_ext].data["RHOIJ"]

        # Number of non-zero elements
        nnz = len(rhoij)

        # (Read) Variance data
        var = (
            np.ones(shape[1:], dtype=float)
            if var_ext is None
            else hdu[var_ext].data.ravel()
        )

        # Units
        unit = hdu[covar_ext].header.get("BUNIT", None)

        # Done with the hdu so close it, if necessary
        if not source_is_hdu:
            hdu.close()

        # Set covariance data
        cij = rhoij * np.sqrt(var[i] * var[j])
        cov = sparse.coo_matrix((cij, (i, j)), shape=shape).tocsr()

        # Report
        if not quiet:
            log.info("Read covariance cube:")
            log.info(f"             shape: {shape}")
            log.info(f"   non-zero values: {nnz}")

        return cls(array=cov, raw_shape=raw_shape, unit=unit)

    @classmethod
    def from_matrix_multiplication(cls, T, Sigma, **kwargs):
        r"""
        Construct the covariance matrix that results from a matrix
        multiplication.

        The matrix multiplication should be of the form:

        .. math::

            {\mathbf T} \times {\mathbf X} = {\mathbf Y}

        where :math:`{\mathbf T}` is a transfer matrix of size :math:`N_y\times
        N_x`, :math:`{\mathbf X}` is a vector of size :math:`N_x`, and
        :math:`{\mathbf Y}` is a vector of length :math:`{N_y}` that results
        from the multiplication.

        The covariance matrix is then

        .. math::

             {\mathbf C} = {\mathbf T} \times {\mathbf \Sigma} \times
             {\mathbf T}^{\rm T},

        where :math:`{\mathbf \Sigma}` is the covariance matrix for the elements
        of :math:`{\mathbf X}`. If ``Sigma`` is provided as a vector of length
        :math:`N_x`, it is assumed that the elements of :math:`{\mathbf X}` are
        independent and the provided vector gives the *variance* in each
        element; i.e., the provided data represent the diagonal of
        :math:`{\mathbf \Sigma}`.

        Parameters
        ----------
        T : `~scipy.sparse.csr_matrix`, `~numpy.ndarray`
            Transfer matrix.  See above.

        Sigma : `~scipy.sparse.csr_matrix`, `~numpy.ndarray`
            Covariance matrix.  See above.

        **kwargs : dict
            Passed directly to main instantiation method.

        Returns
        -------
        :class:`Covariance`
            The covariance matrix resulting from the matrix multiplication.

        Raises
        ------
        ValueError
            Raised if the provided arrays are not two dimensional or if there is
            a shape mismatch.
        """
        if T.ndim != 2:
            raise ValueError("Input transfer matrix must be two-dimensional.")
        nx = T.shape[1]
        if Sigma.shape != (nx, nx) and Sigma.shape != (nx,):
            raise ValueError(
                "Shape of input variance matrix must be either "
                f"({nx},{nx}) or ({nx},)."
            )
        # If it isn't already, convert T to a csr_matrix
        _T = T if isinstance(T, sparse.csr_matrix) else sparse.csr_matrix(T)
        # Set the covariance matrix in X
        _Sigma = (
            sparse.coo_matrix(
                (Sigma, (np.arange(nx), np.arange(nx))), shape=(nx, nx)
            ).tocsr()
            if Sigma.ndim == 1
            else (
                Sigma
                if isinstance(Sigma, sparse.csr_matrix)
                else sparse.csr_matrix(Sigma)
            )
        )
        # Construct the covariance matrix
        return cls(
            array=sparse.triu(_T.dot(_Sigma.dot(_T.transpose()))).tocsr(), **kwargs
        )

    @classmethod
    def from_variance(cls, variance, **kwargs):
        r"""
        Construct a diagonal covariance matrix using the provided variance.

        Parameters
        ----------
        variance : `~numpy.ndarray`
            The variance vector.

        **kwargs : dict
            Passed directly to main instantiation method.

        Returns
        -------
        :class:`Covariance`
            The diagonal covariance matrix.
        """
        return cls(array=sparse.csr_matrix(np.diagflat(variance)), **kwargs)

    def _impose_upper_triangle(self):
        """
        Force :attr:`cov` to only contain non-zero elements in its upper
        triangle.
        """
        # TODO: Could also save space by not saving all the 1s along the
        # diagonal...
        self.cov = sparse.triu(self.cov).tocsr()
        self.nnz = self.cov.nnz

    def full(self):
        r"""
        Return a `~scipy.sparse.csr_matrix` object with both its upper and lower
        triangle filled, ensuring that they are symmetric.

        This method is essentially equivalent to :func:`toarray`
        except that it returns a sparse array.

        Returns
        -------
        `~scipy.sparse.csr_matrix`
            The sparse matrix with both the upper and lower triangles filled
            (with symmetric information).
        """
        a = self.cov
        return sparse.triu(a) + sparse.triu(a, 1).T

    def apply_new_variance(self, var):
        """
        Using the same correlation coefficients, return a new
        :class:`Covariance` object with the provided variance.

        Parameters
        ----------
        var : `~numpy.ndarray`
            Variance vector. Must have a length that matches the shape of this
            :class:`Covariance` instance.  Note that, if the covariance is for
            higher dimensional data, this variance array *must* be flattened to
            1D.

        Returns
        -------
        :class:`Covariance`
            A covariance matrix with the same shape and correlation coefficients
            and this object, but with the provided variance.

        Raises
        ------
        ValueError
            Raised if the length of the variance vector is incorrect.
        """
        if var.shape != self.shape[1:]:
            raise ValueError(
                f"Provided variance has incorrect shape.  Expected {self.shape[1:]}, "
                f"found {var.shape}."
            )

        # Convert to a correlation matrix, if needed
        is_correlation = self.is_correlation
        if not is_correlation:
            self.to_correlation()

        # Pull out the non-zero values
        i, j, c = sparse.find(self.cov)
        # Apply the new variance
        new_cov = Covariance(
            array=sparse.coo_matrix(
                (c * np.sqrt(var[i] * var[j]), (i, j)), shape=self.shape
            ).tocsr(),
            raw_shape=self.raw_shape,
            unit=self.unit,
        )

        # Revert to covariance matrix, if needed
        if not is_correlation:
            self.revert_correlation()

        # Return a new covariance matrix
        return new_cov

    def copy(self):
        """
        Return a copy of this Covariance object.

        Returns
        -------
        :class:`Covariance`
            A copy of the current covariance matrix.
        """
        # If the data is saved as a correlation matrix, first revert to
        # a covariance matrix
        is_correlation = self.is_correlation
        if self.is_correlation:
            self.revert_correlation()

        # Create the new Covariance instance with a copy of the data
        cp = Covariance(array=self.cov.copy(), raw_shape=self.raw_shape, unit=self.unit)

        # If necessary, convert the data to a correlation matrix
        if is_correlation:
            self.to_correlation()
            cp.to_correlation()
        return cp

    def toarray(self):
        """
        Convert the sparse covariance matrix to a dense array, filled
        with zeros where appropriate.

        Returns
        -------
        `~numpy.ndarray`
            Dense array with the full covariance matrix.
        """
        return self.full().toarray()

    def show(self, zoom=None, ofile=None, log10=False):
        """
        Show a covariance/correlation matrix data.

        This converts the covariance matrix to a filled array and plots the
        array using `~matplotlib.pyplot.imshow`. If an output file is provided,
        the image is redirected to the designated output file; otherwise, the
        image is plotted to the screen.

        Parameters
        ----------
        zoom : :obj:`float`, optional
            Factor by which to zoom in on the center of the image by *removing
            the other regions of the array*. E.g. ``zoom=2`` will show only the
            central quarter of the covariance matrix.

        ofile : :obj:`str`, optional
            If provided, the plot is output to this file instead of being
            plotted to the screen.

        log10 : :obj:`bool`, optional
            Plot the base-10 log of the covariance value.
        """
        # Convert the covariance matrix to an array
        a = self.toarray()

        # Remove some fraction of the array to effectively zoom in on
        # the center of the covariance matrix
        if zoom is not None:
            xs = int(self.shape[0] / 2 - self.shape[0] / 2 / zoom)
            xe = xs + int(self.shape[0] / zoom) + 1
            ys = int(self.shape[1] / 2 - self.shape[1] / 2 / zoom)
            ye = ys + int(self.shape[1] / zoom) + 1
            a = a[xs:xe, ys:ye]

        # Create the plot
        fig = plt.figure(1)
        im = plt.imshow(np.ma.log10(a) if log10 else a, interpolation="nearest")
        plt.colorbar()
        if ofile is None:
            # Print the plot to the screen if no output file is provided.
            plt.show()
        else:
            # Print the plot to the designated output file
            fig.canvas.print_figure(ofile)
        fig.clear()
        plt.close(fig)

    def find(self):
        """
        Find the non-zero values in the **full** covariance matrix (not just the
        upper triangle).

        Note that, if this matrix is in correlation format (i.e.,
        :attr:`is_correlation` is True), the returned data is for the
        correlation matrix only.

        This is a simple wrapper for :func:`full` and `~scipy.sparse.find`.

        Returns
        -------
        tuple
            A tuple of arrays ``i``, ``j``, and ``c``. The arrays ``i`` and
            ``j`` contain the index coordinates of the non-zero values, and
            ``c`` contains the values themselves.
        """
        return sparse.find(self.full())

    def cov2raw_indices(self, i, j):
        """
        Given indices along the two axes of the covariance matrix, return the
        relevant indices in the data array.

        Parameters
        ----------
        i : `~numpy.ndarray`
            1D array with the index along the first axis of the covariance matrix
        j : `~numpy.ndarray`
            1D array with the index along the second axis of the covariance matrix

        Returns
        -------
        tuple
            Two tuples providing the indices in the associate data array.  If
            :attr:`raw_shape` is not defined, the input arrays are simply
            returned (and not copied).  Otherwise, the code uses
            `~numpy.unravel_index` to calculate the relevant data-array indices;
            each element in the two-tuple is itself a tuple of N arrays, one
            array per dimension of the data array.

        Examples
        --------
        Given a (6,6) covariance matrix associated with a (3,2) data array, the
        covariance values at matrix locations (0,3), (1,4), and (2,3) provide
        the covariance between data array elements [0,0] and [1,1], elements
        [0,1] and [2,0], and elements [1,0] and [1,1]::

        >>> import numpy as np
        >>> from astropy.nddata import Covariance
        >>> cov = Covariance(
        ...          array=np.diag(np.full(6 - 2, 0.2, dtype=float), k=-2)
        ...             + np.diag(np.full(6 - 1, 0.5, dtype=float), k=-1)
        ...             + np.diag(np.full(6, 1.0, dtype=float), k=0)
        ...             + np.diag(np.full(6 - 1, 0.5, dtype=float), k=1)
        ...             + np.diag(np.full(6 - 2, 0.2, dtype=float), k=2),
        ...          impose_triu=True,
        ...          raw_shape=(3,2),
        ...      )
        >>> cov.toarray()
        array([[1. , 0.5, 0.2, 0. , 0. , 0. ],
               [0.5, 1. , 0.5, 0.2, 0. , 0. ],
               [0.2, 0.5, 1. , 0.5, 0.2, 0. ],
               [0. , 0.2, 0.5, 1. , 0.5, 0.2],
               [0. , 0. , 0.2, 0.5, 1. , 0.5],
               [0. , 0. , 0. , 0.2, 0.5, 1. ]])
        >>> cov.cov2raw_indices([0,1,2], [3,4,3])
        ((array([0, 0, 1]), array([0, 1, 0])), (array([1, 2, 1]), array([1, 0, 1])))

        """
        if self.raw_shape is None:
            return i, j
        _i = np.atleast_1d(i).ravel()
        _j = np.atleast_1d(j).ravel()
        return np.unravel_index(_i, self.raw_shape), np.unravel_index(
            _j, self.raw_shape
        )

    def raw2cov_indices(self, i, j):
        """
        Given indices of elements in the source data array, return the matrix
        coordinates with the associated covariance.  This is the inverse of
        :func:`cov2raw_indices`.

        Parameters
        ----------
        i : `tuple`
            A tuple of N array-like objects providing the indices of elements in
            the N-dimensional data array.
        j : `tuple`
            The same as ``i``, but providing a second set of coordinates at
            which to access the covariance.

        Returns
        -------
        tuple
            A tuple of two arrays providing the indices in the covariance matrix
            associated with the provided data array coordinates.  If
            :attr:`raw_shape` is not defined, the input arrays are simply
            returned (and not copied).  Otherwise, the code uses
            `~numpy.ravel_multi_index` to calculate the relevant covariance
            indices.

        Examples
        --------
        This is the inverse of the operation explained in the examples shown for
        :func:`cov2raw_indices`::

        >>> import numpy as np
        >>> from astropy.nddata import Covariance
        >>> cov = Covariance(
        ...          array=np.diag(np.full(6 - 2, 0.2, dtype=float), k=-2)
        ...             + np.diag(np.full(6 - 1, 0.5, dtype=float), k=-1)
        ...             + np.diag(np.full(6, 1.0, dtype=float), k=0)
        ...             + np.diag(np.full(6 - 1, 0.5, dtype=float), k=1)
        ...             + np.diag(np.full(6 - 2, 0.2, dtype=float), k=2),
        ...          impose_triu=True,
        ...          raw_shape=(3,2),
        ...      )
        >>> i_data, j_data = cov.cov2raw_indices([0,1,2], [3,4,3])
        >>> cov.raw2cov_indices(i_data, j_data)
        (array([0, 1, 2]), array([3, 4, 3]))
        """
        if self.raw_shape is None:
            return i, j
        if len(i) != len(self.raw_shape):
            raise ValueError(
                "Length of input coordinate list (i) is incorrect; expected "
                f"{len(self.raw_shape)}, found {len(i)}"
            )
        if len(j) != len(self.raw_shape):
            raise ValueError(
                "Length of input coordinate list (j) is incorrect; expected "
                f"{len(self.raw_shape)}, found {len(i)}"
            )
        return np.ravel_multi_index(i, self.raw_shape), np.ravel_multi_index(
            j, self.raw_shape
        )

    def coordinate_data(self, reshape=False):
        r"""
        Construct data arrays with the non-zero covariance components in
        coordinate format.

        This procedure is primarily used when constructing the data arrays for
        storage.

        Regardless of whether or not the current internal data is a covariance
        matrix or a correlation matrix, the data is *always* returned as the
        combination of a variance array and the associated correlation matrix.

        Matching the class convention, the returned data only includes the upper
        triangle.

        Four items are returned that contain:

            - :math:`i,j`: The row and column indices, respectively, of the
              covariance matrix.  See below.

            - :math:`rho_{ij}`: The correlation coefficient between array
              elements at indices :math:`i` and :math:`j`.

            - :math:`V_i`: The variance in each array element; i.e., the value
              of :math:`C_{ii} \forall i`.

        If reshape is True and :attr:`raw_shape` is defined, the :math:`i,j`
        indices are converted to the expected coordinates in the raw data array
        using `~numpy.unravel_index`.  These can be reverted to the coordinates
        in the covariance matrix using `~numpy.ravel_multi_index`; see examples.

        Parameters
        ----------
        reshape : :obj:`bool`, optional
            Reshape the output according to :attr:`raw_shape`.

        Returns
        -------
        :obj:`tuple`
            Four objects, where the types depend on if the data has been
            reshaped into an image. See the examples. If not reshaping, the four
            returned objects are all `numpy.ndarray` objects as described above
            in the function description.  If reshaping, the first two objects
            returned are tuples with the index arrays along each of the reshaped
            axes.

        Examples
        --------
        Say we have a (3,2) data array::

        >>> import numpy as np
        >>> data = np.arange(6).astype(float).reshape(3,2) / 4 + 5
        >>> data
        array([[5.  , 5.25],
               [5.5 , 5.75],
               [6.  , 6.25]])

        with the following covariance matrix between elements in the array::

        >>> c = (
        ...         np.diag(np.full(6 - 2, 0.2, dtype=float), k=-2)
        ...         + np.diag(np.full(6 - 1, 0.5, dtype=float), k=-1)
        ...         + np.diag(np.full(6, 1.0, dtype=float), k=0)
        ...         + np.diag(np.full(6 - 1, 0.5, dtype=float), k=1)
        ...         + np.diag(np.full(6 - 2, 0.2, dtype=float), k=2)
        ...     )
        >>> c
        array([[1. , 0.5, 0.2, 0. , 0. , 0. ],
               [0.5, 1. , 0.5, 0.2, 0. , 0. ],
               [0.2, 0.5, 1. , 0.5, 0.2, 0. ],
               [0. , 0.2, 0.5, 1. , 0.5, 0.2],
               [0. , 0. , 0.2, 0.5, 1. , 0.5],
               [0. , 0. , 0. , 0.2, 0.5, 1. ]])

        Note that the shape of the covariance matrix is (6,6); the covariance
        matrix has one row and one column associated with each element of the
        original data array.  The mapping between covariance matrix elements to
        the data array assumes a flattened set of coordinates (cf.
        :func:`transpose_raw_shape`).  For example, the flattened index of
        elements ``data[1,0]`` and ``data[2,0]`` are::

        >>> np.ravel_multi_index((np.array([1,2]), np.array([0,0])), (3,2))
        array([2, 4])

        and we can use numpy functions to determine the relevant indices needed
        to find the covariance between these two elements in the data array::

        >>> i_data = (np.array([1]), np.array([0]))
        >>> j_data = (np.array([2]), np.array([0]))
        >>> c[np.ravel_multi_index(i_data, (3,2)), np.ravel_multi_index(j_data, (3,2))]
        array([0.2])

        When a :class:`Covariance` object has a defined :attr:`raw_shape`, which
        is (3,2) in this example, the indices returned as the first two objects
        in this function are equivalent to the ``i_data`` and ``j_data`` objects
        in this example.
        """
        if reshape and self.raw_shape is None:
            raise ValueError(
                "If reshaping, the raw shape of the data before flattening to the "
                "covariance array (`raw_shape`) must be defined."
            )

        # Only ever print correlation matrices
        is_correlation = self.is_correlation
        if not is_correlation:
            # NOTE: This creates the variance array if it doesn't already exist.
            self.to_correlation()

        # Get the data
        i, j, rhoij = sparse.find(self.cov)

        # If object was originally a covariance matrix, revert it back
        if not is_correlation:
            self.revert_correlation()

        # Return the data.  NOTE: The code forces the variance array to be
        # returned as a copy.
        if reshape:
            # Reshape the indices and the variance array.
            return (
                np.unravel_index(i, self.raw_shape),
                np.unravel_index(j, self.raw_shape),
                rhoij,
                self.var.reshape(self.raw_shape).copy(),
            )
        return i, j, rhoij, self.var.copy()

    def output_tables(self):
        r"""
        Return the covariance data separated into a variance array and a
        `~astropy.table.Table` with the correlation data in coordinate format.

        Coordinate format means that the correlation matrix data is provided in
        three columns providing :math:`rho_{ij}` and the (0-indexed) matrix
        coordinates :math:`i,j`.

        The output correlation table has three columns:

            - 'INDXI': The row index in the covariance matrix.

            - 'INDXJ': The column index in the covariance matrix.

            - 'RHOIJ': The correlation coefficient at the relevant :math:`i,j`
              coordinate.

        If :attr:`raw_shape` is set, the output variance array is appropriately
        reshaped, and the correlation matrix indices are reformatted to match
        the coordinates in the N-dimensional array.

        The `~astropy.table.Table` with the correlation data also contains
        metadata keys that provide the shape of the covariance matrix and the
        raw shape of the correlated data arrays, as needed to reconstruct the
        :class:`Covariance` object.

        Returns
        -------
        var : `~numpy.ndarray`
            Array with the variance data.

        correl : `~astropy.table.Table`
            Table with the correlation matrix in coordinate format and the
            relevant metadata needed to reconstruct the :class:`Covariance`
            object.
        """
        meta = {}
        meta["COVSHAPE"] = str(self.shape)
        if self.unit is not None:
            # TODO: Is this the correct way to do this?
            meta["BUNIT"] = self.unit.to_string()
        reshape = self.raw_shape is not None
        i, j, rhoij, var = self.coordinate_data(reshape=reshape)
        if reshape:
            meta["COVRWSHP"] = str(self.raw_shape)
            i = np.column_stack(i)
            j = np.column_stack(j)
            coo_shape = (i.shape[1],)
        else:
            coo_shape = None
        correl = table.Table(
            [
                table.Column(
                    data=i, name="INDXI", dtype=int, length=self.nnz, shape=coo_shape
                ),
                table.Column(
                    data=j, name="INDXJ", dtype=int, length=self.nnz, shape=coo_shape
                ),
                table.Column(data=rhoij, name="RHOIJ", dtype=float, length=self.nnz),
            ],
            meta=meta,
        )
        return var, correl

    def output_hdus(self, hdr=None):
        r"""
        Construct FITS HDUs and header objects that contain the covariance data.

        Parameters
        ----------
        hdr : `~astropy.io.fits.Header`, optional
            `~astropy.io.fits.Header` instance to which to add covariance
            keywords. If None, a new `astropy.io.fits.Header` instance is
            returned.  The only element added to the primary HDU is the shape of
            the covariance array, stored as the value of keyword 'COVSHAPE'.

        Returns
        -------
        tuple
            Returns three objects: (1) The header for the primary HDU; (2) an
            `~astropy.io.fits.ImageHDU` object with the variance vector; and (3)
            an `~astropy.io.fits.BinTableHDU` object with the correlation
            coefficients.

        Raises
        ------
        TypeError
            Raised if the input ``hdr`` does not have the correct type.
        """
        # Use input header or create a minimal one
        _hdr = fits.Header() if hdr is None else hdr
        # Ensure the input header has the correct type
        if not isinstance(_hdr, fits.Header):
            raise TypeError("Input header must have type astropy.io.fits.Header.")
        # Add the shape of the covariance to the primary header
        _hdr["COVSHAPE"] = (str(self.shape), "Shape of the correlation matrix")
        # Get the output data
        var, correl = self.output_tables()
        # Construct and return the HDUs
        return (
            _hdr,
            fits.ImageHDU(data=var, name="VAR"),
            fits.BinTableHDU(data=correl, name="CORREL"),
        )

    def write(self, ofile, hdr=None, overwrite=False):
        r"""
        Write the covariance object to a FITS file.

        Objects written using this function can be reinstantiated using
        :func:`from_fits`.

        The covariance matrix is stored in "coordinate" format using FITS binary
        tables; see `~scipy.sparse.coo_matrix`. The matrix is *always* stored as
        a correlation matrix, even if the object is currently in the state
        holding the covariance data.

        Independent of the dimensionality of the covariance matrix, the written
        file has a ``PRIMARY`` extension with the keyword ``COVSHAPE`` that
        specifies the original dimensions of the covariance matrix; see
        :attr:`shape`.

        The correlation data are written to the ``CORREL`` extension. The column
        names are:

            - ``INDXI``, ``INDXJ``: indices in the original data array.  These
              columns will contain one value per dimension of the input data.
              If the dimensionality of the data is 2 or more, these indices must
              be "flattened" to produce the relevant indices in the correlation
              matrix; see :func:`coordinate_data` and :attr:`raw_shape`.

            - ``RHOIJ``: The non-zero correlation coefficients located
              the specified coordinates.

        The variance along the diagonal of the covariance matrix is output in an
        ImageHDU in extension ``'VAR'``.

        Parameters
        ----------
        ofile : :obj:`str`
            File name for the output.

        hdr : `~astropy.io.fits.Header`, optional
            A header object to edit and include in the PRIMARY extension.  The
            SHAPE keyword will be added/overwritten.  If None, a blank header is
            instantiated.

        overwrite : :obj:`bool`, optional
            Overwrite any existing file.

        Raises
        ------
        FileExistsError
            Raised if the output file already exists and overwrite is False.
        """
        _ofile = Path(ofile).absolute()
        if _ofile.is_file() and not overwrite:
            raise FileExistsError(
                f"{ofile} exists!  Use 'overwrite=True' to overwrite."
            )

        # Construct HDUList and write the FITS file
        _hdr, ivar_hdu, covar_hdu = self.output_hdus(hdr=hdr)
        fits.HDUList([fits.PrimaryHDU(header=_hdr), ivar_hdu, covar_hdu]).writeto(
            ofile, overwrite=overwrite, checksum=True
        )

    def variance(self, copy=True):
        """
        Return the variance vector of the covariance matrix.

        Parameters
        ----------
        copy : :obj:`bool`, optional
            If false, return a reference to :attr:`var`; otherwise, return a
            copy.

        Returns
        -------
        `~numpy.ndarray`
            The array of variances.
        """
        if self.var is not None:
            return self.var.copy() if copy else self.var

        self.var = np.diag(self.cov.toarray()).copy()
        return self.var

    def to_correlation(self):
        r"""
        Convert the covariance matrix into a correlation matrix by dividing each
        element by the variances.

        Specifically, construct :attr:`var` (:math:`V = \sigma^2`) and convert
        :attr:`cov` from a covariance matrix with elements :math:`C_{ij}` to a
        correlation matrix with :math:`\rho_{ij}` such that

        .. math::

            C_{ij} \equiv \rho_{ij} \sigma_i \sigma_j,

        where the variance is, e.g., :math:`\sigma^2_i = C_{ii}`.

        If the matrix is a correlation matrix already (see
        :attr:`is_correlation`), no operations are performed.  Otherwise, the
        variance is computed, if necessary, and used to normalize the covariance
        values.

        A :class:`Covariance` object can be reverted from a correlation matrix
        using :func:`revert_correlation`.
        """
        # Object is already a correlation matrix
        if self.is_correlation:
            return

        # Ensure that the variance has been calculated
        self.variance()

        self.is_correlation = True
        i, j, c = sparse.find(self.cov)
        self.cov = sparse.coo_matrix(
            (c / np.sqrt(self.var[i] * self.var[j]), (i, j)), shape=self.shape
        ).tocsr()

    def revert_correlation(self):
        r"""
        Revert the object from a correlation matrix back to a full covariance
        matrix.

        That is, this is the reverse operation of :func:`to_correlation`.
        Nothing is done if the :attr:`is_correlation` flag is False.
        Importantly, the variances must have already been calculated!
        """
        if not self.is_correlation:
            return

        i, j, c = sparse.find(self.cov)
        self.cov = sparse.coo_matrix(
            (c * np.sqrt(self.var[i] * self.var[j]), (i, j)), shape=self.shape
        ).tocsr()
        self.is_correlation = False
