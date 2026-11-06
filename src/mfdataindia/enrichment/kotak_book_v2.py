"""Kotak Fund Details panels, with retained v1 fields/identity rules.

The issuer's October layout has no Issuer/Instrument anchor. Read only the
explicit fund-details panel. Performance, AAUM and change tables are never a
substitute for dated closing AUM or Regular BER. Historical v1 remains intact.
"""
import calendar
import re
from decimal import Decimal

from mfdataindia.enrichment import amfi_summary_identity, kotak_book as prior, monthly_facts
from mfdataindia.review.policy import fingerprint

KEY, AMC = prior.KEY, prior.AMC
DIRECTORY, HISTORICAL = prior.DIRECTORY, prior.HISTORICAL
period, reporting_dates = prior.period, prior.reporting_dates
MONTHS = {name.casefold(): n for n in range(1, 13)
          for name in (calendar.month_name[n], calendar.month_abbr[n])}
DATE = r'([A-Za-z]+)\s+([0-9]{1,2}),?\s+(20[0-9]{2})'


def policy():
    return {**prior.policy(), 'version': 2,
            'previous_policy_hash': fingerprint(prior.policy()),
            'layout': 'explicit Fund Details / Fund Performance panels; v1 retained for earlier books',
            'aum': 'dated Monthly AUM / Rs. in Cr.; never AAUM, NAV or performance',
            'expenses': 'dated Regular Base Expense Ratio; relevant change-table columns and Regular row must be unambiguous and agree',
            'identity': prior.policy()['identity'],
            'unrecognized_or_ambiguous_fields': 'unavailable; never guessed or filled from another fund'}


def _date(month, day, year):
    from datetime import date
    try:
        return date(int(year), MONTHS[month.casefold()], int(day)).isoformat()
    except (KeyError, ValueError):
        return None


def _title(text):
    lines = [line.strip() for line in text.splitlines()[:7] if line.strip()]
    if not lines or not re.fullmatch(r'KOTAK [A-Z0-9 &()/+–-]{3,180}', lines[0]):
        return None
    heading = [lines[0]]
    for line in lines[1:]:
        if not re.fullmatch(r'[A-Z0-9 &()/+–-]{3,180}', line):
            break
        heading.append(line)
    return ' '.join(heading)


def detail_pages(extraction):
    result = prior.detail_pages(extraction)
    pages = extraction['pages']
    for index, page in enumerate(pages):
        text = page['text']
        headings = [line for line in text.splitlines()
                    if re.search(r'^\s*Fund Details\s{3,}Fund Performance\b', line)]
        title = _title(text)
        if len(headings) != 1 or title is None:
            continue
        # The performance heading is inset relative to its text. Keep an eight
        # character gutter. A changed/overlapping layout fails closed per field.
        width = headings[0].index('Fund Performance') - 8
        if not 45 <= width <= 90:
            continue
        left = '\n'.join(line[:width].strip() for line in text.splitlines())
        if not re.search(r'(?m)^Name of Fund Managers?:', left) or 'Monthly AUM as on' not in left:
            continue
        names = {prior.normalize(title)}
        names.update(prior.normalize(a) for a in re.findall(
            r'\((?:ERSTWHILE KNOWN AS|FORMERLY(?: KNOWN)? AS) ([^)]{1,250})\)',
            '\n'.join(text.splitlines()[:7]), re.I))
        following = pages[index + 1] if index + 1 < len(pages) else None
        change = following if following and _title(following['text']) == title else None
        result.append({'names': names, 'page': page, 'left': left,
                       'source_hash': extraction['source_hash'], 'layout': 'fund-details-v2',
                       'changes_page': change})
    return result


