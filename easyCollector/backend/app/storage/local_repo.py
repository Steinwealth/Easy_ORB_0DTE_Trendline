"""
Easy Collector - Local Storage Repository
Handles local file storage for snapshots (CSV and JSON)
Saves data for 0DTE symbols and Crypto Futures symbols
"""

import logging
import json
import csv
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List
from app.config import get_settings
from app.models.snapshot_models import Snapshot, CollectionSummary

log = logging.getLogger(__name__)


class LocalRepository:
    """Local file repository for snapshot storage"""
    
    def __init__(self, base_dir: Optional[Path] = None):
        """Initialize local storage with base directory"""
        settings = get_settings()
        
        if base_dir is None:
            # Default: use config's resolved_local_storage_path (Cloud Run: /tmp/easy_collector)
            base_dir = settings.resolved_local_storage_path
        
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        
        # Create subdirectories
        self.json_dir = self.base_dir / "json"
        self.csv_dir = self.base_dir / "csv"
        self.summary_dir = self.base_dir / "summaries"
        
        for dir_path in [self.json_dir, self.csv_dir, self.summary_dir]:
            dir_path.mkdir(parents=True, exist_ok=True)
        
        log.info(f"✅ Local storage initialized: {self.base_dir}")
    
    def save_snapshot(self, snapshot: Snapshot) -> bool:
        """
        Save a snapshot to local files (JSON and CSV)
        
        Args:
            snapshot: Snapshot model instance
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            doc_id = snapshot.get_doc_id()
            snapshot_dict = snapshot.model_dump(exclude_none=True)
            snapshot_dict["doc_id"] = doc_id
            snapshot_dict["collection_timestamp"] = datetime.utcnow().isoformat()
            
            # Determine file paths based on market and date
            date_str = snapshot.timestamp_et.strftime("%Y%m%d")
            market = snapshot.market.value
            snapshot_type = snapshot.snapshot_type.value
            
            # JSON file: organized by market/date
            json_subdir = self.json_dir / market / date_str
            json_subdir.mkdir(parents=True, exist_ok=True)
            json_file = json_subdir / f"{doc_id}.json"
            
            with open(json_file, 'w') as f:
                json.dump(snapshot_dict, f, indent=2, default=str)
            
            # CSV file: append to daily CSV
            csv_file = self.csv_dir / f"{market}_{date_str}_{snapshot_type}.csv"
            
            # Check if file exists to determine if we need headers
            file_exists = csv_file.exists()
            
            with open(csv_file, 'a', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=self._get_csv_fieldnames(snapshot_dict))
                
                if not file_exists:
                    writer.writeheader()
                
                # Flatten nested structures for CSV
                flat_dict = self._flatten_snapshot_dict(snapshot_dict)
                writer.writerow(flat_dict)
            
            log.debug(f"  ✅ Saved snapshot locally: {doc_id}")
            return True
            
        except Exception as e:
            log.error(f"  ❌ Failed to save snapshot locally {snapshot.get_doc_id()}: {e}", exc_info=True)
            return False
    
    def save_run_log(self, summary: CollectionSummary) -> bool:
        """
        Save a collection run log to local files
        
        Args:
            summary: CollectionSummary model instance
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Generate document ID
            doc_id = f"{summary.market.value}_{summary.snapshot_type.value}_{summary.timestamp.strftime('%Y%m%d_%H%M%S')}"
            if summary.session:
                doc_id = f"{summary.market.value}_{summary.session}_{summary.snapshot_type.value}_{summary.timestamp.strftime('%Y%m%d_%H%M%S')}"
            
            run_dict = summary.model_dump(exclude_none=True)
            run_dict["doc_id"] = doc_id
            
            # Save JSON
            date_str = summary.timestamp.strftime("%Y%m%d")
            json_file = self.summary_dir / f"{date_str}_{doc_id}.json"
            
            with open(json_file, 'w') as f:
                json.dump(run_dict, f, indent=2, default=str)
            
            log.debug(f"✅ Saved run log locally: {doc_id}")
            return True
            
        except Exception as e:
            log.error(f"Failed to save run log locally: {e}", exc_info=True)
            return False
    
    def _get_csv_fieldnames(self, snapshot_dict: Dict) -> List[str]:
        """Get CSV fieldnames from snapshot dict (flattened)"""
        flat_dict = self._flatten_snapshot_dict(snapshot_dict)
        return list(flat_dict.keys())
    
    def _flatten_snapshot_dict(self, d: Dict, parent_key: str = '', sep: str = '_') -> Dict:
        """Flatten nested dictionary for CSV export"""
        items = []
        for k, v in d.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else k
            if isinstance(v, dict):
                items.extend(self._flatten_snapshot_dict(v, new_key, sep=sep).items())
            elif isinstance(v, list):
                # Convert lists to JSON strings
                items.append((new_key, json.dumps(v) if v else ''))
            else:
                items.append((new_key, v))
        return dict(items)
    
    def get_snapshots_by_date(self, date_str: str, market: Optional[str] = None) -> List[Dict]:
        """Get all snapshots for a given date"""
        snapshots = []
        
        if market:
            markets = [market]
        else:
            markets = ["US", "CRYPTO"]
        
        for mkt in markets:
            json_subdir = self.json_dir / mkt / date_str
            if json_subdir.exists():
                for json_file in json_subdir.glob("*.json"):
                    try:
                        with open(json_file, 'r') as f:
                            snapshots.append(json.load(f))
                    except Exception as e:
                        log.warning(f"Failed to load {json_file}: {e}")
        
        return snapshots
