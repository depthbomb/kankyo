from kankyo import env, url, Schema, one_of, secret, string, boolean, integer, list_of, duration

schema = Schema(
    database_url=env('DATABASE_URL', url(schemes=('postgres', 'postgresql')), aliases=('DB_URL',)),
    port=env('PORT', integer(minimum=1, maximum=65535), default=8080, description='HTTP listen port.'),
    debug=env('DEBUG', boolean(), optional=True),
    request_timeout=env('REQUEST_TIMEOUT', duration(), default='5s'),
    allowed_hosts=env('ALLOWED_HOSTS', list_of(string(min_length=1), unique=True), default=['localhost']),
    log_level=env('LOG_LEVEL', one_of('debug', 'info', 'warning', 'error'), default='info'),
    api_token=env('API_TOKEN', secret(min_length=1)),
)
