from typing import ClassVar


class FitsInspectionError(Exception):
    error_code: ClassVar[str]
    default_message: ClassVar[str]

    def __init__(self) -> None:
        super().__init__(self.default_message)


class InvalidFitsError(FitsInspectionError):
    error_code = "invalid_fits"
    default_message = "The uploaded file is not a valid FITS file."


class UnsupportedFitsDataError(FitsInspectionError):
    error_code = "unsupported_fits_data"
    default_message = "The FITS file does not contain a supported image."


class FitsStatisticsError(FitsInspectionError):
    error_code = "fits_statistics_failed"
    default_message = "Statistics could not be calculated for the FITS image."
