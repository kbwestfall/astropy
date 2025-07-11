# Licensed under a 3-clause BSD style license - see LICENSE.rst

"""This module defines custom errors and exceptions used in astropy.coordinates."""

__all__ = [
    "ConvertError",
    "NonRotationTransformationError",
    "NonRotationTransformationWarning",
    "UnknownSiteException",
]

from typing import TYPE_CHECKING

from astropy.utils.exceptions import AstropyUserWarning

if TYPE_CHECKING:
    from astropy.coordinates import BaseCoordinateFrame


# TODO: consider if this should be used to `units`?
class UnitsError(ValueError):
    """
    Raised if units are missing or invalid.
    """


class ConvertError(Exception):
    """
    Raised if a coordinate system cannot be converted to another.
    """


class NonRotationTransformationError(ValueError):
    """
    Raised for transformations that are not simple rotations. Such
    transformations can change the angular separation between coordinates
    depending on its direction.
    """

    def __init__(
        self, frame_to: "BaseCoordinateFrame", frame_from: "BaseCoordinateFrame"
    ) -> None:
        self.frame_to = frame_to
        self.frame_from = frame_from

    def __str__(self) -> str:
        return (
            "refusing to transform other coordinates from "
            f"{self.frame_from.replicate_without_data()} to "
            f"{self.frame_to.replicate_without_data()} because angular separation "
            "can depend on the direction of the transformation"
        )


class UnknownSiteException(KeyError):
    def __init__(self, site, attribute, close_names=None):
        self.site = site
        self.attribute = attribute
        self.close_names = close_names

    def __str__(self) -> str:
        msg = (
            f"Site {self.site!r} not in database. Use {self.attribute} to see "
            f"available sites. If {self.site!r} exists in the online astropy-data "
            "repository, use the 'refresh_cache=True' option to download the latest "
            "version."
        )
        if self.close_names:
            msg += f" Did you mean one of: {', '.join(map(repr, self.close_names))}?"
        return msg


class NonRotationTransformationWarning(AstropyUserWarning):
    """
    Emitted for transformations that are not simple rotations. Such
    transformations can change the angular separation between coordinates
    depending on its direction.
    """

    def __init__(
        self, frame_to: "BaseCoordinateFrame", frame_from: "BaseCoordinateFrame"
    ) -> None:
        self.frame_to = frame_to
        self.frame_from = frame_from

    def __str__(self) -> str:
        return (
            "transforming other coordinates from "
            f"{self.frame_from.replicate_without_data()} to "
            f"{self.frame_to.replicate_without_data()}. Angular separation can depend "
            "on the direction of the transformation."
        )
