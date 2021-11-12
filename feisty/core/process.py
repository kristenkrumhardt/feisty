import numpy as np
import xarray as xr

from . import constants, domain, ecosystem


def compute_rate_T_mass_scaling(T, mass, k, a, b, T0=10.0):
    return np.exp(k * (T - T0)) * a * mass ** (-b)


def compute_t_frac_pelagic(
    t_frac_pelagic,
    fish_list,
    biomass,
    food_web,
    pelagic_functional_types,
    demersal_functional_types,
    PI_be_cutoff,
    reset=False,
):
    """Return the fraction of time spent in the pelagic.

    Parameters
    ----------

    t_frac_pelagic : array_like
      DataArray for storing the result of the computation.

    fish_list : list
      List of feisty.ecosystem.fish object.

    biomass : xarray.DataArray
      Biomass array.

    food_web : feisty.food_web
      Food web object.

    reset : boolean, optional
      If "True", reset `t_frac_pelagic` to `t_frac_pelagic_static`.
    """

    for i, fish in enumerate(fish_list):
        if reset:
            t_frac_pelagic[i, :] = fish.t_frac_pelagic_static

        elif fish.pelagic_demersal_coupling:
            prey_pelagic = food_web.get_prey_biomass(
                biomass,
                fish.name,
                prey_functional_type=pelagic_functional_types,
                apply_preference=fish.pdc_apply_pref,
            )
            prey_demersal = food_web.get_prey_biomass(
                biomass,
                fish.name,
                prey_functional_type=demersal_functional_types,
                apply_preference=fish.pdc_apply_pref,
            )

            t_frac_pelagic[i, :] = xr.where(
                domain.ocean_depth < PI_be_cutoff,
                prey_pelagic / (prey_pelagic + prey_demersal),
                1.0,
            )


def t_weighted_mean_temp(T_pelagic, T_bottom, t_frac_pelagic):
    """Compute the time-weighted mean temperature.

    Parameters
    ----------

    T_pelagic : numeric
      Pelagic temperature.

    T_bottom : numeric
      Bottom temperature.

    t_frac_pelagic : numeric
      Fraction of time spent in the pelagic.
    """
    return (T_pelagic * t_frac_pelagic) + (T_bottom * (1.0 - t_frac_pelagic))


def compute_metabolism(metabolism_rate, fish_list, T_habitat):
    """Compute metabolic rate.

    Parameters
    ----------
    metabolism_rate : array_like
      DataArray for storing the result of the computation.

    fish_list : list
      List of feisty.ecosystem.fish object.

    T_habitat : numeric
      The experienced temperature (weighted mean).
    """

    for i, fish in enumerate(fish_list):
        # Metabolism with its own coeff, temp-sens, mass-sens
        metabolism_rate[i, :] = (
            compute_rate_T_mass_scaling(
                T_habitat[i, :], fish.mass, fish.k_metabolism, fish.a_metabolism, fish.b_metabolism
            )
            / 365.0
        )


def compute_pred_encounter_consumption_max(
    encounter_rate_pred, consumption_rate_max_pred, T_habitat, fish_list
):
    """Compute predator encounter and maximum consumption rates."""
    for i, fish in enumerate(fish_list):

        encounter_rate_pred[i, :] = (
            compute_rate_T_mass_scaling(
                T_habitat[i, :],
                fish.mass,
                fish.k_encounter,
                fish.a_encounter,
                fish.b_encounter,
            )
            / 365.0
        )
        consumption_rate_max_pred[i, :] = (
            compute_rate_T_mass_scaling(
                T_habitat[i, :],
                fish.mass,
                fish.k_consumption,
                fish.a_consumption,
                fish.b_consumption,
            )
            / 365.0
        )


