"""
Easy Collector - Firestore Repository
Handles Firestore operations for snapshot storage
Supports idempotent document IDs and run logging
"""

import logging
from datetime import datetime
from typing import Optional, Dict, Any
from google.cloud import firestore
from google.api_core.exceptions import AlreadyExists
from app.config import get_settings
from app.models.snapshot_models import Snapshot, CollectionSummary

log = logging.getLogger(__name__)


class FirestoreRepository:
    """Firestore repository for snapshot storage"""
    
    def __init__(self):
        """Initialize Firestore client"""
        settings = get_settings()
        
        # Store context for error logging
        self.project_id = settings.gcp_project_id
        self.database_id = settings.firestore_database_id
        self.use_emulator = settings.use_firestore_emulator
        
        if settings.use_firestore_emulator:
            # Use emulator for local development
            import os
            os.environ["FIRESTORE_EMULATOR_HOST"] = settings.firestore_emulator_host
            log.info(f"Using Firestore emulator at {settings.firestore_emulator_host}")
        
        try:
            # Build client kwargs (only include database if not default)
            # Treat "(default)", "", and None as "use default"
            db_id = settings.firestore_database_id
            client_kwargs = {"project": settings.gcp_project_id}
            if db_id and db_id != "(default)":
                client_kwargs["database"] = db_id
            
            self.db = firestore.Client(**client_kwargs)
            self.snapshots_collection = "snapshots"
            self.runs_collection = "runs"
            
            log.info(
                f"✅ Firestore client initialized "
                f"(project: {self.project_id}, database: {self.database_id}, "
                f"emulator: {self.use_emulator})"
            )
        except Exception as e:
            log.error(
                f"Failed to initialize Firestore client "
                f"(project: {self.project_id}, database: {self.database_id}, "
                f"emulator: {self.use_emulator}): {e}"
            )
            raise
    
    def save_snapshot(self, snapshot: Snapshot) -> bool:
        """
        Save a snapshot to Firestore with idempotent document ID (atomic create-only)
        
        Uses Firestore's create() method for atomic idempotency without extra reads.
        
        Args:
            snapshot: Snapshot model instance
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Generate idempotent document ID
            doc_id = snapshot.get_doc_id()
            doc_ref = self.db.collection(self.snapshots_collection).document(doc_id)
            
            # Convert snapshot to dict (exclude None values for optional fields)
            snapshot_dict = snapshot.model_dump(exclude_none=True)
            snapshot_dict["doc_id"] = doc_id
            # Use Firestore server timestamp for consistency across instances
            snapshot_dict["collection_timestamp"] = firestore.SERVER_TIMESTAMP
            
            try:
                # Atomic create-only: fails if document exists (idempotent)
                log.debug(f"  Creating snapshot document: {doc_id} (collection: {self.snapshots_collection})")
                doc_ref.create(snapshot_dict)
                log.info(f"  ✅ Created snapshot in Firestore: {doc_id} (project: {self.project_id}, database: {self.database_id})")
                return True
            except AlreadyExists:
                # Document already exists - this is expected for idempotency
                log.debug(f"  Document {doc_id} already exists (idempotent skip) - this is expected")
                return True
            
        except Exception as e:
            doc_id = snapshot.get_doc_id()
            log.error(
                f"  ❌ Failed to save snapshot {doc_id}: {e} "
                f"(project: {self.project_id}, database: {self.database_id}, "
                f"emulator: {self.use_emulator}, collection: {self.snapshots_collection})",
                exc_info=True
            )
            return False
    
    def get_snapshot(self, doc_id: str) -> Optional[Snapshot]:
        """Get a snapshot by document ID"""
        try:
            doc_ref = self.db.collection(self.snapshots_collection).document(doc_id)
            doc = doc_ref.get()
            
            if doc.exists:
                data = doc.to_dict()
                return Snapshot(**data)
            return None
            
        except Exception as e:
            log.error(
                f"Failed to get snapshot {doc_id}: {e} "
                f"(project: {self.project_id}, database: {self.database_id}, "
                f"emulator: {self.use_emulator}, collection: {self.snapshots_collection})",
                exc_info=True
            )
            return None
    
    def save_run_log(self, summary: CollectionSummary) -> bool:
        """
        Save a collection run log to Firestore
        
        Args:
            summary: CollectionSummary model instance
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Generate document ID with microseconds to avoid collisions
            # Format: {market}_{snapshot_type}_{timestamp_with_microseconds}
            timestamp_str = summary.timestamp.strftime('%Y%m%d_%H%M%S_%f')  # includes microseconds
            
            if summary.session:
                doc_id = f"{summary.market.value}_{summary.session}_{summary.snapshot_type.value}_{timestamp_str}"
            else:
                doc_id = f"{summary.market.value}_{summary.snapshot_type.value}_{timestamp_str}"
            
            doc_ref = self.db.collection(self.runs_collection).document(doc_id)
            
            run_dict = summary.model_dump(exclude_none=True)
            run_dict["doc_id"] = doc_id
            # Use Firestore server timestamp for consistency
            run_dict["collection_timestamp"] = firestore.SERVER_TIMESTAMP
            
            doc_ref.set(run_dict)
            
            log.info(f"✅ Saved run log: {doc_id} ({summary.total_snapshots} snapshots)")
            return True
            
        except Exception as e:
            log.error(
                f"Failed to save run log: {e} "
                f"(project: {self.project_id}, database: {self.database_id}, "
                f"emulator: {self.use_emulator}, collection: {self.runs_collection})",
                exc_info=True
            )
            return False
    
    def snapshot_exists(self, doc_id: str) -> bool:
        """
        Check if a snapshot document exists (for idempotency/debugging)
        
        Uses field_paths=[] to minimize payload (only checks existence, no data).
        Note: With create() idempotency in save_snapshot(), this is rarely needed.
        
        Args:
            doc_id: Document ID to check
            
        Returns:
            bool: True if document exists, False otherwise
        """
        try:
            doc_ref = self.db.collection(self.snapshots_collection).document(doc_id)
            # Use select([]) to minimize payload (only check existence)
            doc = doc_ref.get(field_paths=[])
            return doc.exists
        except Exception as e:
            log.error(
                f"Failed to check snapshot existence {doc_id}: {e} "
                f"(project: {self.project_id}, database: {self.database_id}, "
                f"emulator: {self.use_emulator}, collection: {self.snapshots_collection})",
                exc_info=True
            )
            return False
