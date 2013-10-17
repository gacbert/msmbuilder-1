"""
`vmhmm` implements a hidden Markov model with von Mises emissions
"""
# Author: Robert McGibbon <rmcgibbo@gmail.com>
# Copyright (c) 2013, Stanford University
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#
#   Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
#
#   Redistributions in binary form must reproduce the above copyright notice, this
#   list of conditions and the following disclaimer in the documentation and/or
#   other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR
# ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
# ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

#-----------------------------------------------------------------------------
# Imports
#-----------------------------------------------------------------------------

from __future__ import print_function, division

import numpy as np
from sklearn import cluster
from sklearn.hmm import _BaseHMM
import scipy.special
from scipy.interpolate import interp1d
import scipy.version
from scipy.interpolate._fitpack import _bspleval
from scipy.stats.distributions import vonmises
import _vmhmm

#-----------------------------------------------------------------------------
# Globals
#-----------------------------------------------------------------------------
M_2PI = 2 * np.pi
__all__ = ['VonMisesHMM']

#-----------------------------------------------------------------------------
# Code
#-----------------------------------------------------------------------------


class VonMisesHMM(_BaseHMM):
    """
    Hidden Markov Model with von Mises Emissions

    The von Mises distribution, (also known as the circular normal
    distribution or Tikhonov distribution) is a continuous probability
    distribution on the circle. For multivariate signals, the emmissions
    distribution implemented by this model is a product of univariate
    von Mises distributuons -- analogous to the multivariate Gaussian
    distribution with a diagonal covariance matrix.

    This class allows for easy evaluation of, sampling from, and
    maximum-likelihood estimation of the parameters of a HMM.

    Notes
    -----
    The formulas for the maximization step of the E-M algorithim are
    adapted from [1]_, especially equations (11) and (13).

    Parameters
    ----------
    n_components : int
        Number of states in the model.
    random_state: RandomState or an int seed (0 by default)
        A random number generator instance
    n_iter : int, optional
        Number of iterations to perform.
    thresh : float, optional
        Convergence threshold.
    params : string, optional
        Controls which parameters are updated in the training
        process.  Can contain any combination of 's' for startprob,
        't' for transmat, 'm' for means, and 'k' for kappas. Defaults to all
        parameters.
    init_params : string, optional
        Controls which parameters are initialized prior to
        training.  Can contain any combination of 's' for
        startprob, 't' for transmat, 'm' for means, and 'k' for
        kappas, the concentration parameters. Defaults to all parameters.

    Attributes
    ----------
    n_features : int
        Dimensionality of the emissions.
    transmat_ : array, shape (`n_components`, `n_components`)
        Matrix of transition probabilities between states.
    startprob_ : array, shape ('n_components`,)
        Initial state occupation distribution.
    means_ : array, shape (`n_components`, `n_features`)
        Mean parameters for each state.
    kappas_ : array, shape (n_components`, `n_features`)
        Concentration parameter for each state. If `kappa` is zero, the
        distriution is uniform. If large, the distribution is very
        concentrated around the mean.

    References
    ----------
    .. [1] Prati, Andrea, Simone Calderara, and Rita Cucchiara. "Using circular
    statistics for trajectory shape analysis." Computer Vision and Pattern
    Recognition, 2008. CVPR 2008. IEEE Conference on. IEEE, 2008.
    .. [2] Murray, Richard F., and Yaniv Morgenstern. "Cue combination on the
    circle and the sphere." Journal of vision 10.11 (2010).
    """
    def __init__(self, n_components=1, startprob=None, transmat=None,
                 startprob_prior=None, transmat_prior=None,
                 algorithm="viterbi", random_state=None, n_iter=10,
                 thresh=1e-2, params='stmk', init_params='stmk'):
        _BaseHMM.__init__(self, n_components, startprob, transmat,
                          startprob_prior=startprob_prior,
                          transmat_prior=transmat_prior, algorithm=algorithm,
                          random_state=random_state, n_iter=n_iter,
                          thresh=thresh, params=params,
                          init_params=init_params)
        self._fitkappas = self._c_fitkappas

    def _init(self, obs, params='stmk'):
        super(VonMisesHMM, self)._init(obs, params)
        if (hasattr(self, 'n_features')
                and self.n_features != obs[0].shape[1]):
            raise ValueError('Unexpected number of dimensions, got %s but '
                             'expected %s' % (obs[0].shape[1],
                                              self.n_features))
        self.n_features = obs[0].shape[1]

        if 'm' in params:
            # Cluster the sine and cosine of the input data with kmeans to
            # get initial centers
            cluster_centers = cluster.KMeans(n_clusters=self.n_components).fit(
                np.hstack((np.sin(obs[0]), np.cos(obs[0])))).cluster_centers_
            self._means_ = np.arctan2(cluster_centers[:, :self.n_features],
                                      cluster_centers[:, self.n_features:])
        if 'k' in params:
            self._kappas_ = np.ones((self.n_components, self.n_features))

    def _get_means(self):
        """Mean parameters for each state."""
        return self._means_

    def _set_means(self, means):
        means = np.asarray(means)
        if (hasattr(self, 'n_features')
                and means.shape != (self.n_components, self.n_features)):
            raise ValueError('means must have shape '
                             '(n_components, n_features)')
        self._means_ = means.copy()
        self.n_features = self._means_.shape[1]

    means_ = property(_get_means, _set_means)

    def _get_kappas(self):
        """Concentration parameter for each state. If kappa is zero, the
        distriution is uniform. If large, it gets very concentrated about
        mean"""
        return self._kappas_

    def _set_kappas(self, kappas):
        kappas = np.asarray(kappas)
        if (hasattr(self, 'n_features')
                and kappas.shape != (self.n_components, self.n_features)):
            raise ValueError('kappas must have shape '
                             '(n_components, n_features)')
        self._kappas_ = kappas.copy()
        self.n_features = self._kappas_.shape[1]

    kappas_ = property(_get_kappas, _set_kappas)

    def _generate_sample_from_state(self, state, random_state=None):
        """Generate random samples from the output distribution of a state.

        Returns
        -------
        sample : mp.array, shape (`n_features`,)
        """
        x = vonmises.rvs(self._kappas_[state], self._means_[state])
        return circwrap(x)

    def _compute_log_likelihood(self, obs):
        """Compute the log likelihood of each observation in each state

        Parameters
        ----------
        obs : np.array, shape (`n_samples`, `n_features`)

        Returns
        -------
        logl : np.array, shape (`n_samples`, `n_components`)
        """
        return _vmhmm._compute_log_likelihood(obs, self._means_, self._kappas_)

    def _initialize_sufficient_statistics(self):
        stats = super(VonMisesHMM, self)._initialize_sufficient_statistics()
        stats['posteriors'] = []
        stats['obs'] = []
        return stats

    def _accumulate_sufficient_statistics(self, stats, obs, framelogprob,
                                          posteriors, fwdlattice, bwdlattice,
                                          params):
        super(VonMisesHMM, self)._accumulate_sufficient_statistics(
            stats, obs, framelogprob, posteriors, fwdlattice, bwdlattice,
            params)
        # Unfortunately, I'm not quite sure how to accumulate sufficient
        # statistics here, because we need to know the mean shifted cosine of
        # the data, which requires knowing the mean. You could do two passes,
        # but that is MUCH more work, since you have to redo the
        # forwardbackward, so we'll just accumulate the data.
        stats['posteriors'].append(posteriors)
        stats['obs'].append(obs)

    def _py_fitkappas(self, posteriors, obs, means):
        inv_kappas = np.zeros_like(self._kappas_)
        for i in range(self.n_features):
            for j in range(self.n_components):
                n = np.sum(posteriors[:, j] * np.cos(obs[:, i] - means[j, i]))
                d = np.sum(posteriors[:, j])
                inv_kappas[j, i] = n / d
        self._kappas_ = inverse_mbessel_ratio(inv_kappas)

    def _c_fitkappas(self, posteriors, obs, means):
        _vmhmm._fitkappa(posteriors, obs, means, self._kappas_)

    def _fitmeans(self, posteriors, obs, out):
        # this is no possible to speed up in C. the rate limiting step
        # are the matrix multiplys, which are already in MKL with fast
        # numpy. I tried it in C, and I'm 2x as slow for large matrix
        # sizes.
        np.arctan2(np.dot(posteriors.T, np.sin(obs)),
                   np.dot(posteriors.T, np.cos(obs)),
                   out=out)

    def _do_mstep(self, stats, params):
        super(VonMisesHMM, self)._do_mstep(stats, params)

        posteriors = np.vstack(stats['posteriors'])
        obs = np.vstack(stats['obs'])

        if 'm' in params:
            self._fitmeans(posteriors, obs, out=self._means_)
        if 'k' in params:
            self._fitkappas(posteriors, obs, self._means_)

    def overlap_(self):
        """
        Compute the matrix of normalized log overlap integrals between the hidden state distributions

        Notes
        -----
        The analytic formula used here follows from equation (A4) in :ref:`2`

        Returns
        -------
        noverlap : array, shape=(n_components, n_components)
            `noverlap[i,j]` gives the log of normalized overlap integral between
            states i and j. The normalized overlap integral is `integral(f(i)*f(j))
            / sqrt[integral(f(i)*f(i)) * integral(f(j)*f(j))]
        """
        logi0 = lambda x: np.log(scipy.special.i0(x))
        log2pi = np.log(2*np.pi)

        log_overlap = np.zeros((self.n_components, self.n_components))
        for i in range(self.n_components):
            for j in range(self.n_components):
                for s in range(self.n_features):
                    kij = np.sqrt(self._kappas_[i,s]**2+self._kappas_[j,s]**2 +
                                  2*self._kappas_[i,s]*self._kappas_[j,s] *
                                  np.cos(self._means_[i,s]-self._means_[j,s]))
                    val = logi0(kij) - (log2pi + logi0(self._kappas_[i,s]) +
                                                 logi0(self._kappas_[j,s]))
                    log_overlap[i,j] += val

        for i in range(self.n_components):
            for j in range(self.n_components):
                log_overlap[i,j] -= 0.5*(log_overlap[i, i] + log_overlap[j, j])

        return log_overlap


