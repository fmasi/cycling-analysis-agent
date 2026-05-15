"""BikeConfig / AssistConfig dataclasses and the load_bike() helper.

Reads the bikes: dict from USER_PROFILE.md (via profile.load_profile) and
returns a typed config object. Single source of truth for per-bike physics.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from profile import load_profile


class UnknownBikeError(ValueError):
    pass


class UnsupportedSurfaceError(ValueError):
    pass


@dataclass
class AssistConfig:
    type: str
    placement: str
    rated_w: int
    peak_w: int
    torque_nm: int
    sensor: str
    cutoff_kph: float
    levels: list[str]
    boost_mode: bool
    battery_wh: int
    battery_range_km: str
    level_share: dict[str, float]
    default_level_flat: str
    default_level_climb_5pct: str
    default_level_climb_10pct: str


@dataclass
class BikeConfig:
    slug: str
    name: str
    bike_weight_kg: float
    system_weight_kg_default: float
    fr_split: str
    cda: float
    cda_range: str
    drivetrain_efficiency: float
    wheel_circ_m: float
    has_power_meter: bool
    tyres: dict
    crr_by_surface: dict[str, float]
    surfaces_supported: list[str]
    assist: Optional[AssistConfig] = None
    tyre_pressure_psi: Optional[dict] = None
    tyre_pressure_uncertainty_psi: Optional[float] = None
    unvalidated_by_model: bool = False
    unvalidated_by_model_source: Optional[str] = None

    def validate_surface(self, surface: str) -> None:
        if surface in self.crr_by_surface:
            return
        raise UnsupportedSurfaceError(
            f"Surface '{surface}' not supported by bike '{self.slug}'. "
            f"Supported surfaces: {list(self.crr_by_surface)}"
        )


def load_bike(slug: Optional[str] = None, *, profile: Optional[dict] = None) -> BikeConfig:
    if profile is None:
        profile = load_profile()
    bikes = profile.get("bikes") or {}
    if not bikes:
        raise UnknownBikeError("No bikes: block in USER_PROFILE.md")
    if slug is None:
        slug = profile.get("default_bike")
        if slug is None:
            raise UnknownBikeError("default_bike: not set in USER_PROFILE.md")
    if slug not in bikes:
        raise UnknownBikeError(
            f"Unknown bike slug: '{slug}'. Valid slugs: {sorted(bikes)}"
        )
    raw = bikes[slug]
    assist = None
    if "assist" in raw:
        a = raw["assist"]
        assist = AssistConfig(
            type=a["type"],
            placement=a["placement"],
            rated_w=int(a["rated_w"]),
            peak_w=int(a["peak_w"]),
            torque_nm=int(a["torque_nm"]),
            sensor=a["sensor"],
            cutoff_kph=float(a["cutoff_kph"]),
            levels=list(a["levels"]),
            boost_mode=bool(a["boost_mode"]),
            battery_wh=int(a["battery_wh"]),
            battery_range_km=a["battery_range_km"],
            level_share={k: float(v) for k, v in a["level_share"].items()},
            default_level_flat=a["default_level_flat"],
            default_level_climb_5pct=a["default_level_climb_5pct"],
            default_level_climb_10pct=a["default_level_climb_10pct"],
        )
    tyre_pressure_psi = raw.get("tyre_pressure_psi") or None
    tp_uncertainty = raw.get("tyre_pressure_uncertainty_psi")
    return BikeConfig(
        slug=slug,
        name=raw["name"],
        bike_weight_kg=float(raw["bike_weight_kg"]),
        system_weight_kg_default=float(raw["system_weight_kg_default"]),
        fr_split=str(raw["fr_split"]),
        cda=float(raw["cda"]),
        cda_range=raw.get("cda_range", ""),
        drivetrain_efficiency=float(raw["drivetrain_efficiency"]),
        wheel_circ_m=float(raw["wheel_circ_m"]),
        has_power_meter=bool(raw["has_power_meter"]),
        tyres=raw["tyres"],
        crr_by_surface={k: float(v) for k, v in raw["crr_by_surface"].items()},
        surfaces_supported=list(raw["surfaces_supported"]),
        assist=assist,
        tyre_pressure_psi=tyre_pressure_psi,
        tyre_pressure_uncertainty_psi=float(tp_uncertainty) if tp_uncertainty is not None else None,
        unvalidated_by_model=bool(raw.get("unvalidated_by_model", False)),
        unvalidated_by_model_source=raw.get("unvalidated_by_model_source"),
    )
