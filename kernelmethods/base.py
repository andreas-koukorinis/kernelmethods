
from abc import ABC, abstractmethod
from collections import Iterable
from itertools import product as iter_product

import numpy as np
from scipy.sparse import csr_matrix

from kernelmethods.utils import check_callable, ensure_ndarray_2D, get_callable_name


class KernelMatrixException(Exception):
    """Allows to distinguish improper use of KernelMatrix from other code exceptions"""
    pass


class KMAccessError(KernelMatrixException):
    """Error to indicate invalid access to the kernel matrix!"""
    pass

class BaseKernelFunction(ABC):
    """Abstract base class for kernel functions.

    Enforces each derived kernel:
    1. to be callable, with two inputs
    2. to have a name and a str representation

    """

    def __init__(self, name):
        """
        Constructor.

        Parameters
        ----------
        name : str
            short name to describe the nature of the kernel function

        """

        self.name = name

    @abstractmethod
    def __call__(self, x, y):
        """Actual computation!"""

    @abstractmethod
    def __str__(self):
        """Representation"""


class KernelFromCallable(BaseKernelFunction):
    """Class to create a custom kernel from a given callable."""


    def __init__(self, input_func, name=None, **func_params):
        """
        Constructor.

        Parameters
        ----------
        input_func : callable
            A callable that can accept atleast 2 args
            Must not be builtin or C function.
            If func is a C or builtin func, wrap it in a python def

        name : str
            A name to identify this kernel in a human readable way

        func_params : dict
            Parameters to func

        """

        self.func = check_callable(input_func, min_num_args=2)
        self.params = func_params

        super().__init__(name=get_callable_name(input_func, name))


    def __call__(self, x, y):
        """Actual computation!"""

        return self.func(x, y, **self.params)


    def __str__(self):
        """human readable repr"""

        arg_repr = '({})'.format(self.params) if len(self.params) > 0 else ''
        return "{}{}".format(self.name, arg_repr)


    # aliasing them to __str__ for now
    __format__ = __str__
    __repr__ = __str__


