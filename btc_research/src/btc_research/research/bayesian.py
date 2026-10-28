"""One-shot Bayesian estimate of the sign of expected per-bar performance.

Model
-----
Per-bar net equity returns are treated as draws with unknown mean. Dependence
is handled by shrinking the sample size. Observations are not assumed
independent when lag-1 autocorrelation is positive.

Prior
-----
The arithmetic annualised mean has a Normal(0, prior_annual_sd^2) prior.
Dividing by the number of bars per year puts that prior on the per-bar mean.
The default prior_annual_sd is 0.10: a true 20% annual edge is a two-sigma
surprise before any data. This is a prior on the mean, not on volatility.

Posterior
---------
Normal-normal update, prior mean zero. Likelihood variance is sigma^2 / n_eff.
There is no update method. Calling this again on the same sample returns the
same numbers. A previous posterior cannot be fed back in, so repeating the
call cannot manufacture confidence.

n_eff is n * (1-rho)/(1+rho) when lag-1 autocorrelation is positive, and n
otherwise. Negative autocorrelation does not increase confidence.
"""

from __future__ import annotations

import math
from typing import Sequence

from btc_research.stats import effective_sample_size, lag1_autocorr, mean, norm_cdf, stdev

DEFAULT_PRIOR_ANNUAL_SD = 0.10


def estimate_positive_edge(
    bar_returns: Sequence[float],
    *,
    periods_per_year: float,
    prior_annual_sd: float = DEFAULT_PRIOR_ANNUAL_SD,
) -> dict[str, object]:
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive")
    if prior_annual_sd <= 0:
        raise ValueError("prior_annual_sd must be positive")
    n = len(bar_returns)
    if n < 3:
        return {
            "n": n,
            "n_eff": float(n),
            "rho_lag1": None,
            "sample_mean": None,
            "posterior_mean_bar": None,
            "posterior_mean_annual": None,
            "posterior_sd_bar": None,
            "p_mean_positive": None,
            "prior_annual_sd": prior_annual_sd,
            "prior": "Normal(0, prior_annual_sd^2) on the arithmetic annualised mean",
            "note": "too few returns to update",
        }
    rho = lag1_autocorr(bar_returns)
    n_eff = effective_sample_size(n, rho)
    sample_mean = mean(bar_returns)
    sigma = stdev(bar_returns)
    prior_sd_bar = prior_annual_sd / periods_per_year
    prior_var = prior_sd_bar ** 2
    if sigma == 0:
        post_mean = 0.0
        post_var = prior_var
        note = "sample variance was zero; posterior left at the prior"
    else:
        like_var = (sigma ** 2) / n_eff
        post_var = 1.0 / (1.0 / prior_var + 1.0 / like_var)
        post_mean = post_var * (sample_mean / like_var)
        note = "single update from the raw sample; not a sequential posterior"
    post_sd = math.sqrt(post_var)
    if post_sd > 0:
        p_positive = float(norm_cdf(post_mean / post_sd))
    else:
        p_positive = 1.0 if post_mean > 0 else 0.5
    return {
        "n": n,
        "n_eff": n_eff,
        "rho_lag1": rho,
        "sample_mean": sample_mean,
        "sample_stdev": sigma,
        "posterior_mean_bar": post_mean,
        "posterior_mean_annual": post_mean * periods_per_year,
        "posterior_sd_bar": post_sd,
        "posterior_sd_annual": post_sd * periods_per_year,
        "p_mean_positive": p_positive,
        "prior_annual_sd": prior_annual_sd,
        "prior_mean_annual": 0.0,
        "prior": "Normal(0, prior_annual_sd^2) on the arithmetic annualised mean",
        "note": note,
    }
