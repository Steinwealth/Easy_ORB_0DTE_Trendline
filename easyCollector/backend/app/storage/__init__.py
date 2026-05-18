"""Easy Collector - Storage Package"""

from .firestore_repo import FirestoreRepository
from .local_repo import LocalRepository

__all__ = ["FirestoreRepository", "LocalRepository"]
