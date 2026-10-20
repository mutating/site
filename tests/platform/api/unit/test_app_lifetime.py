"""Repeated application creation must release routers and their database state."""

import gc
import weakref

from throng_api.app import create_app
from throng_api.config import Settings


def test_application_factory_does_not_retain_discarded_instances():
    """Framework endpoint caches must not keep every previous API instance alive."""
    instances = [weakref.ref(create_app(Settings(_env_file=None))) for _ in range(3)]
    gc.collect()
    assert all(instance() is None for instance in instances)
