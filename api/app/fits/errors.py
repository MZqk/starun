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


class InvalidXisfError(FitsInspectionError):
    error_code = "invalid_xisf"
    default_message = "The uploaded file is not a valid XISF file."


class UnsupportedXisfDataError(FitsInspectionError):
    error_code = "unsupported_xisf_data"
    default_message = "The XISF file does not contain a supported image."


class XisfStatisticsError(FitsInspectionError):
    error_code = "xisf_statistics_failed"
    default_message = "Statistics could not be calculated for the XISF image."
