"""Conservative US distribution parsing; a firm address never establishes scope."""
import re
_NAMES = '''AL:Alabama|AK:Alaska|AZ:Arizona|AR:Arkansas|CA:California|CO:Colorado|CT:Connecticut|DE:Delaware|FL:Florida|GA:Georgia|HI:Hawaii|ID:Idaho|IL:Illinois|IN:Indiana|IA:Iowa|KS:Kansas|KY:Kentucky|LA:Louisiana|ME:Maine|MD:Maryland|MA:Massachusetts|MI:Michigan|MN:Minnesota|MS:Mississippi|MO:Missouri|MT:Montana|NE:Nebraska|NV:Nevada|NH:New Hampshire|NJ:New Jersey|NM:New Mexico|NY:New York|NC:North Carolina|ND:North Dakota|OH:Ohio|OK:Oklahoma|OR:Oregon|PA:Pennsylvania|RI:Rhode Island|SC:South Carolina|SD:South Dakota|TN:Tennessee|TX:Texas|UT:Utah|VT:Vermont|VA:Virginia|WA:Washington|WV:West Virginia|WI:Wisconsin|WY:Wyoming|DC:District of Columbia'''
STATES = dict(item.split(':') for item in _NAMES.split('|'))
def distribution_states(text: str) -> tuple[set[str], bool]:
    raw = str(text or '').strip()
    if not raw or re.search(r'\b(except|excluding|excludes)\b', raw, re.I):
        return set(), False
    if re.search(r'\b(nationwide|all states|united states|USA)\b|\bU\.S\.', raw, re.I):
        return set(STATES), True
    # Abbreviations are case-sensitive: ordinary words "in"/"or" are not states.
    states = {code for code,name in STATES.items()
              if re.search(r'\b'+re.escape(name)+r'\b',raw,re.I)
              or re.search(r'\b'+code+r'\b',raw)}
    return states, bool(states)


def distribution_summary(text: str) -> str:
    """A short label for a recall's distribution field, e.g. "Nationwide", "27 states" or "LA, TX".

    openFDA often returns a paragraph ("Domestic: AL, AR, ... Foreign: Not applicable."); the
    full text stays in the incident evidence.
    """
    raw = str(text or '').strip()
    if not raw:
        return 'Not supplied'
    states, known = distribution_states(raw)
    if not known:
        return 'Unclear'
    if len(states) >= len(STATES) - 1 and re.search(r'\b(nationwide|all states|united states|USA)\b|\bU\.S\.', raw, re.I):
        return 'Nationwide'
    if len(states) > 6:
        return f'{len(states)} states'
    return ', '.join(sorted(states))
