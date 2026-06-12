import secrets


class UploadError(Exception):
    def __init__(
        self,
        status_code: int,
        error_code: str,
        message: str,
        *,
        diagnostic_id: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        self.diagnostic_id = diagnostic_id
        self.retryable = retryable


class UnexpectedUploadError(UploadError):
    def __init__(self) -> None:
        super().__init__(
            500,
            "internal_error",
            "The upload could not be completed.",
            diagnostic_id=secrets.token_urlsafe(12),
        )


def missing_client_id_error() -> UploadError:
    return UploadError(400, "missing_client_id", "X-Starun-Client-Id is required.")


def unsupported_extension_error() -> UploadError:
    return UploadError(
        415,
        "unsupported_file_extension",
        "Only .fits, .fit, and .fts files are supported.",
    )


def upload_too_large_error() -> UploadError:
    return UploadError(413, "upload_too_large", "The uploaded file is too large.")


def insufficient_storage_error() -> UploadError:
    return UploadError(
        507,
        "insufficient_storage",
        "There is not enough storage available for this upload.",
        retryable=True,
    )
