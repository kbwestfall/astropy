# Licensed under a 3-clause BSD style license - see LICENSE.rst
"""
Defines a class used to store and interface with covariance matrices.
"""

import warnings
from pathlib import Path

import numpy as np

from astropy import table
from astropy.io import fits
from astropy.units import Quantity
from astropy.utils.compat.optional_deps import HAS_SCIPY
from astropy.utils.exceptions import AstropyUserWarning

from .nddata import NDUncertainty

# Required scipy.sparse imports
if HAS_SCIPY:
    from scipy.sparse import coo_matrix, csr_matrix, find, isspmatrix_csr, triu
else:
    err = "Use of 'astropy.nddata.Covariance' requires 'scipy.sparse' module!"

    def find(*args, **kwargs):
        raise ModuleNotFoundError(err)

    def triu(*args, **kwargs):
        raise ModuleNotFoundError(err)

    def csr_matrix(*args, **kwargs):
        raise ModuleNotFoundError(err)

    def coo_matrix(*args, **kwargs):
        raise ModuleNotFoundError(err)

    def isspmatrix_csr(*args, **kwargs):
        raise ModuleNotFoundError(err)


__all__ = ["Covariance"]

# Disabling doctests when scipy isn't present.
__doctest_requires__ = {"*": ["scipy"]}


def _get_csr(arr):
    """
    Confirm the array is a `~scipy.sparse.csr_matrix` or try to convert it to
    one.

    Parameters
    ----------
    arr : array-like
        An array that either is a `~scipy.sparse.csr_matrix` or can be converted
        to one.

    Returns
    -------
    `~scipy.sparse.csr_matrix`
        Converted or original matrix.
    """
    # Check the input type
    if isspmatrix_csr(arr):
        return arr
    try:
        return csr_matrix(arr)
    except ValueError:
        raise TypeError(
            "Input matrix is not a scipy.sparse.csr_matrix and could "
            "not be converted to one."
        )


def _impose_sparse_value_threshold(arr, threshold):
    """
    Remove values from a sparse matrix if their absolute value is below the
    provided threshold.

    Parameters
    ----------
    arr : `~scipy.sparse.csr_matrix`
        Array to manipulate.
    threshold : :obj:`float`
        Threshold value

    Returns
    -------
    `~scipy.sparse.csr_matrix`
        Manipulated or original matrix.
    """
    i, j, aij = find(arr)
    index = np.logical_not(np.absolute(aij) < threshold)
    if all(index):
        return arr
    return coo_matrix((aij[index], (i[index], j[index])), shape=arr.shape).tocsr()


def _parse_shape(shape):
    """
    Parse a string representation of an array shape into a tuple.

    Parameters
    ----------
    shape : str
        String representation of the tuple.  It should only contain
        comma-separated integers and parentheses.

    Returns
    -------
    tuple
        Tuple with the shape.
    """
    return tuple([int(n) for n in shape.strip("()").split(",") if len(n) > 0])


