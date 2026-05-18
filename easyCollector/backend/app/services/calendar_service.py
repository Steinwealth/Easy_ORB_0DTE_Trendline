"""
Easy Collector - Calendar Service
Handles holiday detection, early close logic, and calendar tagging
Uses the same dynamic holiday calculator logic as Easy ORB Strategy

Optimization: Attempts to import from shared modules if available (for deployed environment),
falls back to inline implementation if not available (for standalone deployment)
"""

from datetime import date, datetime, time, timedelta
from typing import Optional, Dict, List, Tuple
from functools import lru_cache
from app.config import get_settings
from app.utils.time_utils import get_market_tz, now_et, parse_time_string

# Try to import shared holiday calculator (if deployed with Easy ORB Strategy)
try:
    import sys
    from pathlib import Path
    # Add parent modules directory to path if available
    parent_modules = Path(__file__).parent.parent.parent.parent.parent / "modules"
    if parent_modules.exists():
        sys.path.insert(0, str(parent_modules))
    
    from dynamic_holiday_calculator import (
        calculate_us_holidays as _shared_calculate_us_holidays,
        calculate_low_volume_holidays as _shared_calculate_low_volume_holidays,
        calculate_early_close_days as _shared_calculate_early_close_days
    )
    USE_SHARED_CALCULATOR = True
except ImportError:
    USE_SHARED_CALCULATOR = False

# Module-level caches for holiday calculations (by year)
_holiday_cache: Dict[int, List[Tuple[date, str]]] = {}
_low_volume_cache: Dict[int, List[Tuple[date, str]]] = {}
_early_close_cache: Dict[int, List[Tuple[date, str, str]]] = {}


def observed_date(d: date) -> date:
    """
    Calculate the observed date for a fixed holiday.
    US markets observe holidays on weekdays:
    - If holiday falls on Saturday, observe on Friday
    - If holiday falls on Sunday, observe on Monday
    
    Args:
        d: Calendar date of the holiday
    
    Returns:
        Observed date (weekday)
    """
    # If holiday falls on Saturday, observe on Friday
    if d.weekday() == 5:
        return d - timedelta(days=1)
    # If holiday falls on Sunday, observe on Monday
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def calculate_easter(year: int) -> date:
    """Calculate Easter Sunday for a given year using the Anonymous Gregorian algorithm"""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    n = (h + l - 7 * m + 114) // 31
    p = (h + l - 7 * m + 114) % 31
    return date(year, n, p + 1)


def get_nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    """Get the nth occurrence of a weekday in a given month (0=Monday, 6=Sunday)"""
    first_day = date(year, month, 1)
    days_ahead = weekday - first_day.weekday()
    if days_ahead < 0:
        days_ahead += 7
    first_weekday = first_day + timedelta(days=days_ahead)
    return first_weekday + timedelta(weeks=n-1)


def calculate_us_holidays(year: int) -> List[Tuple[date, str]]:
    """Calculate all US market holidays for a given year (uses shared calculator if available)"""
    # Check cache first
    if year in _holiday_cache:
        return _holiday_cache[year]
    
    if USE_SHARED_CALCULATOR:
        holidays = _shared_calculate_us_holidays(year)
        _holiday_cache[year] = holidays
        return holidays
    
    # Inline implementation (fallback if shared module not available)
    holidays = []
    
    # Fixed holidays (with observed date handling)
    holidays.extend([
        (observed_date(date(year, 1, 1)), "New Year's Day"),
        (observed_date(date(year, 7, 4)), "Independence Day"),
        (observed_date(date(year, 12, 25)), "Christmas Day"),
        (observed_date(date(year, 6, 19)), "Juneteenth"),
    ])
    
    # Variable holidays
    mlk_day = get_nth_weekday_of_month(year, 1, 0, 3)  # 3rd Monday of January
    holidays.append((mlk_day, "Martin Luther King Jr. Day"))
    
    presidents_day = get_nth_weekday_of_month(year, 2, 0, 3)  # 3rd Monday of February
    holidays.append((presidents_day, "Presidents' Day"))
    
    easter = calculate_easter(year)
    good_friday = easter - timedelta(days=2)
    holidays.append((good_friday, "Good Friday"))
    
    # Memorial Day - Last Monday of May
    last_day_of_may = date(year, 6, 1) - timedelta(days=1)
    days_back = last_day_of_may.weekday()
    memorial_day = last_day_of_may - timedelta(days=days_back)
    holidays.append((memorial_day, "Memorial Day"))
    
    labor_day = get_nth_weekday_of_month(year, 9, 0, 1)  # 1st Monday of September
    holidays.append((labor_day, "Labor Day"))
    
    thanksgiving = get_nth_weekday_of_month(year, 11, 3, 4)  # 4th Thursday
    holidays.append((thanksgiving, "Thanksgiving Day"))
    
    holidays = sorted(holidays)
    _holiday_cache[year] = holidays
    return holidays