def compute_encounter(
    encounter_rate_link,
    encounter_rate_total,
    encounter_rate_pred,
    biomass,
    T_habitat,
    t_frac_pelagic,
    food_web,
):
    """
    Compute encounter rates.

    Parameters
    ----------
    da : xarray.DataArray
      DataArray to be filled

    biomass_prey : float
      Prey biomass density.

    T_habitat : array_like
       Experienced temperature.

    t_frac_pelagic : float
      Fraction of time spent in pelagic.

    t_frac_prey : float
      Time spent in area with that prey item.
    """

    for i, link in enumerate(food_web):
        if link.preference == 0:
            encounter_rate_link[i, :] = 0.0
        else:
            t_frac_pelagic_pred = t_frac_pelagic.isel(fish=link.i_fish)
            t_frac_prey_pred = t_frac_pelagic_pred
            if link.prey.is_demersal:
                t_frac_prey_pred = 1.0 - t_frac_pelagic_pred

            bio = biomass.isel(group=link.ndx_prey)
            pref = link.preference
            enc = encounter_rate_pred[link.i_fish, :]
            encounter_rate_link[i, :] = xr.where(
                t_frac_prey_pred > 0,
                bio * pref * enc,
                0.0,
            )

    for i, name in enumerate(food_web.fish_names):
        encounter_rate_total[i, :] = encounter_rate_link.isel(
            feeding_link=food_web.pred_link_ndx[name]
        ).sum('feeding_link')


def compute_consumption(
    consumption_rate_link,
    consumption_rate_max_pred,
    encounter_rate_link,
    encounter_rate_total,
    T_habitat,
    food_web,
):
    """
    Consumption rates.
    """

    for i, link in enumerate(food_web):
        enc = consumption_rate_link[i, :]
        cmax = consumption_rate_max_pred[link.i_fish, :]
        enc_total = encounter_rate_total[link.i_fish, :]
        consumption_rate_link[i, :] = cmax * enc / (cmax + enc_total)


def compute_rescale_zoo_consumption(
    consumption_rate_link,
    consumption_zoo_frac_mort,
    consumption_zoo_scaled,
    consumption_zoo_raw,
    biomass,
    zoo_mortality,
    food_web,
):
    """Limit zooplankton consumption by mortality term."""

    for zoo_i in food_web.zoo_names:

        link_ndx = food_web.prey_link_ndx[zoo_i]
        biomass_zoo_pred = food_web._get_biomass_zoo_pred(biomass, zoo_i)

        bio_con_zoo = biomass_zoo_pred * food_web.get_consumption(consumption_rate_link, prey=zoo_i)
        bio_con_zoo_sum = bio_con_zoo.sum('group')

        zoo_mortality_i = zoo_mortality.sel(zooplankton=zoo_i)

        consumption_zoo_frac_mort[link_ndx, :] = bio_con_zoo_sum / (zoo_mortality_i + constants.eps)

        bio_con_zoo_scaled = (bio_con_zoo / bio_con_zoo_sum) * zoo_mortality_i

        consumption_zoo_scaled[link_ndx, :] = np.where(
            bio_con_zoo_sum > zoo_mortality_i,
            bio_con_zoo_scaled / biomass_zoo_pred,
            consumption_rate_link.isel(feeding_link=link_ndx),
        )
        consumption_zoo_raw[link_ndx, :] = consumption_rate_link[link_ndx, :]
        consumption_rate_link[link_ndx, :] = consumption_zoo_scaled[link_ndx, :]


def compute_ingestion(ingestion_rate, consumption_rate_link, food_web):
    """Compute ingestion.

    Parameters
    ----------

    ingestion_rate : array_like
      DataArray for storing the result of the computation.

    food_web : feisty.food_web
      Food web object.
    """
    for i, name in enumerate(food_web.fish_names):
        ingestion_rate[i, :] = food_web.get_consumption(consumption_rate_link, predator=name).sum(
            'group'
        )


def compute_predation(predation_flux, predation_zoo_flux, consumption_rate_link, biomass, food_web):
    """Compute predation.

    Parameters
    ----------

    predation_flux : array_like
      DataArray for storing the result of the computation.

    food_web : feisty.food_web
      Food web object.

    biomass : xarray.DataArray
      Biomass array.
    """
    for i, name in enumerate(food_web.fish_names):
        # not eaten?
        if name not in food_web.prey_ndx_pred:
            continue

        ndx = food_web.prey_ndx_pred[name]
        predation_flux[i, :] = (
            food_web.get_consumption(consumption_rate_link, prey=name) * biomass[ndx, :]
        ).sum('group')

    for i, name in enumerate(food_web.zoo_names):
        ndx = food_web.prey_ndx_pred[name]
        predation_zoo_flux[i, :] = (
            food_web.get_consumption(consumption_rate_link, prey=name) * biomass[ndx, :]
        ).sum('group')