class Covariance(NDUncertainty):
    r"""
    A general utility for storing, manipulating, and I/O of covariance matrices.

    Covariance matrices of higher dimensional arrays are always assumed to be
    stored following row-major indexing.  That is, the covariance value
    :math:`\Sigma_{ij}` for an image of size :math:`(N_x,N_y)` is the covariance
    between image pixels :math:`I_{x_i,y_i}` and :math:`I_{x_j,y_j}`, where
    :math:`i = x_i + N_x y_i` and, similarly, :math:`j = x_j + N_x y_j`.

    Covariance matrices are symmetric by definition, :math:`\Sigma_{ij} =
    \Sigma_{ji}`, so it is not necessary to keep both the upper and lower
    triangles of the matrix.  Instantiation of this object *always* uses
    `~scipy.sparse.triu` when setting the covariance matrix data.

    Parameters
    ----------
    array : array-like, `~scipy.sparse.csr_matrix`
        Covariance matrix to store. Input **must** be covariance data, not
        correlation data.  If the array is not a `~scipy.sparse.csr_matrix`
        instance, it must be convertible to one.  To match the calling sequence
        for `NDUncertainty`, ``array`` has a default value of None, but it
        *must* be provided.

    raw_shape : :obj:`tuple`, optional
        The covariance data is for a higher dimensional array with this shape.
        For example, if the covariance data is for a 2D image with shape
        ``(nx,ny)`` -- the shape of the covariance array must be ``(nx*ny,
        nx*ny)`` -- set ``raw_shape=(nx,ny)``. This is primarily used for
        reading and writing.  If None, any higher dimensionality is ignored.

    unit : unit-like, optional
        Unit for the covariance values.

    Raises
    ------
    TypeError
        Raised if the input array not a `scipy.sparse.csr_matrix` object and
        cannot be converted to one.

    ValueError
        Raised if ``raw_shape`` is provided and the input covariance matrix
        ``array`` does not have the expected shape or if ``array`` is None.

    Attributes
    ----------
    raw_shape : :obj:`tuple`
        The covariance data is for a higher dimensional array with this shape.
        For example, if the covariance data is for a 2D image, this would be
        ``(nx,ny)`` and the shape of the covariance array would be ``(nx*ny,
        nx*ny)``.
    """

    _var_ext = "VAR"
    """
    Name of the FITS extension used to save the variance data.
    """

    _covar_ext = "CORREL"
    """
    Name of the FITS extension used to save the correlation data in coordinate
    format.
    """

    def __init__(self, array=None, raw_shape=None, unit=None):
        if array is None:
            raise ValueError("Covariance object cannot be instantiated with None.")

        # Convert the covariance matrix to a correlation matrix for storage
        self._var, self._rho = Covariance.to_correlation(array)
        # The correlation matrix is symmetric by definition (or it should be!),
        # so only keep the upper triangle.
        self._rho = triu(self._rho).tocsr()

        # Set the raw shape and check it; note self._rho must be defined so that
        # call to self.shape below is valid.
        self.raw_shape = raw_shape
        if self.raw_shape is not None and np.prod(self.raw_shape) != self.shape[0]:
            raise ValueError(
                "Product of ``raw_shape`` must match the covariance axis length."
            )

        # Workspace for index mapping from flattened to original data arrays
        self._data_index_map = None

        super().__init__(array=self._rho, copy=False, unit=unit)

    @property
    def shape(self):
        """Tuple with the shape of the covariance matrix"""
        return self._rho.shape

    @property
    def nnz(self):
        """
        The number of non-zero (NNZ) elements in the full covariance matrix,
        *including* both the upper and lower triangles.
        """
        return self.stored_nnz * 2 - self._rho.shape[0]

    @property
    def stored_nnz(self):
        """
        The number of non-zero elements stored by the object, which only
        counts the non-zero elements in the upper triangle.
        """
        return self._rho.nnz

    @property
    def variance(self):
        """
        The diagonal of the covariance matrix.
        """
        return self._var

    @variance.setter
    def variance(self, value):
        raise NotImplementedError(
            "Directly setting variance values is not allowed for Covariance objects."
        )

    @property
    def uncertainty_type(self):
        """``"cov"``: `Covariance` implements a covariance matrix."""
        return "cov"

    @property
    def quantity(self):
        """
        The covariance matrix as an dense `~astropy.units.Quantity` object.
        """
        return Quantity(self.to_dense(), self.unit, copy=False, dtype=self._rho.dtype)

    def _data_unit_to_uncertainty_unit(self, value):
        """
        Return the uncertainty unit for covariances given the data unit.
        """
        return value**2

    def __repr__(self):
        return f"<{self.__class__.__name__}; shape = {self.shape}>"

    # Skip error propagation for now
    def _propagate_add(self, other_uncert, result_data, correlation):
        return None

    def _propagate_subtract(self, other_uncert, result_data, correlation):
        return None

    def _propagate_multiply(self, other_uncert, result_data, correlation):
        return None

    def _propagate_divide(self, other_uncert, result_data, correlation):
        return None

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
            The absolute value of any *covariance* matrix entry less than this
            is assumed to be equivalent to (and set to) 0.

        rho_tol : :obj:`float`, optional
            The absolute value of any *correlation coefficient* less than this
            is assumed to be equivalent to (and set to) 0.

        **kwargs : dict, optional
            Passed directly to main instantiation method.

        Returns
        -------
        `Covariance`
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
    def from_array(cls, covar, cov_tol=None, rho_tol=None, **kwargs):
        r"""
        Define a covariance object from an array.

        .. note::

            The only difference between this method and the direct instantiation
            method (i.e., ``Covariance(array=covar)``) is that it can be used to
            impose tolerances on the covariance value and/or correlation
            coefficients.

        Parameters
        ----------
        covar : array-like
            Array with the covariance data. The object must be either a
            `~scipy.sparse.csr_matrix` or an object that can be converted to
            one.  It must also be 2-dimensional and square.

        cov_tol : :obj:`float`, optional
            The absolute value of any *covariance* matrix entry less than this
            is assumed to be equivalent to (and set to) 0.

        rho_tol : :obj:`float`, optional
            The absolute value of any *correlation coefficient* less than this
            is assumed to be equivalent to (and set to) 0.

        **kwargs : dict, optional
            Passed directly to main instantiation method.

        Returns
        -------
        `Covariance`
            The covariance matrix built using the provided array.
        """
        var, rho = Covariance.to_correlation(covar)
        if rho_tol is not None:
            rho = _impose_sparse_value_threshold(rho, rho_tol)
        _covar = Covariance.revert_correlation(var, rho)
        if cov_tol is not None:
            _covar = _impose_sparse_value_threshold(_covar, cov_tol)
        return cls(array=_covar, **kwargs)

    @classmethod
    def from_table(cls, var, correl):
        r"""
        Construct the covariance matrix from a variance array and a table with
        the correlation matrix in coordinate format.

        This is the inverse operation of `to_table`.  The class can read
        covariance data written by other programs *as long as they have a
        commensurate format*; see `to_table`.

        Parameters
        ----------
        var : `~numpy.ndarray`
            Array with the variance data; i.e. the diagonal of the covariance
            matrix.

        correl : `~astropy.table.Table`
            The correlation matrix in coordinate format; see `to_table`.

        Returns
        -------
        `Covariance`
            The covariance matrix constructed from the tabulated data.

        Raises
        ------
        ValueError
            Raised if ``correl.meta`` is None, if the provide variance array
            does not have the correct size, or if the data is multidimensional
            and the table columns do not have the right shape.
        """
        # Read shapes
        if "COVSHAPE" not in correl.meta:
            raise ValueError("Table meta dictionary *must* contain COVSHAPE")

        shape = _parse_shape(correl.meta["COVSHAPE"])
        raw_shape = (
            _parse_shape(correl.meta["COVRWSHP"]) if "COVRWSHP" in correl.meta else None
        )

        if var.size != shape[0]:
            raise ValueError(
                f"Incorrect size of variance array; expected {shape[0]}, "
                f"found {var.size}."
            )
        _var = var.ravel()

        # Number of non-zero elements
        nnz = len(correl)

        # Read coordinate data
        # WARNING: If the data is written correctly, it should always be true that i<=j
        if raw_shape is None:
            i = correl["INDXI"].data
            j = correl["INDXJ"].data
        else:
            ndim = correl["INDXI"].shape[1]
            if len(raw_shape) != ndim:
                raise ValueError(
                    "Mismatch between COVRWSHP keyword and tabulated data."
                )
            i = np.ravel_multi_index(correl["INDXI"].data.T, raw_shape)
            j = np.ravel_multi_index(correl["INDXJ"].data.T, raw_shape)

        # Units
        unit = correl.meta.get("BUNIT", None)

        # Set covariance data
        cij = correl["RHOIJ"].data * np.sqrt(_var[i] * _var[j])
        cov = coo_matrix((cij, (i, j)), shape=shape).tocsr()
        # Fill in the lower triangle (primarily to avoid the warning from the
        # to_correlation method!)
        cov = triu(cov) + triu(cov, 1).T
        # Instantiate
        return cls(array=cov, raw_shape=raw_shape, unit=unit)

    @classmethod
    def read(cls, source):
        r"""
        Read covariance data from a FITS file.

        This read operation matches the data saved to a FITS file using `write`.
        The class can read covariance data written by other programs *as long as
        they have a commensurate format*. See the description of the `write`
        method.

        Parameters
        ----------
        source : :obj:`str`, `~pathlib.Path`, `~astropy.io.fits.HDUList`
            Source containing the data.  It can be a string name, `pathlib.Path`
            object, or a previously opened `~astropy.io.fits.HDUList`.

        Returns
        -------
        `Covariance`
            The covariance matrix read from the provided source.
        """
        # Open the provided source, if it hasn't been yet
        source_is_hdu = isinstance(source, fits.HDUList)
        hdu = source if source_is_hdu else fits.open(source)

        # Parse data
        if cls._var_ext in hdu:
            var = hdu[cls._var_ext].data.ravel()
        else:
            shape = _parse_shape(hdu[cls._covar_ext].header["COVSHAPE"])
            var = np.ones(shape[1:], dtype=float)
        correl = table.Table(
            hdu[cls._covar_ext].data, meta=dict(hdu[cls._covar_ext].header)
        )

        # Done with the hdu so close it, if necessary
        if not source_is_hdu:
            hdu.close()

        # Construct and return
        return cls.from_table(var, correl)

    @classmethod
    def from_matrix_multiplication(cls, T, Sigma, **kwargs):
        r"""
        Construct the covariance matrix that results from a matrix
        multiplication.

        Linear operations on a dataset (e.g., binning or smoothing) can be
        written as matrix multiplications of the form

        .. math::

            {\mathbf y} = {\mathbf T}\ {\mathbf x},

        where :math:`{\mathbf T}` is a transfer matrix of size :math:`N_y\times
        N_x`, :math:`{\mathbf x}` is a vector of size :math:`N_x`, and
        :math:`{\mathbf y}` is a vector of length :math:`{N_y}` that results
        from the multiplication.  If :math:`{\mathbf \Sigma}_x` is the
        covariance matrix for :math:`{\mathbf x}`, then the covariance matrix
        for :math:`{\mathbf Y}` is

        .. math::

            {\mathbf \Sigma}_y = {\mathbf T}\ {\mathbf \Sigma}_x\
            {\mathbf T}^\top.

        If ``Sigma`` is provided as a vector of length :math:`N_x`, it is
        assumed that the elements of :math:`{\mathbf X}` are independent and the
        provided vector gives the *variance* in each element; i.e., the provided
        data represent the diagonal of :math:`{\mathbf \Sigma}`.

        Parameters
        ----------
        T : `~scipy.sparse.csr_matrix`, `~numpy.ndarray`
            Transfer matrix.  See above.

        Sigma : `~scipy.sparse.csr_matrix`, `~numpy.ndarray`
            Covariance matrix.  See above.

        **kwargs : dict, optional
            Passed directly to main instantiation method.

        Returns
        -------
        `Covariance`
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
                f"Shape of input variance matrix must be either ({nx},{nx}) or ({nx},)."
            )
        # If it isn't already, convert T to a csr_matrix
        _T = T if isinstance(T, csr_matrix) else csr_matrix(T)
        # Set the covariance matrix in X
        _Sigma = (
            coo_matrix((Sigma, (np.arange(nx), np.arange(nx))), shape=(nx, nx)).tocsr()
            if Sigma.ndim == 1
            else (Sigma if isinstance(Sigma, csr_matrix) else csr_matrix(Sigma))
        )
        # Construct the covariance matrix
        return cls(_T.dot(_Sigma.dot(_T.transpose())).tocsr(), **kwargs)

    @classmethod
    def from_variance(cls, variance, **kwargs):
        """
        Construct a diagonal covariance matrix using the provided variance.

        Parameters
        ----------
        variance : `~numpy.ndarray`
            The variance vector.

        **kwargs : dict, optional
            Passed directly to main instantiation method.

        Returns
        -------
        `Covariance`
            The diagonal covariance matrix.
        """
        return cls(csr_matrix(np.diagflat(variance)), **kwargs)

    def to_sparse(self, correlation=False):
        """
        Return the full covariance matrix as a `~scipy.sparse.csr_matrix`
        object.

        This method is essentially equivalent to `to_dense` except that it
        returns a sparse array.

        Parameters
        ----------
        correlation : :obj:`bool`, optional
            Return the *correlation* matrix.  If False, return the covariance
            matrix.

        Returns
        -------
        `~scipy.sparse.csr_matrix`
            The sparse matrix with both the upper and lower triangles filled
            (with symmetric information).
        """
        _rho = triu(self._rho) + triu(self._rho, 1).T
        if correlation:
            return _rho
        return Covariance.revert_correlation(self._var, _rho)

    def apply_new_variance(self, var):
        """
        Using the same correlation coefficients, return a new `Covariance`
        object with the provided variance.

        Parameters
        ----------
        var : `~numpy.ndarray`
            Variance vector. Must have a length that matches the shape of this
            `Covariance` instance.  Note that, if the covariance is for
            higher dimensional data, this variance array *must* be flattened to
            1D.

        Returns
        -------
        `Covariance`
            A covariance matrix with the same shape and correlation coefficients
            and this object, but with the provided variance.

        Raises
        ------
        ValueError
            Raised if the length of the variance vector is incorrect.
        """
        if var.shape != self._var.shape:
            raise ValueError(
                f"Provided variance has incorrect shape.  Expected {self._var.shape}, "
                f"found {var.shape}."
            )

        # Create a copy
        cov = self.copy()
        # Replace the variance vector and return
        cov._var = var.copy()
        return cov

    def copy(self):
        """
        Return a copy of this Covariance object.

        Returns
        -------
        `Covariance`
            A copy of the current covariance matrix.
        """
        # Create the new Covariance instance with a copy of the data
        return Covariance(
            Covariance.revert_correlation(self._var, self.to_sparse(correlation=True)),
            raw_shape=self.raw_shape,
            unit=self.unit,
        )

    def to_dense(self, correlation=False):
        """
        Return the full covariance matrix as a `numpy.ndarray` object (a "dense"
        array).

        Parameters
        ----------
        correlation : bool, optional
            Flag to return the correlation matrix, instead of the covariance
            matrix.  Note that setting this to True does *not* also return the
            variance vector.

        Returns
        -------
        `~numpy.ndarray`
            Dense array with the full covariance matrix.
        """
        return self.to_sparse(correlation=correlation).toarray()

    def find(self, correlation=False):
        """
        Find the non-zero values in the **full** covariance matrix (not just the
        upper triangle).

        This is a simple wrapper for `to_sparse` and `~scipy.sparse.find`.

        Parameters
        ----------
        correlation : bool, optional
            Flag to return the correlation data, instead of the covariance data.
            Note that setting this to True does *not* also return the variance
            vector.

        Returns
        -------
        i, j : `numpy.ndarray`
            Arrays containing the index coordinates of the non-zero values in
            the covariance (or correlation) matrix.
        c : `numpy.ndarray`
            The non-zero covariance (or correlation) matrix values located at
            the provided ``i,j`` coordinates.
        """
        return find(self.to_sparse(correlation=correlation))

    def cov2raw_indices(self, i, j):
        r"""
        Given indices along the two axes of the covariance matrix, return the
        relevant indices in the data array.  This is the inverse of
        `raw2cov_indices`.

        Parameters
        ----------
        i : `~numpy.ndarray`
            1D array with the index along the first axis of the covariance
            matrix.  Must be in the range :math:`0...n-1`, where :math:`n` is
            the length of the covariance-matrix axes.

        j : `~numpy.ndarray`
            1D array with the index along the second axis of the covariance
            matrix.  Must be in the range :math:`0...n-1`, where :math:`n` is
            the length of the covariance-matrix axes.

        Returns
        -------
        raw_i, raw_j : tuple, `numpy.ndarray`
            If `raw_shape` is not defined, the input arrays are simply returned
            (and not copied).  Otherwise, the code uses `~numpy.unravel_index`
            to calculate the relevant data-array indices; each element in the
            two-tuple is itself a tuple of :math:`N_{\rm dim}` arrays, one array
            per dimension of the data array.

        Raises
        ------
        ValueError
            Raised if the provided indices fall outside the range of covariance
            matrix.

        Examples
        --------
        Given a (6,6) covariance matrix associated with a (3,2) data array, the
        covariance values at matrix locations (0,3), (1,4), and (2,3) provide
        the covariance between data array elements [0,0] and [1,1], elements
        [0,1] and [2,0], and elements [1,0] and [1,1]:

        >>> import numpy as np
        >>> from astropy.nddata import Covariance
        >>> cov = Covariance(
        ...          array=np.diag(np.full(6 - 2, 0.2, dtype=float), k=-2)
        ...             + np.diag(np.full(6 - 1, 0.5, dtype=float), k=-1)
        ...             + np.diag(np.full(6, 1.0, dtype=float), k=0)
        ...             + np.diag(np.full(6 - 1, 0.5, dtype=float), k=1)
        ...             + np.diag(np.full(6 - 2, 0.2, dtype=float), k=2),
        ...          raw_shape=(3,2),
        ...      )
        >>> cov.to_dense()
        array([[1. , 0.5, 0.2, 0. , 0. , 0. ],
               [0.5, 1. , 0.5, 0.2, 0. , 0. ],
               [0.2, 0.5, 1. , 0.5, 0.2, 0. ],
               [0. , 0.2, 0.5, 1. , 0.5, 0.2],
               [0. , 0. , 0.2, 0.5, 1. , 0.5],
               [0. , 0. , 0. , 0.2, 0.5, 1. ]])
        >>> cov.cov2raw_indices([0,1,2], [3,4,3])  # doctest: +ELLIPSIS
        ((array([0, 0, 1]...), array([0, 1, 0])...), (array([1, 2, 1]...), array([1, 0, 1]...)))

        """
        if self.raw_shape is None:
            if np.any(
                (i < 0) | (i > self.shape[0] - 1) | (j < 0) | (j > self.shape[1] - 1)
            ):
                raise ValueError(
                    "Some indices not valid for covariance matrix with shape "
                    f"{self.shape}."
                )
            return i, j
        return np.unravel_index(
            np.atleast_1d(i).ravel(), self.raw_shape
        ), np.unravel_index(np.atleast_1d(j).ravel(), self.raw_shape)

    def raw2cov_indices(self, i, j):
        r"""
        Given indices of elements in the source data array, return the matrix
        coordinates with the associated covariance.  This is the inverse of
        `cov2raw_indices`.

        Parameters
        ----------
        i : array-like, `tuple`
            A tuple of :math:`N_{\rm dim}` array-like objects providing the
            indices of elements in the N-dimensional data array.  This can be an
            array-like object if ``raw_shape`` is undefined, in which case the
            values must be in the range :math:`0...n-1`, where `n` is the length
            of the data array.

        j : array-like, `tuple`
            The same as `i`, but providing a second set of coordinates at which
            to access the covariance.

        Returns
        -------
        cov_i, cov_j : `numpy.ndarray`
            Arrays providing the indices in the covariance matrix associated
            with the provided data array coordinates.  If ``raw_shape`` is not
            defined, the input arrays are simply returned (and not copied).
            Otherwise, the code uses `~numpy.ravel_multi_index` to calculate the
            relevant covariance indices.

        Raises
        ------
        ValueError
            Raised if the provided indices fall outside the range of data array,
            or if the length of the `i` or `j` tuples is not :math:`N_{\rm
            dim}`.

        Examples
        --------
        This is the inverse of the operation explained in the examples shown for
        `cov2raw_indices`:

        >>> import numpy as np
        >>> from astropy.nddata import Covariance
        >>> cov = Covariance(
        ...          array=np.diag(np.full(6 - 2, 0.2, dtype=float), k=-2)
        ...             + np.diag(np.full(6 - 1, 0.5, dtype=float), k=-1)
        ...             + np.diag(np.full(6, 1.0, dtype=float), k=0)
        ...             + np.diag(np.full(6 - 1, 0.5, dtype=float), k=1)
        ...             + np.diag(np.full(6 - 2, 0.2, dtype=float), k=2),
        ...          raw_shape=(3,2),
        ...      )
        >>> i_data, j_data = cov.cov2raw_indices([0,1,2], [3,4,3])
        >>> cov.raw2cov_indices(i_data, j_data)  # doctest: +ELLIPSIS
        (array([0, 1, 2]...), array([3, 4, 3]...))

        """
        if self.raw_shape is None:
            if np.any(
                (i < 0) | (i > self.shape[0] - 1) | (j < 0) | (j > self.shape[1] - 1)
            ):
                raise ValueError(
                    "Some indices not valid for covariance matrix with shape "
                    f"{self.shape}."
                )
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

        Coordinate format means that the correlation matrix data is provided in
        three columns providing :math:`rho_{ij}` and the (0-indexed) matrix
        coordinates :math:`i,j`.

        This procedure is primarily used when constructing the data arrays for
        storage.  The data is *always* returned as the combination of a variance
        vector and the associated correlation matrix.  Matching the class
        convention, the returned data only includes the upper triangle.

        Parameters
        ----------
        reshape : :obj:`bool`, optional
            If ``reshape`` is True and `raw_shape` is defined, the :math:`i,j`
            indices are converted to the expected coordinates in the raw data
            array using `~numpy.unravel_index`.  These can be reverted to the
            coordinates in the covariance matrix using
            `~numpy.ravel_multi_index`; see examples.  See also
            `raw2cov_indices` and `cov2raw_indices`.

        Returns
        -------
        i, j : tuple, `numpy.ndarray`
            The row and column indices, :math:`i,j`: of the covariance matrix.
            If reshaping, these are tuples with the index arrays along each of
            the reshaped axes.
        rhoij : `numpy.ndarray`
            The correlation coefficient, :math:`rho_{ij}`, between array
            elements at indices :math:`i` and :math:`j`.
        var : `numpy.ndarray`
            The variance, :math:`V_i`, for each data element, which is the
            diagonal of the covariance matrix.

        Raises
        ------
        ValueError
            Raised if `reshape` is True but `raw_shape` is undefined.

        Examples
        --------
        Say we have a (3,2) data array:

        >>> import numpy as np
        >>> data = np.arange(6).astype(float).reshape(3,2) / 4 + 5
        >>> data
        array([[5.  , 5.25],
               [5.5 , 5.75],
               [6.  , 6.25]])

        with the following covariance matrix between elements in the array:

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
        the data array assumes a (row-major) flattened set of coordinates.  For
        example, the flattened index of elements ``data[1,0]`` and ``data[2,0]``
        are:

        >>> np.ravel_multi_index((np.array([1,2]), np.array([0,0])), (3,2))  # doctest: +ELLIPSIS
        array([2, 4]...)

        and we can use `numpy` functions to determine the relevant indices
        needed to find the covariance between these two elements in the data
        array:

        >>> i_data = (np.array([1]), np.array([0]))
        >>> j_data = (np.array([2]), np.array([0]))
        >>> c[np.ravel_multi_index(i_data, (3,2)), np.ravel_multi_index(j_data, (3,2))]
        array([0.2])

        When a `Covariance` object has a defined `raw_shape`, which is (3,2) in
        this example, the indices returned as the first two objects in this
        function are equivalent to the ``i_data`` and ``j_data`` objects in this
        example.

        """
        if reshape and self.raw_shape is None:
            raise ValueError(
                "If reshaping, the raw shape of the data before flattening to the "
                "covariance array (``raw_shape``) must be defined."
            )

        # Get the data (only stores the upper triangle!)
        i, j, rhoij = find(self._rho)

        # Return the data.  NOTE: The code forces the variance array to be
        # returned as a copy.
        if reshape:
            # Reshape the indices and the variance array.
            return (
                np.unravel_index(i, self.raw_shape),
                np.unravel_index(j, self.raw_shape),
                rhoij,
                self._var.reshape(self.raw_shape).copy(),
            )
        return i, j, rhoij, self._var.copy()

    def to_table(self):
        r"""
        Return the covariance data separated into a variance vector and a
        `~astropy.table.Table` with the correlation data in coordinate format.

        Coordinate format means that the correlation matrix data is provided in
        three columns providing :math:`rho_{ij}` and the (0-indexed) matrix
        coordinates :math:`i,j`.

        The output correlation table has three columns:

            - ``'INDXI'``: The row index in the covariance matrix.

            - ``'INDXJ'``: The column index in the covariance matrix.

            - ``'RHOIJ'``: The correlation coefficient at the relevant
              :math:`i,j` coordinate.

        The table also contains the following metadata:

            - ``'COVSHAPE'``: The shape of the covariance matrix

            - ``'BUNIT'``: (If `unit` is defined) The string representation of
              the covariance units.

            - ``'COVRWSHP'``: (If `raw_shape` is defined) The raw shape of the
              associated data array.

        If ``raw_shape`` is set, the output variance array is appropriately
        reshaped, and the correlation matrix indices are reformatted to match
        the coordinates in the N-dimensional array.

        .. warning::

            Recall that the storage of covariance matrices for higher
            dimensional data always assumes a row-major storage order.

        Objects instantiated by this method can be used to re-instantiate the
        `Covariance` object using `from_table`.

        Returns
        -------
        var : `~numpy.ndarray`
            Array with the variance data.

        correl : `~astropy.table.Table`
            Table with the correlation matrix in coordinate format and the
            relevant metadata.
        """
        meta = {}
        meta["COVSHAPE"] = str(self.shape)
        if self.unit is not None:
            meta["BUNIT"] = self.unit.to_string()
        reshape = self.raw_shape is not None
        i, j, rhoij, var = self.coordinate_data(reshape=reshape)
        triu_nnz = rhoij.size
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
                    data=i, name="INDXI", dtype=int, length=triu_nnz, shape=coo_shape
                ),
                table.Column(
                    data=j, name="INDXJ", dtype=int, length=triu_nnz, shape=coo_shape
                ),
                table.Column(data=rhoij, name="RHOIJ", dtype=float, length=triu_nnz),
            ],
            meta=meta,
        )
        return var, correl

    def write(self, ofile, header=None, overwrite=False):
        r"""
        Write the covariance object to a FITS file.

        Objects written using this function can be reinstantiated using `read`.

        The covariance matrix is *always* stored as the combination of a
        variance array (the diagonal of the covariance matrix) and the
        correlation matrix in coordinate format (see
        `~scipy.sparse.coo_matrix`).  The FITS file has three extensions:

            - 'PRIMARY': empty

            - 'VAR' (`~astropy.io.fits.ImageHDU`): The variance array

            - 'CORREL' (`~astropy.io.fits.BinTableHDU`): The correlation matrix
              data.

        The correlation data table has three columns:

            - ``INDXI``, ``INDXJ``: indices in the original data array.  These
              columns will contain one value per dimension of the input data.
              If the dimensionality of the data is 2 or more, these indices must
              be "flattened" to produce the relevant indices in the correlation
              matrix; see `coordinate_data` and ``raw_shape``.

            - ``RHOIJ``: The non-zero correlation coefficients located
              the specified coordinates.

        The extension with the correlation data also includes the following in
        its header (cf. `to_table`):

            - ``'COVSHAPE'``: The shape of the covariance matrix

            - ``'BUNIT'``: (If `unit` is defined) The string representation of
              the covariance units.

            - ``'COVRWSHP'``: (If `raw_shape` is defined) The raw shape of the
              associated data array.

        Parameters
        ----------
        ofile : :obj:`str`
            File name for the output.

        header : `~astropy.io.fits.Header`, optional
            A header object to edit and include in the PRIMARY extension.

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

        # Get the output data
        var, correl = self.to_table()
        # Construct the HDUList and write the FITS file
        fits.HDUList(
            [
                fits.PrimaryHDU(header=header),
                fits.ImageHDU(data=var, name="VAR"),
                fits.BinTableHDU(data=correl, name="CORREL"),
            ]
        ).writeto(ofile, overwrite=overwrite, checksum=True)

    @property
    def data_shape(self):
        """
        The expected shape of the data array associated with this covariance array.
        """
        return (self.shape[0],) if self.raw_shape is None else self.raw_shape

    @property
    def data_index_map(self):
        """
        An array mapping the index along each axis of the covariance matrix to
        the raw shape of the associated data array.
        """
        if self._data_index_map is None:
            self._data_index_map = np.arange(self.shape[0])
            if self.raw_shape is not None:
                self._data_index_map = self._data_index_map.reshape(self.raw_shape)
        return self._data_index_map

    def sub_matrix(self, data_slice):
        """
        Return a new `Covariance` instance with the submatrix associated with
        the provided subsection of the data array.

        Parameters
        ----------
        data_slice : slice, array-like
            Anything that can be used to slice a `numpy.ndarray`.  To generate a
            slice using syntax that mimics accessing numpy array elements, use
            `numpy.s_` or `numpy.index_exp`; see examples.

        Returns
        -------
        `Covariance`
            A new covariance object that is relevant to the selected submatrix
            of the data array.

        Examples
        --------
        Create a Covariance matrix:

        >>> import numpy as np
        >>> from scipy import sparse
        >>> from astropy.nddata import Covariance
        >>> diagonals = [
        ...     np.ones(10, dtype=float),
        ...     np.full(10-1, 0.5, dtype=float),
        ...     np.full(10-2, 0.2, dtype=float),
        ... ]
        >>> cov = Covariance(array=sparse.diags(diagonals, [0, 1, 2]))
        >>> cov.to_dense()
        array([[1. , 0.5, 0.2, 0. , 0. , 0. , 0. , 0. , 0. , 0. ],
               [0.5, 1. , 0.5, 0.2, 0. , 0. , 0. , 0. , 0. , 0. ],
               [0.2, 0.5, 1. , 0.5, 0.2, 0. , 0. , 0. , 0. , 0. ],
               [0. , 0.2, 0.5, 1. , 0.5, 0.2, 0. , 0. , 0. , 0. ],
               [0. , 0. , 0.2, 0.5, 1. , 0.5, 0.2, 0. , 0. , 0. ],
               [0. , 0. , 0. , 0.2, 0.5, 1. , 0.5, 0.2, 0. , 0. ],
               [0. , 0. , 0. , 0. , 0.2, 0.5, 1. , 0.5, 0.2, 0. ],
               [0. , 0. , 0. , 0. , 0. , 0.2, 0.5, 1. , 0.5, 0.2],
               [0. , 0. , 0. , 0. , 0. , 0. , 0.2, 0.5, 1. , 0.5],
               [0. , 0. , 0. , 0. , 0. , 0. , 0. , 0.2, 0.5, 1. ]])

        Construct a submatrix of every other data element:

        >>> cov.sub_matrix(np.s_[::2])
        <Covariance; shape = (5, 5)>
        >>> cov.sub_matrix(np.s_[::2]).to_dense()
        array([[1. , 0.2, 0. , 0. , 0. ],
               [0.2, 1. , 0.2, 0. , 0. ],
               [0. , 0.2, 1. , 0.2, 0. ],
               [0. , 0. , 0.2, 1. , 0.2],
               [0. , 0. , 0. , 0.2, 1. ]])

        """
        sub_map = self.data_index_map[data_slice]
        index = sub_map.ravel()
        return Covariance(
            self.to_sparse()[np.ix_(index, index)],
            raw_shape=None if len(sub_map.shape) == 1 else sub_map.shape,
        )

    @staticmethod
    def to_correlation(cov):
        r"""
        Convert a covariance matrix into a correlation matrix by dividing each
        element by the variances.

        Specifically, extract ``var`` (:math:`V_i = C_{ii} \equiv \sigma^2_i`)
        and convert ``cov`` from a covariance matrix with elements
        :math:`C_{ij}` to a correlation matrix with :math:`\rho_{ij}` such that

        .. math::

            C_{ij} \equiv \rho_{ij} \sigma_i \sigma_j.

        To revert a variance vector and correlation matrix back to a covariance
        matrix, use :func:`revert_correlation`.

        Parameters
        ----------
        cov : array-like
            Covariance matrix to convert.  Must be a `~scipy.sparse.csr_matrix`
            instance or convertible to one.

        Returns
        -------
        var : `numpy.ndarray`
            Variance vector
        rho : `~scipy.sparse.csr_matrix`
            Correlation matrix

        Raises
        ------
        ValueError
            Raised if the input array is not 2D and square.
        """
        # Make sure it's a sparse matrix or can be converted to one.
        _cov = _get_csr(cov)

        # Check that it's 2D
        if _cov.ndim != 2:
            raise ValueError("Covariance arrays must be 2-dimensional.")
        # Check that it's square
        if _cov.shape[0] != _cov.shape[1]:
            raise ValueError("Covariance matrices must be square.")
        # Check that it's symmetric
        flip_diff = _cov - _cov.T
        if not np.allclose(flip_diff.data, np.zeros_like(flip_diff.data)):
            warnings.warn(
                "Asymmetry detected in covariance matrix.  Covariance matrix will be "
                "modified to be symmetric using the upper triangle of the provided "
                "matrix.",
                AstropyUserWarning,
            )

        # Save the diagonal
        var = _cov.diagonal()
        # Find all the non-zero elements
        i, j, c = find(_cov)
        rho = coo_matrix(
            (c / np.sqrt(var[i] * var[j]), (i, j)), shape=_cov.shape
        ).tocsr()
        return var, rho

    @staticmethod
    def revert_correlation(var, rho):
        r"""
        Revert a variance vector and correlation matrix into a covariance matrix.

        This is the reverse operation of `to_correlation`.

        Parameters
        ----------
        var : `~numpy.ndarray`
            Variance vector.  Length must match the diagonal of ``rho``.
        rho : `~numpy.ndarray`, `~scipy.sparse.csr_matrix`
            Correlation matrix.  Diagonal must have the same length as ``var``.

        Returns
        -------
        `~scipy.sparse.csr_matrix`
            Covariance matrix.
        """
        i, j, c = find(_get_csr(rho))
        return coo_matrix(
            (c * np.sqrt(var[i] * var[j]), (i, j)), shape=rho.shape
        ).tocsr()
