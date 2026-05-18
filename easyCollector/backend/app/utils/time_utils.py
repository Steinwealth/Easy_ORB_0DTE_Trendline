"""
Easy Collector - Time Utilities
Handles timezone conversions, schedule calculations, and time-based logic
"""

from datetime import datetime, time, timedelta
from typing import Optional, Tuple
import pytz
from pytz.exceptions import AmbiguousTimeError, NonExistentTimeError

# Cache timezone objects (pytz caches internally, but avoid repeated lookups)
_MARKET_TZ = pytz.timezone("America/New_York")
_UTC_TZ = pytz.timezone("UTC")

# Export ET_TZ for compatibility (used by us_intraday_cache, yfinance_client)
ET_TZ = _MARKET_TZ


def get_market_tz() -> pytz.timezone:
    """Get market timezone (ET/New York) - cached instance"""
    return _MARKET_TZ


def get_utc_tz() -> pytz.timezone:
    """Get UTC timezone - cached instance"""
    return _UTC_TZ


def ensure_tz(dt: datetime, tz: pytz.timezone) -> datetime:
    """
    Ensure dt is timezone-aware in tz.
    If dt is naive, localize with DST-safe handling.
    
    Handles DST transition edge cases:
    - NonExistentTimeError (spring forward): pushes forward 1 hour
    - AmbiguousTimeError (fall back): chooses standard time (is_dst=False)
    
    Args:
        dt: Datetime (naive or timezone-aware)
        tz: Target timezone (pytz.timezone)
    
    Returns:
        Timezone-aware datetime in tz
    """
    if dt.tzinfo is None:
        # Naive datetime: localize with DST-safe handling
        try:
            return tz.localize(dt, is_dst=None)  # strict mode
        except NonExistentTimeError:
            # Spring forward: time doesn't exist (e.g., 2:00-3:00 AM)
            # Push forward 1 hour
            return tz.localize(dt + timedelta(hours=1), is_dst=True)
        except AmbiguousTimeError:
            # Fall back: time is ambiguous (e.g., 1:00-2:00 AM occurs twice)
            # Choose standard time (is_dst=False)
            return tz.localize(dt, is_dst=False)
    # Already timezone-aware: convert to target timezone
    return dt.astimezone(tz)


def now_et() -> datetime:
    """Get current time in ET timezone"""
    return datetime.now(get_market_tz())


def now_utc() -> datetime:
    """Get current time in UTC"""
    return datetime.now(get_utc_tz())


def et_to_utc(et_time: datetime) -> datetime:
    """Convert ET datetime to UTC"""
    et_time = ensure_tz(et_time, get_market_tz())
    return et_time.astimezone(get_utc_tz())


def utc_to_et(utc_time: datetime) -> datetime:
    """Convert UTC datetime to ET"""
    utc_time = ensure_tz(utc_time, get_utc_tz())
    return utc_time.astimezone(get_market_tz())


def get_today_et() -> datetime:
    """Get today's date at midnight ET"""
    today = now_et().date()
    return ensure_tz(datetime.combine(today, time.min), get_market_tz())


def parse_time_string(time_str: str) -> time:
    """Parse time string in HH:MM format"""
    hour, minute = map(int, time_str.split(":"))
    return time(hour, minute)


def get_next_session_time(session_open_time: time, reference_time: Optional[datetime] = None) -> datetime:
    """
    Get next occurrence of a session open time in ET
    
    Args:
        session_open_time: Session open time (e.g., time(9, 30))
        reference_time: Reference time (default: now ET)
    
    Returns:
        Next session open datetime in ET (timezone-aware)
    """
    if reference_time is None:
        reference_time = now_et()
    else:
        reference_time = ensure_tz(reference_time, get_market_tz())
    
    # Get today at session open time
    today_session = ensure_tz(
        datetime.combine(reference_time.date(), session_open_time),
        get_market_tz()
    )
    
    # If session time has already passed today, use tomorrow
    if today_session <= reference_time:
        tomorrow = reference_time.date() + timedelta(days=1)
        return ensure_tz(
            datetime.combine(tomorrow, session_open_time),
            get_market_tz()
        )
    
    return today_session


def get_previous_session_time(session_open_time: time, reference_time: Optional[datetime] = None) -> datetime:
    """
    Get previous occurrence of a session open time in ET
    
    Args:
        session_open_time: Session open time
        reference_time: Reference time (default: now ET)
    
    Returns:
        Previous session open datetime in ET (timezone-aware)
    """
    if reference_time is None:
        reference_time = now_et()
    else:
        reference_time = ensure_tz(reference_time, get_market_tz())
    
    # Get today at session open time
    today_session = ensure_tz(
        datetime.combine(reference_time.date(), session_open_time),
        get_market_tz()
    )
    
    # If session time hasn't passed today, use yesterday
    if today_session > reference_time:
        yesterday = reference_time.date() - timedelta(days=1)
        return ensure_tz(
            datetime.combine(yesterday, session_open_time),
            get_market_tz()
        )
    
    return today_session


def get_minutes_since_open(open_time: datetime, reference_time: Optional[datetime] = None) -> int:
    """Calculate minutes since session open"""
    if reference_time is None:
        reference_time = now_et()
    else:
        reference_time = ensure_tz(reference_time, get_market_tz())
    
    open_time = ensure_tz(open_time, get_market_tz())
    
    delta = reference_time - open_time
    return int(delta.total_seconds() / 60)


