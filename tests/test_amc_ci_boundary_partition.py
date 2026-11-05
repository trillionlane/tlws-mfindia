"""New AMC database composition gates run early; every test still runs once."""
from test_ci_test_partition import Partition,Item,collect,population


def test_amc_native_composition_is_early_without_dropping_reordering_or_repeating_tests():
    prior=population()
    control=[Item('tests/test_ingestion_gate_postgres.py::test_concurrency[core]',True),
             Item('tests/test_ingestion_gate_postgres.py::test_current_main[core]',True),
             Item('tests/test_monthly_derivation_replay_postgres.py::test_replay[core]',True)]
    unit=Item('tests/test_ingestion_gate_postgres.py::test_unit_only',False)
    baseline=Partition('focused')
    old_early,old_later=collect(baseline,prior)
    first=Partition('focused')
    early,later=collect(first,[*prior,*control,unit])
    assert early==[*old_early,*[item.nodeid for item in control]]
    assert later==[*old_later,unit.nodeid]
    second=Partition('remainder',{'exit_code':0,'inventory':first.inventory})
    second_later,second_early=collect(second,[*prior,*control,unit])
    assert second_later==later and second_early==early
    assert not set(early)&set(later)
    assert len(early+later)==len(set(early+later))==len(prior)+len(control)+1
