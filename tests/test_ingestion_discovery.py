"""Provider-free protocol cases; native claim/publication proof is separate."""
from datetime import datetime,timezone,timedelta
import json
import pytest
from mfdataindia.enrichment import ingestion_discovery as d
from mfdataindia.enrichment import uti_book

URL=uti_book.CATALOGUE+'?year=2026&month=November'

def row(kind):
    url=uti_book.CDN+'utimf-job/ebook/UTI Fund Watch ('+kind+')-November 2026.pdf'
    return {'name':'UTI Fund Watch ('+kind+')-November 2026','url':url,'doc':url,
            'type':'pdf','category':'Fact Sheet','month':'November','year':'2026'}


def test_empty_incomplete_and_complete_never_infer_reporting_date_from_filename():
    assert d.uti_catalogue(b'{"rows":[]}',URL).state==d.State.WAITING
    incomplete=d.uti_catalogue(json.dumps({'rows':[row('Active')]}).encode(),URL)
    assert incomplete.state==d.State.INCOMPLETE and incomplete.latest_period_found is None
    full=d.uti_catalogue(json.dumps({'rows':[row('Active'),row('Passive')]}).encode(),URL)
    assert full.state==d.State.READY and len(full.files)==2 and full.latest_period_found is None


def test_duplicate_catalogue_json_and_ambiguous_english_pair_rejected():
    with pytest.raises(ValueError):
        d.uti_catalogue(b'{"rows":[],"rows":[]}',URL)
    with pytest.raises(ValueError):
        d.uti_catalogue(json.dumps({'rows':[row('Active'),row('Active'),row('Passive')]}).encode(),URL)


def test_same_period_revision_is_a_distinct_work_and_identical_inputs_are_stable():
    documents=d.manifest([{'role':'current_active','content':b'%PDF-one'},
                          {'role':'current_passive','content':b'%PDF-two'}])
    key=d.work('uti-expanded','2026-10-31',documents,'a'*64,'b'*64)
    assert key==d.work('uti-expanded','2026-10-31',documents,'a'*64,'b'*64)
    changed=d.manifest([{'role':'current_active','content':b'%PDF-revision'},
                        {'role':'current_passive','content':b'%PDF-two'}])
    assert d.work('uti-expanded','2026-10-31',changed,'a'*64,'b'*64)['work_id']!=key['work_id']
    assert d.work('uti-expanded','2026-10-31',documents,'c'*64,'b'*64)['work_id']!=key['work_id']


def test_waiting_cadence_preserves_daily_revision_check_after_publication():
    now=datetime(2026,11,5,9,tzinfo=timezone.utc)
    last=(now-timedelta(hours=7)).isoformat()
    assert d.due('uti-expanded',now,last,waiting=True)
    assert not d.due('uti-expanded',now,last,waiting=False)