class KernelMatrix(object):
    """
    The KernelMatrix class.

    """

    def __init__(self,
                 kernel,
                 name='KernelMatrix'):
        """
        Constructor.

        Parameters
        ----------
        kernel : BaseKernelFunction
            kernel function that populates the kernel matrix

        name : str
            short name to describe the nature of the kernel function

        """

        if not isinstance(kernel, BaseKernelFunction):
            raise TypeError('Input kernel must be derived from '
                            ' kernelmethods.BaseKernelFunction')

        self.kernel = kernel
        self.name = name


    def attach_to(self, sample):
        """Attach this kernel to a given sample

        Parameters
        ----------
        sample : ndarray
            Input sample to operate on
            Must be 2D of shape (num_samples, num_features)

        """

        self.sample = ensure_ndarray_2D(sample)
        self.num_samples = self.sample.shape[0]
        self.shape = (self.num_samples, self.num_samples)

        self._populated_fully = False


    @property
    def full_dense(self):
        """Fully populated kernel matrix in dense ndarray format."""

        return self._populate_fully(fill_lower_tri=True).todense()

    @property
    def full_sparse(self):
        """Kernel matrix populated in upper tri in sparse array format."""

        return self._populate_fully(fill_lower_tri=False)

    def diag(self):
        """Returns the diagonal of the kernel matrix"""

        return np.array([self._eval_kernel(idx, idx) for idx in range(self.num_samples)])

    def _eval_kernel(self, idx_one, idx_two):
        """Returns kernel value between samples identified by indices one and two"""

        return self.kernel(self._features(idx_one), self._features(idx_two))

    def _features(self, index):
        """Returns the sample [features] corresponding to a given index."""

        return self.sample[index,:]

    def __getitem__(self, index_obj):
        """
        Item getter to allow for efficient access
        to partial or random portions of kernel matrix!

        Indexing here is aimed to be compliant with numpy implementation
        as much as possible: https://docs.scipy.org/doc/numpy-1.13.0/reference/arrays.indexing.html#arrays-indexing

        """

        if not len(index_obj) == 2 or not isinstance(index_obj, tuple):
            raise KMAccessError('Invalid attempt to access the kernel matrix '
                                '-: must supply two [sets/ranges of] indices in a tuple!')

        set_one, are_all_selected_dim_one = self._get_indices_in_sample(index_obj[0])
        set_two, are_all_selected_dim_two = self._get_indices_in_sample(index_obj[1])

        # below code prevents user from [VERY] inefficiently computing
        # the entire kernel matrix with KM[:,:],
        # without exploiting the fact that KM is symmetric
        if are_all_selected_dim_one and are_all_selected_dim_two:
            return self._populate_fully(fill_lower_tri=True)
        else:
            return self._compute_for_index_combinations(set_one, set_two)

    def _get_indices_in_sample(self, index_obj_per_dim):
        """
        Turn an index or slice object on a given dimension
        into a set of row indices into sample the kernel matrix is attached to.

        As the kernel matrix is 2D and symmetric of known size,
            dimension size doesn't need to be specified, it is taken from self.num_samples

        """

        are_all_selected = False

        if isinstance(index_obj_per_dim, int):
            indices = [index_obj_per_dim, ]  # making it iterable
        elif isinstance(index_obj_per_dim, slice):
            if index_obj_per_dim is None:
                are_all_selected = True
            indices = index_obj_per_dim.indices(self.num_samples)
        elif isinstance(index_obj_per_dim, Iterable):
            indices = map(int, index_obj_per_dim)
        else:
            raise KMAccessError('Invalid index method/indices for kernel matrix!\n'
                                ' For each of the two dimensions of size {num_samples},'
                                ' only int, slice or iterable objects are allowed,'
                                ' whose values must all be >=0 and < {num_samples}'
                                ''.format(num_samples=self.num_samples))

        # enforcing constraints
        if any(indices < 0) or any(indices >= self.num_samples):
            raise KMAccessError('Invalid index method/indices for kernel matrix!\n'
                                ' For each of the two dimensions of size {num_samples},'
                                ' only int, slice or iterable objects are allowed,'
                                ' whose values must all be >=0 and < {num_samples}'
                                ''.format(num_samples=self.num_samples))

        # removing duplicates and sorting
        indices = sorted(list(set(indices)))

        if len(indices) == self.num_samples:
            are_all_selected = True

        return indices, are_all_selected

    def _compute_for_index_combinations(self, set_one, set_two):
        """Computes value of kernel matrix for all combinations of given set of indices"""

        output = np.array([self._eval_kernel(idx_one, idx_two)
                           for idx_one, idx_two in iter_product(set_one, set_two)],
                          dtype=self.sample.dtype)

        return output

    def _populate_fully(self, fill_lower_tri=False):
        """Applies the kernel function on all pairs of points in a sample.

        CAUTION: this may not always be necessary,
            and can take HUGE memory for LARGE datasets,
            and also can take a lot of time.

        """

        # kernel matrix is symmetric - so we need only to STORE half the matrix!
        output = csr_matrix((self.num_samples, self.num_samples),
                            dtype=self.sample.dtype)

        try:
            for idx_one in range(self.num_samples):
                # kernel matrix is symmetric - so we need only compute half the matrix!
                # computing the kernel for diagonal elements i,i as well
                #   if not change index_two starting point to index_one+1
                for idx_two in range(idx_one, self.num_samples):
                    output[idx_one, idx_two] = self._eval_kernel(idx_one, idx_two)
        except:
            self._populated_fully = False
        else:
            self._populated_fully = True

        if fill_lower_tri:
            idx_lower_tri = np.tril_indices(self.num_samples)
            output[idx_lower_tri] = output.T[idx_lower_tri]

        return output