def _expense_table_issue(changes, as_of, raw):
    """An unreadable relevant table is not evidence that the panel agrees."""
    ambiguous = 'same_period_expense_table_ambiguous'
    lines = changes['text'].splitlines()
    anchors = [(i, line.index('Expense Ratio*')) for i, line in enumerate(lines)
               if 'Expense Ratio*' in line]
    if len(anchors) != 1:
        return ambiguous
    start, column = anchors[0]
    table = '\n'.join(line[column:].strip() for line in lines[start + 1:])
    if table.count('Change in Risk-o-meter') != 1:
        return ambiguous
    table = table.split('Change in Risk-o-meter', 1)[0]
    rows = [line.strip() for line in table.splitlines() if line.strip()]
    header = re.compile(r'Change In\s+([A-Za-z]+) (20[0-9]{2})\s+([A-Za-z]+) (20[0-9]{2})')
    headers = [header.fullmatch(line) for line in rows if header.fullmatch(line)]
    if len(headers) != 1 or not rows or header.fullmatch(rows[0]) is None:
        return ambiguous
    first_month, first_year, second_month, second_year = headers[0].groups()
    periods = [_date(first_month, 1, first_year), _date(second_month, 1, second_year)]
    if None in periods or periods[0] == periods[1]:
        return ambiguous
    periods = [day[:7] for day in periods]
    if as_of[:7] not in periods:
        return None  # A clearly dated other-period table does not contradict it.
    cell = r'(?:[0-9]+\.[0-9]+%|NA)'
    parsed = [re.fullmatch(r'(Regular|Direct)\s+(' + cell + r')\s+(' + cell + r')', line)
              for line in rows[1:]]
    if (not parsed or any(row is None for row in parsed)
            or len({row[1] for row in parsed}) != len(parsed)):
        return ambiguous
    regular = [row for row in parsed if row[1] == 'Regular']
    if len(regular) != 1:
        return ambiguous
    value = regular[0][2 + periods.index(as_of[:7])]
    if value == 'NA' or Decimal(value[:-1]) > Decimal('10'):
        return ambiguous
    if Decimal(value[:-1]) != Decimal(raw):
        return 'same_period_expense_values_conflict'
    return None