def calculate_low_volume_holidays(year: int) -> List[Tuple[date, str]]:
    """Calculate low-volume holidays (uses shared calculator if available)"""
    # Check cache first
    if year in _low_volume_cache:
        return _low_volume_cache[year]
    
    if USE_SHARED_CALCULATOR:
        low_volume_days = _shared_calculate_low_volume_holidays(year)
        _low_volume_cache[year] = low_volume_days
        return low_volume_days
    
    # Inline implementation (fallback)
    low_volume_days = []
    
    halloween = date(year, 10, 31)
    if halloween.weekday() < 5:
        low_volume_days.append((halloween, "Halloween"))
    
    columbus_day = get_nth_weekday_of_month(year, 10, 0, 2)
    low_volume_days.append((columbus_day, "Indigenous Peoples' Day / Columbus Day"))
    
    veterans_day = date(year, 11, 11)
    if veterans_day.weekday() < 5:
        low_volume_days.append((veterans_day, "Veterans Day"))
    
    thanksgiving = get_nth_weekday_of_month(year, 11, 3, 4)
    day_before_thanksgiving = thanksgiving - timedelta(days=1)
    if day_before_thanksgiving.weekday() < 5:
        low_volume_days.append((day_before_thanksgiving, "Day Before Thanksgiving"))
    
    black_friday = thanksgiving + timedelta(days=1)
    if black_friday.weekday() < 5:
        low_volume_days.append((black_friday, "Black Friday"))
    
    christmas_eve = date(year, 12, 24)
    if christmas_eve.weekday() < 5:
        low_volume_days.append((christmas_eve, "Christmas Eve"))
    
    nye = date(year, 12, 31)
    if nye.weekday() < 5:
        low_volume_days.append((nye, "New Year's Eve"))
    
    christmas = date(year, 12, 25)
    day_after_christmas = date(year, 12, 26)
    if christmas.weekday() < 5 and day_after_christmas.weekday() < 5:
        low_volume_days.append((day_after_christmas, "Day After Christmas"))
    
    new_years = date(year, 1, 1)
    day_after_ny = date(year, 1, 2)
    if new_years.weekday() < 5 and day_after_ny.weekday() < 5:
        low_volume_days.append((day_after_ny, "Day After New Year's"))
    
    low_volume_days = sorted(low_volume_days)
    _low_volume_cache[year] = low_volume_days
    return low_volume_days


def calculate_early_close_days(year: int) -> List[Tuple[date, str, str]]:
    """Calculate early close days (uses shared calculator if available)"""
    # Check cache first
    if year in _early_close_cache:
        return _early_close_cache[year]
    
    if USE_SHARED_CALCULATOR:
        early_close_days = _shared_calculate_early_close_days(year)
        _early_close_cache[year] = early_close_days
        return early_close_days
    
    # Inline implementation (fallback)
    early_close_days = []
    
    # July 3 early close: only if July 4 is Tue-Fri (weekday) AND July 3 is also a weekday
    july3 = date(year, 7, 3)
    july4 = date(year, 7, 4)
    # Check if July 4 is observed on July 4 (not observed on July 3 or 5)
    july4_observed = observed_date(july4)
    if july4_observed == july4 and july4.weekday() in [1, 2, 3, 4] and july3.weekday() < 5:
        # July 4 is Tue-Fri and observed on July 4, and July 3 is a weekday
        early_close_days.append((july3, "Independence Day Eve", "13:00"))
    
    thanksgiving = get_nth_weekday_of_month(year, 11, 3, 4)
    black_friday = thanksgiving + timedelta(days=1)
    early_close_days.append((black_friday, "Black Friday", "13:00"))
    
    # Christmas Eve early close: only if Christmas is a weekday
    christmas = date(year, 12, 25)
    christmas_observed = observed_date(christmas)
    if christmas_observed == christmas and christmas.weekday() in [0, 1, 2, 3, 4]:
        # Christmas is a weekday and observed on Dec 25
        christmas_eve = date(year, 12, 24)
        if christmas_eve.weekday() < 5:  # Christmas Eve must also be a weekday
            early_close_days.append((christmas_eve, "Christmas Eve", "13:00"))
    
    early_close_days = sorted(early_close_days)
    _early_close_cache[year] = early_close_days
    return early_close_days


