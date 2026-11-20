import json
from pathlib import Path

import pytest

from mfdataindia.nse import contract, sch44, sch44_reconcile as review

SOURCE = (Path(__file__).parent / 'fixtures/nse01/sch44_synthetic.psv').read_bytes()
ISIN, OTHER_ISIN = 'INZZ00000019', 'INZZ00000027'


def source(*, amc='FIXTURE_MF', flag='N', competing=False, marker='Current'):
    header, row, *_ = SOURCE.decode().splitlines()
    cells = row.split('|')
    cells[4:6] = [ISIN, amc]
    cells[7:9] = ['NORMAL', f'{marker} title ignored']
    cells[26:28] = ['Y', flag]
    rows = ['|'.join(cells)]
    if competing:
        cells[4] = OTHER_ISIN
        rows.append('|'.join(cells))
    return ('\n'.join([header, *rows]) + '\n').encode()


def catalogue(slot, plan='REGULAR'):
    fund = {
        'amfi_scheme_code': 100001, 'amfi_amc_name': 'Fixture Mutual Fund',
        'scheme_name': 'Catalogue title ignored', 'plan_type': plan, 'option_type': 'IDCW',
        'in_scope': plan == 'REGULAR',
        **{key: ISIN if key == slot else None for key in (review.GROWTH_SLOT, review.REINVEST_SLOT)},
    }
    return contract.canonical({'format': sch44.CATALOGUE_FORMAT, 'as_of': '2026-11-17',
                               'dataset_version': 'fixture', 'funds': [fund]})


def amfi(slot, date='14-Nov-2026', plan='Regular Plan', option='Monthly IDCW Option', *, competing=False):
    payout, reinvest = (ISIN, '-') if slot == review.GROWTH_SLOT else ('-', ISIN)
    return (
        'Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date\n'
        'Open Ended Schemes (Other Scheme - Other ETFs)\n\n'
        'Fixture Mutual Fund\n'
        f'100001;{payout};{reinvest};Regular Plan IDCW in title;{plan};{option};10;{date}\n'
        + (f'100002;{payout};{reinvest};Competing variant;Regular Plan;Monthly IDCW Option;10;{date}\n'
           if competing else '')
    ).encode()


def reconcile(slot=review.REINVEST_SLOT, *, flag='N', issuer='FIXTURE_MF', earlier_competing=False,
              current_competing=False, explicit_plan='Regular Plan', explicit_option='Monthly IDCW Option',
              second_option='Monthly IDCW Option', plan='REGULAR', amfi_competing=False,
              earlier_flag='N'):
    return review.report(
        source(amc=issuer, flag=flag, competing=current_competing),
        catalogue(slot, plan),
        source(amc=issuer, flag=earlier_flag, competing=earlier_competing, marker='Retained'),
        amfi(slot, plan=explicit_plan, option=explicit_option, competing=amfi_competing),
        amfi(slot, date='16-Nov-2026', plan=explicit_plan, option=second_option),
    )


@pytest.mark.parametrize(('flag', 'slot'), [('N', review.REINVEST_SLOT), ('Y', review.GROWTH_SLOT)])
def test_flag_disagreement_retains_both_facts(flag, slot):
    result = reconcile(slot, flag=flag)
    assert result['counts'] == {'EXACT': 1, 'UNMATCHED': 0, 'AMBIGUOUS': 0, 'OUT_OF_SCOPE': 0}
    row, = result['rows']
    assert row['source_reinvestment_flag'] == flag
    assert row['amfi_isin_slot'] == slot
    assert row['flag_slot_disagreement'] == review.DISAGREEMENT
    assert row['nse_scheme_code'] == 'SYNTH_A'
    assert row['earlier_nse_line'] == 2
    assert row['amfi_code'] == 100001 and row['amfi_plan'] == 'REGULAR' and row['amfi_option'] == 'IDCW'
    assert result['flag_slot_disagreement_counts']['EXACT'] == 1
    assert result['publication'] == 'NOT_ATTEMPTED'
    assert result['source_scope'] == 'UNVERIFIED' and result['effective_date'] is None


@pytest.mark.parametrize(('kwargs', 'reason'), [
    ({'issuer': 'MISSING_MF'}, 'ISSUER_NOT_CORROBORATED'),
    ({'explicit_plan': ''}, 'EXPLICIT_AMFI_VARIANT_NOT_CORROBORATED'),
    ({'explicit_option': ''}, 'EXPLICIT_AMFI_VARIANT_NOT_CORROBORATED'),
    ({'second_option': 'Quarterly IDCW Option'}, 'EXPLICIT_AMFI_VARIANT_NOT_CORROBORATED'),
    ({'amfi_competing': True}, 'EXPLICIT_AMFI_VARIANT_NOT_CORROBORATED'),
    ({'earlier_competing': True}, 'NON_UNIQUE_EARLIER_NSE_SCHEME_KEY'),
    ({'current_competing': True}, 'NON_UNIQUE_CURRENT_NSE_SCHEME_KEY'),
])
def test_missing_or_conflicting_evidence_does_not_resolve(kwargs, reason):
    result = reconcile(**kwargs)
    first = result['rows'][0]
    assert first['status'] == 'AMBIGUOUS' and first['reason'] == reason
    assert first['amfi_isin_slot'] is None
    assert first['source_reinvestment_flag'] == 'N'
    assert result['counts']['EXACT'] == 0


