// HTTP-shaped errors: handlers throw these, the controller renders them as
// { error, request_id, state } with the carried status code.

export function httpError(status, error, detail) {
  const err = new Error(detail ?? error);
  err.status = status;
  err.error = error;
  return err;
}

export function isHttpError(err) {
  return err != null && typeof err.status === 'number' && typeof err.error === 'string';
}
