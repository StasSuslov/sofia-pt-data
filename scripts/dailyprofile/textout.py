"""Small text helpers shared by the stdout report."""

import sys


def say(text: str = '') -> None:
    """Write one line to stdout."""
    sys.stdout.write(f'{text}\n')


def comma(number: float) -> str:
    """Thousands separators, as '{n:,}'."""
    return format(number, ',')


def signed(number: float) -> str:
    """Two decimals with an explicit sign, as '{n:+.2f}'."""
    return format(number, '+.2f')


def percent(share: float) -> str:
    """One-decimal percentage, as '{share:.1%}'."""
    return format(share, '.1%')


def window_label(window: tuple) -> str:
    """'07:00-18:45' for a (lo, hi) window."""
    return '-'.join(window)


def period_label(period: dict) -> str:
    """'<key> <first>..<last> (<n> weekdays)'."""
    key = period['period_key']
    span = '..'.join((period['first_date'], period['last_date']))
    weekdays = len(period['days_in_median_mon_fri'])
    return f'{key} {span} ({weekdays} weekdays)'


def iqr_text(paired_result: dict) -> str:
    """'IQR -1.00 to +2.00' for a paired result."""
    low, high = paired_result['iqr']
    return f'IQR {signed(low)} to {signed(high)}'