def test_direct_is_verified_but_out_of_scope():
    result = reconcile(plan='DIRECT', explicit_plan='Direct Plan')
    row, = result['rows']
    assert result['counts']['OUT_OF_SCOPE'] == 1
    assert (row['status'], row['amfi_plan'], row['amfi_isin_slot']) == (
        'OUT_OF_SCOPE', 'DIRECT', review.REINVEST_SLOT,
    )
    assert row['source_reinvestment_flag'] == 'N'


def test_cli_runs_offline_and_preserves_inputs(tmp_path, capsys):
    slot = review.REINVEST_SLOT
    inputs = {
        'source': source(), 'catalogue': catalogue(slot),
        'earlier-source': source(marker='Retained'), 'amfi-first': amfi(slot),
        'amfi-second': amfi(slot, date='16-Nov-2026'),
    }
    args = []
    paths = {}
    for name, raw in inputs.items():
        path = tmp_path / name
        path.write_bytes(raw)
        paths[name] = path
        args.extend((f'--{name}', str(path)))
    assert review.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['profile'] == review.PROFILE and result['counts']['EXACT'] == 1
    assert sorted(tmp_path.iterdir()) == sorted(paths.values())
    assert {name: path.read_bytes() for name, path in paths.items()} == inputs
    with pytest.raises(contract.ImportError, match='NSE_EVIDENCE_MUST_BE_DISTINCT'):
        review.report(inputs['source'], inputs['catalogue'], inputs['source'],
                      inputs['amfi-first'], inputs['amfi-second'])


@pytest.mark.parametrize(("flag", "slot"), [("N", review.REINVEST_SLOT), ("Y", review.GROWTH_SLOT)])
def test_current_only_nse_route_has_distinct_namespace_and_no_invented_history(flag, slot):
    current = source(flag=flag)
    earlier = source(marker="Retained").replace(b"SYNTH_A", b"OLDER_DIFFERENT_ROUTE")
    result = review.report(
        current, catalogue(slot), earlier, amfi(slot), amfi(slot, date="16-Nov-2026")
    )
    (row,) = result["rows"]
    assert row["status"] == "EXACT" and row["amfi_code"] == 100001
    assert row["nse_scheme_code"] == "SYNTH_A" and row["earlier_nse_line"] is None
    assert row["amfi_isin_slot"] == slot and row["source_reinvestment_flag"] == flag
    assert row["flag_slot_disagreement"] == review.DISAGREEMENT


def test_retained_route_rebound_still_blocks_current_only_rule():
    old = source(marker="Retained").replace(ISIN.encode(), OTHER_ISIN.encode())
    row = review.report(
        source(),
        catalogue(review.REINVEST_SLOT),
        old,
        amfi(review.REINVEST_SLOT),
        amfi(review.REINVEST_SLOT, date="16-Nov-2026"),
    )["rows"][0]
    assert row["status"] == "AMBIGUOUS" and row["reason"] == "NSE_SCHEME_CODE_HISTORY_CONFLICT"


@pytest.mark.parametrize("evidence", ["issuer", "plan", "option", "periodicity", "slot"])
def test_current_only_route_cannot_waive_other_variant_evidence(evidence):
    slot = review.REINVEST_SLOT
    current = source(amc="UNKNOWN_MF" if evidence == "issuer" else "FIXTURE_MF")
    earlier = source(marker="Retained").replace(b"SYNTH_A", b"OLD_ROUTE")
    first = amfi(
        slot,
        plan="" if evidence == "plan" else "Regular Plan",
        option="" if evidence == "option" else "Monthly IDCW Option",
        competing=evidence == "slot",
    )
    second = amfi(
        slot,
        date="16-Nov-2026",
        option="Quarterly IDCW Option" if evidence == "periodicity" else "Monthly IDCW Option",
    )
    row = review.report(current, catalogue(slot), earlier, first, second)["rows"][0]
    assert row["status"] != "EXACT" and row["amfi_isin_slot"] is None


def test_direct_current_only_route_keeps_scope_boundary():
    slot = review.REINVEST_SLOT
    earlier = source(marker="Retained").replace(b"SYNTH_A", b"OLD_ROUTE")
    row = review.report(
        source(),
        catalogue(slot, "DIRECT"),
        earlier,
        amfi(slot, plan="Direct Plan"),
        amfi(slot, date="16-Nov-2026", plan="Direct Plan"),
    )["rows"][0]
    assert row["status"] == "OUT_OF_SCOPE" and row["amfi_plan"] == "DIRECT"