def get_minutes_to_next_session(next_session_time: datetime, reference_time: Optional[datetime] = None) -> int:
    """Calculate minutes until next session open"""
    if reference_time is None:
        reference_time = now_et()
    else:
        reference_time = ensure_tz(reference_time, get_market_tz())
    
    next_session_time = ensure_tz(next_session_time, get_market_tz())
    
    delta = next_session_time - reference_time
    return int(delta.total_seconds() / 60)


def get_us_orb_time(date: Optional[datetime] = None) -> datetime:
    """Get ORB snapshot time (9:45 ET) for a given date"""
    if date is None:
        date = now_et()
    else:
        date = ensure_tz(date, get_market_tz())
    
    orb_time = time(9, 45)
    return ensure_tz(
        datetime.combine(date.date(), orb_time),
        get_market_tz()
    )


def get_us_signal_time(date: Optional[datetime] = None) -> datetime:
    """Get SIGNAL snapshot time (10:30 ET) for a given date"""
    if date is None:
        date = now_et()
    else:
        date = ensure_tz(date, get_market_tz())
    
    signal_time = time(10, 30)
    return ensure_tz(
        datetime.combine(date.date(), signal_time),
        get_market_tz()
    )


def get_us_outcome_time(date: Optional[datetime] = None, early_close_time: Optional[time] = None) -> datetime:
    """
    Get OUTCOME snapshot time (15:55 ET or early_close - 5 min)
    
    Args:
        date: Date to calculate for (default: today)
        early_close_time: Early close time if applicable
    
    Returns:
        OUTCOME snapshot datetime in ET (timezone-aware)
    """
    if date is None:
        date = now_et()
    else:
        date = ensure_tz(date, get_market_tz())
    
    # If early close, capture 5 minutes before
    if early_close_time:
        outcome_time = datetime.combine(date.date(), early_close_time) - timedelta(minutes=5)
        return ensure_tz(outcome_time, get_market_tz())
    
    # Normal close: 15:55 ET (5 min before 16:00 close)
    outcome_time = time(15, 55)
    return ensure_tz(
        datetime.combine(date.date(), outcome_time),
        get_market_tz()
    )


def get_crypto_orb_time(session_open_time: datetime) -> datetime:
    """
    Get ORB snapshot time (open + 15 minutes) for crypto session
    
    Args:
        session_open_time: Session open datetime (assumed ET, will be normalized)
    
    Returns:
        ORB snapshot datetime (timezone-aware, same tz as input)
    """
    session_open_time = ensure_tz(session_open_time, get_market_tz())
    return session_open_time + timedelta(minutes=15)


def get_crypto_signal_time(session_open_time: datetime) -> datetime:
    """
    Get SIGNAL snapshot time (open + 60 minutes) for crypto session
    
    Args:
        session_open_time: Session open datetime (assumed ET, will be normalized)
    
    Returns:
        SIGNAL snapshot datetime (timezone-aware, same tz as input)
    """
    session_open_time = ensure_tz(session_open_time, get_market_tz())
    return session_open_time + timedelta(minutes=60)


def get_crypto_outcome_time(next_session_open_time: datetime) -> datetime:
    """
    Get OUTCOME snapshot time (next session open - 5 minutes) for crypto
    
    Args:
        next_session_open_time: Next session open datetime (assumed ET, will be normalized)
    
    Returns:
        OUTCOME snapshot datetime (timezone-aware, same tz as input)
    """
    next_session_open_time = ensure_tz(next_session_open_time, get_market_tz())
    return next_session_open_time - timedelta(minutes=5)


def _in_us_market_hours(dt_et: datetime) -> bool:
    """True if dt_et (ET) is within US regular session Mon–Fri 9:30–16:00."""
    if dt_et.weekday() >= 5:
        return False
    return time(9, 30, 0) <= dt_et.time() <= time(16, 0, 0)


def in_us_market_hours(dt_et: Optional[datetime] = None) -> bool:
    """True if the given time (or now ET) is within US regular session Mon–Fri 9:30–16:00."""
    if dt_et is None:
        dt_et = datetime.now(get_market_tz())
    dt_et = ensure_tz(dt_et, get_market_tz())
    return _in_us_market_hours(dt_et)


def get_last_us_session_2h_window_utc() -> Tuple[datetime, datetime]:
    """
    Return (start_utc, end_utc) for the last 2 hours (14:00–16:00 ET) of the most recent
    US regular session. Use when outside US market hours to fetch a window that has data.
    """
    mt = get_market_tz()
    now_et = datetime.now(mt)
    d = now_et.date()
    if now_et.weekday() >= 5:  # Sat or Sun
        while d.weekday() >= 5:
            d -= timedelta(days=1)
    elif now_et.time() < time(9, 30, 0):  # Before open
        d -= timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
    start_et = ensure_tz(datetime.combine(d, time(14, 0, 0)), mt)
    end_et = ensure_tz(datetime.combine(d, time(16, 0, 0)), mt)
    return start_et.astimezone(get_utc_tz()), end_et.astimezone(get_utc_tz())


def format_datetime_for_doc_id(dt: datetime) -> str:
    """
    Format datetime for Firestore document ID (YYYYMMDD_HHMM_ET)
    
    Args:
        dt: Datetime (naive or timezone-aware)
    
    Returns:
        Formatted string: YYYYMMDD_HHMM_ET
    """
    dt_et = ensure_tz(dt, get_market_tz())
    return dt_et.strftime("%Y%m%d_%H%M_ET")