def compute_natural_mortality(mortality_rate, fish_list, T_habitat, mortality_types):
    """Compute natural mortality.

    Parameters
    ----------
    mortality_rate : array_like
      DataArray for storing the result of the computation.

    fish_list : list
      List of feisty.ecosystem.fish object.

    T_habitat : numeric
      The experienced temperature (weighted mean).
    """

    for i, fish in enumerate(fish_list):

        if fish.mortality_type == mortality_types['none']:
            mortality_rate[i, :] = 0.0

        elif fish.mortality_type == mortality_types['constant']:
            mortality_rate[i, :] = fish.mortality_coeff

        elif fish.mortality_type == mortality_types['Hartvig']:
            mortality_rate[i, :] = (
                np.exp(0.063 * (T_habitat[i, :] - 10.0)) * 0.84 * fish.mass ** (-0.25) / 365.0
            )

        elif fish.mortality_type == mortality_types['Mizer']:
            mortality_rate[i, :] = (
                np.exp(0.063 * (T_habitat[i, :] - 10.0)) * 3.0 * fish.mass ** (-0.25) / 365.0
            )

        elif fish.mortality_type == mortality_types['Jennings & Collingridge']:
            # TODO: clean up here
            temp2 = T_habitat[i, :] + 273.0
            Tref = 283.0
            E = 0.6
            k = 8.62e-5
            tfact = np.exp((-1 * E / k) * ((1.0 / temp2) - (1.0 / Tref)))
            mortality_rate[i, :] = tfact * 0.5 * fish.mass ** (-0.33) / 365.0

        elif fish.mortality_type == mortality_types['Peterson & Wrob']:
            # Peterson & Wroblewski (daily & uses dry weight)
            mortality_rate[i, :] = (
                np.exp(0.063 * (T_habitat[i, :] - 15.0)) * 5.26e-3 * (fish.mass / 9.0) ** (-0.25)
            )

        elif fish.mortality_type == mortality_types['temperature-dependent']:
            mortality_rate[i, :] = np.exp(0.063 * (T_habitat[i, :] - 10.0)) * fish.mortality_coeff

        elif fish.mortality_type == mortality_types['weight-dependent']:
            mortality_rate[i, :] = 0.5 * fish.mass ** (-0.25) / 365.0

        else:
            raise ValueError(f'unknown mortality type {fish.mortality_type}')


def compute_benthic_biomass_update(
    benthic_biomass_new, consumption_rate_link, benthic_prey_list, biomass, food_web, poc_flux
):
    """
    bio_in = benthic biomass
    det = poc_flux flux to bottom (g/m2/d)
    con = biomass specific consumption rate by MD & LD
    bio = biomass of MD & LD
    """

    for i, benthic_prey in enumerate(benthic_prey_list):
        # eaten = consumption * biomass_pred
        # pred = sum(eaten, 2)
        biomass_bent = biomass.sel(group=benthic_prey.name)
        predation = (
            biomass.isel(group=food_web.prey_ndx_pred[benthic_prey.name])
            * food_web.get_consumption(consumption_rate_link, prey=benthic_prey.name)
        ).sum('group')

        # Needs to be in units of per time (g/m2/d) * (g/m2)
        growth = benthic_prey.benthic_efficiency * poc_flux

        if not benthic_prey.lcarrying_capacity:  # no carrying capacity
            benthic_biomass_new[i, :] = biomass_bent + growth - predation
        else:
            # logistic
            benthic_biomass_new[i, :] = (
                biomass_bent
                + growth * (1.0 - biomass_bent / benthic_prey.carrying_capacity)
                - predation
            )

    benthic_biomass_new[:, :] = np.where(
        benthic_biomass_new < 0.0,
        constants.eps,
        benthic_biomass_new,
    )


