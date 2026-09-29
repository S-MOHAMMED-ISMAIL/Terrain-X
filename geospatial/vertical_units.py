"""D3: vertical (Z) units of physical elevation rasters — resolved ONLY from
documented metadata, never assumed.

Why this exists: a raster's horizontal CRS says nothing about the unit of its
pixel VALUES. A DEM in UTM metres can carry elevations in feet. Terrain
derivatives (slope/aspect/hillshade, the ground filter) combine vertical and
horizontal distances, so they need both in one known unit.

## Sources (documented metadata only)

1. The GDAL band unit type (`dataset.units[0]`). GDAL sets it from a GeoTIFF's
   vertical CRS, and producers set it explicitly (TERRAIN-X writes it on every
   calibrated elevation raster whose reference unit is known).
2. The vertical component of a compound CRS (WKT2 `VERTCRS ... LENGTHUNIT`),
   read with `GTIFF_REPORT_COMPD_CS=YES` (GDAL otherwise hides it).
3. For GCP references: the vertical component of the declared compound
   `gcp_crs` (e.g. "EPSG:32613+5703").

Both raster sources are read; if they disagree the result is a CONFLICT.
No source -> UNKNOWN. A declared unit that is not supported -> UNSUPPORTED.
Never inferred from file names, value ranges or the horizontal CRS.

## Supported units (exact definitions)

- metre (factor 1)
- international foot: 0.3048 m exactly
- US survey foot: 1200/3937 m exactly
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import rasterio
from rasterio.crs import CRS
from rasterio.errors import CRSError, RasterioIOError


@dataclass(frozen=True)
class VerticalUnit:
    code: str  # "m" | "ft" | "ftUS"
    name: str  # canonical GDAL/PROJ unit name, written as the GeoTIFF band unit
    to_metre: float  # exact conversion factor to metres


METRE = VerticalUnit("m", "metre", 1.0)
FOOT = VerticalUnit("ft", "foot", 0.3048)
US_SURVEY_FOOT = VerticalUnit("ftUS", "US survey foot", 1200.0 / 3937.0)
SUPPORTED_UNITS = (METRE, FOOT, US_SURVEY_FOOT)

_ALIASES: dict[str, VerticalUnit] = {}
for _unit, _names in (
    (METRE, ("metre", "meter", "metres", "meters", "m")),
    (FOOT, ("foot", "feet", "ft", "international foot", "foot (international)", "intl foot")),
    (
        US_SURVEY_FOOT,
        ("us survey foot", "us survey feet", "ftus", "us-ft", "foot_us", "us_survey_foot"),
    ),
):
    for _name in _names:
        _ALIASES[_name] = _unit

KNOWN = "known"
UNKNOWN = "unknown"
UNSUPPORTED = "unsupported"
CONFLICT = "conflict"

# Internal convention for every physical terrain derivative (D3).
CALCULATION_UNIT = METRE


class VerticalUnitError(Exception):
    """Physical Z units are required but could not be established."""


@dataclass(frozen=True)
class VerticalUnitResolution:
    status: str  # KNOWN | UNKNOWN | UNSUPPORTED | CONFLICT
    unit: VerticalUnit | None  # set iff status == KNOWN
    source: str | None  # where the unit came from (or was looked for)
    declared: str | None  # the raw declared value(s), verbatim
    diagnostic: str | None  # why it is not KNOWN

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "unit": self.unit.code if self.unit else None,
            "unit_name": self.unit.name if self.unit else None,
            "to_metre": self.unit.to_metre if self.unit else None,
            "source": self.source,
            "declared": self.declared,
            "diagnostic": self.diagnostic,
        }


def normalize_unit(name: str | None) -> VerticalUnit | None:
    """The supported unit a declared unit name denotes, or None."""
    if name is None:
        return None
    return _ALIASES.get(name.strip().lower())


def _from_declared(declared: str, source: str) -> VerticalUnitResolution:
    unit = normalize_unit(declared)
    if unit is None:
        return VerticalUnitResolution(
            UNSUPPORTED,
            None,
            source,
            declared,
            f"The declared vertical unit '{declared}' ({source}) is not supported; "
            "supported: metre, foot (0.3048 m), US survey foot (1200/3937 m).",
        )
    return VerticalUnitResolution(KNOWN, unit, source, declared, None)


def compound_crs_vertical_unit_name(crs: CRS | None) -> str | None:
    """The vertical axis unit name of a compound CRS (WKT2), else None."""
    if crs is None:
        return None
    try:
        wkt = crs.to_wkt(version="WKT2_2019")
    except CRSError:
        return None
    match = re.search(r'VERTCRS\[.*?LENGTHUNIT\["([^"]+)"', wkt, re.S)
    return match.group(1) if match else None


def unknown(source: str, what: str) -> VerticalUnitResolution:
    return VerticalUnitResolution(
        UNKNOWN,
        None,
        source,
        None,
        f"The vertical unit of {what} could not be established from its metadata "
        "(no band unit type and no vertical CRS). It is not assumed to be metres.",
    )


def read_raster_vertical_unit(path: str | Path) -> VerticalUnitResolution:
    """Resolves band 1's vertical unit from the raster's own metadata."""
    try:
        with rasterio.Env(GTIFF_REPORT_COMPD_CS=True):
            with rasterio.open(path) as dataset:
                band_unit = (dataset.units[0] or "").strip() or None
                vertical_name = compound_crs_vertical_unit_name(dataset.crs)
    except (RasterioIOError, ValueError) as exc:
        return VerticalUnitResolution(
            UNKNOWN, None, None, None, f"The raster could not be read: {exc}"
        )
    if band_unit is None and vertical_name is None:
        return unknown("geotiff_band_unit+vertical_crs", "this elevation raster")
    if band_unit is not None and vertical_name is not None:
        a, b = normalize_unit(band_unit), normalize_unit(vertical_name)
        if a is None or b is None or a != b:
            return VerticalUnitResolution(
                CONFLICT,
                None,
                "geotiff_band_unit+vertical_crs",
                f"band unit '{band_unit}', vertical CRS unit '{vertical_name}'",
                f"The band unit '{band_unit}' and the vertical CRS unit '{vertical_name}' "
                "do not agree on a supported unit.",
            )
        return VerticalUnitResolution(
            KNOWN,
            a,
            "geotiff_band_unit+vertical_crs",
            f"band unit '{band_unit}', vertical CRS unit '{vertical_name}'",
            None,
        )
    if band_unit is not None:
        return _from_declared(band_unit, "geotiff_band_unit")
    return _from_declared(vertical_name, "vertical_crs")


def crs_vertical_unit(crs_text: str | None, *, source: str) -> VerticalUnitResolution:
    """Resolves the vertical unit declared by a (compound) CRS string, e.g. a
    GCP reference's `gcp_crs`."""
    try:
        crs = CRS.from_user_input(crs_text) if crs_text else None
    except CRSError:
        crs = None
    name = compound_crs_vertical_unit_name(crs)
    if name is None:
        return VerticalUnitResolution(
            UNKNOWN,
            None,
            source,
            crs_text,
            f"The CRS '{crs_text}' declares no vertical component, so the unit of its "
            "elevations could not be established. It is not assumed to be metres. Declare "
            "a compound CRS (e.g. 'EPSG:32613+5703' for metres) to make it explicit.",
        )
    return _from_declared(name, source)