def circwrap(x):
    "Wrap an array on (-pi, pi)"
    return x - M_2PI * np.floor(x / M_2PI + 0.5)


class inverse_mbessel_ratio(object):
    """
    Inverse the function given by the ratio modified Bessel function of the
    first kind of order 1 to the modified Bessel function of the first kind
    of order 0.

    y = A(x) = I_1(x) / I_0(x)

    This function computes A^(-1)(y) by way of a precomputed spline
    interpolation
    """

    def __init__(self, n_points=512):
        self._n_points = n_points
        self._is_fit = False
        self._min_x = 1e-5
        self._max_x = 700

    def _fit(self):
        """We want to do the fitting once, but not at import time since it
        slows down the loading of the interpreter"""
        # Fitting takes about 0.5s on a laptop, and wth 512 points and cubic
        # interpolation, gives typical errors around 1e-9
        x = np.logspace(np.log10(self._min_x), np.log10(self._max_x),
                        self._n_points)
        y = self.bessel_ratio(x)
        self._min = np.min(y)
        self._max = np.max(y)

        # Spline fit the log of the inverse function
        self._spline = interp1d(y, np.log(x), kind='cubic')
        self._is_fit = True

    def __call__(self, y):
        if not self._is_fit:
            self._fit()

        y = np.asarray(y)
        y = np.clip(y, a_min=self._min, a_max=self._max)

        if np.any(np.logical_or(0 > y, y > 1)):
            raise ValueError('Domain error. y must be in (0, 1)')
        x = np.exp(self._spline(y))

        ## DEBUGGING CODE
        # # for debugging, the line below prints the error in the inverse
        # # by printing y - A(A^(-1)(y
        # print('spline inverse error', y - self.bessel_ratio(x))

        return x

    @staticmethod
    def bessel_ratio(x):
        numerator = scipy.special.iv(1, x)
        denominator = scipy.special.iv(0, x)
        return numerator / denominator

# Shadown the inverse_mbessel_ratio with an instance.
inverse_mbessel_ratio = inverse_mbessel_ratio()