def compute_energy_avail(energy_avail_rate, ingestion_rate, metabolism_rate, fish_list):
    """Compute energy available for growth (nu)."""

    for i, fish in enumerate(fish_list):
        energy_avail_rate[i, :] = (ingestion_rate[i, :] * fish.assim_efficiency) - metabolism_rate[
            i, :
        ]


def compute_growth(
    growth_rate, energy_avail_rate, predation_rate, mortality_rate, fish_catch_rate, fish_list
):
    """Compute energy available for somatic growth (gamma).
    nmort = natural mortality rate
    Frate = fishing mortality rate
    d = predation loss
    selec = harvested selectivity (adults 100%, juveniles 10%)
    """

    for i, fish in enumerate(fish_list):
        death = predation_rate[i, :] + mortality_rate[i, :] + fish_catch_rate[i, :]
        somatic_growth_potential = fish.energy_frac_somatic_growth * energy_avail_rate[i, :]

        gg = (somatic_growth_potential - death) / (
            1.0 - (fish.size_class_bnds_ratio ** (1.0 - (death / somatic_growth_potential)))
        )
        growth_rate[i, :] = xr.where(gg < energy_avail_rate[i, :], gg, energy_avail_rate[i, :])
        lndx = np.isnan(gg) | (gg < 0)
        growth_rate[i, lndx] = 0.0


def compute_reproduction(reproduction_rate, growth_rate, energy_avail_rate, fish_list):
    """Compute reproduction from energy available for growth and reproduction (nu) and energy available for somatic growth (gamma)."""
    for i, fish in enumerate(fish_list):

        if fish.energy_frac_somatic_growth == 1.0:
            reproduction_rate[i, :] = 0.0
        else:
            # energy available
            rho = xr.where(
                energy_avail_rate[i, :] > 0.0,
                (1.0 - fish.energy_frac_somatic_growth) * energy_avail_rate[i, :],
                0.0,
            )
            # add what would be growth to next size up as repro
            reproduction_rate[i, :] = rho + growth_rate[i, :]
            growth_rate[i, :] = 0.0


def compute_recruitment(
    recruitment_flux,
    reproduction_rate,
    growth_rate,
    biomass,
    reproduction_routing,
):
    """Compute "recruitment" from reproduction (i.e., larval production) or growth."""
    for link in reproduction_routing:
        if link.is_larval:
            recruitment_flux[link.i_fish, :] = (
                link.efficiency
                * reproduction_rate[link.i_fish, :]
                * biomass.isel(group=link.ndx_from)
            )
        else:
            recruitment_flux[link.i_fish, :] = growth_rate[link.i_fish, :] * biomass.isel(
                group=link.ndx_from
            )


def compute_total_tendency(
    total_tendency,
    recruitment_flux,
    energy_avail_rate,
    growth_rate,
    reproduction_rate,
    mortality_rate,
    predation_flux,
    fish_catch_rate,
    biomass,
    fish_list,
):
    """
    Compute the total time tendency of fish.

    Inputs with "_rate" suffix are specific rates (1/d); inputs with "_flux" suffix are actually mass fluxes (g/d).

    Parameters
    ----------
    total_tendency : array_like

    recruitment_flux : array_like

    energy_avail_rate : array_like

    growth_rate : array_like

    reproduction_rate : array_like

    mortality_rate : array_like

    predation_flux : array_like

    fish_catch_rate : array_like

    biomass : array_like

    fish_list : list
      List of feisty.ecosystem.fish object.
    """
    for i, fish in enumerate(fish_list):
        total_tendency[i, :] = (
            recruitment_flux[i, :]
            + biomass.sel(group=fish.name)
            * (
                (
                    energy_avail_rate[i, :]
                    - reproduction_rate[i, :]
                    - growth_rate[i, :]
                    - mortality_rate[i, :]
                    - fish_catch_rate[i, :]
                )
            )
            - predation_flux[i, :]
        )


def compute_fish_catch(fish_catch_rate, fishing_rate, fish_list):
    """Compute fishing rate.
    %F = fishing rate per day
    %selec = fishery selectivity
    """
    for i, fish in enumerate(fish_list):
        # Linear fishing mortality
        fish_catch_rate[i, :] = fish.harvest_selectivity * fishing_rate[:]