def fields(detail, as_of):
    if detail.get('layout') != 'fund-details-v2':
        return prior.fields(detail, as_of)
    left, text = detail['left'], detail['page']['text']
    values = {k: prior.missing(as_of, 'explicit_field_anchor_missing_or_ambiguous') for k in prior.FIELDS}
    if reporting_dates(left) != {as_of}:
        return {k: prior.missing(as_of, 'same_fund_reporting_context_unavailable') for k in prior.FIELDS}

    def accept(key, value, unit, raw, anchor):
        values[key] = {'status': 'accepted', 'value': value, 'unit': unit,
                       'scope': 'regular_plan' if key == 'base_expense_ratio' else 'fund',
                       'reason': None, 'disclosure_as_of': as_of,
                       'field_effective_date': as_of if key == 'aum' else None,
                       'date_basis': 'explicit_field_date' if key == 'aum' else 'document_context_not_field_effective_date',
                       'raw_value': raw, 'source_anchor': anchor}

    aums = re.findall(r'(?m)^Monthly AUM as on\s*([0-9][0-9,.]*)?\s*\n' + DATE
                      + r'\s*\(Rs\. in Cr\.\)\s*([0-9][0-9,.]*)?\s*$', left)
    if len(aums) == 1:
        first, month, day, year, second = aums[0]
        if bool(first) != bool(second) and _date(month, day, year) == as_of:
            raw = first or second
            accept('aum', monthly_facts.decimal(raw, high=Decimal('1000000000000')),
                   'INR_crore', raw, 'Monthly AUM / Rs. in Cr. / ' + as_of)
    benchmarks = re.findall(r'(?ms)^Benchmark Name:\s*(.{1,600}?)(?=^NAV(?:/Unit| as on)\b)', left)
    if len(benchmarks) == 1:
        candidate = ' '.join(benchmarks[0].split())
        primary = re.fullmatch(r'(.{1,250}?\bTRI\s*\(Tier\s*1\))(?:,?\s+.{1,250}\(Tier\s*2\))?', candidate, re.I)
        candidate = primary[1] if primary else candidate
        base = re.sub(r'\s*\(Tier\s*1\)$', '', candidate, flags=re.I)
        if base.casefold() in {n.casefold() for n in prior.REVIEWED_TRI_LABELS}:
            accept('benchmark_name', candidate, 'name', candidate, 'Benchmark Name / primary Tier 1 if specified')
    costs = re.findall(r'(?m)^Base Expense Ratio as on ' + DATE
                       + r'\s*\nRegular Plan\s*:?\s*([0-9]+\.[0-9]+)%\s*$', left)
    footnote = ' '.join(left.split())
    if len(costs) == 1 and re.search(r'expense ratio disclosed above represents the Base Expense Ratio\s*\(BER\)', footnote, re.I):
        month, day, year, raw = costs[0]
        if _date(month, day, year) == as_of:
            accept('base_expense_ratio', monthly_facts.decimal(raw, high=Decimal('10')),
                   'percent', raw, 'Regular Plan / dated Base Expense Ratio / ' + as_of)
            changes = detail.get('changes_page')
            if changes:
                issue = _expense_table_issue(changes, as_of, raw)
                if issue:
                    values['base_expense_ratio'] = prior.missing(as_of, issue)
    managers = re.findall(r'(?ms)^Name of Fund Managers?:\s*\n(.{1,1800}?)(?=^Minimum Investment\s*$)', left)
    if len(managers) == 1:
        # Every manager is in this complete panel, interleaved with experience
        # and start-date metadata. Stopping at the FIRST Total Experience loses
        # co-managers. Unknown content withholds the complete field instead.
        lines = [line.strip() for line in managers[0].splitlines() if line.strip()]
        metadata = r'(?:Total\s+Experience:\s*[0-9]+(?:\.[0-9]+)? years|Managing Since:\s*[0-9]{1,2}-[A-Za-z]{3}-[0-9]{2,4})'
        name_pattern = r"(?:Mr|Ms|Mrs)\. [A-Z][A-Za-z]*(?:[ .'-]+[A-Z][A-Za-z]*)*\.?"
        names = [line for line in lines if re.fullmatch(name_pattern, line)]
        if (1 <= len(names) <= 15 and len(names) == len(set(names))
                and all(re.fullmatch(name_pattern, line) or re.fullmatch(metadata, line)
                        for line in lines)
                and not any(re.search(r'\b(?:and|unknown|portfolio)\b', n, re.I) for n in names)):
            accept('fund_managers', names, 'names', ' '.join(names), 'Name of Fund Managers / complete panel')
    risks = re.findall(r'The risk of the scheme is\s+(Low to Moderate|Moderately High|Very High|Moderate|High|Low)\b', ' '.join(text.split()), re.I)
    if len(risks) == 1:
        value = {n.casefold(): n for n in ('Low', 'Low to Moderate', 'Moderate', 'Moderately High', 'High', 'Very High')}[risks[0].casefold()]
        accept('risk_level', value, 'risk_category', risks[0], 'The risk of the scheme is')
    return values


def derive(identity, bridges, details, as_of):
    """Same direct AMFI code/ISIN/fund-name selection as v1; no fuzzy matching."""
    def gap(reason):
        return {'status': 'gap', 'reason': reason,
                'fields': {k: prior.missing(as_of, reason) for k in prior.FIELDS}}
    if identity['amc'] != AMC:
        return gap('unsupported_current_identity')
    matches, reason = amfi_summary_identity.exact_matches(identity, bridges)
    if reason:
        return gap(reason)
    names = {prior.normalize(r['fund_name']) for r in matches}
    if len(names) != 1:
        return gap('issuer_family_identity_conflict')
    hits = [p for p in details if names & p['names']]
    if len(hits) != 1:
        return gap('monthly_heading_missing_or_ambiguous')
    detail = hits[0]
    values = fields(detail, as_of)
    for value in values.values():
        value.update(content_hash=detail['source_hash'], source_url=detail['source_url'],
                     detail_page=detail['page']['page'], detail_text_hash=detail['page']['text_hash'])
    usable = any(v['status'] == 'accepted' for v in values.values())
    return {'status': 'usable' if usable else 'gap', 'reason': None if usable else 'monthly_fields_unavailable',
            'fields': values, 'identity_evidence': matches, 'detail_page': detail['page']['page'],
            'detail_text_hash': detail['page']['text_hash'], 'detail_content_hash': detail['source_hash']}