def require_known(resolution: VerticalUnitResolution, operation: str) -> VerticalUnit:
    """The resolved unit, or VerticalUnitError explaining why `operation`
    (which needs physical Z units) must not run."""
    if resolution.status == KNOWN and resolution.unit is not None:
        return resolution.unit
    raise VerticalUnitError(
        f"{operation} needs physical vertical units, which could not be established: "
        f"{resolution.diagnostic}"
    )


def horizontal_to_metre(crs: CRS) -> float:
    """Metres per linear unit of a projected CRS (raises for an unknown unit)."""
    try:
        _name, factor = crs.linear_units_factor
    except CRSError as exc:
        raise VerticalUnitError(f"The horizontal unit of {crs} is unknown: {exc}") from exc
    return float(factor)


def unit_provenance(
    resolution: VerticalUnitResolution, *, horizontal_unit: str, crs: str, method: str
) -> dict:
    """The provenance block recorded on physical-derivative artifacts."""
    unit = resolution.unit
    return {
        "source_vertical_unit": unit.code if unit else None,
        "source_vertical_unit_name": unit.name if unit else None,
        "vertical_unit_source": resolution.source,
        "vertical_unit_declared": resolution.declared,
        "calculation_vertical_unit": CALCULATION_UNIT.code,
        "calculation_horizontal_unit": CALCULATION_UNIT.code,
        "vertical_conversion_factor": unit.to_metre if unit else None,
        "horizontal_unit": horizontal_unit,
        "crs": crs,
        "method": method,
        "source_raster_modified": False,
    }