def get_holiday_info(check_date: Optional[date] = None) -> Dict:
    """
    Get comprehensive holiday information for a given date
    
    Returns:
        Dict with keys:
        - is_market_closed: bool
        - is_us_holiday: bool
        - holiday_name: str or None
        - is_low_volume_holiday: bool
        - is_early_close: bool
        - early_close_time_et: str or None (HH:MM format)
    """
    if check_date is None:
        check_date = now_et().date()
    
    settings = get_settings()
    if not settings.holidays_enabled:
        return {
            "is_market_closed": False,
            "is_us_holiday": False,
            "holiday_name": None,
            "is_low_volume_holiday": False,
            "is_early_close": False,
            "early_close_time_et": None
        }
    
    # Treat weekends as market closed
    if check_date.weekday() >= 5:
        return {
            "is_market_closed": True,
            "is_us_holiday": False,
            "holiday_name": "Weekend",
            "is_low_volume_holiday": False,
            "is_early_close": False,
            "early_close_time_et": None
        }
    
    # Calculate holidays for current year and adjacent years (to catch observed holidays
    # that spill over, e.g., New Year's observed on Dec 31 of prior year)
    years = {check_date.year - 1, check_date.year, check_date.year + 1}
    bank_holidays = []
    for y in years:
        bank_holidays.extend(calculate_us_holidays(y))
    
    # Check bank holidays (market closed)
    for holiday_date, holiday_name in bank_holidays:
        if holiday_date == check_date:
            return {
                "is_market_closed": True,
                "is_us_holiday": True,
                "holiday_name": holiday_name,
                "is_low_volume_holiday": False,
                "is_early_close": False,
                "early_close_time_et": None
            }
    
    # Check early close days
    early_close_days = calculate_early_close_days(check_date.year)
    for close_date, close_name, close_time_str in early_close_days:
        if close_date == check_date:
            return {
                "is_market_closed": False,
                "is_us_holiday": False,
                "holiday_name": close_name,  # Set holiday_name for early close
                "is_low_volume_holiday": True,  # Early close days are also low volume
                "is_early_close": True,
                "early_close_time_et": close_time_str
            }
    
    # Check low-volume holidays
    low_volume_days = calculate_low_volume_holidays(check_date.year)
    for low_vol_date, low_vol_name in low_volume_days:
        if low_vol_date == check_date:
            return {
                "is_market_closed": False,
                "is_us_holiday": False,
                "holiday_name": low_vol_name,  # Set holiday_name for low volume
                "is_low_volume_holiday": True,
                "is_early_close": False,
                "early_close_time_et": None
            }
    
    return {
        "is_market_closed": False,
        "is_us_holiday": False,
        "holiday_name": None,
        "is_low_volume_holiday": False,
        "is_early_close": False,
        "early_close_time_et": None
    }


def get_macro_event_info(check_date: Optional[date] = None) -> Dict:
    """
    Get macro event information for a given date (stub implementation)
    
    Returns:
        Dict with keys:
        - is_macro_event_day: bool
        - macro_events: List[str]
        - is_fed_day: bool
    """
    if check_date is None:
        check_date = now_et().date()
    
    settings = get_settings()
    date_str = check_date.strftime("%Y-%m-%d")
    
    # Check if date is in macro event list (stub)
    macro_events = []
    is_fed_day = False
    
    # Fix: Use macro_event_dates_list directly (safer)
    dates = getattr(settings, "macro_event_dates_list", None) or []
    if date_str in dates:
        macro_events.append("FOMC Announcement")  # Placeholder
        is_fed_day = True
    
    return {
        "is_macro_event_day": len(macro_events) > 0,
        "macro_events": macro_events,
        "is_fed_day": is_fed_day
    }


def get_calendar_tags(check_date: Optional[date] = None) -> Dict:
    """
    Get all calendar tags for a given date (holiday + macro events + liquidity risk)
    
    Returns:
        Dict with all calendar-related tags for snapshot
    """
    if check_date is None:
        check_date = now_et().date()
    
    holiday_info = get_holiday_info(check_date)
    macro_info = get_macro_event_info(check_date)
    
    # Determine liquidity risk flag
    liquidity_risk = (
        holiday_info["is_market_closed"] or
        holiday_info["is_low_volume_holiday"] or
        macro_info["is_macro_event_day"]
    )
    
    return {
        **holiday_info,
        **macro_info,
        "liquidity_risk_flag": liquidity_risk
    }


def get_early_close_time(check_date: Optional[date] = None) -> Optional[time]:
    """Get early close time for a date if applicable, otherwise None"""
    holiday_info = get_holiday_info(check_date)
    
    if holiday_info["is_early_close"] and holiday_info["early_close_time_et"]:
        return parse_time_string(holiday_info["early_close_time_et"])
    
    return None
