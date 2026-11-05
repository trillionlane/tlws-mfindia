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
            'expenses': 'dated Regular Base Expense Ratio; conflicting same-month change-table value withheld',
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
                change = changes['text']
                anchors = [line.index('Expense Ratio*') for line in change.splitlines() if 'Expense Ratio*' in line]
                if len(anchors) == 1:
                    change = '\n'.join(line[anchors[0]:].strip() for line in change.splitlines())
                    change = change.split('Change in Risk-o-meter', 1)[0]
                else:
                    change = ''
                headers = re.findall(r'(?m)^.*Change In\s+([A-Za-z]+) (20[0-9]{2})\s+([A-Za-z]+) (20[0-9]{2})\s*$', change)
                rows = re.findall(r'(?m)^\s*Regular\s+([0-9]+\.[0-9]+)%\s+([0-9]+\.[0-9]+)%\s*$', change)
                if len(headers) == 1 and len(rows) == 1:
                    _, _, month, year = headers[0]
                    current = f'{year}-{MONTHS.get(month.casefold(), 0):02d}'
                    if current == as_of[:7] and Decimal(rows[0][1]) != Decimal(raw):
                        values['base_expense_ratio'] = prior.missing(as_of, 'same_period_expense_values_conflict')
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
