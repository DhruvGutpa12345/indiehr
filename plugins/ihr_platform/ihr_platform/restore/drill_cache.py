"""The restore drill's cache (P1-T22b, settings/drill.py): this process's memory, never Redis.

CARE's locks (care/utils/lock.py, around the role sync) call django-redis's ``set(..., nx=True)``;
here that is ``add()``, which is what it means. Nothing is shared with any other process.
"""

from django.core.cache.backends.base import DEFAULT_TIMEOUT
from django.core.cache.backends.locmem import LocMemCache


class DrillCache(LocMemCache):
    def set(
        self, key, value, timeout=DEFAULT_TIMEOUT, version=None, nx=False, **kwargs
    ):
        if nx:
            return self.add(key, value, timeout, version)
        super().set(key, value, timeout, version)
        return True

    def delete_pattern(self, pattern, version=None, **kwargs):
        """django-redis's: every key of this process's memory matching ``pattern``."""
        import fnmatch

        full = self.make_key(pattern, version=version)
        with self._lock:
            gone = [k for k in list(self._cache) if fnmatch.fnmatchcase(k, full)]
            for key in gone:
                self._delete(key)
        return len(gone)
