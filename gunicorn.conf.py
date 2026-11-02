"""Start sync after Gunicorn forks, including when app preloading is enabled."""
def post_worker_init(worker):
    from app import start_background_workers
    start_background_workers()
