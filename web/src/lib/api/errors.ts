export class StarunApiError extends Error {
  readonly errorCode: string;
  readonly retryable: boolean;
  readonly quotaCharged: boolean;
  readonly diagnosticId: string | null;
  readonly status: number;

  constructor(
    errorCode: string,
    message: string,
    retryable: boolean,
    quotaCharged: boolean,
    diagnosticId: string | null,
    status: number,
  ) {
    super(message);
    this.name = "StarunApiError";
    this.errorCode = errorCode;
    this.retryable = retryable;
    this.quotaCharged = quotaCharged;
    this.diagnosticId = diagnosticId;
    this.status = status;
  }
}
